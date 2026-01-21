import opentimelineio as otio
from typing import List, Optional, Any
from semantic_kernel.functions import kernel_function

class Agentable:
    """Base class for things that an agent can do actions upon."""
    def __init__(self, description: str):
        self.description = description

    def to_semantic_kernel_plugin(self) -> Any:
        """Returns the instance itself to be used as a plugin/class instance in SK."""
        return self

class Asset(Agentable):
    """Represents a media asset."""
    def __init__(self, file_path: str):
        super().__init__(f"Asset located at {file_path}")
        self.file_path = file_path
        self.metadata = {}

    @kernel_function(description="Get the file path of the asset")
    def get_file_path(self) -> str:
        return self.file_path

class AssetBin(Agentable):
    """Holds a collection of assets."""
    def __init__(self):
        super().__init__("A bin containing media assets available for use.")
        self.assets: List[Asset] = []

    def add(self, file_path: str) -> Asset:
        asset = Asset(file_path)
        self.assets.append(asset)
        return asset

    @kernel_function(description="List all assets in the bin")
    def list_assets(self) -> str:
        return ", ".join([a.file_path for a in self.assets])
    
    @kernel_function(description="Get an asset path by fuzzy filename match")
    def get_asset_path(self, filename: str) -> str:
        for asset in self.assets:
            if filename in asset.file_path:
                return asset.file_path
        return "Asset not found"

class UserInstruction:
    """Represents a user instruction/prompt."""
    def __init__(self, text: str):
        self.text = text

class Analysis:
    """The result of an analysis."""
    def __init__(self, asset_path: str, content: Any):
        self.asset_path = asset_path
        self.content = content
    
    def __repr__(self):
        return f"<Analysis of {self.asset_path}: {self.content}>"

class Analyzer(Agentable):
    """Base class for things that analyze assets."""
    def __init__(self, description: str = "Generic Analyzer"):
        super().__init__(description)

    @kernel_function(description="Analyze an asset given its file path")
    def analyze_asset(self, asset_path: str) -> str:
        raise NotImplementedError

class OTIOTimeline(Agentable):
    """Wrapper around OTIO timeline."""
    def __init__(self, name: str = "Main Timeline"):
        super().__init__(f"Timeline named {name}")
        self.timeline = otio.schema.Timeline(name=name)
        self.track = otio.schema.Track()
        self.timeline.tracks.append(self.track)
    
    @kernel_function(description="Add a clip to the timeline from an asset path")
    def add_clip(self, asset_path: str, duration_frames: int = 100) -> str:
        clip = otio.schema.Clip(name=asset_path.split("/")[-1], source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(duration_frames, 24)
        ))
        self.track.append(clip)
        return f"Added clip {asset_path} to timeline."

    @kernel_function(description="Get a summary of the timeline")
    def get_summary(self) -> str:
        return f"Timeline '{self.timeline.name}' has {len(self.track)} items."
