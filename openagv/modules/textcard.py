"""
Text Card Generator Plugin using Pillow.
Generates image assets with text content for use in video overlays.
"""
import os
import hashlib
from typing import Optional, TYPE_CHECKING
from PIL import Image, ImageDraw, ImageFont

from ..core import Agentable, agent_action

if TYPE_CHECKING:
    from ..storage import StorageBackend


class TextCardGenerator(Agentable):
    """
    Plugin that generates text card images using Pillow.
    Can be used to create title cards, lower thirds, or text overlays.

    Generated images are stored via the StorageBackend under the
    "generated/" key prefix and registered with the AssetBin.
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
        self.storage: "StorageBackend | None" = None

        # Ensure output directory exists (used as temp workspace)
        os.makedirs(self.output_dir, exist_ok=True)

    def set_asset_bin(self, asset_bin):
        """Connect to an AssetBin for registering generated images."""
        self.asset_bin = asset_bin

    def set_storage(self, storage: "StorageBackend"):
        self.storage = storage

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        if self.font_path and os.path.exists(self.font_path):
            return ImageFont.truetype(self.font_path, size)

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

        return ImageFont.load_default()

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
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
        content = f"{text}_{style}_{self.width}x{self.height}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _save_and_register(self, img: Image.Image, filename: str) -> str:
        """Save image to output_dir, then store via backend and register with AssetBin."""
        local_path = os.path.abspath(os.path.join(self.output_dir, filename))
        img.save(local_path, "PNG")

        # If we have a storage backend + asset bin, store properly
        if self.asset_bin:
            asset = self.asset_bin.add(local_path)
            return f"Generated: {asset.storage_key} (asset_id: {asset.id})"

        return f"Generated: {local_path}"

    @agent_action(description="Generate a simple text card image with text centered on a solid background.")
    def generate_simple_card(
        self,
        text: str,
        background_color: str = "#000000",
        text_color: str = "#FFFFFF",
        filename: Optional[str] = None,
    ) -> str:
        try:
            img = Image.new("RGBA", (self.width, self.height), background_color)
            draw = ImageDraw.Draw(img)

            font = self._get_font(self.font_size)
            max_text_width = int(self.width * 0.8)
            lines = self._wrap_text(text, font, max_text_width)

            line_height = self.font_size + 10
            total_height = len(lines) * line_height

            y_offset = (self.height - total_height) // 2
            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                x = (self.width - line_width) // 2
                draw.text((x, y_offset), line, fill=text_color, font=font)
                y_offset += line_height

            if not filename:
                card_hash = self._generate_hash(text, "simple")
                filename = f"textcard_{card_hash}.png"

            return self._save_and_register(img, filename)

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
        try:
            img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            font = self._get_font(self.font_size)
            max_text_width = int(self.width * 0.9)
            lines = self._wrap_text(text, font, max_text_width)

            line_height = self.font_size + 10
            total_height = len(lines) * line_height

            if position == "top":
                y_start = padding
            elif position == "center":
                y_start = (self.height - total_height) // 2
            else:
                y_start = self.height - total_height - padding

            y_offset = y_start
            shadow_offset = 2

            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                x = (self.width - line_width) // 2

                for dx in [-shadow_offset, 0, shadow_offset]:
                    for dy in [-shadow_offset, 0, shadow_offset]:
                        if dx != 0 or dy != 0:
                            draw.text((x + dx, y_offset + dy), line, fill=shadow_color, font=font)

                draw.text((x, y_offset), line, fill=text_color, font=font)
                y_offset += line_height

            if not filename:
                card_hash = self._generate_hash(text, f"overlay_{position}")
                filename = f"overlay_{card_hash}.png"

            return self._save_and_register(img, filename)

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
        try:
            img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            bar_height = 100 if subtitle else 70
            bar_y = self.height - bar_height - 50

            bar_rgb = tuple(int(bar_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
            bar_rgba = bar_rgb + (bar_opacity,)

            draw.rectangle(
                [(0, bar_y), (self.width, bar_y + bar_height)],
                fill=bar_rgba
            )

            title_font = self._get_font(self.font_size)
            title_y = bar_y + 15
            draw.text((40, title_y), title, fill=text_color, font=title_font)

            if subtitle:
                subtitle_font = self._get_font(int(self.font_size * 0.7))
                subtitle_y = title_y + self.font_size + 5
                draw.text((40, subtitle_y), subtitle, fill=text_color, font=subtitle_font)

            if not filename:
                card_hash = self._generate_hash(f"{title}_{subtitle}", "lower_third")
                filename = f"lower_third_{card_hash}.png"

            return self._save_and_register(img, filename)

        except Exception as e:
            return f"Error generating lower third: {str(e)}"
