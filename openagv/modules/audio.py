import httpx
import os
from typing import Optional, Any
from ..core import Analyzer, AssetType, Analysis, agent_action

class DeepgramAnalyzer(Analyzer):
    def __init__(self, api_key: str):
        super().__init__(
            name="DeepgramAnalyzer",
            description="Transcribes audio using Deepgram",
            supported_types=[AssetType.AUDIO, AssetType.VIDEO]
        )
        self.api_key = api_key

    @agent_action(description="Transcribes an audio/video asset to text")
    async def analyze_asset(self, asset_id: str) -> bool:
        if not self.asset_bin:
             print(f"[DeepgramAnalyzer] Error: AssetBin not set.")
             return False

        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            print(f"[DeepgramAnalyzer] Error: Asset with ID {asset_id} not found.")
            return False
        
        try:
            # Get audio path (converting video if necessary)
            audio_path = await asset.get_audio_format()
        except Exception as e:
            print(f"[DeepgramAnalyzer] Error preparing audio: {e}")
            return False

        if not os.path.exists(audio_path):
            print(f"[DeepgramAnalyzer] Audio file not found: {audio_path}")
            return False

        url = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true"
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/octet-stream"
        }

        try:
            with open(audio_path, "rb") as audio_file:
                content = audio_file.read()

            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, content=content, timeout=60.0)
            
            if response.status_code != 200:
                print(f"[DeepgramAnalyzer] Error from Deepgram: {response.status_code} - {response.text}")
                return False

            data = response.json()
            transcript = data.get('results', {}).get('channels', [{}])[0].get('alternatives', [{}])[0].get('transcript', '')

            if not transcript:
                print("[DeepgramAnalyzer] No transcription available (silence or error).")
                return False

            # Append Analysis to the Asset
            # We use the original asset path for the record, or we could use the audio path?
            # Typically we want to associate it with the source asset.
            original_path = asset.file_path
            analysis = Analysis(original_path, transcript, self.name)
            asset.append_analysis(analysis)

            return True

        except Exception as e:
            print(f"[DeepgramAnalyzer] Exception during transcription: {str(e)}")
            return False