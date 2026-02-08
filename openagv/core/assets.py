import os
import asyncio
import subprocess
import tempfile
from typing import List, Optional, TYPE_CHECKING

from .base import Agentable, agent_action
from .enums import AssetType
from .analysis import Analysis

if TYPE_CHECKING:
    from ..storage import StorageBackend


class Asset(Agentable):
    """Represents a media asset."""
    def __init__(self, storage_key: str, asset_type: AssetType):
        super().__init__(f"{asset_type.name} Asset at {storage_key}")
        self.storage_key = storage_key
        self.asset_type = asset_type
        self.id = ""
        self.metadata = {}
        self.analyses: List['Analysis'] = []

    @agent_action(description="Get the storage key of the asset")
    def get_storage_key(self) -> str:
        return self.storage_key

    @agent_action(description="Get the type of the asset")
    def get_asset_type(self) -> str:
        return self.asset_type.name

    def local_path(self, storage: "StorageBackend") -> str:
        """Resolve to a real local file path via the storage backend.

        Use this when you need a real file on disk (FFmpeg, PIL, etc.).
        """
        return storage.load_to_temp(self.storage_key)

    def append_analysis(self, analysis: 'Analysis'):
        """Adds an analysis result to this asset."""
        self.analyses.append(analysis)

    add_analysis = append_analysis

    def has_analysis_from(self, analyzer_name: str) -> bool:
        """Checks if this asset has been analyzed by the given analyzer."""
        return any(a.analyzer_name == analyzer_name for a in self.analyses)

    @agent_action(description="Get a hint about which analyzers have processed this asset")
    def get_analysis_hint(self) -> str:
        if not self.analyses:
            return "No analyses performed."
        names = [a.analyzer_name for a in self.analyses]
        return f"Analyzed by: {', '.join(names)}"

    async def get_audio_format(self, storage: "StorageBackend") -> str:
        """Returns a path to an audio file.

        If the asset is video, converts to audio using ffmpeg.
        If already audio, returns the local path.
        """
        local = self.local_path(storage)

        if self.asset_type == AssetType.AUDIO:
            return local

        if self.asset_type == AssetType.VIDEO:
            temp_dir = tempfile.gettempdir()
            name, _ = os.path.splitext(os.path.basename(local))
            output_path = os.path.join(temp_dir, f"{name}_extracted.mp3")

            if os.path.exists(output_path):
                return output_path

            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["ffmpeg", "-i", local, "-vn", "-acodec", "libmp3lame", "-y", output_path],
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
            "storage_key": self.storage_key,
            "asset_type": self.asset_type.name,
            "metadata": self.metadata,
            "analyses": [a.to_dict() for a in self.analyses]
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Asset':
        asset_type = AssetType[data["asset_type"]]
        storage_key = data["storage_key"]

        if asset_type == AssetType.IMAGE:
            asset = ImageAsset(storage_key)
        elif asset_type == AssetType.VIDEO:
            asset = VideoAsset(storage_key)
        elif asset_type == AssetType.AUDIO:
            asset = AudioAsset(storage_key)
        else:
            asset = cls(storage_key, asset_type)

        asset.id = data.get("id", "")
        asset.metadata = data.get("metadata", {})
        if "analyses" in data:
            asset.analyses = [Analysis.from_dict(a) for a in data["analyses"]]
        return asset


class ImageAsset(Asset):
    def __init__(self, storage_key: str):
        super().__init__(storage_key, AssetType.IMAGE)


class VideoAsset(Asset):
    def __init__(self, storage_key: str):
        super().__init__(storage_key, AssetType.VIDEO)


class AudioAsset(Asset):
    def __init__(self, storage_key: str):
        super().__init__(storage_key, AssetType.AUDIO)
