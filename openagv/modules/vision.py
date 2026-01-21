from typing import Any
from ..core import Analyzer, agent_action, AssetType

class ORVisionAnalyzer(Analyzer):
    def __init__(self, client: Any, model: str):
        super().__init__(
            description=f"Vision Analyzer using model {model}",
            supported_types=[AssetType.IMAGE, AssetType.VIDEO]
        )
        self.client = client
        self.model = model

    @agent_action(description="Analyze a video/image asset to describe its content")
    def analyze_asset(self, asset_path: str) -> str:
        # Note: In a real implementation, we would verify the asset type here or assume
        # the executor has done so.
        print(f"[DEBUG] ORVisionAnalyzer analyzing {asset_path} with {self.model}...")
        return f"Analysis result for {asset_path}: Contains a person chopping vegetables."
