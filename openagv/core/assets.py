import os
import asyncio
import subprocess
import tempfile
from typing import List, Optional

from .base import Agentable, agent_action
from .enums import AssetType
from .analysis import Analysis

class Asset(Agentable):
    """Represents a media asset."""
    def __init__(self, file_path: str, asset_type: AssetType):
        super().__init__(f"{asset_type.name} Asset located at {file_path}")
        self.file_path = file_path
        self.asset_type = asset_type
        self.id = ""
        self.metadata = {}
        self.analyses: List['Analysis'] = []
        self._save_callback = None

    def set_save_callback(self, callback):
        self._save_callback = callback
    
    def _notify_save(self):
        if self._save_callback:
            self._save_callback(self)

    @agent_action(description="Get the file path of the asset")
    def get_file_path(self) -> str:
        return self.file_path
    
    @agent_action(description="Get the type of the asset")
    def get_asset_type(self) -> str:
        return self.asset_type.name

    def append_analysis(self, analysis: 'Analysis'):
        """Adds an analysis result to this asset."""
        self.analyses.append(analysis)
        self._notify_save()

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
