import hashlib
import os
import json
from typing import List, Optional, TYPE_CHECKING

from .base import Agentable, agent_action
from .enums import AssetType
from .assets import Asset, ImageAsset, VideoAsset, AudioAsset

if TYPE_CHECKING:
    from ..storage import StorageBackend


class AssetBin(Agentable):
    """Holds a collection of assets.

    Uses a StorageBackend for file operations. If no storage is provided,
    file operations (add from local path) will raise.
    """
    def __init__(self, storage: Optional["StorageBackend"] = None):
        super().__init__("A bin containing media assets available for use.")
        self.assets: List[Asset] = []
        self.analyzers = []
        self.storage: Optional["StorageBackend"] = storage

    def set_storage(self, storage: "StorageBackend"):
        self.storage = storage

    def register_analyzer(self, analyzer):
        """Registers an analyzer to be tracked by the bin."""
        self.analyzers.append(analyzer)

    def _calculate_file_hash(self, file_path: str) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _detect_asset_type(self, path: str) -> AssetType:
        lower_path = path.lower()
        if lower_path.endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif")):
            return AssetType.IMAGE
        elif lower_path.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
            return AssetType.VIDEO
        elif lower_path.endswith((".mp3", ".wav", ".aac", ".flac")):
            return AssetType.AUDIO
        return AssetType.UNKNOWN

    def add(self, file_path: str, asset_type: Optional[AssetType] = None) -> Asset:
        """Add an asset from a local file path.

        The file is stored into the storage backend and the asset
        receives a storage_key. Requires a storage backend to be set.
        """
        if not self.storage:
            raise RuntimeError("No StorageBackend set on AssetBin. Call set_storage() first.")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Asset file not found: {file_path}")

        if asset_type is None:
            asset_type = self._detect_asset_type(file_path)

        # Calculate hash for dedup and storage key
        file_hash = self._calculate_file_hash(file_path)
        ext = os.path.splitext(file_path)[1]
        storage_key = f"assets/{file_hash}{ext}"

        # Check for existing asset with same id (dedup)
        existing = self.get_asset_by_id(file_hash)
        if existing:
            return existing

        # Store file via backend
        self.storage.store(file_path, storage_key)

        # Create asset
        if asset_type == AssetType.IMAGE:
            asset = ImageAsset(storage_key)
        elif asset_type == AssetType.VIDEO:
            asset = VideoAsset(storage_key)
        elif asset_type == AssetType.AUDIO:
            asset = AudioAsset(storage_key)
        else:
            asset = Asset(storage_key, asset_type)

        asset.id = file_hash
        self.assets.append(asset)
        return asset

    def add_wildcard(self, pattern: str) -> List[Asset]:
        """Add assets using a wildcard/glob pattern."""
        import glob as globmod
        files = globmod.glob(pattern, recursive=True)
        added_assets = []
        for file_path in files:
            if os.path.isfile(file_path):
                try:
                    asset = self.add(file_path)
                    added_assets.append(asset)
                except Exception:
                    pass
        return added_assets

    def get_asset_by_path(self, storage_key: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.storage_key == storage_key), None)

    def get_asset_by_id(self, asset_id: str) -> Optional[Asset]:
        return next((a for a in self.assets if a.id == asset_id), None)

    @agent_action(description="List all assets in the bin. Returns list of ID, storage key, and type.")
    def list_assets(self) -> str:
        return "\n".join([f"ID: {a.id} | Key: {a.storage_key} ({a.asset_type.name})" for a in self.assets])

    @agent_action(description="Get an asset storage key by fuzzy filename match")
    def get_asset_path(self, filename: str) -> str:
        for asset in self.assets:
            if filename in asset.storage_key:
                return asset.storage_key
        return "Asset not found"

    @agent_action(description="List analyzers that can still be run on a specific asset (by ID)")
    def list_possible_analyzers(self, asset_id: str) -> str:
        asset = self.get_asset_by_id(asset_id)
        if not asset:
            return "Asset not found."

        possible = []
        for analyzer in self.analyzers:
            if analyzer.can_analyze(asset) and not asset.has_analysis_from(analyzer.name):
                possible.append(analyzer.name)

        if not possible:
            return "No pending analyzers for this asset."
        return f"Possible analyzers for {asset.storage_key}: {', '.join(possible)}"

    @agent_action(description="List all assets and their pending analyzers")
    def list_possible_unanalyzed(self) -> str:
        results = []
        for asset in self.assets:
            possible = []
            for analyzer in self.analyzers:
                if analyzer.can_analyze(asset) and not asset.has_analysis_from(analyzer.name):
                    possible.append(analyzer.name)
            if possible:
                results.append(f"Asset: {asset.storage_key} (ID: {asset.id}) -> Pending: {', '.join(possible)}")

        if not results:
            return "No pending analyses found."
        return "\n".join(results)

    @agent_action(description="Get the analyses for a specific asset by ID. Optional: limit character length.")
    def get_asset_analysis(self, asset_id: str, max_chars: int = -1) -> str:
        asset = self.get_asset_by_id(asset_id)
        if not asset:
            return "Asset not found."

        if not asset.analyses:
            return f"No analyses found for asset {asset_id}."

        output = []
        for analysis in asset.analyses:
            content_str = str(analysis.content)
            if max_chars > 0 and len(content_str) > max_chars:
                content_str = content_str[:max_chars] + "... (truncated)"
            output.append(f"[{analysis.analyzer_name}]: {content_str}")

        return "\n".join(output)

    @agent_action(description="Get all analyses for all assets. Optional: limit character length per analysis.")
    def get_all_assets_analysis(self, max_chars: int = -1) -> str:
        output = []
        for asset in self.assets:
            if asset.analyses:
                asset_output = [f"--- Asset: {asset.storage_key} (ID: {asset.id}) ---"]
                for analysis in asset.analyses:
                    content_str = str(analysis.content)
                    if max_chars > 0 and len(content_str) > max_chars:
                        content_str = content_str[:max_chars] + "... (truncated)"
                    asset_output.append(f"[{analysis.analyzer_name}]: {content_str}")
                output.append("\n".join(asset_output))

        if not output:
            return "No analyses found in bin."
        return "\n\n".join(output)

    @agent_action(description="Get the estimated size (character count) of analyses for each asset.")
    def get_analysis_sizes(self) -> str:
        output = []
        total_chars = 0
        for asset in self.assets:
            asset_chars = 0
            details = []
            for analysis in asset.analyses:
                chars = len(str(analysis.content))
                asset_chars += chars
                details.append(f"{analysis.analyzer_name}: {chars} chars")

            if asset_chars > 0:
                output.append(f"Asset: {asset.storage_key} (ID: {asset.id}) | Total: {asset_chars} chars | Details: {', '.join(details)}")
                total_chars += asset_chars

        if not output:
            return "No analyses found."

        output.append(f"--- Grand Total: {total_chars} chars ---")
        return "\n".join(output)

    def to_dict(self) -> dict:
        return {
            "assets": [a.to_dict() for a in self.assets]
        }

    def json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict, storage: Optional["StorageBackend"] = None) -> 'AssetBin':
        ab = cls(storage=storage)
        if "assets" in data:
            ab.assets = [Asset.from_dict(a) for a in data["assets"]]
        return ab
