"""Project wrapper for openAGV.

Bundles AssetBin, OTIOTimeline, ChecklistManager, StorageBackend, and
LLMConfig into a single serializable unit. Provides submit() for
kicking off agent jobs and duplicate() for iteration.
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
from .analysis_config import AnalysisConfig, AnalyzerSpec
from .job import Job, JobStatus, JobResult, ChatMessage, EventType
from .llm import LLMConfig
from .storage import StorageBackend, LocalStorageBackend

logger = logging.getLogger(__name__)


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

        # Configurable system prompt (None = use executor default)
        self.system_prompt: str | None = None

        # Persistent chat history across jobs
        self.chat_history: list[ChatMessage] = []

        # Analysis config (optional)
        self.analysis_config: AnalysisConfig | None = None

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
    # Analysis config
    # ------------------------------------------------------------------

    def set_analysis_config(self, config: AnalysisConfig) -> None:
        self.analysis_config = config

    # ------------------------------------------------------------------
    # Job queue
    # ------------------------------------------------------------------

    def _completed_job_ids(self) -> set[str]:
        return {j.id for j in self.jobs if j.status == JobStatus.COMPLETED}

    def enqueue_analysis(self, asset_id: str) -> Job | None:
        """Create an analysis job for the given asset (idempotent)."""
        # Skip if a pending/running analysis job already exists for this asset
        for j in self.jobs:
            if (
                j.job_type == "analysis"
                and j.asset_id == asset_id
                and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
            ):
                return j

        asset = self.asset_bin.get_asset_by_id(asset_id)
        if not asset:
            return None

        job = Job(
            instruction=f"Analyze asset {asset_id}",
            author="system",
            job_type="analysis",
            asset_id=asset_id,
        )
        self.jobs.append(job)
        self.updated_at = datetime.now(timezone.utc)
        return job

    def next_ready_jobs(self, limit: int | None = None) -> list[Job]:
        """Return PENDING jobs whose blocked_by are all COMPLETED.

        Respects max_concurrent for analysis jobs.
        """
        completed = self._completed_job_ids()
        ready: list[Job] = []

        # Count currently running analysis jobs
        running_analysis = sum(
            1 for j in self.jobs
            if j.job_type == "analysis" and j.status == JobStatus.RUNNING
        )
        max_concurrent = (
            self.analysis_config.max_concurrent
            if self.analysis_config
            else 3
        )

        for job in self.jobs:
            if not job.is_ready(completed):
                continue

            if job.job_type == "analysis":
                if running_analysis >= max_concurrent:
                    continue
                running_analysis += 1

            ready.append(job)
            if limit and len(ready) >= limit:
                break

        return ready

    async def run_job(self, job: Job) -> None:
        """Run a single job to completion."""
        job._mark_running()
        job._emit(EventType.STATE_CHANGE, {"from": "PENDING", "to": "RUNNING"})

        try:
            if job.job_type == "analysis":
                await self._run_analysis_job(job)
            else:
                await self._run_edit_job(job)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            job._mark_failed(str(e))

    async def _run_analysis_job(self, job: Job) -> None:
        asset = self.asset_bin.get_asset_by_id(job.asset_id)
        if not asset:
            job._mark_failed(f"Asset {job.asset_id} not found")
            return

        specs = (
            self.analysis_config.specs_for_type(asset.asset_type)
            if self.analysis_config
            else []
        )

        if not specs:
            # No analyzers configured — auto-complete
            job._mark_completed(JobResult(agent_response="No analyzers configured"))
            return

        for spec in specs:
            # Find registered module by class name
            analyzer = next(
                (m for m in self._modules if m.__class__.__name__ == spec.name),
                None,
            )
            if not analyzer:
                logger.warning(
                    "Analyzer %s not found in registered modules, skipping",
                    spec.name,
                )
                continue

            job._emit(EventType.STEP_STARTED, {"step_name": f"Running {spec.name}"})
            try:
                await analyzer.analyze_asset(asset)
                job._emit(EventType.STEP_COMPLETED, {"step_name": f"Running {spec.name}"})
            except Exception as e:
                job._mark_failed(f"{spec.name} failed: {e}")
                return

        job._mark_completed(
            JobResult(agent_response=f"Analysis complete for asset {job.asset_id}")
        )

    async def _run_edit_job(self, job: Job) -> None:
        if not self.llm_config:
            job._mark_failed(
                "No LLMConfig set on project. "
                "Pass llm_config= at construction or load time."
            )
            return

        _, _, chat_completion = self.llm_config.create_clients()

        from .executor import SKLoopExecutor

        executor = SKLoopExecutor(
            asset_bin=self.asset_bin,
            instruction=UserInstruction(job.instruction),
            chat_completion=chat_completion,
            uses=[*self._modules, self.timeline],
            system_prompt=self.system_prompt,
        )

        # Seed executor with prior project-level chat history
        for msg in self.chat_history:
            if msg.role == "user":
                executor.chat_history.add_user_message(msg.content)
            elif msg.role == "agent":
                executor.chat_history.add_assistant_message(msg.content)

        # Wire stepper events → job events
        def _on_stepper_change(stepper):
            if stepper.current_step:
                step = stepper.current_step
                if step.status == "IN_PROGRESS":
                    job._emit(EventType.STEP_STARTED, {
                        "step_name": step.name,
                        "step_index": stepper.current_step_index,
                    })
                elif step.status == "COMPLETED":
                    job._emit(EventType.STEP_COMPLETED, {
                        "step_name": step.name,
                        "step_index": stepper.current_step_index,
                    })

        executor.register_callback(_on_stepper_change)
        executor.set_event_emitter(job._emit)

        await executor.start()

        agent_response = ""
        if executor.chat_history and len(executor.chat_history) > 0:
            last = executor.chat_history[-1]
            agent_response = str(last.content) if last.content else ""

        job._add_chat_message("agent", agent_response, "agent:video-editor")

        # Accumulate into project-level persistent chat history
        self.chat_history.append(ChatMessage(role="user", content=job.instruction, author=job.author))
        self.chat_history.append(ChatMessage(role="agent", content=agent_response, author="agent:video-editor"))

        job._mark_completed(JobResult(agent_response=agent_response))

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    def submit(
        self,
        instruction: str,
        author: str = "user",
    ) -> Job:
        """Submit an edit instruction. Auto-depends on pending/running analysis jobs."""
        if not self.llm_config:
            raise RuntimeError(
                "No LLMConfig set on project. "
                "Pass llm_config= at construction or load time."
            )

        # Block on all pending/running analysis jobs
        analysis_deps = [
            j.id for j in self.jobs
            if j.job_type == "analysis"
            and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
        ]

        job = Job(
            instruction=instruction,
            author=author,
            job_type="edit",
            blocked_by=analysis_deps,
        )

        job._add_chat_message("system", "Video editor system prompt", "system")
        job._add_chat_message("user", instruction, author)

        self.jobs.append(job)
        self.updated_at = datetime.now(timezone.utc)
        return job

    async def nudge(self, instruction: str, author: str = "user") -> None:
        """Nudge the running edit job with an additional instruction."""
        running = next(
            (j for j in self.jobs
             if j.job_type == "edit" and j.status == JobStatus.RUNNING),
            None,
        )
        if not running:
            raise RuntimeError("No active job to nudge.")

        running._add_chat_message("user", instruction, author)
        running._emit(EventType.LOG, {
            "message": f"Nudged by {author}: {instruction}",
            "level": "INFO",
        })

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
            "system_prompt": self.system_prompt,
            "chat_history": [m.to_dict() for m in self.chat_history],
            "analysis_config": self.analysis_config.to_dict() if self.analysis_config else None,
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

        # Analysis config
        if data.get("analysis_config"):
            project.analysis_config = AnalysisConfig.from_dict(data["analysis_config"])

        # System prompt
        project.system_prompt = data.get("system_prompt")

        # Persistent chat history
        project.chat_history = [
            ChatMessage.from_dict(m) for m in data.get("chat_history", [])
        ]

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
