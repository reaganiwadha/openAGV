import base64
import os
from typing import TYPE_CHECKING
from openai import OpenAI
from ..core import Analyzer, agent_action, AssetType

if TYPE_CHECKING:
    from ..storage import StorageBackend


class ORVisionAnalyzer(Analyzer):
    def __init__(self, client: OpenAI, model: str):
        super().__init__(
            name=f"ORVisionAnalyzer ({model})",
            description=f"Vision Analyzer using model {model}",
            supported_types=[AssetType.IMAGE],
        )
        self.client = client
        self.model = model
        self.storage: "StorageBackend | None" = None

    def set_storage(self, storage: "StorageBackend"):
        self.storage = storage

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    @agent_action(description="Analyze a video/image asset to describe its content")
    async def analyze_asset(self, asset_id: str) -> bool:
        if not self.asset_bin:
             print(f"[ERROR] AssetBin not set for {self.name}")
             return False

        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            print(f"[ERROR] Asset with ID {asset_id} not found.")
            return False

        # Resolve to local path via storage backend
        storage = self.storage or (self.asset_bin.storage if self.asset_bin else None)
        if not storage:
            print(f"[ERROR] No StorageBackend available for {self.name}")
            return False

        try:
            asset_path = asset.local_path(storage)
        except Exception as e:
            print(f"[ERROR] Could not resolve asset path: {e}")
            return False

        print(f"[DEBUG] ORVisionAnalyzer analyzing {asset_path} with {self.model}...")

        if not os.path.exists(asset_path):
            print(f"Error: Asset not found at {asset_path}")
            return False

        ext = os.path.splitext(asset_path)[1].lower()
        content = ""

        if ext in [".jpg", ".jpeg", ".png"]:
            media_type = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
            base64_image = self._encode_image(asset_path)

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in detail."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_image}"
                            },
                        },
                    ],
                }
            ]
        elif ext in [".mp4", ".mov", ".avi", ".webm"]:
            content = f"Video analysis for {asset_path} is not yet fully implemented (requires frame extraction)."
            from ..core import Analysis

            asset.append_analysis(Analysis(asset.storage_key, content, self.name))
            return True

        else:
            print(f"Unsupported file type for analysis: {ext}")
            return False

        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=300
            )
            content = response.choices[0].message.content
            if not content:
                print(
                    f"[WARN] Empty content received from model {self.model}. Raw response: {response}"
                )
                return False

            from ..core import Analysis

            asset.append_analysis(Analysis(asset.storage_key, content, self.name))
            return True

        except Exception as e:
            print(f"Error analyzing asset: {str(e)}")
            return False
