from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import shutil
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit

from .errors import StateError, ValidationError
from .storage import read_json, require_vault, safe_child, utc_now, write_json

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
LOCAL_KINDS = {"paper", "document", "chat"}


def _manifest_path(vault: Path) -> Path:
    return vault / ".learning-wiki/manifest.json"


def _load_manifest(vault: Path) -> dict[str, Any]:
    manifest = read_json(_manifest_path(vault))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("sources"), dict):
        raise StateError("manifest.json has an unsupported schema")
    return manifest


def _hash_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _require_kind(kind: str) -> str:
    if kind not in LOCAL_KINDS:
        raise ValidationError(f"unsupported source kind: {kind}")
    return kind


def _require_title(title: str) -> str:
    normalized = title.strip()
    if not normalized:
        raise ValidationError("title must not be empty")
    if len(normalized) > 300 or "\x00" in normalized:
        raise ValidationError("title must be at most 300 characters")
    return normalized


def _source_id(kind: str, digest: str) -> str:
    return f"{kind}-{digest[:16]}"


def _relative(path: Path, vault: Path) -> str:
    return path.relative_to(vault).as_posix()


def _record_source(vault: Path, manifest: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    source_id = record["id"]
    source_dir = safe_child(vault, "sources", source_id)
    source_dir.mkdir(parents=True, exist_ok=False)
    write_json(source_dir / "metadata.json", record)
    manifest["sources"][source_id] = record
    write_json(_manifest_path(vault), manifest)
    with (vault / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {record['imported_at']} SOURCE_ADD id={source_id} kind={record['kind']}\n")
    return {"duplicate": False, "source": record}


def add_local_source(
    vault: Path,
    source_path: Path,
    *,
    kind: str,
    title: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    _require_kind(kind)
    path = source_path.expanduser().resolve()
    if not path.is_file():
        raise ValidationError(f"source is not a regular file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValidationError(f"source exceeds {max_bytes} byte limit")
    with path.open("rb") as handle:
        digest = _hash_stream(handle)

    manifest = _load_manifest(root)
    source_id = _source_id(kind, digest)
    existing = manifest["sources"].get(source_id)
    if existing:
        return {"duplicate": True, "source": existing}

    source_dir = safe_child(root, "sources", source_id)
    if source_dir.exists():
        raise StateError(f"orphan source directory already exists: {source_id}")
    source_dir.mkdir(parents=True)
    snapshot = safe_child(source_dir, path.name)
    shutil.copyfile(path, snapshot)
    snapshot.chmod(0o444)
    imported_at = utc_now()
    record = {
        "id": source_id,
        "kind": kind,
        "title": _require_title(title or path.stem),
        "origin": str(path),
        "snapshot": _relative(snapshot, root),
        "sha256": digest,
        "size": size,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "imported_at": imported_at,
        "status": "new",
    }
    write_json(source_dir / "metadata.json", record)
    manifest["sources"][source_id] = record
    write_json(_manifest_path(root), manifest)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {imported_at} SOURCE_ADD id={source_id} kind={kind}\n")
    return {"duplicate": False, "source": record}


def _normalize_https_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url.strip())
        port = parsed.port
    except ValueError as error:
        raise ValidationError("invalid URL") from error
    if parsed.scheme.lower() != "https":
        raise ValidationError("web sources must use HTTPS")
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("URL credentials are not allowed")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValidationError("local network URLs are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValidationError("private or reserved IP URLs are not allowed")

    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        rendered_host = f"{rendered_host}:{port}"
    path = parsed.path or "/"
    return urlunsplit(("https", rendered_host, path, parsed.query, ""))


def add_url_source(vault: Path, url: str, *, title: str | None = None) -> dict[str, Any]:
    root = require_vault(vault)
    normalized = _normalize_https_url(url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    source_id = _source_id("web", digest)
    manifest = _load_manifest(root)
    existing = manifest["sources"].get(source_id)
    if existing:
        return {"duplicate": True, "source": existing}

    record = {
        "id": source_id,
        "kind": "web",
        "title": _require_title(title or urlsplit(normalized).hostname or "Web source"),
        "origin": normalized,
        "snapshot": None,
        "sha256": digest,
        "size": 0,
        "media_type": "text/html",
        "imported_at": utc_now(),
        "status": "new",
    }
    return _record_source(root, manifest, record)


def attach_web_snapshot(
    vault: Path,
    source_id: str,
    snapshot_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    manifest = _load_manifest(root)
    source = manifest["sources"].get(source_id)
    if source is None:
        raise ValidationError(f"unknown source ID: {source_id}")
    if source.get("kind") != "web":
        raise ValidationError(f"source is not a web reference: {source_id}")
    if source.get("snapshot") is not None:
        raise StateError(f"web source already has a snapshot: {source_id}")
    path = snapshot_path.expanduser().resolve()
    if not path.is_file():
        raise ValidationError(f"snapshot is not a regular file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValidationError(f"source exceeds {max_bytes} byte limit")
    with path.open("rb") as handle:
        digest = _hash_stream(handle)
    suffix = path.suffix.lower() if path.suffix.lower() in {".html", ".md", ".txt"} else ".txt"
    destination = safe_child(root, "sources", source_id, f"snapshot{suffix}")
    shutil.copyfile(path, destination)
    destination.chmod(0o444)
    source["reference_sha256"] = source["sha256"]
    source["sha256"] = digest
    source["size"] = size
    source["snapshot"] = _relative(destination, root)
    source["media_type"] = mimetypes.guess_type(destination.name)[0] or "text/plain"
    source["snapshot_at"] = utc_now()
    write_json(root / "sources" / source_id / "metadata.json", source)
    write_json(_manifest_path(root), manifest)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {source['snapshot_at']} WEB_SNAPSHOT id={source_id}\n")
    return {"source": source}


def add_snippet(
    vault: Path,
    *,
    title: str,
    text: str,
    kind: str = "chat",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    root = require_vault(vault)
    if kind != "chat":
        raise ValidationError("snippets currently support kind=chat only")
    normalized_title = _require_title(title)
    if not text.strip():
        raise ValidationError("snippet text must not be empty")
    payload = text.encode("utf-8")
    if len(payload) > max_bytes:
        raise ValidationError(f"source exceeds {max_bytes} byte limit")
    digest = hashlib.sha256(payload).hexdigest()
    source_id = _source_id(kind, digest)
    manifest = _load_manifest(root)
    existing = manifest["sources"].get(source_id)
    if existing:
        return {"duplicate": True, "source": existing}

    source_dir = safe_child(root, "sources", source_id)
    if source_dir.exists():
        raise StateError(f"orphan source directory already exists: {source_id}")
    source_dir.mkdir(parents=True)
    snapshot = source_dir / "snippet.md"
    snapshot.write_bytes(payload)
    snapshot.chmod(0o444)
    imported_at = utc_now()
    record = {
        "id": source_id,
        "kind": kind,
        "title": normalized_title,
        "origin": "inline:chat",
        "snapshot": _relative(snapshot, root),
        "sha256": digest,
        "size": len(payload),
        "media_type": "text/markdown",
        "imported_at": imported_at,
        "status": "new",
    }
    write_json(source_dir / "metadata.json", record)
    manifest["sources"][source_id] = record
    write_json(_manifest_path(root), manifest)
    with (root / "log.md").open("a", encoding="utf-8") as log:
        log.write(f"- {imported_at} SOURCE_ADD id={source_id} kind={kind}\n")
    return {"duplicate": False, "source": record}


def list_sources(vault: Path) -> list[dict[str, Any]]:
    root = require_vault(vault)
    manifest = _load_manifest(root)
    return sorted(manifest["sources"].values(), key=lambda item: (item["imported_at"], item["id"]))
