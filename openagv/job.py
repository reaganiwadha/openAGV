"""Chat history model for openAGV.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

@dataclass
class ChatMessage:
    """A single message in the project's conversation log with author attribution."""

    role: str  # "system" | "user" | "agent" | "tool"
    content: str
    author: str  # e.g. "user:jane@company.com", "agent:video-editor", "system"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "author": self.author,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatMessage":
        return cls(
            role=data["role"],
            content=data["content"],
            author=data["author"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
        )