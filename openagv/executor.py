from typing import List, Callable, Any, Optional
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.filters import FilterTypes, FunctionInvocationContext
from semantic_kernel.contents import ChatHistory
from .core import Agentable, AssetBin, UserInstruction, SystemInstruction, Analyzer, Timeline, ChecklistManager
from .stepper import Stepper, StepperState

VIDEO_EDITOR_SYSTEM_PROMPT = """
You are an expert Autonomous Video Editor AI. Your goal is to understand the user's request and construct a high-quality video timeline using the available media assets.

**Core Responsibilities:**
1.  **Analyze Assets:** You must understand the content of the assets in the AssetBin.
    *   Use `list_possible_unanalyzed` to find assets needing analysis.
    *   Use `get_analysis_sizes` to estimate content size.
    *   Use `get_asset_analysis` to read analysis results.
    *   **CRITICAL:** Do NOT re-analyze assets that already have sufficient analysis. Check metadata first.
2.  **Construct Timeline:** Use the `Timeline` tools (e.g., `OTIOTimeline`) to assemble the video.
    *   Add clips using `add_clip_by_id`.
    *   Arrange them cohesively based on the user's story or intent.
3.  **Memory & Checklist:** Use the `ChecklistManager` to maintain a persistent state.
    *   **Plan:** At the start, use `set_checklist` to initialize a list of tasks. Pass a list of strings or a newline-separated plan.
    *   **Track:** As you complete tasks, use `mark_task_completed` (passing the task number, e.g., 1, 2) to cross them off.
    *   **Adapt:** Use `append_new_task` if you discover new necessary steps.
    *   **Verify:** Before finishing, use `get_remaining_tasks` to ensure the list is empty/all tasks are done.
4.  **Autonomous Execution:**
    *   Do NOT ask the user for clarifying questions. Infer the best course of action.
    *   If a specific detail is missing, use a reasonable default or creative choice.
    *   Continue executing tools until the request is fully satisfied.

**Constraint:**
*   Only output a final text response when the task is effectively complete (the timeline is exported or ready).
*   If you encounter an error, try to fix it yourself (e.g., try a different analyzer or asset).
"""

