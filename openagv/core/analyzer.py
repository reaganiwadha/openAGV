from typing import List, Optional, TYPE_CHECKING
from .base import Agentable, agent_action
from .enums import AssetType
from .assets import Asset

if TYPE_CHECKING:
    from .bin import AssetBin

class Analyzer(Agentable):
    """Base class for things that analyze assets."""
    def __init__(self, name: str, description: str = "Generic Analyzer", supported_types: List[AssetType] = []):
        super().__init__(description)
        self.name = name
        self.supported_types = supported_types
        self.asset_bin: Optional['AssetBin'] = None

    def set_asset_bin(self, asset_bin: 'AssetBin'):
        self.asset_bin = asset_bin

    def can_analyze(self, asset: 'Asset') -> bool:
        return asset.asset_type in self.supported_types

    @agent_action(description="Analyze an asset by its ID")
    async def analyze_asset(self, asset_id: str) -> bool:
        raise NotImplementedError
