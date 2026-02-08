# CLAUDE.md

## Project

openAGV — agentic video editing library. Uses Semantic Kernel for LLM orchestration, OpenTimelineIO for timeline composition, FFmpeg for rendering. Designed as a backend library (not a server) for use behind FastAPI/etc.

## Setup

```bash
uv sync            # install deps
uv run pdoc openagv -o docs   # generate docs
```

Python >=3.13 required. Package manager is `uv` (lockfile: `uv.lock`).

No test suite yet — verify changes with `python -m py_compile <file>` and the sample notebooks.

## Architecture

```
openagv/
  core/          # Base classes, assets, timeline, asset bin
    base.py      # Agentable base class — agent_action = kernel_function
    assets.py    # Asset with storage_key identity, SHA-256 hash dedup
    bin.py       # AssetBin — asset collection with StorageBackend
    timeline.py  # OTIOTimeline — wraps opentimelineio
  modules/       # Pluggable analyzers/generators
    vision.py    # ORVisionAnalyzer (OpenAI-compatible vision API)
    audio.py     # DeepgramAnalyzer
    textcard.py  # TextCardGenerator (Pillow-based)
  executor.py    # SKLoopExecutor — Semantic Kernel agentic loop with streaming
  project.py     # Project — top-level wrapper, JSON serializable, job management
  job.py         # Job, Event, ChatMessage — async broadcast to SSE subscribers
  llm.py         # LLMConfig + create_clients() factory
  storage.py     # StorageBackend protocol + LocalStorageBackend
  renderer.py    # FfmpegOTIORenderer
  stepper.py     # Checklist/step tracking
```

## Key patterns

- **`@agent_action`** is an alias for Semantic Kernel's `@kernel_function`. Any method decorated with it becomes an LLM-callable tool.
- **StorageBackend protocol**: All file access goes through `storage.store()` / `storage.load_to_temp()`. Assets use `storage_key` (e.g. `assets/<sha256>.jpg`), never raw file paths.
- **Project is the main entry point**: `Project` owns AssetBin, OTIOTimeline, ChecklistManager, jobs. `project.submit()` launches an async executor. `project.to_dict()` / `Project.from_dict()` for JSON persistence.
- **API keys excluded from serialization**: `LLMConfig.to_dict()` omits `api_key`. Re-inject via `from_dict(data, api_key=...)`.
- **Job event broadcasting**: `job.stream()` returns an `AsyncIterator[Event]` via per-subscriber `asyncio.Queue`. Multiple SSE listeners supported.
- **Token streaming**: Executor uses `get_streaming_chat_message_contents()` and emits `token` events through the event emitter.

## Server persistence

openagv is a library, not a server. The server (e.g. FastAPI) is responsible for storing and loading projects. The intended pattern:

**Saving**: Call `project.to_dict()` → store the resulting dict as JSON/JSONB in your database. The dict contains everything needed to reconstruct the project: asset bin, timeline (embedded OTIO), checklist, jobs, chat history, system prompt, LLM config (minus `api_key`), and analysis config. Media files live in the `StorageBackend`, not in the dict.

**Loading**: Call `Project.from_dict(data, api_key=..., storage=...)` to reconstruct. The server must re-inject two runtime concerns:
1. `api_key` — excluded from serialization for security. Pass it from your secrets store.
2. `storage` — a `StorageBackend` instance. The server decides local vs. cloud. For `LocalStorageBackend`, pass `root` (shared across projects) and the project's `id`.

**Modules**: `from_dict()` restores `_module_names` (a list of class name strings) but does NOT reinstantiate module objects. The server must call `project.register_module()` for each module after loading. This is intentional — modules hold runtime state (API clients, configs) that the server controls.

**File storage layout** (`LocalStorageBackend`):
```
{root}/{project_id}/
    assets/        # uploaded media (keyed by SHA-256 hash)
    generated/     # text cards, overlays from generators
    renders/       # final rendered video files
    timelines/     # exported .otio files
```

**Chat history**: `project.chat_history` persists user/agent messages across jobs, giving the LLM conversational context when a new job starts. Serialized in `to_dict()`. Distinct from per-job `job.chat_history` which tracks a single execution's messages.

**Event types**: Use the `EventType` enum from `job.py` for event type constants. It's a `str` enum so values serialize as plain strings (`"token"`, `"completed"`, etc.).

## Style

- No linter configured. Follow existing code style (no type stubs, minimal docstrings).
- `from __future__ import annotations` is NOT used — use `X | None` directly (Python 3.13+).
- Use `TYPE_CHECKING` guard for circular imports (e.g. StorageBackend in core modules).
- Avoid adding unnecessary error handling, docstrings, or abstractions beyond what's needed.

## Common tasks

- **Add a new module**: Subclass `Agentable`, use `@agent_action` for LLM-callable methods. Add `set_storage()` and `set_asset_bin()` if needed. Register via `project.register_module()`.
- **Modify asset handling**: Assets use `storage_key` not `file_path`. Use `asset.local_path(storage)` to get a real filesystem path for FFmpeg/PIL.
- **Timeline clips**: `target_url` in OTIO clips must be a real local path (resolved via `timeline._resolve_path()`), not a storage key.
