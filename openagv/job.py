"""Job model, events, and chat history for openAGV.

Provides the Job class that wraps an SKLoopExecutor run with
trackable status, author-attributed chat history, and an async
event stream for real-time progress reporting.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, AsyncIterator


class JobStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ChatMessage:
    """A single message in the job's conversation log with author attribution.

    This wraps Semantic Kernel's internal ChatHistory with an author field
    so the server can attribute messages to users vs. agent vs. system.
    """

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


@dataclass
class Event:
    """A real-time event emitted by a Job during execution.

    Events are pushed to an asyncio.Queue and consumed via job.stream().
    """

    type: str  # "state_change" | "step_started" | "step_completed" |
    # "tool_call" | "token" | "agent_message" | "log" |
    # "completed" | "error" | "cancelled"
    timestamp: datetime
    job_id: str
    data: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "job_id": self.job_id,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class JobResult:
    """Result of a completed job."""

    agent_response: str
    timeline_path: str | None = None  # storage key
    render_path: str | None = None  # storage key
    assets_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_response": self.agent_response,
            "timeline_path": self.timeline_path,
            "render_path": self.render_path,
            "assets_snapshot": self.assets_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "JobResult":
        return cls(
            agent_response=data["agent_response"],
            timeline_path=data.get("timeline_path"),
            render_path=data.get("render_path"),
            assets_snapshot=data.get("assets_snapshot", {}),
        )


class Job:
    """Wraps a single agent execution run.

    Provides status tracking, author-attributed chat history, and
    an async event stream via broadcast pattern (multiple listeners).
    """

    def __init__(self, instruction: str, author: str):
        self.id: str = str(uuid.uuid4())
        self.status: JobStatus = JobStatus.PENDING
        self.created_at: datetime = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self.author: str = author
        self.instruction: str = instruction
        self.result: JobResult | None = None
        self.error: str | None = None
        self.chat_history: list[ChatMessage] = []

        # Runtime-only, not serialized
        self._task: asyncio.Task | None = None
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._done_event: asyncio.Event = asyncio.Event()

    def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Push an event to all subscriber queues."""
        event = Event(
            type=event_type,
            timestamp=datetime.now(timezone.utc),
            job_id=self.id,
            data=data or {},
        )
        for queue in self._subscribers:
            queue.put_nowait(event)

    def _add_chat_message(
        self,
        role: str,
        content: str,
        author: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.chat_history.append(
            ChatMessage(
                role=role,
                content=content,
                author=author,
                metadata=metadata or {},
            )
        )

    async def stream(self) -> AsyncIterator[Event]:
        """Subscribe to real-time events from this job.

        Each call creates a new independent subscriber queue, so multiple
        SSE connections can listen simultaneously.

        Yields events until the job completes, fails, or is cancelled.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.type in ("completed", "error", "cancelled"):
                    break
        finally:
            self._subscribers.remove(queue)

    async def wait(self) -> None:
        """Block until the job finishes (completed, failed, or cancelled)."""
        await self._done_event.wait()

    async def cancel(self) -> None:
        """Cancel the running job."""
        if self.status != JobStatus.RUNNING:
            return
        self.status = JobStatus.CANCELLED
        self.finished_at = datetime.now(timezone.utc)
        if self._task and not self._task.done():
            self._task.cancel()
        self._emit("cancelled")
        self._done_event.set()

    def _mark_completed(self, result: JobResult) -> None:
        self.status = JobStatus.COMPLETED
        self.finished_at = datetime.now(timezone.utc)
        self.result = result
        self._emit("completed", {"result": result.to_dict()})
        self._done_event.set()

    def _mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.finished_at = datetime.now(timezone.utc)
        self.error = error
        self._emit("error", {"message": error})
        self._done_event.set()

    def _mark_running(self) -> None:
        self.status = JobStatus.RUNNING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.name,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "author": self.author,
            "instruction": self.instruction,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "chat_history": [m.to_dict() for m in self.chat_history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        job = cls(instruction=data["instruction"], author=data["author"])
        job.id = data["id"]
        job.status = JobStatus[data["status"]]
        job.created_at = datetime.fromisoformat(data["created_at"])
        job.finished_at = (
            datetime.fromisoformat(data["finished_at"])
            if data.get("finished_at")
            else None
        )
        job.error = data.get("error")
        job.chat_history = [
            ChatMessage.from_dict(m) for m in data.get("chat_history", [])
        ]
        if data.get("result"):
            job.result = JobResult.from_dict(data["result"])
        return job
