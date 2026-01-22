import base64
import os
from typing import Any
from openai import OpenAI
from ..core import Analyzer, agent_action, AssetType

class ORVisionAnalyzer(Analyzer):
    def __init__(self, client: OpenAI, model: str):
        super().__init__(
            name=f"ORVisionAnalyzer ({model})",
            description=f"Vision Analyzer using model {model}",
            supported_types=[AssetType.IMAGE, AssetType.VIDEO]
        )
        self.client = client
        self.model = model

    def _encode_image(self, image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    @agent_action(description="Analyze a video/image asset to describe its content")
    def analyze_asset(self, asset_path: str) -> str:
        print(f"[DEBUG] ORVisionAnalyzer analyzing {asset_path} with {self.model}...")
        
        # Check if file exists
        if not os.path.exists(asset_path):
            return f"Error: Asset not found at {asset_path}"

        # Determine media type (simplified)
        ext = os.path.splitext(asset_path)[1].lower()
        if ext in ['.jpg', '.jpeg', '.png']:
            media_type = "image/jpeg" if ext in ['.jpg', '.jpeg'] else "image/png"
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
                            }
                        }
                    ]
                }
            ]
        elif ext in ['.mp4', '.mov', '.avi', '.webm']:
            # Note: Direct video upload support varies by provider/model.
            # OpenAI typically requires frame extraction for 'vision' models, 
            # or uses a specific video-capable model. 
            # For OpenRouter/Gemini via OpenAI-compat, some support direct video, others don't.
            # We will assume a simple "text-only" fallback or frame extraction 
            # is NOT implemented yet to keep it simple, or attempt a generic video prompt if supported.
            # BUT, standard OpenAI python client doesn't automatically handle video file upload in chat completions 
            # efficiently without frames.
            # Let's assume for now we just treat it as "unsupported for direct local upload" 
            # unless we implement frame extraction. 
            # OR, if the user implies a model that takes video?
            # Let's implement a placeholder for video that warns or (if you prefer) frame extraction.
            # Given "Agentic" nature, let's just try to send a text description request? No that fails.
            
            return f"Video analysis for {asset_path} is not yet fully implemented (requires frame extraction)."
            
        else:
             return f"Unsupported file type for analysis: {ext}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=300
            )
            content = response.choices[0].message.content
            if not content:
                print(f"[WARN] Empty content received from model {self.model}. Raw response: {response}")
                return "Analysis returned no text content. The model might not support this task or returned an empty response."
            return content
        except Exception as e:
            return f"Error analyzing asset: {str(e)}"
