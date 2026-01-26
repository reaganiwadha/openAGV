"""
Text Card Generator Plugin using Pillow.
Generates image assets with text content for use in video overlays.
"""
import os
import hashlib
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

from ..core import Agentable, agent_action


class TextCardGenerator(Agentable):
    """
    Plugin that generates text card images using Pillow.
    Can be used to create title cards, lower thirds, or text overlays.
    """

    def __init__(
        self,
        output_dir: str = "./text_cards",
        width: int = 1280,
        height: int = 720,
        font_size: int = 48,
        font_path: Optional[str] = None,
    ):
        super().__init__(
            description="Text Card Generator - creates text overlay images using Pillow"
        )
        self.output_dir = output_dir
        self.width = width
        self.height = height
        self.font_size = font_size
        self.font_path = font_path
        self.asset_bin = None

        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

    def set_asset_bin(self, asset_bin):
        """Connect to an AssetBin for registering generated images."""
        self.asset_bin = asset_bin

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get a font, falling back to default if custom font not available."""
        if self.font_path and os.path.exists(self.font_path):
            return ImageFont.truetype(self.font_path, size)

        # Try common system fonts
        common_fonts = [
            "arial.ttf",
            "Arial.ttf",
            "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ]

        for font_name in common_fonts:
            try:
                return ImageFont.truetype(font_name, size)
            except (IOError, OSError):
                continue

        # Ultimate fallback to default font
        return ImageFont.load_default()

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """Wrap text to fit within max_width."""
        words = text.split()
        lines = []
        current_line = []

        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = font.getbbox(test_line)
            line_width = bbox[2] - bbox[0]

            if line_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]

        if current_line:
            lines.append(' '.join(current_line))

        return lines

    def _generate_hash(self, text: str, style: str) -> str:
        """Generate a unique hash for the text card."""
        content = f"{text}_{style}_{self.width}x{self.height}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @agent_action(description="Generate a simple text card image with text centered on a solid background.")
    def generate_simple_card(
        self,
        text: str,
        background_color: str = "#000000",
        text_color: str = "#FFFFFF",
        filename: Optional[str] = None,
    ) -> str:
        """
        Generate a simple text card with centered text.

        Args:
            text: The text to display
            background_color: Background color in hex format (e.g., "#000000")
            text_color: Text color in hex format (e.g., "#FFFFFF")
            filename: Optional filename (auto-generated if not provided)

        Returns:
            Path to the generated image or error message
        """
        try:
            # Create image
            img = Image.new("RGBA", (self.width, self.height), background_color)
            draw = ImageDraw.Draw(img)

            # Get font and wrap text
            font = self._get_font(self.font_size)
            max_text_width = int(self.width * 0.8)
            lines = self._wrap_text(text, font, max_text_width)

            # Calculate total text height
            line_height = self.font_size + 10
            total_height = len(lines) * line_height

            # Draw text centered
            y_offset = (self.height - total_height) // 2
            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                x = (self.width - line_width) // 2
                draw.text((x, y_offset), line, fill=text_color, font=font)
                y_offset += line_height

            # Save image
            if not filename:
                card_hash = self._generate_hash(text, "simple")
                filename = f"textcard_{card_hash}.png"

            output_path = os.path.abspath(os.path.join(self.output_dir, filename))
            img.save(output_path, "PNG")

            # Register with AssetBin if connected
            asset_id = None
            if self.asset_bin:
                asset = self.asset_bin.add(output_path)
                asset_id = asset.id

            if asset_id:
                return f"Generated text card: {output_path} (asset_id: {asset_id})"
            return f"Generated text card: {output_path}"

        except Exception as e:
            return f"Error generating text card: {str(e)}"

    @agent_action(description="Generate a transparent text overlay image suitable for compositing over video.")
    def generate_overlay(
        self,
        text: str,
        text_color: str = "#FFFFFF",
        shadow_color: str = "#000000",
        position: str = "bottom",
        padding: int = 40,
        filename: Optional[str] = None,
    ) -> str:
        """
        Generate a transparent text overlay suitable for compositing.

        Args:
            text: The text to display
            text_color: Text color in hex format
            shadow_color: Shadow/outline color for readability
            position: Text position - "top", "center", or "bottom"
            padding: Padding from edges
            filename: Optional filename (auto-generated if not provided)

        Returns:
            Path to the generated image or error message
        """
        try:
            # Create transparent image
            img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Get font and wrap text
            font = self._get_font(self.font_size)
            max_text_width = int(self.width * 0.9)
            lines = self._wrap_text(text, font, max_text_width)

            # Calculate total text height
            line_height = self.font_size + 10
            total_height = len(lines) * line_height

            # Calculate vertical position
            if position == "top":
                y_start = padding
            elif position == "center":
                y_start = (self.height - total_height) // 2
            else:  # bottom
                y_start = self.height - total_height - padding

            # Draw text with shadow for readability
            y_offset = y_start
            shadow_offset = 2

            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                x = (self.width - line_width) // 2

                # Draw shadow/outline
                for dx in [-shadow_offset, 0, shadow_offset]:
                    for dy in [-shadow_offset, 0, shadow_offset]:
                        if dx != 0 or dy != 0:
                            draw.text((x + dx, y_offset + dy), line, fill=shadow_color, font=font)

                # Draw main text
                draw.text((x, y_offset), line, fill=text_color, font=font)
                y_offset += line_height

            # Save image
            if not filename:
                card_hash = self._generate_hash(text, f"overlay_{position}")
                filename = f"overlay_{card_hash}.png"

            output_path = os.path.abspath(os.path.join(self.output_dir, filename))
            img.save(output_path, "PNG")

            # Register with AssetBin if connected
            asset_id = None
            if self.asset_bin:
                asset = self.asset_bin.add(output_path)
                asset_id = asset.id

            if asset_id:
                return f"Generated overlay: {output_path} (asset_id: {asset_id})"
            return f"Generated overlay: {output_path}"

        except Exception as e:
            return f"Error generating overlay: {str(e)}"

    @agent_action(description="Generate a lower-third style text overlay with a semi-transparent background bar.")
    def generate_lower_third(
        self,
        title: str,
        subtitle: str = "",
        bar_color: str = "#000000",
        bar_opacity: int = 180,
        text_color: str = "#FFFFFF",
        filename: Optional[str] = None,
    ) -> str:
        """
        Generate a lower-third style overlay with title and optional subtitle.

        Args:
            title: Main title text
            subtitle: Optional subtitle text
            bar_color: Background bar color in hex
            bar_opacity: Bar opacity (0-255)
            text_color: Text color in hex
            filename: Optional filename

        Returns:
            Path to the generated image or error message
        """
        try:
            # Create transparent image
            img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Calculate bar dimensions
            bar_height = 100 if subtitle else 70
            bar_y = self.height - bar_height - 50

            # Parse bar color and add opacity
            bar_rgb = tuple(int(bar_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
            bar_rgba = bar_rgb + (bar_opacity,)

            # Draw semi-transparent bar
            draw.rectangle(
                [(0, bar_y), (self.width, bar_y + bar_height)],
                fill=bar_rgba
            )

            # Draw title
            title_font = self._get_font(self.font_size)
            title_y = bar_y + 15
            draw.text((40, title_y), title, fill=text_color, font=title_font)

            # Draw subtitle if provided
            if subtitle:
                subtitle_font = self._get_font(int(self.font_size * 0.7))
                subtitle_y = title_y + self.font_size + 5
                draw.text((40, subtitle_y), subtitle, fill=text_color, font=subtitle_font)

            # Save image
            if not filename:
                card_hash = self._generate_hash(f"{title}_{subtitle}", "lower_third")
                filename = f"lower_third_{card_hash}.png"

            output_path = os.path.abspath(os.path.join(self.output_dir, filename))
            img.save(output_path, "PNG")

            # Register with AssetBin if connected
            asset_id = None
            if self.asset_bin:
                asset = self.asset_bin.add(output_path)
                asset_id = asset.id

            if asset_id:
                return f"Generated lower third: {output_path} (asset_id: {asset_id})"
            return f"Generated lower third: {output_path}"

        except Exception as e:
            return f"Error generating lower third: {str(e)}"
