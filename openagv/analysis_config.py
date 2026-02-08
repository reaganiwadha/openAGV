from dataclasses import dataclass, field
from typing import Any

from .core.enums import AssetType


@dataclass
class AnalyzerSpec:
    name: str  # class name, e.g. "ORVisionAnalyzer"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "params": self.params}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalyzerSpec":
        return cls(name=data["name"], params=data.get("params", {}))


@dataclass
class AnalysisConfig:
    type_map: dict[AssetType, list[AnalyzerSpec]] = field(default_factory=dict)
    max_concurrent: int = 3

    def specs_for_type(self, asset_type: AssetType) -> list[AnalyzerSpec]:
        return self.type_map.get(asset_type, [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "type_map": {
                at.name: [s.to_dict() for s in specs]
                for at, specs in self.type_map.items()
            },
            "max_concurrent": self.max_concurrent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisConfig":
        type_map: dict[AssetType, list[AnalyzerSpec]] = {}
        for type_name, specs_data in data.get("type_map", {}).items():
            asset_type = AssetType[type_name]
            type_map[asset_type] = [AnalyzerSpec.from_dict(s) for s in specs_data]
        return cls(
            type_map=type_map,
            max_concurrent=data.get("max_concurrent", 3),
        )
