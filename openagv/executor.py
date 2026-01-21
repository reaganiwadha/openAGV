from typing import List, Any
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function
from .core import Agentable, AssetBin, UserInstruction
from .stepper import Stepper, StepperState

def _create_sk_plugin_wrapper(agentable: Agentable) -> Any:
    """
    Dynamically creates a wrapper object for an Agentable instance 
    that exposes methods decorated with @agent_action as SK kernel functions.
    """
    class PluginWrapper:
        pass
    
    wrapper = PluginWrapper()
    
    # Inspect the agentable instance for methods with _is_agent_action
    for name in dir(agentable):
        # Skip private members
        if name.startswith("_"):
            continue
            
        attr = getattr(agentable, name)
        
        # Check if it's a method and has the marker
        if callable(attr) and getattr(attr, "_is_agent_action", False):
            description = getattr(attr, "_agent_action_description", "No description provided.")
            
            # Create a closure to capture the method
            def make_proxy(method):
                @kernel_function(description=description, name=name)
                def proxy(*args, **kwargs):
                    return method(*args, **kwargs)
                return proxy
            
            # Attach the decorated proxy method to the wrapper
            setattr(wrapper, name, make_proxy(attr))
            
    return wrapper

class SKLoopExecutor(Stepper):
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, uses: List[Agentable] = [], debug: bool = False):
        super().__init__()
        self.asset_bin = asset_bin
        self.instruction = instruction
        self.uses = uses
        self.debug = debug
        self.kernel = Kernel()
        
        # Wrap and add AssetBin
        asset_bin_wrapper = _create_sk_plugin_wrapper(self.asset_bin)
        self.kernel.add_plugin(asset_bin_wrapper, plugin_name="AssetBin")
        
        # Wrap and add other modules
        for module in uses:
            name = module.__class__.__name__
            module_wrapper = _create_sk_plugin_wrapper(module)
            self.kernel.add_plugin(module_wrapper, plugin_name=name)

    def start(self):
        """Starts the execution loop."""
        self.set_state(StepperState.PLANNING)
        self.log(f"Starting execution with instruction: '{self.instruction.text}'")
        
        if self.debug:
            self.log(f"Loaded Plugins: {list(self.kernel.plugins.keys())}", "DEBUG")

        # Define high-level steps
        self.add_steps([
            ("Intent Recognition", "Understand what the user wants."),
            ("Asset Retrieval", "Find the necessary assets."),
            ("Execution", "Perform the requested action.")
        ])
        
        # In a real scenario, we would use:
        # await self.kernel.invoke_prompt(self.instruction.text)
        # using a registered ChatCompletionService.
        
        # For this PoC, we simulate the agent's reasoning process:
        self.log("(Mocking LLM Reasoning Process)", "DEBUG")
        
        # Step 1: Intent Recognition
        self.start_next_step() # Intent Recognition
        
        # Simple keyword matching to simulate "intent recognition"
        intent_analyze = "Analyze" in self.instruction.text
        intent_chop = "chop" in self.instruction.text
        
        if intent_analyze and intent_chop:
            self.log("Agent identified intent: Analyze 'chop' asset.")
            self.complete_current_step()
            
            self.set_state(StepperState.EXECUTING)
            
            # Step 2: Asset Retrieval
            self.start_next_step() # Asset Retrieval
            self.log("Agent decided to find asset 'chop'...")
            
            # Simulate calling the function
            asset_path = self.asset_bin.get_asset_path("chop")
            self.log(f"Tool 'AssetBin.get_asset_path' returned: '{asset_path}'")
            
            if asset_path and asset_path != "Asset not found":
                self.complete_current_step()
                
                # Step 3: Execution
                self.start_next_step() # Execution
                
                # Retrieve the actual asset object to check type compatibility
                asset_obj = self.asset_bin.get_asset_by_path(asset_path)
                
                # Find an analyzer that supports this asset
                analyzer = next((
                    m for m in self.uses 
                    if hasattr(m, 'analyze_asset') and hasattr(m, 'can_analyze') and m.can_analyze(asset_obj)
                ), None)
                
                if analyzer:
                    self.log(f"Agent decided to analyze '{asset_path}' (Type: {asset_obj.asset_type.name}) using {analyzer.__class__.__name__}...")
                    result = analyzer.analyze_asset(asset_path)
                    self.log(f"Tool '{analyzer.__class__.__name__}.analyze_asset' returned: '{result}'")
                    self.complete_current_step()
                    self.set_state(StepperState.FINISHED)
                else:
                    self.log(f"Agent could not find a suitable analyzer tool for asset type {asset_obj.asset_type.name}.", "ERROR")
                    self.fail_current_step("No suitable analyzer found.")
            else:
                self.log("Agent could not find the asset.", "ERROR")
                self.fail_current_step("Asset not found.")
                # Since asset retrieval failed, we can't really proceed to execution, but let's make sure state is consistent
                self.set_state(StepperState.ERROR)
        else:
            self.log("Agent did not understand the instruction in this Mock implementation.", "WARNING")
            self.fail_current_step("Intent not recognized.")
            self.set_state(StepperState.FINISHED)

        self.log("Execution finished.")
