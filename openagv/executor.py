from typing import List, Any
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function, KernelArguments
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.contents.chat_history import ChatHistory
from .core import Agentable, AssetBin, UserInstruction, SystemInstruction
from .stepper import Stepper, StepperState

class StepperPlugin:
    def __init__(self, stepper: Stepper):
        self.stepper = stepper

    @kernel_function(description="Update the current step being performed", name="advance_step")
    def advance_step(self, name: str, description: str = ""):
        self.stepper.advance_step(name, description)

class SKLoopExecutor(Stepper):
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, chat_completion: OpenAIChatCompletion, uses: List[Agentable] = [], debug: bool = False):
        super().__init__()
        self.asset_bin = asset_bin
        self.user_instruction = instruction
        self.system_instruction = SystemInstruction("You are a helpful AI assistant capable of analyzing and manipulating media assets. Always use 'advance_step' to announce what you are doing before you do it.")
        self.uses = uses
        self.debug = debug
        self.kernel = Kernel()
        
        self.kernel.add_service(chat_completion)
        
        # Add StepperPlugin so the agent can report steps
        self.kernel.add_plugin(StepperPlugin(self), plugin_name="Stepper")
        
        # Add AssetBin directly as a plugin
        self.kernel.add_plugin(self.asset_bin, plugin_name="AssetBin")
        
        # Add other modules directly as plugins
        for module in uses:
            name = module.__class__.__name__
            self.kernel.add_plugin(module, plugin_name=name)

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
