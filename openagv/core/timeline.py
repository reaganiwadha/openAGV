import opentimelineio as otio
import os
from typing import Optional, Dict, TYPE_CHECKING

from .base import Agentable, agent_action
from .bin import AssetBin

if TYPE_CHECKING:
    from ..storage import StorageBackend


class Timeline(Agentable):
    """Abstract base class for a timeline."""
    def __init__(self, description: str):
        super().__init__(description)
        self.asset_bin: Optional[AssetBin] = None
        self.storage: Optional["StorageBackend"] = None

    def set_asset_bin(self, asset_bin: AssetBin):
        self.asset_bin = asset_bin

    def set_storage(self, storage: "StorageBackend"):
        self.storage = storage

    def _resolve_path(self, storage_key: str) -> str:
        """Resolve a storage key to a real local path for OTIO/FFmpeg.

        Falls back to the key itself if no storage backend is available
        (e.g. when loaded from an .otio file with absolute paths).
        """
        if self.storage:
            return self.storage.load_to_temp(storage_key)
        if self.asset_bin and self.asset_bin.storage:
            return self.asset_bin.storage.load_to_temp(storage_key)
        return storage_key

    @agent_action(description="Add a clip to the timeline using an asset ID.")
    def add_clip_by_id(self, asset_id: str, duration_seconds: float = 5.0) -> str:
        """Adds a clip to the timeline. Abstract method."""
        raise NotImplementedError

    @agent_action(description="Get a summary of the timeline.")
    def get_summary(self) -> str:
        """Returns a summary of the timeline. Abstract method."""
        raise NotImplementedError


