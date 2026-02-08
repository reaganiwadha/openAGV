import os
import subprocess
from typing import Optional, List, Tuple, TYPE_CHECKING
import opentimelineio as otio
from openagv.core.timeline import OTIOTimeline

if TYPE_CHECKING:
    from .storage import StorageBackend


class FfmpegOTIORenderer:
    """
    Renders an OTIOTimeline to a video file using FFmpeg.
    Supports multiple tracks with overlay compositing.

    Can optionally use a StorageBackend to store the rendered output.
    The timeline's target_url values should already be resolved to
    local paths (done by OTIOTimeline._resolve_path at clip-add time).
    """
    def __init__(self, storage: Optional["StorageBackend"] = None):
        self.otio_timeline: Optional[OTIOTimeline] = None
        self.storage: Optional["StorageBackend"] = storage

    def set_otio(self, otio_timeline: OTIOTimeline):
        """Sets the OTIO timeline to be rendered."""
        self.otio_timeline = otio_timeline

    def set_storage(self, storage: "StorageBackend"):
        self.storage = storage

    def validate(self) -> bool:
        """
        Validates the timeline for rendering.
        Checks if timeline is set and if all clips refer to existing files.
        Raises ValueError if invalid.
        """
        if not self.otio_timeline:
            raise ValueError("No OTIOTimeline set.")

        # Validate main track
        track = self.otio_timeline.track
        if track:
            for i, item in enumerate(track):
                if isinstance(item, otio.schema.Clip):
                    if not item.media_reference or not hasattr(item.media_reference, 'target_url') or not item.media_reference.target_url:
                        raise ValueError(f"Main track clip {i} ({item.name}) has no valid media reference target_url.")

                    path = item.media_reference.target_url
                    if not os.path.exists(path):
                        raise ValueError(f"Main track clip {i} ({item.name}) references missing file: {path}")

        # Validate overlay tracks
        for track_name, overlay_track in self.otio_timeline._overlay_tracks.items():
            for i, item in enumerate(overlay_track):
                if isinstance(item, otio.schema.Clip):
                    if not item.media_reference or not hasattr(item.media_reference, 'target_url') or not item.media_reference.target_url:
                        raise ValueError(f"Overlay track '{track_name}' clip {i} ({item.name}) has no valid media reference.")

                    path = item.media_reference.target_url
                    if not os.path.exists(path):
                        raise ValueError(f"Overlay track '{track_name}' clip {i} ({item.name}) references missing file: {path}")

        return True

    def _is_image(self, path: str) -> bool:
        """Check if file is an image based on extension."""
        ext = os.path.splitext(path)[1].lower()
        return ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']

    def _get_track_clips_with_timing(self, track: otio.schema.Track) -> List[Tuple[float, float, str]]:
        """
        Extract clips from a track with their start times and durations.

        Returns:
            List of (start_time, duration, file_path) tuples
        """
        clips = []
        current_time = 0.0

        for item in track:
            if isinstance(item, otio.schema.Gap):
                if item.source_range:
                    current_time += item.source_range.duration.to_seconds()
            elif isinstance(item, otio.schema.Clip):
                duration = 5.0
                if item.source_range:
                    duration = item.source_range.duration.to_seconds()

                path = item.media_reference.target_url
                clips.append((current_time, duration, path))
                current_time += duration

        return clips

    def render(self, output_path: str = "otio.mp4", *, store_key: str | None = None) -> str:
        """
        Renders the timeline to the specified output path using FFmpeg.

        Args:
            output_path: Local path for the rendered file.
            store_key: If provided (and storage is set), store the rendered
                       file under this key and return the key.

        Returns:
            The output path or storage key.
        """
        self.validate()

        if not self.otio_timeline or not self.otio_timeline.track or len(self.otio_timeline.track) == 0:
            print("Warning: Empty timeline, nothing to render.")
            return output_path

        target_width = getattr(self.otio_timeline, 'width', 1280) or 1280
        target_height = getattr(self.otio_timeline, 'height', 720) or 720
        fps = getattr(self.otio_timeline, 'fps', 30) or 30

        has_overlays = bool(self.otio_timeline._overlay_tracks)

        if has_overlays:
            self._render_with_overlays(output_path, target_width, target_height, fps)
        else:
            self._render_simple(output_path, target_width, target_height)

        # Store rendered output via backend if requested
        if self.storage and store_key:
            self.storage.store(output_path, store_key)
            return store_key

        return output_path

    def _render_simple(self, output_path: str, target_width: int, target_height: int):
        """Render without overlays - simple concatenation."""
        cmd = ['ffmpeg', '-y']
        filter_parts = []
        input_count = 0

        for item in self.otio_timeline.track:
            if isinstance(item, otio.schema.Clip):
                path = item.media_reference.target_url

                start_time = 0.0
                duration = 5.0

                if item.source_range:
                    start_time = item.source_range.start_time.to_seconds()
                    duration = item.source_range.duration.to_seconds()

                if self._is_image(path):
                    cmd.extend(['-loop', '1', '-t', str(duration), '-i', path])
                else:
                    cmd.extend(['-ss', str(start_time), '-t', str(duration), '-i', path])

                scale_filter = f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                filter_parts.append(f"[{input_count}:v]{scale_filter}[v{input_count}];")
                input_count += 1

        if input_count == 0:
            print("No clips found in timeline to render.")
            return

        concat_inputs = "".join([f"[v{i}]" for i in range(input_count)])
        filter_parts.append(f"{concat_inputs}concat=n={input_count}:v=1:a=0[outv]")

        full_filter = "".join(filter_parts)
        cmd.extend(['-filter_complex', full_filter])
        cmd.extend(['-map', '[outv]'])
        cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', output_path])

        self._run_ffmpeg(cmd)

    def _render_with_overlays(self, output_path: str, target_width: int, target_height: int, fps: float):
        """
        Render with overlay tracks using FFmpeg overlay filter.
        """
        cmd = ['ffmpeg', '-y']
        filter_parts = []
        input_index = 0

        main_track_clips = []
        for item in self.otio_timeline.track:
            if isinstance(item, otio.schema.Clip):
                path = item.media_reference.target_url

                start_time = 0.0
                duration = 5.0
                if item.source_range:
                    start_time = item.source_range.start_time.to_seconds()
                    duration = item.source_range.duration.to_seconds()

                if self._is_image(path):
                    cmd.extend(['-loop', '1', '-t', str(duration), '-i', path])
                else:
                    cmd.extend(['-ss', str(start_time), '-t', str(duration), '-i', path])

                main_track_clips.append(input_index)
                input_index += 1

        if not main_track_clips:
            print("No clips in main track to render.")
            return

        for i, idx in enumerate(main_track_clips):
            scale_filter = f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            filter_parts.append(f"[{idx}:v]{scale_filter}[main{i}];")

        concat_inputs = "".join([f"[main{i}]" for i in range(len(main_track_clips))])
        filter_parts.append(f"{concat_inputs}concat=n={len(main_track_clips)}:v=1:a=0[base];")

        current_base = "base"
        overlay_counter = 0

        for track_name, overlay_track in self.otio_timeline._overlay_tracks.items():
            overlay_clips = self._get_track_clips_with_timing(overlay_track)

            for start_time, duration, path in overlay_clips:
                if self._is_image(path):
                    cmd.extend(['-loop', '1', '-t', str(duration), '-i', path])
                else:
                    cmd.extend(['-t', str(duration), '-i', path])

                overlay_input = input_index
                input_index += 1

                overlay_scaled = f"ovl_scaled{overlay_counter}"
                filter_parts.append(f"[{overlay_input}:v]scale={target_width}:{target_height},format=rgba[{overlay_scaled}];")

                new_base = f"comp{overlay_counter}"
                enable_expr = f"between(t,{start_time},{start_time + duration})"
                filter_parts.append(f"[{current_base}][{overlay_scaled}]overlay=0:0:enable='{enable_expr}'[{new_base}];")

                current_base = new_base
                overlay_counter += 1

        if filter_parts:
            last_filter = filter_parts[-1]
            if last_filter.endswith(';'):
                last_filter = last_filter[:-1]
            last_bracket = last_filter.rfind('[')
            if last_bracket != -1:
                last_filter = last_filter[:last_bracket] + '[outv]'
            filter_parts[-1] = last_filter

        full_filter = "".join(filter_parts)
        cmd.extend(['-filter_complex', full_filter])
        cmd.extend(['-map', '[outv]'])
        cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', output_path])

        self._run_ffmpeg(cmd)

    def _run_ffmpeg(self, cmd: List[str]):
        """Execute FFmpeg command and handle errors."""
        print(f"Executing FFmpeg render...")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg render failed:\nStdout: {result.stdout}\nStderr: {result.stderr}")
            print(f"Render complete: {cmd[-1]}")
        except FileNotFoundError:
            raise RuntimeError("FFmpeg executable not found. Please ensure ffmpeg is installed and in your PATH.")