class SKLoopExecutor(Stepper):
    def __init__(
        self,
        asset_bin: AssetBin,
        chat_completion: OpenAIChatCompletion,
        uses: List[Agentable] = [],
        checklist_manager: Optional[ChecklistManager] = None,
        debug: bool = False,
        bug_user: bool = False,
        system_prompt: str | None = None
    ):
        super().__init__(debug=debug)
        self.asset_bin = asset_bin
        self.bug_user = bug_user

        prompt_text = system_prompt or VIDEO_EDITOR_SYSTEM_PROMPT
        if self.bug_user:
            prompt_text += "\n\n**NOTE:** You ARE allowed to ask the user for clarification if absolutely necessary."

        self.system_instruction = SystemInstruction(prompt_text)
        self.uses = uses
        self.kernel = Kernel()

        self.kernel.add_service(chat_completion)

        # Register monitoring filter
        self.kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, self._monitoring_filter)

        # Add AssetBin directly as a plugin
        self.kernel.add_plugin(self.asset_bin, plugin_name="AssetBin")

        # Add ChecklistManager as a plugin
        self.checklist_manager = checklist_manager or ChecklistManager()
        self.kernel.add_plugin(self.checklist_manager, plugin_name="ChecklistManager")

        # Add other modules directly as plugins
        for module in uses:
            name = module.__class__.__name__
            self.kernel.add_plugin(module, plugin_name=name)
            if hasattr(module, "set_asset_bin"):
                module.set_asset_bin(self.asset_bin)
            if hasattr(module, "set_storage") and self.asset_bin.storage:
                module.set_storage(self.asset_bin.storage)
            if isinstance(module, Analyzer):
                self.asset_bin.register_analyzer(module)

        # Initialize ChatHistory (empty initially, caller should populate or use default)
        self.chat_history = ChatHistory()
        self.chat_history.add_system_message(self.system_instruction.prompt)

        # Event emitter callback — set by Job to receive events
        self._event_emitter: Callable[[str, dict[str, Any]], None] | None = None

    def set_event_emitter(self, emitter: Callable[[str, dict[str, Any]], None]):
        """Register a callback to emit events (used by Job)."""
        self._event_emitter = emitter

    def _emit_event(self, event_type: str, data: dict[str, Any] | None = None):
        if self._event_emitter:
            self._event_emitter(event_type, data or {})

    async def _monitoring_filter(self, context: FunctionInvocationContext, next):
        """Filter to monitor function invocations and update steps."""
        func_name = context.function.name
        plugin_name = context.function.plugin_name

        should_track = plugin_name and plugin_name not in ["_sys", "Agent"]

        if should_track:
            self.advance_step(f"{plugin_name}.{func_name}", f"Agent is executing {func_name} from {plugin_name}")

            # Emit tool_call event
            args_dict = {k: str(v) for k, v in context.arguments.items()}
            self._emit_event("tool_call", {
                "plugin": plugin_name,
                "function": func_name,
                "args": args_dict,
            })

            if self.debug:
                args_str = ", ".join([f"{k}='{v}'" for k, v in context.arguments.items()])
                self.log(f"Invoking {plugin_name}.{func_name} with args: {{{args_str}}}", "DEBUG")

        await next(context)

        if should_track and self.debug:
            self.log(f"Result from {plugin_name}.{func_name}: {context.result}", "DEBUG")

    def set_system_instruction(self, instruction: SystemInstruction):
        """Replace the default system instruction."""
        self.system_instruction = instruction
        if len(self.chat_history) > 0:
            self.chat_history[0].content = instruction.prompt

    async def _execute_cycle(self):
        """Executes a cycle of the LLM loop with token streaming."""
        self.set_state(StepperState.PLANNING)
        self._emit_event("state_change", {"from": "IDLE", "to": "PLANNING"})

        if self.debug:
            self.log(f"Chat History: {len(self.chat_history)} messages", "DEBUG")

        service_id = next(iter(self.kernel.services.keys()))
        service = self.kernel.get_service(service_id=service_id)

        execution_settings = self.kernel.get_prompt_execution_settings_from_service_id(service_id)
        execution_settings.function_choice_behavior = FunctionChoiceBehavior.Auto()

        try:
            self._emit_event("state_change", {"from": "PLANNING", "to": "EXECUTING"})
            self.set_state(StepperState.EXECUTING)

            # Use streaming API for token-level events
            full_content = ""
            result_content = None

            async for messages in service.get_streaming_chat_message_contents(
                chat_history=self.chat_history,
                settings=execution_settings,
                kernel=self.kernel,
            ):
                for msg in messages:
                    if msg.content:
                        full_content += msg.content
                        self._emit_event("token", {
                            "content": msg.content,
                            "role": "agent",
                        })
                    result_content = msg

            # Add the final assembled response to history
            if full_content:
                self.chat_history.add_assistant_message(full_content)

            self._emit_event("agent_message", {"content": full_content})
            self.log(f"Final Agent Response: {full_content}")
            self.complete_current_step()
            self.set_state(StepperState.FINISHED)

        except Exception as e:
            self.log(f"Execution failed: {str(e)}", "ERROR")
            self._emit_event("error", {"message": str(e)})
            self.fail_current_step(str(e))
            self.set_state(StepperState.ERROR)
            raise e

    async def nudge(self, instruction: UserInstruction):
        """Advances the action by adding a new user instruction."""
        self.chat_history.add_user_message(instruction.prompt)
        self.log(f"Nudged with: {instruction.prompt}")
        await self._execute_cycle()

    async def start(self):
        """Starts the execution loop using the real LLM."""
        self.log(f"Starting execution...")
        await self._execute_cycle()
        self.log("Execution finished.")