class OTIOTimeline(Timeline):
    """
    Concrete implementation of a timeline using OpenTimelineIO.
    Supports multiple tracks for layering/overlays.
    Track 0 is the base video track, higher numbered tracks are overlays.
    """
    def __init__(self, width: int, height: int, fps: float, name: str = "Main Timeline"):
        super().__init__(f"OTIO Timeline named {name}")
        self.width = width
        self.height = height
        self.fps = fps
        self.timeline = otio.schema.Timeline(name=name)
        self.timeline.metadata["openagv"] = {
            "width": width,
            "height": height,
            "fps": fps
        }
        # Main video track (track 0)
        self.track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        self.timeline.tracks.append(self.track)

        # Dictionary to store overlay tracks by name
        self._overlay_tracks: Dict[str, otio.schema.Track] = {}

    @agent_action(description="Add a clip to the timeline using an asset ID.")
    def add_clip_by_id(self, asset_id: str, duration_seconds: float = 5.0) -> str:
        if not self.asset_bin:
            return "Error: AssetBin not connected to Timeline."

        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            return f"Error: Asset with ID {asset_id} not found."

        # Resolve storage key to real local path for OTIO/FFmpeg
        local_path = self._resolve_path(asset.storage_key)
        media_reference = otio.schema.ExternalReference(target_url=local_path)

        duration_frames = int(duration_seconds * self.fps)
        clip = otio.schema.Clip(
            name=os.path.basename(asset.storage_key),
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, self.fps),
                duration=otio.opentime.RationalTime(duration_frames, self.fps)
            )
        )
        self.track.append(clip)
        return f"Added clip '{asset.storage_key}' ({duration_seconds}s) to timeline."

    def _get_or_create_overlay_track(self, track_name: str) -> otio.schema.Track:
        """Get an existing overlay track or create a new one."""
        if track_name not in self._overlay_tracks:
            track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
            self._overlay_tracks[track_name] = track
            self.timeline.tracks.append(track)
        return self._overlay_tracks[track_name]

    @agent_action(description="Add an overlay clip at a specific time position on an overlay track.")
    def add_overlay_by_id(
        self,
        asset_id: str,
        start_time_seconds: float,
        duration_seconds: float = 5.0,
        track_name: str = "Overlay1"
    ) -> str:
        """
        Add an overlay clip at a specific time position.

        Args:
            asset_id: ID of the asset to add
            start_time_seconds: When the overlay should start (in seconds from timeline start)
            duration_seconds: Duration of the overlay
            track_name: Name of the overlay track (default: "Overlay1")

        Returns:
            Success or error message
        """
        if not self.asset_bin:
            return "Error: AssetBin not connected to Timeline."

        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            return f"Error: Asset with ID {asset_id} not found."

        overlay_track = self._get_or_create_overlay_track(track_name)

        local_path = self._resolve_path(asset.storage_key)
        media_reference = otio.schema.ExternalReference(target_url=local_path)

        duration_frames = int(duration_seconds * self.fps)
        start_frames = int(start_time_seconds * self.fps)

        clip = otio.schema.Clip(
            name=os.path.basename(asset.storage_key),
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, self.fps),
                duration=otio.opentime.RationalTime(duration_frames, self.fps)
            )
        )

        # Calculate current track duration
        current_duration = overlay_track.duration()
        current_frames = int(current_duration.to_seconds() * self.fps)

        # Add gap if needed to position the clip at the right time
        if start_frames > current_frames:
            gap_frames = start_frames - current_frames
            gap = otio.schema.Gap(
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, self.fps),
                    duration=otio.opentime.RationalTime(gap_frames, self.fps)
                )
            )
            overlay_track.append(gap)

        overlay_track.append(clip)
        return f"Added overlay '{asset.storage_key}' ({duration_seconds}s) at {start_time_seconds}s on track '{track_name}'."

    def add_overlay_by_path(
        self,
        file_path: str,
        start_time_seconds: float,
        duration_seconds: float = 5.0,
        track_name: str = "Overlay1"
    ) -> str:
        """
        Add an overlay clip directly by file path (useful for generated assets).

        Args:
            file_path: Path to the overlay file
            start_time_seconds: When the overlay should start
            duration_seconds: Duration of the overlay
            track_name: Name of the overlay track

        Returns:
            Success or error message
        """
        if not os.path.exists(file_path):
            return f"Error: File not found: {file_path}"

        overlay_track = self._get_or_create_overlay_track(track_name)

        media_reference = otio.schema.ExternalReference(target_url=file_path)

        duration_frames = int(duration_seconds * self.fps)
        start_frames = int(start_time_seconds * self.fps)

        clip = otio.schema.Clip(
            name=os.path.basename(file_path),
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, self.fps),
                duration=otio.opentime.RationalTime(duration_frames, self.fps)
            )
        )

        # Calculate current track duration
        current_duration = overlay_track.duration()
        current_frames = int(current_duration.to_seconds() * self.fps)

        # Add gap if needed
        if start_frames > current_frames:
            gap_frames = start_frames - current_frames
            gap = otio.schema.Gap(
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, self.fps),
                    duration=otio.opentime.RationalTime(gap_frames, self.fps)
                )
            )
            overlay_track.append(gap)

        overlay_track.append(clip)
        return f"Added overlay '{file_path}' ({duration_seconds}s) at {start_time_seconds}s on track '{track_name}'."

    def get_overlay_tracks(self) -> list:
        """Get list of overlay track names."""
        return list(self._overlay_tracks.keys())

    @agent_action(description="Get a summary of the timeline.")
    def get_summary(self) -> str:
        duration = self.track.duration()
        summary = f"Timeline '{self.timeline.name}' - Main track: {len(self.track)} items, {duration.to_seconds()}s"

        if self._overlay_tracks:
            overlay_info = []
            for name, track in self._overlay_tracks.items():
                clip_count = sum(1 for item in track if isinstance(item, otio.schema.Clip))
                track_duration = track.duration().to_seconds()
                overlay_info.append(f"{name}: {clip_count} clips, {track_duration}s")
            summary += f" | Overlays: {', '.join(overlay_info)}"

        return summary

    def to_otio_file(self, file_path: str):
        otio.adapters.write_to_file(self.timeline, file_path)

    @classmethod
    def from_otio(cls, file_path: str) -> 'OTIOTimeline':
        timeline = otio.adapters.read_from_file(file_path)

        # Try to recover metadata
        meta = timeline.metadata.get("openagv", {})
        width = meta.get("width", 1920)
        height = meta.get("height", 1080)
        fps = meta.get("fps", 24.0)

        # Reconstruct the wrapper
        wrapper = cls(width=width, height=height, fps=fps, name=timeline.name)
        wrapper.timeline = timeline

        # Find video tracks
        video_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video]

        if video_tracks:
            # First video track is main track
            wrapper.track = video_tracks[0]

            # Additional video tracks are overlay tracks
            for track in video_tracks[1:]:
                track_name = track.name or f"Overlay{len(wrapper._overlay_tracks) + 1}"
                wrapper._overlay_tracks[track_name] = track
        elif len(timeline.tracks) > 0:
            wrapper.track = timeline.tracks[0]
        else:
            # Setup default structure if empty
            wrapper.track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
            wrapper.timeline.tracks.append(wrapper.track)

        return wrapper
