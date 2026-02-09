"""Project wrapper for openAGV.

Bundles AssetBin, OTIOTimeline, ChecklistManager, StorageBackend, and
LLMConfig into a single serializable unit.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .core import (
    Agentable,
    AssetBin,
    Analyzer,
    OTIOTimeline,
    UserInstruction,
    ChecklistManager,
)
from .job import ChatMessage
from .llm import LLMConfig
from .storage import StorageBackend, LocalStorageBackend
from .executor import SKLoopExecutor

logger = logging.getLogger(__name__)


class ProjectValidationError(Exception):
    """Raised when project data fails structural validation."""
    pass


class Project:
    """A self-contained openAGV project.

    Owns all state needed for autonomous video editing: assets, timeline,
    checklist, LLM config, and chat history. Fully JSON-serializable
    (minus secrets and runtime objects).
    """

    def __init__(
        self,
        name: str = "Untitled Project",
        llm_config: LLMConfig | None = None,
        storage: StorageBackend | None = None,
        *,
        id: str | None = None,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
    ):
        self.id: str = id or str(uuid.uuid4())
        self.name: str = name
        self.parent_id: str | None = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)

        self.llm_config: LLMConfig | None = llm_config
        self.storage: StorageBackend | None = storage

        # Core components
        self.asset_bin: AssetBin = AssetBin(storage=storage)
        self.timeline: OTIOTimeline = OTIOTimeline(
            width=width, height=height, fps=fps
        )
        self.timeline.set_asset_bin(self.asset_bin)
        if storage:
            self.timeline.set_storage(storage)
        self.checklist: ChecklistManager = ChecklistManager()

        # Registered modules (analyzers, generators, etc.)
        self._modules: list[Agentable] = []
        self._module_names: list[str] = []

        # Configurable system prompt (None = use executor default)
        self.system_prompt: str | None = None

        # Persistent chat history
        self.chat_history: list[ChatMessage] = []

        # Runtime executor instance
        self._executor: SKLoopExecutor | None = None

    def register_module(self, module: Agentable) -> None:
        """Register an analyzer or generator module."""
        self._modules.append(module)
        name = module.__class__.__name__
        if name not in self._module_names:
            self._module_names.append(name)

        if hasattr(module, "set_asset_bin"):
            module.set_asset_bin(self.asset_bin)
        if hasattr(module, "set_storage") and self.storage:
            module.set_storage(self.storage)
        if isinstance(module, Analyzer):
            self.asset_bin.register_analyzer(module)
        
        # Invalidate executor so it picks up the new module next run
        self._executor = None

    def add_assets(self, pattern: str) -> list:
        """Add assets from a glob pattern. Convenience wrapper."""
        return self.asset_bin.add_wildcard(pattern)

    async def preanalyze_pendings(self, max_concurrent: int = 3) -> None:
        """Analyze assets that haven't been analyzed yet by registered analyzers."""
        tasks = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _analyze_safely(analyzer: Analyzer, asset_id: str):
            async with semaphore:
                try:
                    logger.info(f"Running {analyzer.name} on {asset_id}")
                    await analyzer.analyze_asset(asset_id)
                except Exception as e:
                    logger.error(f"Analysis failed for {asset_id} with {analyzer.name}: {e}")

        for asset in self.asset_bin.assets:
            for module in self._modules:
                if isinstance(module, Analyzer) and module.can_analyze(asset):
                    if not asset.has_analysis_from(module.name):
                        tasks.append(_analyze_safely(module, asset.id))

        if tasks:
            logger.info(f"Starting {len(tasks)} analysis tasks...")
            await asyncio.gather(*tasks)
            self.updated_at = datetime.now(timezone.utc)
            logger.info("Pre-analysis complete.")
        else:
            logger.info("No pending analyses found.")

    async def rag_loop(self, instruction: str) -> str:
        """Execute a RAG loop for the given instruction.

        Initializes or reuses the LLM executor, runs the cycle,
        and returns the final agent response.
        """
        if not self.llm_config:
            raise RuntimeError(
                "No LLMConfig set on project. "
                "Pass llm_config= at construction or load time."
            )

        # Initialize executor if needed
        if not self._executor:
            try:
                _, _, chat_completion = self.llm_config.create_clients()
            except Exception as e:
                raise RuntimeError(f"Failed to initialize LLM clients: {e}") from e

            self._executor = SKLoopExecutor(
                asset_bin=self.asset_bin,
                chat_completion=chat_completion,
                uses=[*self._modules, self.timeline],
                checklist_manager=self.checklist,
                system_prompt=self.system_prompt,
            )

            # Seed executor with prior project-level chat history
            # Note: SKLoopExecutor init adds system prompt, so we skip index 0 if it's system?
            # Actually SK ChatHistory from project history:
            for msg in self.chat_history:
                if msg.role == "user":
                    self._executor.chat_history.add_user_message(msg.content)
                elif msg.role == "agent":
                    self._executor.chat_history.add_assistant_message(msg.content)

        # Run the loop via nudge
        await self._executor.nudge(UserInstruction(instruction))

        # Capture the new response (last message)
        # nudge() added user msg, then agent replied. So last 2 messages are relevant?
        # We need to return the agent response text.
        agent_response = ""
        if len(self._executor.chat_history) > 0:
            last = self._executor.chat_history[-1]
            agent_response = str(last.content) if last.content else ""

        # Update project history (append ONLY the new interaction)
        # We append what we just did: the instruction and the response.
        self.chat_history.append(ChatMessage(role="user", content=instruction, author="user"))
        self.chat_history.append(ChatMessage(role="agent", content=agent_response, author="agent:video-editor"))

        self.updated_at = datetime.now(timezone.utc)
        return agent_response

    def duplicate(
        self,
        name: str | None = None,
        storage: StorageBackend | None = None,
    ) -> "Project":
        """Create a copy of this project with fresh timeline and checklist.

        Assets are symlinked from the original (for LocalStorageBackend).
        """
        new_id = str(uuid.uuid4())
        new_storage = storage

        # If both are LocalStorageBackend, symlink assets
        if (
            new_storage is None
            and self.storage is not None
            and isinstance(self.storage, LocalStorageBackend)
        ):
            new_storage = LocalStorageBackend(
                root=self.storage.root, project_id=new_id
            )
            for asset in self.asset_bin.assets:
                new_storage.symlink_from(self.storage, asset.storage_key)

        new_project = Project(
            name=name or f"{self.name} (copy)",
            llm_config=LLMConfig(
                provider=self.llm_config.provider,
                model=self.llm_config.model,
                service_id=self.llm_config.service_id,
                base_url=self.llm_config.base_url,
                api_key=self.llm_config.api_key,
            ) if self.llm_config else None,
            storage=new_storage,
            id=new_id,
            width=self.timeline.width,
            height=self.timeline.height,
            fps=self.timeline.fps,
        )
        new_project.parent_id = self.id

        # Copy asset metadata (not files — those are symlinked)
        new_project.asset_bin = AssetBin.from_dict(
            self.asset_bin.to_dict(), storage=new_storage
        )

        # Re-register same module types
        new_project._module_names = list(self._module_names)

        return new_project

    def to_dict(self) -> dict[str, Any]:
        """Serialize project to a dict. Suitable for JSON/JSONB storage."""
        self.updated_at = datetime.now(timezone.utc)
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "llm_config": self.llm_config.to_dict() if self.llm_config else None,
            "asset_bin": self.asset_bin.to_dict(),
            "timeline": {
                "width": self.timeline.width,
                "height": self.timeline.height,
                "fps": self.timeline.fps,
                "name": self.timeline.timeline.name,
                "_otio": json.loads(
                    self.timeline.timeline.to_json_string()
                ),
            },
            "checklist": {
                "tasks": self.checklist.tasks,
            },
            "modules": self._module_names,
            "system_prompt": self.system_prompt,
            "chat_history": [m.to_dict() for m in self.chat_history],
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        api_key: str | None = None,
        storage: StorageBackend | None = None,
    ) -> "Project":
        """Deserialize a project from a dict."""
        for field in ("id", "asset_bin"):
            if field not in data:
                raise ProjectValidationError(f"Missing required field: {field}")

        llm_config = None
        if data.get("llm_config"):
            llm_config = LLMConfig.from_dict(data["llm_config"], api_key=api_key)

        tl_data = data.get("timeline", {})
        width = tl_data.get("width", 1920)
        height = tl_data.get("height", 1080)
        fps = tl_data.get("fps", 30.0)

        project = cls(
            name=data.get("name", "Untitled Project"),
            llm_config=llm_config,
            storage=storage,
            id=data["id"],
            width=width,
            height=height,
            fps=fps,
        )

        project.parent_id = data.get("parent_id")
        project.created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else project.created_at
        project.updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else project.updated_at

        project.asset_bin = AssetBin.from_dict(data["asset_bin"], storage=storage)
        project.timeline.set_asset_bin(project.asset_bin)

        import opentimelineio as otio
        if "_otio" in tl_data:
            otio_timeline = otio.adapters.read_from_string(
                json.dumps(tl_data["_otio"]), "otio_json"
            )
            project.timeline.timeline = otio_timeline
            video_tracks = [
                t for t in otio_timeline.tracks
                if t.kind == otio.schema.TrackKind.Video
            ]
            if video_tracks:
                project.timeline.track = video_tracks[0]
                for track in video_tracks[1:]:
                    name = track.name or f"Overlay{len(project.timeline._overlay_tracks) + 1}"
                    project.timeline._overlay_tracks[name] = track

        if storage:
            project.timeline.set_storage(storage)

        checklist_data = data.get("checklist", {})
        if "tasks" in checklist_data:
            project.checklist.tasks = checklist_data["tasks"]

        project._module_names = data.get("modules", [])

        project.system_prompt = data.get("system_prompt")

        project.chat_history = [
            ChatMessage.from_dict(m) for m in data.get("chat_history", [])
        ]

        return project

    @classmethod
    def from_json(
        cls,
        json_str: str,
        *,
        api_key: str | None = None,
        storage: StorageBackend | None = None,
    ) -> "Project":
        """Deserialize from a JSON string."""
        return cls.from_dict(
            json.loads(json_str), api_key=api_key, storage=storage
        )