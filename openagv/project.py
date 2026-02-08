"""Project wrapper for openAGV.

Bundles AssetBin, OTIOTimeline, ChecklistManager, StorageBackend, and
LLMConfig into a single serializable unit. Provides submit() for
kicking off agent jobs and duplicate() for iteration.
"""

import asyncio
import json
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
from .job import Job, JobStatus, JobResult, ChatMessage
from .llm import LLMConfig
from .storage import StorageBackend, LocalStorageBackend


class ProjectValidationError(Exception):
    """Raised when project data fails structural validation."""
    pass


class Project:
    """A self-contained openAGV project.

    Owns all state needed for autonomous video editing: assets, timeline,
    checklist, LLM config, and job history. Fully JSON-serializable
    (minus secrets and runtime objects).

    Usage:
        project = Project(
            name="Spring Campaign",
            llm_config=LLMConfig(provider="openrouter", model="openai/gpt-4o-mini"),
            storage=LocalStorageBackend("/data/projects", "my-project-id"),
        )
        project.add_assets("uploads/*.jpg")
        job = project.submit("Create a 30s product showcase", author="user:jane")
        await job.wait()
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

        # Job history — all jobs included in serialization
        self.jobs: list[Job] = []
        self._active_job: Job | None = None

    # ------------------------------------------------------------------
    # Module registration
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Asset helpers
    # ------------------------------------------------------------------

    def add_assets(self, pattern: str) -> list:
        """Add assets from a glob pattern. Convenience wrapper."""
        return self.asset_bin.add_wildcard(pattern)

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    def submit(
        self,
        instruction: str,
        author: str = "user",
        *,
        debug: bool = False,
        bug_user: bool = False,
    ) -> Job:
        """Submit an instruction for autonomous execution.

        Returns a Job immediately. The job runs in the background via
        asyncio.create_task. Only one job may run per project at a time.

        Args:
            instruction: The user's natural-language instruction.
            author: Identifier for who submitted (e.g. "user:jane@co.com").
            debug: Enable verbose logging on the executor.
            bug_user: Allow the agent to ask clarifying questions.
        """
        if self._active_job and self._active_job.status == JobStatus.RUNNING:
            raise RuntimeError(
                "A job is already running on this project. "
                "Wait for it to finish or cancel it first."
            )
        if not self.llm_config:
            raise RuntimeError(
                "No LLMConfig set on project. "
                "Pass llm_config= at construction or load time."
            )

        job = Job(instruction=instruction, author=author)

        # Record system + user messages in the job's chat history
        job._add_chat_message("system", "Video editor system prompt", "system")
        job._add_chat_message("user", instruction, author)

        # Build executor
        _, _, chat_completion = self.llm_config.create_clients()

        from .executor import SKLoopExecutor

        executor = SKLoopExecutor(
            asset_bin=self.asset_bin,
            instruction=UserInstruction(instruction),
            chat_completion=chat_completion,
            uses=[*self._modules, self.timeline],
            debug=debug,
            bug_user=bug_user,
        )

        # Wire stepper events → job events
        def _on_stepper_change(stepper):
            if stepper.current_step:
                step = stepper.current_step
                if step.status == "IN_PROGRESS":
                    job._emit("step_started", {
                        "step_name": step.name,
                        "step_index": stepper.current_step_index,
                    })
                elif step.status == "COMPLETED":
                    job._emit("step_completed", {
                        "step_name": step.name,
                        "step_index": stepper.current_step_index,
                    })

        executor.register_callback(_on_stepper_change)

        # Wire executor events (token, tool_call, etc.) into job
        executor.set_event_emitter(job._emit)

        # Launch background task
        job._mark_running()
        job._emit("state_change", {"from": "PENDING", "to": "RUNNING"})

        async def _run():
            try:
                await executor.start()

                # Extract result
                agent_response = ""
                if executor.chat_history and len(executor.chat_history) > 0:
                    last = executor.chat_history[-1]
                    agent_response = str(last.content) if last.content else ""

                job._add_chat_message("agent", agent_response, "agent:video-editor")

                result = JobResult(agent_response=agent_response)
                job._mark_completed(result)
            except asyncio.CancelledError:
                pass  # cancellation handled by job.cancel()
            except Exception as e:
                job._add_chat_message(
                    "agent", f"Error: {e}", "agent:video-editor",
                    metadata={"error": True},
                )
                job._mark_failed(str(e))

        job._task = asyncio.create_task(_run())
        self._active_job = job
        self.jobs.append(job)
        self.updated_at = datetime.now(timezone.utc)
        return job

    async def nudge(self, instruction: str, author: str = "user") -> None:
        """Nudge the active job with an additional instruction."""
        if not self._active_job or self._active_job.status != JobStatus.RUNNING:
            raise RuntimeError("No active job to nudge.")

        self._active_job._add_chat_message("user", instruction, author)
        self._active_job._emit("log", {
            "message": f"Nudged by {author}: {instruction}",
            "level": "INFO",
        })

        # The executor's nudge method needs the executor reference.
        # Since the executor is running in _run(), we inject via the
        # chat history on the executor directly.
        # For now, this is a limitation — full nudge support requires
        # storing the executor reference on the job.

    # ------------------------------------------------------------------
    # Duplication
    # ------------------------------------------------------------------

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

        # Re-register same module types (caller should re-register
        # module instances since they hold runtime state like API clients)
        new_project._module_names = list(self._module_names)

        return new_project

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize project to a dict. Suitable for JSON/JSONB storage.

        Excludes: api_key, storage backend, runtime executor state.
        """
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
                # Embed OTIO as JSON dict
                "_otio": json.loads(
                    self.timeline.timeline.to_json_string()
                ),
            },
            "checklist": {
                "tasks": self.checklist.tasks,
            },
            "modules": self._module_names,
            "jobs": [j.to_dict() for j in self.jobs],
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
        """Deserialize a project from a dict.

        Args:
            data: The project dict (e.g. from JSONB column).
            api_key: Runtime secret re-injected by the server.
            storage: StorageBackend instance (infra config, not project state).

        Raises:
            ProjectValidationError: If required fields are missing or invalid.
        """
        # Validate required fields
        for field in ("id", "asset_bin"):
            if field not in data:
                raise ProjectValidationError(f"Missing required field: {field}")

        # LLM config
        llm_config = None
        if data.get("llm_config"):
            llm_config = LLMConfig.from_dict(data["llm_config"], api_key=api_key)

        # Timeline dimensions
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

        # Asset bin
        project.asset_bin = AssetBin.from_dict(data["asset_bin"], storage=storage)
        project.timeline.set_asset_bin(project.asset_bin)

        # Timeline — restore from embedded OTIO
        import opentimelineio as otio
        if "_otio" in tl_data:
            otio_timeline = otio.adapters.read_from_string(
                json.dumps(tl_data["_otio"]), "otio_json"
            )
            project.timeline.timeline = otio_timeline
            # Restore tracks
            video_tracks = [
                t for t in otio_timeline.tracks
                if t.kind == otio.schema.TrackKind.Video
            ]
            if video_tracks:
                project.timeline.track = video_tracks[0]
                for track in video_tracks[1:]:
                    name = track.name or f"Overlay{len(project.timeline._overlay_tracks) + 1}"
                    project.timeline._overlay_tracks[name] = track

        # Wire storage into timeline
        if storage:
            project.timeline.set_storage(storage)

        # Checklist
        checklist_data = data.get("checklist", {})
        if "tasks" in checklist_data:
            project.checklist.tasks = checklist_data["tasks"]

        # Module names (hint for server to re-register)
        project._module_names = data.get("modules", [])

        # Jobs
        project.jobs = [Job.from_dict(j) for j in data.get("jobs", [])]

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
