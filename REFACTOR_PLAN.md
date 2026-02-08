# openAGV Refactoring Plan: Server-Ready Architecture

> Status: DRAFT — awaiting feedback before implementation
> Breaking changes: ALLOWED (alpha stage)
> FastAPI integration: deferred to later, openagv stays a pure library

## Decisions Log

| Question | Decision |
|----------|----------|
| Multiple timelines per project? | No. Single timeline. Users duplicate the project to try a different timeline. |
| Auth / multi-tenancy? | Server's concern. But openagv tracks an `author` on chat messages and user-initiated actions for history clarity. |
| Backwards compat with notebooks? | Breaking changes OK. We're alpha. Will update examples after. |
| FastAPI contrib module? | Not now. openagv is a library; server integration comes later. |
| Project duplication strategy? | Symlink assets. Fast, space-efficient. If original is deleted, that's the server's problem to manage. |
| Token streaming? | Yes. Hook into SK's streaming API. Emit `token` events for "typing" UX. |
| LLM config ownership? | Project creates clients internally via `create_clients()`. Caller provides config args (provider, model). API keys excluded from serialization — re-injected at load time. |
| Persistence approach? | Pure JSON. `project.to_json()` / `Project.from_json()`. No database logic in openagv. Server stores as JSONB. Validation is shared: openagv validates structure on load, server validates business rules. |
| SqliteAssetBin? | Dropped. AssetBin is JSON-only. Server's JSONB column replaces what SQLite was doing. |
| Job history in project JSON? | Yes. All completed jobs (with chat history) are included in `project.to_json()`. One blob. |
| Timeline in project JSON? | Embedded inline. OTIO data is a nested object inside the project JSON. |
| API keys in JSON? | Excluded. `to_json()` includes provider + model but NOT api_key. Server re-injects secrets on load/execute. |

---

## Goal

Make openAGV usable as a backend library behind a FastAPI (or similar) server, serving a React frontend for content-creator teams. The four pillars:

1. **Project wrapper** — single entry point that bundles all components
2. **Job model** — async execution with trackable status
3. **SSE event streaming** — real-time progress to the frontend (including token streaming)
4. **Storage backend** — abstract file I/O away from local-only paths

---

## 1. Project Wrapper

### Problem

Today, using openAGV requires manually wiring 5+ objects:

```python
ab = SqliteAssetBin("db.db")
vision = ORVisionAnalyzer(client, ab)
timeline = OTIOTimeline(ab)
checklist = ChecklistManager()
executor = SKLoopExecutor(ab, instruction, chat_completion, uses=[vision, timeline])
```

A server needs isolated, self-contained units per user/project.

### Proposed: `Project` class

```
openagv/project.py
```

Responsibilities:
- Owns an `AssetBin`, `OTIOTimeline`, `ChecklistManager`, `StorageBackend`
- Holds LLM config (provider, model) — creates clients internally
- Provides `register_module(module)` for analyzers/generators
- Provides `submit(instruction, author=) -> Job` to kick off agent work
- Provides `duplicate() -> Project` — symlinks assets, fresh timeline
- Fully JSON-serializable

### Project identity

- `id`: Auto-generated UUID by default, can be overridden at creation
- `name`: Optional human-readable label
- `created_at`, `updated_at`: timestamps
- `parent_id`: UUID of source project if this is a duplicate, else None

### LLM config

Project stores config but not secrets:

```python
@dataclass
class LLMConfig:
    provider: str          # "ollama", "openrouter", "openai"
    model: str             # "qwen2.5:14b", "gpt-4o-mini", etc.
    service_id: str = "default"
    base_url: str | None = None  # optional override

    # NOT serialized:
    api_key: str | None = None   # injected at runtime, excluded from to_dict()
```

Usage:
```python
# Creating a project
project = Project(
    name="Spring Campaign",
    llm_config=LLMConfig(provider="openrouter", model="openai/gpt-4o-mini"),
    storage=LocalStorageBackend("/data/projects"),
)

# Loading from JSON — server re-injects the key
data = json.loads(db_row.project_json)
project = Project.from_dict(data, api_key=os.environ["OPENROUTER_API_KEY"],
                            storage=LocalStorageBackend("/data/projects"))
```

### Duplication

```python
new_project = project.duplicate(name="v2 — shorter cuts")
# new_project gets: symlinked assets, same modules, same LLM config
# new_project gets: fresh empty timeline, fresh checklist, new ID
# new_project.parent_id = project.id
```

### Project lifecycle

