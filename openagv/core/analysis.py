from typing import Any


class Analysis:
    """The result of an analysis."""
    def __init__(self, storage_key: str, content: Any, analyzer_name: str):
        self.storage_key = storage_key
        self.content = content
        self.analyzer_name = analyzer_name

    def __repr__(self):
        return f"<Analysis of {self.storage_key} by {self.analyzer_name}: {self.content}>"

    def to_dict(self) -> dict:
        return {
            "storage_key": self.storage_key,
            "analyzer_name": self.analyzer_name,
            "content": self.content
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Analysis':
        return cls(
            storage_key=data["storage_key"],
            content=data["content"],
            analyzer_name=data["analyzer_name"]
        )
