from typing import List
from semantic_kernel import Kernel
from semantic_kernel.functions import KernelArguments
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.filters import FilterTypes, FunctionInvocationContext
from .core import Agentable, AssetBin, UserInstruction, SystemInstruction
from .stepper import Stepper, StepperState

class SKLoopExecutor(Stepper):
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, chat_completion: OpenAIChatCompletion, uses: List[Agentable] = [], debug: bool = False):
        super().__init__(debug=debug)
        self.asset_bin = asset_bin
        self.user_instruction = instruction
        self.system_instruction = SystemInstruction("You are a helpful AI assistant capable of analyzing and manipulating media assets.")
        self.uses = uses
        self.kernel = Kernel()
        
        self.kernel.add_service(chat_completion)
        
        # Register monitoring filter
        self.kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, self._monitoring_filter)
        
        # Add AssetBin directly as a plugin
        self.kernel.add_plugin(self.asset_bin, plugin_name="AssetBin")
        
        # Add other modules directly as plugins
        for module in uses:
            name = module.__class__.__name__
            self.kernel.add_plugin(module, plugin_name=name)
            if hasattr(module, "set_asset_bin"):
                module.set_asset_bin(self.asset_bin)

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

    async def start(self):
        """Starts the execution loop using the real LLM."""
        self.set_state(StepperState.PLANNING)
        self.log(f"Starting real execution with instruction: '{self.user_instruction.prompt}'")
        
        if self.debug:
            self.log(f"System Prompt: {self.system_instruction.prompt}", "DEBUG")
            self.log(f"Loaded Plugins: {list(self.kernel.plugins.keys())}", "DEBUG")

        # Enable auto function calling
        execution_settings = self.kernel.get_prompt_execution_settings_from_service_id(
            next(iter(self.kernel.services.keys()))
        )
        execution_settings.function_choice_behavior = FunctionChoiceBehavior.Auto()

        try:
            # Use KernelArguments to pass settings safely
            arguments = KernelArguments(settings=execution_settings)
            
            # Simple prompt construction
            full_prompt = f"{self.system_instruction.prompt}\n\nUser: {self.user_instruction.prompt}"
            
            result = await self.kernel.invoke_prompt(
                prompt=full_prompt,
                arguments=arguments
            )
            
            self.log(f"Final Agent Response: {result}")
            self.complete_current_step()
            self.set_state(StepperState.FINISHED)
            
        except Exception as e:
            self.log(f"Execution failed: {str(e)}", "ERROR")
            self.fail_current_step(str(e))
            self.set_state(StepperState.ERROR)

        self.log("Execution finished.")