Reusable across multiple executions. A content creator iterates:
1. "Create a 30s product showcase" → Job 1
2. "Make the cuts faster and add lower thirds" → Job 2
3. "Add background music" → Job 3

All jobs accumulate in the project and are included in serialization.

---

## 2. JSON Serialization Model

### Core principle

openagv has **zero database code**. Everything goes in and out as JSON dicts. The server owns persistence (JSONB column, file, whatever).

### `project.to_dict()` / `project.to_json()` output structure

```json
{
  "id": "uuid-here",
  "name": "Spring Campaign",
  "parent_id": null,
  "created_at": "2026-02-08T12:00:00Z",
  "updated_at": "2026-02-08T14:30:00Z",

  "llm_config": {
    "provider": "openrouter",
    "model": "openai/gpt-4o-mini",
    "service_id": "default",
    "base_url": null
  },

  "asset_bin": {
    "assets": [
      {
        "id": "sha256-hash",
        "storage_key": "assets/sha256-hash.jpg",
        "asset_type": "IMAGE",
        "metadata": {},
        "analyses": [
          {"analyzer_name": "ORVisionAnalyzer", "content": "A red flower..."}
        ]
      }
    ]
  },

  "timeline": {
    "_otio": { ... },
    "width": 1280,
    "height": 720,
    "fps": 30
  },

  "modules": ["ORVisionAnalyzer", "TextCardGenerator"],

  "jobs": [
    {
      "id": "job-uuid",
      "status": "COMPLETED",
      "author": "user:jane@company.com",
      "instruction": "Create a product showcase",
      "created_at": "2026-02-08T12:00:00Z",
      "finished_at": "2026-02-08T12:02:30Z",
      "chat_history": [
        {
          "role": "system",
          "content": "You are an expert...",
          "author": "system",
          "timestamp": "2026-02-08T12:00:00Z",
          "metadata": {}
        },
        {
          "role": "user",
          "content": "Create a product showcase",
          "author": "user:jane@company.com",
          "timestamp": "2026-02-08T12:00:01Z",
          "metadata": {}
        },
        {
          "role": "agent",
          "content": "I'll start by analyzing the assets...",
          "author": "agent:video-editor",
          "timestamp": "2026-02-08T12:00:05Z",
          "metadata": {"tokens_used": 150}
        }
      ],
      "result": {
        "agent_response": "I've created a 30-second showcase...",
        "timeline_path": "timelines/output.otio",
        "render_path": "renders/output.mp4",
        "assets_snapshot": {}
      },
      "error": null
    }
  ]
}
```

### Validation on load

`Project.from_dict(data)` validates:
- Required fields present (id, llm_config, asset_bin)
- Asset types are valid enum values
- Job statuses are valid
- Timeline structure is parseable by OTIO
- Raises `ProjectValidationError` with details on failure

The server does its own validation (permissions, quota, etc.) — openagv only validates structural integrity.

### What is NOT serialized

