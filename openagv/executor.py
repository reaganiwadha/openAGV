from typing import List
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
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, chat_completion: OpenAIChatCompletion, uses: List[Agentable] = [], debug: bool = False, bug_user: bool = False):
        super().__init__(debug=debug)
        self.asset_bin = asset_bin
        self.user_instruction = instruction
        self.bug_user = bug_user
        
        system_prompt = VIDEO_EDITOR_SYSTEM_PROMPT
        if self.bug_user:
            system_prompt += "\n\n**NOTE:** You ARE allowed to ask the user for clarification if absolutely necessary."
        
        self.system_instruction = SystemInstruction(system_prompt)
        self.uses = uses
        self.kernel = Kernel()
        
        self.kernel.add_service(chat_completion)
        
        # Register monitoring filter
        self.kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, self._monitoring_filter)
        
        # Add AssetBin directly as a plugin
        self.kernel.add_plugin(self.asset_bin, plugin_name="AssetBin")
        
        # Add ChecklistManager as a plugin
        self.checklist_manager = ChecklistManager()
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

        # Initialize ChatHistory
        self.chat_history = ChatHistory()
        self.chat_history.add_system_message(self.system_instruction.prompt)
        self.chat_history.add_user_message(self.user_instruction.prompt)

    async def _monitoring_filter(self, context: FunctionInvocationContext, next):
        """Filter to monitor function invocations and update steps."""
        func_name = context.function.name
        plugin_name = context.function.plugin_name
        
        # Don't track the main chat loop or internal functions if any
        # We also ignore if plugin_name is empty (often the case for the prompt function itself)
        should_track = plugin_name and plugin_name not in ["_sys", "Agent"]
        
        if should_track:
            self.advance_step(f"{plugin_name}.{func_name}", f"Agent is executing {func_name} from {plugin_name}")
            if self.debug:
                args_str = ", ".join([f"{k}='{v}'" for k, v in context.arguments.items()])
                self.log(f"Invoking {plugin_name}.{func_name} with args: {{{args_str}}}", "DEBUG")
            
        await next(context)
        
        if should_track and self.debug:
            self.log(f"Result from {plugin_name}.{func_name}: {context.result}", "DEBUG")

    def set_system_instruction(self, instruction: SystemInstruction):
        """Replace the default system instruction."""
        self.system_instruction = instruction
        # Reset chat history with new system prompt but keep user messages? 
        # For simplicity, we just rebuild the system message at index 0
        if len(self.chat_history) > 0:
            self.chat_history[0].content = instruction.prompt

    async def _execute_cycle(self):
        """Executes a cycle of the LLM loop."""
        self.set_state(StepperState.PLANNING)
        
        if self.debug:
            self.log(f"Chat History: {len(self.chat_history)} messages", "DEBUG")

        # Get the first service ID and the service itself
        service_id = next(iter(self.kernel.services.keys()))
        service = self.kernel.get_service(service_id=service_id)

        # Enable auto function calling
        execution_settings = self.kernel.get_prompt_execution_settings_from_service_id(service_id)
        execution_settings.function_choice_behavior = FunctionChoiceBehavior.Auto()

        try:
            # Invoke using chat service directly, passing kernel for tool execution
            result_content = await service.get_chat_message_content(
                chat_history=self.chat_history,
                settings=execution_settings,
                kernel=self.kernel
            )
            
            # Add the agent's response to history
            if result_content:
                self.chat_history.add_message(result_content)

            self.log(f"Final Agent Response: {result_content}")
            self.complete_current_step()
            self.set_state(StepperState.FINISHED)
            
        except Exception as e:
            self.log(f"Execution failed: {str(e)}", "ERROR")
            self.fail_current_step(str(e))
            self.set_state(StepperState.ERROR)

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
