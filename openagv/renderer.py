import os
import subprocess
from typing import Optional
import opentimelineio as otio
from openagv.core.timeline import OTIOTimeline

class FfmpegOTIORenderer:
    """
    Renders an OTIOTimeline to a video file using FFmpeg.
    This class is not Agentable.
    """
    def __init__(self):
        self.otio_timeline: Optional[OTIOTimeline] = None

    def set_otio(self, otio_timeline: OTIOTimeline):
        """Sets the OTIO timeline to be rendered."""
        self.otio_timeline = otio_timeline

    def validate(self) -> bool:
        """
        Validates the timeline for rendering.
        Checks if timeline is set and if all clips refer to existing files.
        Raises ValueError if invalid.
        """
        if not self.otio_timeline:
            raise ValueError("No OTIOTimeline set.")
        
        # Validate tracks and clips
        # We assume the structure from OTIOTimeline class (single track)
        track = self.otio_timeline.track
        if not track:
            # It's technically valid to have an empty timeline (renders nothing), 
            # but usually we want content. We'll allow it but warn in logs if we had them.
            return True

        for i, item in enumerate(track):
            if isinstance(item, otio.schema.Clip):
                if not item.media_reference or not hasattr(item.media_reference, 'target_url') or not item.media_reference.target_url:
                     raise ValueError(f"Clip {i} ({item.name}) has no valid media reference target_url.")
                
                path = item.media_reference.target_url
                if not os.path.exists(path):
                     raise ValueError(f"Clip {i} ({item.name}) references missing file: {path}")
        
        return True

    def render(self, output_path: str = "otio.mp4"):
        """
        Renders the timeline to the specified output path using FFmpeg.
        Handling:
        - Resizes all inputs to 1280x720 (with padding) to ensure concat works.
        - Handles images (loops them) and videos (seeks/trims).
        - Renders video stream only (for now) to avoid issues with mixed silent/sound assets.
        """
        self.validate()
        
        if not self.otio_timeline or not self.otio_timeline.track or len(self.otio_timeline.track) == 0:
            print("Warning: Empty timeline, nothing to render.")
            return

        cmd = ['ffmpeg', '-y']
        filter_parts = []
        input_count = 0
        
        def is_image(path):
            ext = os.path.splitext(path)[1].lower()
            return ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp']

        for item in self.otio_timeline.track:
            if isinstance(item, otio.schema.Clip):
                path = item.media_reference.target_url
                
                # Default duration if not specified
                start_time = 0.0
                duration = 5.0
                
                if item.source_range:
                    start_time = item.source_range.start_time.to_seconds()
                    duration = item.source_range.duration.to_seconds()
                
                # Build Input Args
                if is_image(path):
                    # Loop image for the specific duration
                    cmd.extend(['-loop', '1', '-t', str(duration), '-i', path])
                else:
                    # Seek video
                    # -ss before -i is faster. -t defines duration of the clip.
                    cmd.extend(['-ss', str(start_time), '-t', str(duration), '-i', path])
                
                # Build Filter Chain for this input
                # scale=1280:720:force_original_aspect_ratio=decrease checks bounds
                # pad=1280:720:(ow-iw)/2:(oh-ih)/2 centers it
                # setsar=1 ensures pixel aspect ratio is square
                scale_filter = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"
                filter_parts.append(f"[{input_count}:v]{scale_filter}[v{input_count}];")
                
                input_count += 1

        if input_count == 0:
            print("No clips found in timeline to render.")
            return

        # Concat inputs
        concat_inputs = "".join([f"[v{i}]" for i in range(input_count)])
        # n=input_count, v=1 (video out), a=0 (no audio out)
        filter_parts.append(f"{concat_inputs}concat=n={input_count}:v=1:a=0[outv]")
        
        full_filter = "".join(filter_parts)
        cmd.extend(['-filter_complex', full_filter])
        cmd.extend(['-map', '[outv]'])
        
        # Output settings
        # libx264, yuv420p for compatibility
        cmd.extend(['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', output_path])

        print(f"Executing FFmpeg render...")
        # We don't print the full cmd as it might be huge, but for debug:
        # print(f"DEBUG CMD: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg render failed:\nStout: {result.stdout}\nStderr: {result.stderr}")
            print(f"Render complete: {output_path}")
        except FileNotFoundError:
             raise RuntimeError("FFmpeg executable not found. Please ensure ffmpeg is installed and in your PATH.")