- `api_key` — re-injected on load
- `StorageBackend` instance — re-created on load (it's infra config, not project state)
- `executor` / `_task` on Jobs — runtime-only, not resumable
- Module instances (analyzers/generators) — re-registered on load using `modules` list as a hint

---

## 3. Job Model + Chat History

### Problem

`SKLoopExecutor.start()` is a blocking `await` with no way to:
- Track it by ID from another HTTP request
- Query its status externally
- Cancel it cleanly
- Retrieve results after completion

### Proposed: `Job` class

```
openagv/job.py
```

```
Job
├── id: str (uuid)
├── status: JobStatus (PENDING, RUNNING, COMPLETED, FAILED, CANCELLED)
├── created_at: datetime
├── finished_at: datetime | None
├── author: str                   # who submitted this job
├── instruction: str
├── result: JobResult | None
├── error: str | None
├── chat_history: list[ChatMessage]  # full conversation with attribution
├── executor: SKLoopExecutor      # NOT serialized
└── _task: asyncio.Task           # NOT serialized
```

### Chat history with author tracking

```python
@dataclass
class ChatMessage:
    role: str          # "system" | "user" | "agent" | "tool"
    content: str
    author: str        # "user:jane@company.com" | "agent:video-editor" | "system"
    timestamp: datetime
    metadata: dict     # tool call details, token count, etc.
```

SK's internal `ChatHistory` is still used for LLM calls. `ChatMessage` is the external-facing log that wraps it with attribution.

### Interaction with Project

```python
# Non-blocking — returns Job immediately, runs in background
job = project.submit("Create a product showcase video", author="user:jane")

# Check status
job.status  # JobStatus.RUNNING

# Nudge mid-execution
await job.nudge("Also add background music", author="user:jane")

# Wait for completion
await job.wait()

# Get results
job.result.render_path  # storage key for the rendered video

# Browse conversation
job.chat_history  # list[ChatMessage] with full attribution
```

### Concurrency

One active job per project at a time. If a job is running, `project.submit()` raises. The server can queue at its layer.

### Cancellation

```python
await job.cancel()
# 1. Sets status to CANCELLED
# 2. Calls Stepper.stop()
# 3. Calls asyncio.Task.cancel() on the background task
# 4. Emits a "cancelled" event
```

---

## 4. SSE Event Streaming + Token Streaming

### Problem

`Stepper` has a callback system (`register_callback`) but it's synchronous and in-process only. A React frontend needs real-time updates over HTTP, including LLM token streaming for a "typing" UX.

### Approach: asyncio.Queue on Job (framework-agnostic)

openagv uses `asyncio.Queue` internally — no web framework dependency. The server drains the queue however it wants (SSE, WebSocket, polling).

```python
@dataclass
class Event:
    type: str          # see event types table below
    timestamp: datetime
    job_id: str
    data: dict         # payload varies by type
```

### Event flow

```
Stepper._notify_on_change()
    ↓
Job._on_stepper_event()      # registered as Stepper callback
    ↓
Event pushed to Job._event_queue
    ↓
job.stream()                  # async generator that yields from queue
```

For token streaming, the executor uses SK's streaming API:
```
SK streaming response
    ↓
SKLoopExecutor._execute_cycle() iterates over streamed chunks
    ↓
Each chunk → Event(type="token", data={"content": "..."})
    ↓
Pushed to Job._event_queue alongside other events
```

### Event types and payloads

| Event type | `data` payload |
|------------|----------------|
| `state_change` | `{"from": "PLANNING", "to": "EXECUTING"}` |
| `step_started` | `{"step_name": "AssetBin.list_assets", "step_index": 3}` |
| `step_completed` | `{"step_name": "AssetBin.list_assets", "step_index": 3}` |
| `tool_call` | `{"plugin": "ORVisionAnalyzer", "function": "analyze_asset", "args": {...}}` |
| `token` | `{"content": "I'll", "role": "agent"}` |
| `agent_message` | `{"content": "I've finished composing the timeline."}` |
| `log` | `{"message": "...", "level": "INFO"}` |
| `completed` | `{"result": {... JobResult as dict ...}}` |
| `error` | `{"message": "...", "traceback": "..."}` |
| `cancelled` | `{}` |

### Multiple listeners

`job.stream()` returns a new queue-tap each time it's called, so multiple SSE connections can subscribe independently. Internally uses a broadcast pattern — Job fans out events to all subscriber queues.

### Token streaming integration

The `SKLoopExecutor._execute_cycle()` currently uses `service.get_chat_message_content()`. To support token streaming, it switches to `service.get_streaming_chat_message_content()` and iterates over chunks:

```python
# Current (non-streaming)
result = await service.get_chat_message_content(...)

# New (streaming)
async for chunk in service.get_streaming_chat_message_content(...):
    self._emit_token(chunk)  # pushes Event to Job's queue
```

The full message is still accumulated for chat history — streaming is purely for the real-time UX.

---

## 5. Storage Backend

### Problem

`AssetBin` and `FfmpegOTIORenderer` use local file paths everywhere. This works for notebooks but not for cloud deployment.

### Proposed: `StorageBackend` protocol

```
openagv/storage.py
```

```python
class StorageBackend(Protocol):
    def store(self, source_path: str, dest_key: str) -> str:
        """Copy a local file into managed storage. Returns the storage key."""
        ...

    def retrieve(self, key: str) -> str:
        """Get the canonical path/URI for a stored file."""
        ...

    def load_to_temp(self, key: str) -> str:
        """Download/copy file to a temp directory, return the local path.
        Caller uses this when they need a real local file (FFmpeg, PIL, etc.).
        For LocalStorageBackend this is a no-op that returns the real path.
        For S3 this downloads to a temp dir. Temp files are cleaned up
        when the StorageBackend is closed or via explicit cleanup()."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def cleanup_temp(self) -> None:
        """Remove all temp files created by load_to_temp()."""
        ...

    def get_url(self, key: str) -> str:
        """Get a URL/path suitable for client download."""
        ...
```

Sync methods. FFmpeg/PIL are sync anyway. Future cloud backends use `asyncio.to_thread` internally.

`load_to_temp()` is the method that FFmpeg, PIL, and any other local-file-dependent tool should use. It makes the contract explicit: "give me a real local path I can read." For `LocalStorageBackend` it's a trivial pass-through. For cloud backends it downloads to a temp dir and tracks the file for cleanup.

### Built-in: `LocalStorageBackend`

Managed directory structure:

```
{root}/
├── {project_id}/
│   ├── assets/          # uploaded/imported media files
│   ├── generated/       # text cards, overlays, etc.
│   ├── renders/         # final rendered outputs
│   └── timelines/       # exported .otio files
```

No `project.db` or `project.json` — those are gone. The server stores project state in JSONB. Storage backend only manages media files.

- `store()` copies file into the appropriate subdirectory
- `retrieve()` returns the local path directly
- `get_url()` returns the local path (server maps to static file route)
- Keys are relative paths like `assets/abc123.jpg`

### Integration points

- `Asset.file_path` → renamed to `Asset.storage_key`
- `Asset.local_path(storage)` → resolves to a real local path via `storage.load_to_temp(key)`
- `AssetBin.add()` stores file via backend, sets `storage_key`
- `AssetBin` no longer does `os.path.exists()` — uses `storage.exists()`
- `FfmpegOTIORenderer` receives a `StorageBackend`, calls `load_to_temp()` to get local paths for FFmpeg input, then `store()` for output
- Renderer calls `storage.cleanup_temp()` after render completes
- `SqliteAssetBin` is **removed**

### Asset identity

- `Asset.id` stays as SHA-256 content hash (deduplication)
- `Asset.storage_key` is the location within storage (e.g. `assets/{hash}.jpg`)
- Separate concerns: identity vs. location

### Project duplication and storage

When `project.duplicate()` is called:
1. New project gets a new storage directory `{root}/{new_project_id}/`
2. Assets are **symlinked** from the original project's storage
3. Timeline and checklist start fresh
4. `parent_id` references original project

---

## 6. Removals

These are removed in this refactor:

| What | Why |
|------|-----|
| `SqliteAssetBin` | Replaced by JSON serialization + server JSONB |
| `core/bin.py: SqliteAssetBin` class | Removed entirely |
| `Asset.file_path` field | Renamed to `Asset.storage_key` |
| `Asset.set_save_callback()` | No longer needed — persistence is JSON dump, not per-change callbacks |
| `AssetBin.json_dump()` / `from_json_file()` | Replaced by `Project.to_json()` / `Project.from_json()` |

---

## File Structure (Proposed)

```
openagv/
├── __init__.py          # updated exports
├── project.py           # NEW — Project wrapper, JSON serialization
├── job.py               # NEW — Job, JobResult, ChatMessage, Event, JobStatus
├── storage.py           # NEW — StorageBackend protocol + LocalStorageBackend
├── executor.py          # MODIFIED — token streaming, event emission
├── stepper.py           # MODIFIED — async-compatible event emission
├── llm.py               # MINOR — LLMConfig dataclass added
├── renderer.py          # MODIFIED — use storage backend for I/O
├── core/
│   ├── bin.py           # MODIFIED — use storage backend, drop SqliteAssetBin
│   ├── assets.py        # MODIFIED — storage_key replaces file_path
│   ├── timeline.py      # MODIFIED — inline serialization for project JSON
│   └── ...              # rest mostly unchanged
└── modules/
    └── ...              # minor changes (resolve paths via storage)
```

---

## Implementation Order

| Phase | What | Depends on |
|-------|------|------------|
| **Phase 1** | `StorageBackend` protocol + `LocalStorageBackend` | nothing |
| **Phase 2** | `Event`, `ChatMessage`, `Job`, `JobStatus`, `JobResult` models | nothing |
| **Phase 3** | `LLMConfig` dataclass, refactor `create_clients()` to accept it | nothing |
| **Phase 4** | Refactor `Asset` → `storage_key`, wire `AssetBin` to use `StorageBackend`, drop `SqliteAssetBin` | Phase 1 |
| **Phase 5** | `Project` wrapper (bundles everything, JSON serialization, duplicate) | Phase 1-4 |
| **Phase 6** | Wire token streaming + events into `SKLoopExecutor` + `Job` | Phase 2 + 5 |
| **Phase 7** | Update `Renderer` to use storage backend | Phase 4 |
| **Phase 8** | Update `__init__.py` exports, update example notebooks | Phase 5-7 |

Phases 1, 2, and 3 can be done in parallel.

---

## Open Questions (Remaining)

None — all major decisions resolved. Ready for implementation on approval.
