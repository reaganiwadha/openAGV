import opentimelineio as otio
import os
from typing import Optional

from .base import Agentable, agent_action
from .bin import AssetBin

class Timeline(Agentable):
    """Abstract base class for a timeline."""
    def __init__(self, description: str):
        super().__init__(description)
        self.asset_bin: Optional[AssetBin] = None

    def set_asset_bin(self, asset_bin: AssetBin):
        self.asset_bin = asset_bin

    @agent_action(description="Add a clip to the timeline using an asset ID.")
    def add_clip_by_id(self, asset_id: str, duration_seconds: float = 5.0) -> str:
        """Adds a clip to the timeline. Abstract method."""
        raise NotImplementedError

    @agent_action(description="Get a summary of the timeline.")
    def get_summary(self) -> str:
        """Returns a summary of the timeline. Abstract method."""
        raise NotImplementedError

class OTIOTimeline(Timeline):
    """Concrete implementation of a timeline using OpenTimelineIO."""
    def __init__(self, name: str = "Main Timeline"):
        super().__init__(f"OTIO Timeline named {name}")
        self.timeline = otio.schema.Timeline(name=name)
        self.track = otio.schema.Track()
        self.timeline.tracks.append(self.track)
        self.fps = 24.0

    @agent_action(description="Add a clip to the timeline using an asset ID.")
    def add_clip_by_id(self, asset_id: str, duration_seconds: float = 5.0) -> str:
        if not self.asset_bin:
            return "Error: AssetBin not connected to Timeline."
        
        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            return f"Error: Asset with ID {asset_id} not found."

        # Create a reference to the media
        # In a real scenario, we might want to use a proper MediaReference
        # For now, we point to the file path
        media_reference = otio.schema.ExternalReference(target_url=asset.file_path)

        duration_frames = int(duration_seconds * self.fps)
        clip = otio.schema.Clip(
            name=os.path.basename(asset.file_path),
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, self.fps),
                duration=otio.opentime.RationalTime(duration_frames, self.fps)
            )
        )
        self.track.append(clip)
        return f"Added clip '{asset.file_path}' ({duration_seconds}s) to timeline."

    @agent_action(description="Get a summary of the timeline.")
    def get_summary(self) -> str:
        duration = self.track.duration()
        return f"Timeline '{self.timeline.name}' has {len(self.track)} items. Total duration: {duration.to_seconds()} seconds."

    def to_otio_file(self, file_path: str):
        otio.adapters.write_to_file(self.timeline, file_path)

    @classmethod
    def from_otio(cls, file_path: str) -> 'OTIOTimeline':
        timeline = otio.adapters.read_from_file(file_path)
        # We assume the first track is the main video track for this simple agent
        # Reconstruct the wrapper
        wrapper = cls(name=timeline.name)
        wrapper.timeline = timeline
        # Find the first track or create one if empty?
        # Ideally we search for a Video track.
        tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video]
        if tracks:
            wrapper.track = tracks[0]
        elif len(timeline.tracks) > 0:
             wrapper.track = timeline.tracks[0]
        else:
            # Setup default structure if empty
            wrapper.track = otio.schema.Track()
            wrapper.timeline.tracks.append(wrapper.track)
            
        return wrapper
