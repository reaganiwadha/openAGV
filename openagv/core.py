import opentimelineio as otio
from typing import List, Optional, Any, Set
from enum import Enum, auto
from semantic_kernel.functions import kernel_function

# Alias agent_action to kernel_function for direct SK compatibility
agent_action = kernel_function

class AssetType(Enum):
    IMAGE = auto()
    VIDEO = auto()
    AUDIO = auto()
    UNKNOWN = auto()

class Agentable:
    """Base class for things that an agent can do actions upon."""
    def __init__(self, description: str):
        self.description = description

class Asset(Agentable):
    """Represents a media asset."""
    def __init__(self, file_path: str, asset_type: AssetType):
        super().__init__(f"{asset_type.name} Asset located at {file_path}")
        self.file_path = file_path
        self.asset_type = asset_type
        self.metadata = {}

    @agent_action(description="Get the file path of the asset")
    def get_file_path(self) -> str:
        return self.file_path
    
    @agent_action(description="Get the type of the asset")
    def get_asset_type(self) -> str:
        return self.asset_type.name

class ImageAsset(Asset):
    def __init__(self, file_path: str):
        super().__init__(file_path, AssetType.IMAGE)

class VideoAsset(Asset):
    def __init__(self, file_path: str):
        super().__init__(file_path, AssetType.VIDEO)

class AudioAsset(Asset):
    def __init__(self, file_path: str):
        super().__init__(file_path, AssetType.AUDIO)

class AssetBin(Agentable):
    """Holds a collection of assets."""
    def __init__(self):
        super().__init__("A bin containing media assets available for use.")
        self.assets: List[Asset] = []

    def add(self, file_path: str, asset_type: Optional[AssetType] = None) -> Asset:
        """
        Adds an asset. If type is not provided, it attempts to guess from extension.
        """
        if asset_type is None:
            lower_path = file_path.lower()
            if lower_path.endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif')):
                asset_type = AssetType.IMAGE
            elif lower_path.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                asset_type = AssetType.VIDEO
            elif lower_path.endswith(('.mp3', '.wav', '.aac', '.flac')):
                asset_type = AssetType.AUDIO
            else:
                asset_type = AssetType.UNKNOWN

        if asset_type == AssetType.IMAGE:
            asset = ImageAsset(file_path)
        elif asset_type == AssetType.VIDEO:
            asset = VideoAsset(file_path)
        elif asset_type == AssetType.AUDIO:
            asset = AudioAsset(file_path)
        else:
            asset = Asset(file_path, asset_type)

        self.assets.append(asset)
        return asset

    def get_asset_by_path(self, path: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.file_path == path), None)

    @agent_action(description="List all assets in the bin")
    def list_assets(self) -> str:
        return ", ".join([f"{a.file_path} ({a.asset_type.name})" for a in self.assets])
    
    @agent_action(description="Get an asset path by fuzzy filename match")
    def get_asset_path(self, filename: str) -> str:
        for asset in self.assets:
            if filename in asset.file_path:
                return asset.file_path
        return "Asset not found"

class Instruction:
    """Base class for instructions."""
    def __init__(self, prompt: str):
        self.prompt = prompt

class SystemInstruction(Instruction):
    """Represents a system instruction."""
    pass

class UserInstruction(Instruction):
    """Represents a user instruction/prompt."""
    pass

class Analysis:
    """The result of an analysis."""
    def __init__(self, asset_path: str, content: Any):
        self.asset_path = asset_path
        self.content = content
    
    def __repr__(self):
        return f"<Analysis of {self.asset_path}: {self.content}>"

class Analyzer(Agentable):
    """Base class for things that analyze assets."""
    def __init__(self, description: str = "Generic Analyzer", supported_types: List[AssetType] = []):
        super().__init__(description)
        self.supported_types = supported_types

    def can_analyze(self, asset: Asset) -> bool:
        return asset.asset_type in self.supported_types

    @agent_action(description="Analyze an asset given its file path")
    def analyze_asset(self, asset_path: str) -> str:
        raise NotImplementedError

class OTIOTimeline(Agentable):
    """Wrapper around OTIO timeline."""
    def __init__(self, name: str = "Main Timeline"):
        super().__init__(f"Timeline named {name}")
        self.timeline = otio.schema.Timeline(name=name)
        self.track = otio.schema.Track()
        self.timeline.tracks.append(self.track)
    
    @agent_action(description="Add a clip to the timeline from an asset path")
    def add_clip(self, asset_path: str, duration_frames: int = 100) -> str:
        clip = otio.schema.Clip(name=asset_path.split("/")[-1], source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(duration_frames, 24)
        ))
        self.track.append(clip)
        return f"Added clip {asset_path} to timeline."

    @agent_action(description="Get a summary of the timeline")
    def get_summary(self) -> str:
        return f"Timeline '{self.timeline.name}' has {len(self.track)} items."
