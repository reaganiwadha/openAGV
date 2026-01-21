from typing import List, Any
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function
from .core import Agentable, AssetBin, UserInstruction

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

class SKLoopExecutor:
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, uses: List[Agentable] = [], debug: bool = False):
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
        print(f"[*] Starting SKLoopExecutor with instruction: '{self.instruction.text}'")
        
        if self.debug:
            print(f"[*] Loaded Plugins: {list(self.kernel.plugins.keys())}")
            # Optional: Inspect plugin functions to verify registration
            for plugin_name, plugin in self.kernel.plugins.items():
                 print(f"    - Plugin '{plugin_name}' functions: {list(plugin.functions.keys())}")

        # In a real scenario, we would use:
        # await self.kernel.invoke_prompt(self.instruction.text)
        # using a registered ChatCompletionService.
        
        # For this PoC, we simulate the agent's reasoning process:
        print("[*] (Mocking LLM Reasoning Process)")
        
        # Simple keyword matching to simulate "intent recognition"
        if "Analyze" in self.instruction.text and "chop" in self.instruction.text:
            print(" -> Agent decided to find asset 'chop'...")
            # Simulate calling the function
            asset_path = self.asset_bin.get_asset_path("chop")
            print(f" -> Tool 'AssetBin.get_asset_path' returned: '{asset_path}'")
            
            if asset_path and asset_path != "Asset not found":
                # Retrieve the actual asset object to check type compatibility
                asset_obj = self.asset_bin.get_asset_by_path(asset_path)
                
                # Find an analyzer that supports this asset
                analyzer = next((
                    m for m in self.uses 
                    if hasattr(m, 'analyze_asset') and hasattr(m, 'can_analyze') and m.can_analyze(asset_obj)
                ), None)
                
                if analyzer:
                    print(f" -> Agent decided to analyze '{asset_path}' (Type: {asset_obj.asset_type.name}) using {analyzer.__class__.__name__}...")
                    result = analyzer.analyze_asset(asset_path)
                    print(f" -> Tool '{analyzer.__class__.__name__}.analyze_asset' returned: '{result}'")
                else:
                    print(f" -> Agent could not find a suitable analyzer tool for asset type {asset_obj.asset_type.name}.")
            else:
                print(" -> Agent could not find the asset.")
        else:
            print(" -> Agent did not understand the instruction in this Mock implementation.")

        print("[*] Execution finished.")
