from typing import List
from semantic_kernel import Kernel
from .core import Agentable, AssetBin, UserInstruction

class SKLoopExecutor:
    def __init__(self, asset_bin: AssetBin, instruction: UserInstruction, uses: List[Agentable] = [], debug: bool = False):
        self.asset_bin = asset_bin
        self.instruction = instruction
        self.uses = uses
        self.debug = debug
        self.kernel = Kernel()
        
        # Add AssetBin as a plugin
        # In SK 1.x, add_plugin can take an object with @kernel_function decorators
        self.kernel.add_plugin(self.asset_bin, plugin_name="AssetBin")
        
        # Add other modules as plugins
        for module in uses:
            name = module.__class__.__name__
            self.kernel.add_plugin(module, plugin_name=name)

    def start(self):
        """Starts the execution loop."""
        print(f"[*] Starting SKLoopExecutor with instruction: '{self.instruction.text}'")
        
        if self.debug:
            print(f"[*] Loaded Plugins: {list(self.kernel.plugins.keys())}")

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
                # Find an analyzer
                analyzer = next((m for m in self.uses if hasattr(m, 'analyze_asset')), None)
                if analyzer:
                    print(f" -> Agent decided to analyze '{asset_path}' using {analyzer.__class__.__name__}...")
                    result = analyzer.analyze_asset(asset_path)
                    print(f" -> Tool '{analyzer.__class__.__name__}.analyze_asset' returned: '{result}'")
                else:
                    print(" -> Agent could not find a suitable analyzer tool.")
            else:
                print(" -> Agent could not find the asset.")
        else:
            print(" -> Agent did not understand the instruction in this Mock implementation.")

        print("[*] Execution finished.")
