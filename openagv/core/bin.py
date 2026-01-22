import hashlib
import os
import json
import glob
import sqlite3
from typing import List, Optional

from .base import Agentable, agent_action
from .enums import AssetType
from .assets import Asset, ImageAsset, VideoAsset, AudioAsset
from .analyzer import Analyzer

class AssetBin(Agentable):
    """Holds a collection of assets."""
    def __init__(self):
        super().__init__("A bin containing media assets available for use.")
        self.assets: List[Asset] = []
        self.analyzers: List[Analyzer] = []

    def register_analyzer(self, analyzer: Analyzer):
        """Registers an analyzer to be tracked by the bin."""
        self.analyzers.append(analyzer)

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
            if lower_path.endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif")):
                asset_type = AssetType.IMAGE
            elif lower_path.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
                asset_type = AssetType.VIDEO
            elif lower_path.endswith((".mp3", ".wav", ".aac", ".flac")):
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
        return self._on_asset_added(asset)

    def _on_asset_added(self, asset: Asset) -> Asset:
        """Hook called after an asset is created but before adding to list.
        Subclasses can override this to implement persistence or duplicate checking."""
        self.assets.append(asset)
        return asset

    def add_wildcard(self, pattern: str) -> List[Asset]:
        """Adds assets using a wildcard pattern."""
        files = glob.glob(pattern, recursive=True)
        added_assets = []
        for file_path in files:
            if os.path.isfile(file_path):
                try:
                    asset = self.add(file_path)
                    added_assets.append(asset)
                except Exception:
                    pass
        return added_assets

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
        return f"Possible analyzers for {asset.file_path}: {', '.join(possible)}"

    @agent_action(description="List all assets and their pending analyzers")
    def list_possible_unanalyzed(self) -> str:
        results = []
        for asset in self.assets:
            possible = []
            for analyzer in self.analyzers:
                if analyzer.can_analyze(asset) and not asset.has_analysis_from(analyzer.name):
                    possible.append(analyzer.name)
            if possible:
                results.append(f"Asset: {asset.file_path} (ID: {asset.id}) -> Pending: {', '.join(possible)}")
        
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
                asset_output = [f"--- Asset: {asset.file_path} (ID: {asset.id}) ---"]
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
                output.append(f"Asset: {asset.file_path} (ID: {asset.id}) | Total: {asset_chars} chars | Details: {', '.join(details)}")
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
        """Serializes the AssetBin to a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def json_dump(self, file_path: str):
        """Serializes the AssetBin to a JSON file."""
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> 'AssetBin':
        bin = cls()
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

class SqliteAssetBin(AssetBin):
    """An AssetBin that persists to a SQLite database."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        super().__init__() 
        self.description = f"A persistent bin (SQLite) at {db_path} containing media assets."
        self._init_db()
        self._load_assets()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    file_path TEXT,
                    asset_type TEXT,
                    data TEXT
                )
            """)

    def _load_assets(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT data FROM assets")
            self.assets = []
            for row in cursor:
                try:
                    data = json.loads(row[0])
                    asset = Asset.from_dict(data)
                    asset.set_save_callback(self._persist_asset)
                    self.assets.append(asset)
                except Exception:
                    pass

    def _on_asset_added(self, asset: Asset) -> Asset:
        existing = self.get_asset_by_id(asset.id)
        if existing:
            return existing
        
        asset.set_save_callback(self._persist_asset)
        self.assets.append(asset)
        self._persist_asset(asset)
        return asset

    def _persist_asset(self, asset: Asset):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO assets (id, file_path, asset_type, data)
                VALUES (?, ?, ?, ?)
            """, (asset.id, asset.file_path, asset.asset_type.name, json.dumps(asset.to_dict())))
