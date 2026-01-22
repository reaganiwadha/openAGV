from typing import Any

class Analysis:
    """The result of an analysis."""
    def __init__(self, asset_path: str, content: Any, analyzer_name: str):
        self.asset_path = asset_path
        self.content = content
        self.analyzer_name = analyzer_name
    
    def __repr__(self):
        return f"<Analysis of {self.asset_path} by {self.analyzer_name}: {self.content}>"

    def to_dict(self) -> dict:
        return {
            "asset_path": self.asset_path,
            "analyzer_name": self.analyzer_name,
            "content": self.content
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Analysis':
        return cls(
            asset_path=data["asset_path"],
            content=data["content"],
            analyzer_name=data["analyzer_name"]
        )
