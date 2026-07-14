from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .errors import StateError
from .storage import require_vault, safe_child, state_file, write_json


def _acquire_descriptor_lock(descriptor: int) -> None:
    if os.name == "nt":
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_descriptor_lock(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def workspace_lock(vault: Path) -> Iterator[None]:
    """Acquire the workspace's non-blocking, single-writer lock."""
    root = require_vault(vault)
    lock_path = state_file(root, "workspace.lock")
    if lock_path.is_symlink():
        raise StateError("workspace lock must not be a symlink")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        try:
            _acquire_descriptor_lock(descriptor)
        except OSError as error:
            if error.errno in {
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EDEADLK", errno.EACCES),
            }:
                raise StateError("workspace is locked by another writer") from error
            raise
        acquired = True
        yield
    finally:
        try:
            if acquired:
                _release_descriptor_lock(descriptor)
        finally:
            os.close(descriptor)


def persist_manifest(
    vault: Path,
    manifest: dict[str, Any],
    source_ids: Iterable[str],
) -> None:
    """Persist changed source records and their manifest under an existing lock."""
    root = require_vault(vault)
    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        raise StateError("manifest.json has an unsupported schema")
    for source_id in dict.fromkeys(source_ids):
        source = sources.get(source_id)
        if not isinstance(source_id, str) or not isinstance(source, dict):
            raise StateError(f"manifest has an invalid source record: {source_id}")
        metadata_path = safe_child(root, "sources", source_id, "metadata.json")
        write_json(metadata_path, source)
    write_json(state_file(root, "manifest.json"), manifest)
