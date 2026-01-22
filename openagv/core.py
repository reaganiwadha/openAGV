import opentimelineio as otio
import os
import asyncio
import subprocess
import tempfile
import json
import hashlib
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
        self.id = ""
        self.metadata = {}
        self.analyses: List['Analysis'] = []

    @agent_action(description="Get the file path of the asset")
    def get_file_path(self) -> str:
        return self.file_path
    
    @agent_action(description="Get the type of the asset")
    def get_asset_type(self) -> str:
        return self.asset_type.name

    def append_analysis(self, analysis: 'Analysis'):
        """Adds an analysis result to this asset."""
        self.analyses.append(analysis)

    # Alias for backward compatibility if needed, or just use append
    add_analysis = append_analysis

    def has_analysis_from(self, analyzer_name: str) -> bool:
        """Checks if this asset has been analyzed by the given analyzer."""
        return any(a.analyzer_name == analyzer_name for a in self.analyses)

    @agent_action(description="Get a hint about which analyzers have processed this asset")
    def get_analysis_hint(self) -> str:
        """Returns a string describing the analysis state."""
        if not self.analyses:
            return "No analyses performed."
        names = [a.analyzer_name for a in self.analyses]
        return f"Analyzed by: {', '.join(names)}"

    async def get_localized_path(self) -> str:
        """Returns the local file path of the asset."""
        # In a cloud scenario, this might download the file. 
        # Here we just return the local path.
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Asset file not found: {self.file_path}")
        return self.file_path

    async def get_audio_format(self) -> str:
        """
        Returns a path to an audio file. 
        If the asset is video, it converts it to audio using ffmpeg.
        If it's already audio, returns the path.
        """
        local_path = await self.get_localized_path()
        
        if self.asset_type == AssetType.AUDIO:
            return local_path
            
        if self.asset_type == AssetType.VIDEO:
            # Generate temp path
            temp_dir = tempfile.gettempdir()
            filename = os.path.basename(local_path)
            name, _ = os.path.splitext(filename)
            output_path = os.path.join(temp_dir, f"{name}_extracted.mp3")
            
            # Check if already exists to save time? 
            # For now, let's overwrite to ensure freshness or handle existing
            if os.path.exists(output_path):
                return output_path

            # Run ffmpeg
            # ffmpeg -i input -vn -acodec libmp3lame -y output
            try:
                # Use asyncio.to_thread with subprocess.run to avoid NotImplementedError 
                # on Windows SelectorEventLoop (common in Jupyter/embedded envs)
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["ffmpeg", "-i", local_path, "-vn", "-acodec", "libmp3lame", "-y", output_path],
                    capture_output=True,
                    check=False
                )
                
                if result.returncode != 0:
                    raise RuntimeError(f"FFmpeg failed: {result.stderr.decode()}")
                
                return output_path
            except FileNotFoundError:
                raise RuntimeError("ffmpeg not found in PATH.")
        
        raise ValueError(f"Cannot get audio format for asset type: {self.asset_type}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "asset_type": self.asset_type.name,
            "metadata": self.metadata,
            "analyses": [a.to_dict() for a in self.analyses]
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Asset':
        asset_type = AssetType[data["asset_type"]]
        file_path = data["file_path"]
        
        # Instantiate specific subclass if appropriate, or generic Asset
        if asset_type == AssetType.IMAGE:
            asset = ImageAsset(file_path)
        elif asset_type == AssetType.VIDEO:
            asset = VideoAsset(file_path)
        elif asset_type == AssetType.AUDIO:
            asset = AudioAsset(file_path)
        else:
            asset = cls(file_path, asset_type)
            
        asset.id = data.get("id", "")
        asset.metadata = data.get("metadata", {})
        if "analyses" in data:
            asset.analyses = [Analysis.from_dict(a) for a in data["analyses"]]
        return asset

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

    def _calculate_file_hash(self, file_path: str) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read and update hash string value in blocks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def add(self, file_path: str, asset_type: Optional[AssetType] = None) -> Asset:
        """
        Adds an asset. If type is not provided, it attempts to guess from extension.
        Throws FileNotFoundError if the file does not exist.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Asset file not found: {file_path}")

        if asset_type is None:
            lower_path = file_path.lower()
            if lower_path.endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif')):
                asset_type = AssetType.IMAGE
            elif lower_path.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
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

        asset.id = self._calculate_file_hash(file_path)
        self.assets.append(asset)
        return asset

    def get_asset_by_path(self, path: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.file_path == path), None)
    
    def get_asset_by_id(self, asset_id: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.id == asset_id), None)

    @agent_action(description="List all assets in the bin. Returns list of ID, filepath, and type.")
    def list_assets(self) -> str:
        return "\n".join([f"ID: {a.id} | File: {a.file_path} ({a.asset_type.name})" for a in self.assets])
    
    @agent_action(description="Get an asset path by fuzzy filename match")
    def get_asset_path(self, filename: str) -> str:
        for asset in self.assets:
            if filename in asset.file_path:
                return asset.file_path
        return "Asset not found"

    def to_dict(self) -> dict:
        return {
            "assets": [a.to_dict() for a in self.assets]
        }

    def json(self) -> str:
        """Serializes the AssetBin to a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def json_dump(self, file_path: str):
        """Serializes the AssetBin to a JSON file."""
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> 'AssetBin':
        bin = cls()
        # We manually reconstruct assets avoiding the 'add' method's existence check
        # because the files might not exist on this machine if we are just loading metadata,
        # OR we assume they must exist. 
        # The prompt implies we are just passing references around, so maybe we shouldn't strictly enforce existence on load?
        # But 'Asset' constructor calls super which just sets description.
        # However, 'add' checks existence. 
        # Let's bypass 'add' and directly append to self.assets to avoid FileNotFoundError during simple deserialization
        # (unless the user wants to validate).
        # We will assume just data restoration.
        if "assets" in data:
            bin.assets = [Asset.from_dict(a) for a in data["assets"]]
        return bin

    @classmethod
    def from_json(cls, json_str: str) -> 'AssetBin':
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_json_file(cls, file_path: str) -> 'AssetBin':
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)

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
    def __init__(self, asset_path: str, content: Any, analyzer_name: str):
        self.asset_path = asset_path
        self.content = content
        self.analyzer_name = analyzer_name
    
    def __repr__(self):
        return f"<Analysis of {self.asset_path} by {self.analyzer_name}: {self.content}>"

    def to_dict(self) -> dict:
        return {
            "asset_path": self.asset_path,
            "analyzer_name": self.analyzer_name,
            "content": self.content
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Analysis':
        return cls(
            asset_path=data["asset_path"],
            content=data["content"],
            analyzer_name=data["analyzer_name"]
        )

class Analyzer(Agentable):
    """Base class for things that analyze assets."""
    def __init__(self, name: str, description: str = "Generic Analyzer", supported_types: List[AssetType] = []):
        super().__init__(description)
        self.name = name
        self.supported_types = supported_types
        self.asset_bin: Optional[AssetBin] = None

    def set_asset_bin(self, asset_bin: AssetBin):
        self.asset_bin = asset_bin

    def can_analyze(self, asset: Asset) -> bool:
        return asset.asset_type in self.supported_types

    @agent_action(description="Analyze an asset")
    async def analyze_asset(self, asset: Asset) -> bool:
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
