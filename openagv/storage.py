"""Storage backend abstraction for openAGV.

Defines the StorageBackend protocol and provides a LocalStorageBackend
implementation for managed local file storage.
"""

import os
import shutil
import tempfile
from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for file storage backends.

    All methods are synchronous. Cloud backends (future) can use
    asyncio.to_thread internally if needed.
    """

    def store(self, source_path: str, dest_key: str) -> str:
        """Copy a local file into managed storage.

        Args:
            source_path: Path to the local file to store.
            dest_key: Relative key within storage (e.g. "assets/abc123.jpg").

        Returns:
            The storage key for the stored file.
        """
        ...

    def retrieve(self, key: str) -> str:
        """Get the canonical path/URI for a stored file.

        Args:
            key: The storage key.

        Returns:
            Canonical path or URI.
        """
        ...

    def load_to_temp(self, key: str) -> str:
        """Ensure file is available as a real local path.

        Use this when you need a local file for tools like FFmpeg or PIL.
        For local backends this returns the real path directly.
        For cloud backends this downloads to a temp directory.

        Temp files are tracked and cleaned up via cleanup_temp().

        Args:
            key: The storage key.

        Returns:
            A local filesystem path to the file.
        """
        ...

    def exists(self, key: str) -> bool:
        """Check if a file exists in storage."""
        ...

    def delete(self, key: str) -> None:
        """Delete a file from storage."""
        ...

    def cleanup_temp(self) -> None:
        """Remove all temp files created by load_to_temp()."""
        ...

    def get_url(self, key: str) -> str:
        """Get a URL/path suitable for client download.

        For local storage this returns the filesystem path.
        For S3 this would return a presigned URL.
        """
        ...


class LocalStorageBackend:
    """Managed local file storage.

    Directory structure:
        {root}/{project_id}/
            assets/          # uploaded/imported media files
            generated/       # text cards, overlays, etc.
            renders/         # final rendered outputs
            timelines/       # exported .otio files
    """

    def __init__(self, root: str, project_id: str):
        self.root = root
        self.project_id = project_id
        self._base_path = os.path.join(root, project_id)
        self._temp_files: list[str] = []

    @property
    def base_path(self) -> str:
        return self._base_path

    def _ensure_dir(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _full_path(self, key: str) -> str:
        return os.path.join(self._base_path, key)

    def store(self, source_path: str, dest_key: str) -> str:
        full_dest = self._full_path(dest_key)
        self._ensure_dir(full_dest)

        # Don't copy if source and dest are the same file
        if os.path.abspath(source_path) == os.path.abspath(full_dest):
            return dest_key

        shutil.copy2(source_path, full_dest)
        return dest_key

    def retrieve(self, key: str) -> str:
        return self._full_path(key)

    def load_to_temp(self, key: str) -> str:
        # For local storage, the file is already local — just return the path.
        return self._full_path(key)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._full_path(key))

    def delete(self, key: str) -> None:
        path = self._full_path(key)
        if os.path.exists(path):
            os.remove(path)

    def cleanup_temp(self) -> None:
        # No temp files created for local storage, but support the protocol.
        for path in self._temp_files:
            if os.path.exists(path):
                os.remove(path)
        self._temp_files.clear()

    def get_url(self, key: str) -> str:
        return self._full_path(key)

    def symlink_from(self, source_backend: "LocalStorageBackend", key: str) -> str:
        """Create a symlink to a file in another project's storage.

        Used for project duplication — avoids copying large media files.

        Args:
            source_backend: The storage backend to link from.
            key: The storage key (same key in both source and dest).

        Returns:
            The storage key.
        """
        source_path = source_backend._full_path(key)
        dest_path = self._full_path(key)
        self._ensure_dir(dest_path)

        if os.path.exists(dest_path) or os.path.islink(dest_path):
            os.remove(dest_path)

        os.symlink(os.path.abspath(source_path), dest_path)
        return key
