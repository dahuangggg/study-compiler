from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import StateError, StudyCompilerError, ValidationError
from .lessons import RELATED_BLOCK_END, RELATED_BLOCK_START, parse_lesson
from .schemas import relationship_target_slug
from .state import workspace_lock
from .storage import require_vault, safe_child, write_json

RELATED_START = RELATED_BLOCK_START
RELATED_END = RELATED_BLOCK_END
_CANONICAL_LINK_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\[\[lessons/([a-z0-9]+(?:-[a-z0-9]+)*)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]"
)
_ANY_LESSON_LINK_RE = re.compile(
    r"(?<!\\)(?:\\\\)*\[\[lessons/([^\]|#]+)(?:[|#][^\]]*)?\]\]"
)
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_BACKTICK_RUN_RE = re.compile(r"`+")
_HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?(?:-->|\Z)")
_LIST_ITEM_RE = re.compile(r"(?:[-+*]|\d+[.)])[ \t]+")
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^[ \t]{0,3}>[ \t]?")


def validate_related_block(body: str) -> None:
    starts = body.count(RELATED_START)
    ends = body.count(RELATED_END)
    if starts == 0 and ends == 0:
        return
    if starts != 1 or ends != 1 or body.index(RELATED_START) > body.index(RELATED_END):
        raise StateError("lesson has a malformed Related Lessons block")


def strip_related_block(body: str) -> str:
    validate_related_block(body)
    if RELATED_START not in body:
        return body.strip()
    start = body.index(RELATED_START)
    heading = "## Related Lessons"
    heading_start = body.rfind(heading, 0, start)
    if heading_start >= 0 and not body[heading_start + len(heading) : start].strip():
        start = heading_start
    end = body.index(RELATED_END, start) + len(RELATED_END)
    return (body[:start] + body[end:]).strip()


def render_related_block(relationships: list[dict[str, str]]) -> str:
    if not relationships:
        return ""
    lines = ["## Related Lessons", "", RELATED_START]
    for relationship in relationships:
        lines.append(f"- {relationship['target']} — `{relationship['type']}`")
    lines.append(RELATED_END)
    return "\n".join(lines)


def replace_related_block(
    body: str,
    relationships: list[dict[str, str]],
) -> str:
    teaching_body = strip_related_block(body)
    inline_targets = set(
        _CANONICAL_LINK_RE.findall(_without_code_examples(teaching_body))
    )
    managed_relationships = [
        relationship
        for relationship in relationships
        if relationship_target_slug(relationship["target"]) not in inline_targets
    ]
    block = render_related_block(managed_relationships)
    if not block:
        return teaching_body
    return f"{teaching_body}\n\n{block}"


def _strip_inline_code(line: str) -> str:
    rendered: list[str] = []
    cursor = 0
    while opener := _BACKTICK_RUN_RE.search(line, cursor):
        marker = opener.group(0)
        closer = _BACKTICK_RUN_RE.search(line, opener.end())
        while closer is not None and closer.group(0) != marker:
            closer = _BACKTICK_RUN_RE.search(line, closer.end())
        if closer is None:
            break
        rendered.append(line[cursor : opener.start()])
        rendered.append(" " * (closer.end() - opener.start()))
        cursor = closer.end()
    rendered.append(line[cursor:])
    return "".join(rendered)


def _markdown_container_content(line: str) -> tuple[str, int]:
    """Return content after Markdown blockquote markers and its quote depth."""
    content = line
    depth = 0
    while match := _BLOCKQUOTE_PREFIX_RE.match(content):
        content = content[match.end() :]
        depth += 1
    return content, depth


def _indent_width(line: str) -> int:
    width = 0
    for character in line:
        if character == " ":
            width += 1
        elif character == "\t":
            width += 4 - (width % 4)
        else:
            break
    return width


def _without_code_examples(body: str) -> str:
    rendered: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    list_context = False
    list_quote_depth: int | None = None
    for line in _HTML_COMMENT_RE.sub("", body).splitlines(keepends=True):
        structural_line, quote_depth = _markdown_container_content(line.rstrip("\r\n"))
        if fence_character is not None:
            closing = re.fullmatch(
                rf"[ \t]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                structural_line,
            )
            if closing is not None and quote_depth == fence_quote_depth:
                fence_character = None
                fence_length = 0
            rendered.append("\n" if line.endswith(("\n", "\r")) else "")
            continue
        opening = _FENCE_OPEN_RE.match(structural_line)
        if opening is not None:
            marker = opening.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            fence_quote_depth = quote_depth
            rendered.append("\n" if line.endswith(("\n", "\r")) else "")
            continue

        stripped = structural_line.lstrip(" \t")
        blank = not stripped
        if blank:
            list_context = False
            list_quote_depth = None
            rendered.append(line)
            continue
        if not blank and list_quote_depth != quote_depth:
            list_context = False
            list_quote_depth = None
        indentation = _indent_width(structural_line)
        list_item = _LIST_ITEM_RE.match(stripped) is not None
        if indentation >= 4 and (
            not list_context or (indentation >= 8 and not list_item)
        ):
            rendered.append("\n" if line.endswith(("\n", "\r")) else "")
            continue
        list_context = list_item or (list_context and indentation >= 4)
        list_quote_depth = quote_depth if list_context else None
        rendered.append(line)
    return _strip_inline_code("".join(rendered))


def _body_lesson_links(body: str) -> tuple[list[str], list[str]]:
    searchable = _without_code_examples(body)
    targets = _CANONICAL_LINK_RE.findall(searchable)
    invalid: list[str] = []
    for raw in _ANY_LESSON_LINK_RE.findall(searchable):
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", raw) is None:
            invalid.append(raw)
    return targets, invalid


def _collect_graph(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    pages: dict[str, dict[str, Any]] = {}
    paths = sorted((root / "lessons").glob("*.md"))
    for path in paths:
        if path.is_symlink():
            errors.append(f"graph lesson {path.name} must not be a symlink")
            continue
        try:
            metadata, body = parse_lesson(path.read_text(encoding="utf-8"))
            validate_related_block(body)
        except (StudyCompilerError, OSError, UnicodeError, TypeError) as error:
            errors.append(f"invalid graph lesson {path.name}: {error}")
            continue
        if metadata.get("slug") != path.stem:
            errors.append(f"graph lesson slug does not match file name: {path.name}")
            continue
        if metadata.get("status") != "approved":
            errors.append(f"graph lesson is not approved: {path.name}")
            continue
        expected_body = replace_related_block(body, metadata["relationships"])
        if body.strip() != expected_body:
            errors.append(f"lesson {path.name} Related Lessons block is stale")
        body_links, invalid_links = _body_lesson_links(body)
        for invalid in invalid_links:
            errors.append(
                "lesson "
                f"{path.name} has an invalid lesson link target: "
                f"{json.dumps(invalid, ensure_ascii=True)}"
            )
        pages[path.stem] = {
            "slug": path.stem,
            "title": metadata["title"],
            "path": path.relative_to(root).as_posix(),
            "relationships": metadata["relationships"],
            "body_links": body_links,
        }

    known = set(pages)
    typed_edges: list[dict[str, Any]] = []
    body_edges: set[tuple[str, str]] = set()
    broken_links: list[dict[str, str]] = []
    outgoing: dict[str, set[str]] = {slug: set() for slug in known}
    incoming: dict[str, set[str]] = {slug: set() for slug in known}

    for source, page in sorted(pages.items()):
        seen_typed: set[tuple[str, str]] = set()
        for relationship in page["relationships"]:
            target = relationship_target_slug(relationship["target"])
            key = (target, relationship["type"])
            if key in seen_typed:
                errors.append(
                    f"lesson {source}.md has duplicate relationship: {target} {relationship['type']}"
                )
                continue
            seen_typed.add(key)
            if target not in known:
                errors.append(
                    f"lesson {source}.md relationship has missing target: {target}"
                )
                continue
            if target == source:
                errors.append(f"lesson {source}.md relationship is a self-link")
                continue
            typed_edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": relationship["type"],
                    "typed": True,
                }
            )
            outgoing[source].add(target)
            incoming[target].add(source)
        for target in page["body_links"]:
            if target == source:
                continue
            if target not in known:
                broken_links.append({"source": source, "target": target})
                continue
            body_edges.add((source, target))
            outgoing[source].add(target)
            incoming[target].add(source)

    for item in broken_links:
        errors.append(
            f"lesson {item['source']}.md has broken lesson link: {item['target']}"
        )
    typed_pairs = {(edge["source"], edge["target"]) for edge in typed_edges}
    untyped_edges = [
        {
            "source": source,
            "target": target,
            "type": None,
            "typed": False,
        }
        for source, target in sorted(body_edges - typed_pairs)
    ]
    return {
        "errors": errors,
        "pages": pages,
        "typed_edges": sorted(
            typed_edges,
            key=lambda edge: (edge["source"], edge["target"], edge["type"]),
        ),
        "untyped_edges": untyped_edges,
        "broken_links": sorted(
            broken_links, key=lambda item: (item["source"], item["target"])
        ),
        "outgoing": outgoing,
        "incoming": incoming,
    }


def _graph_report_unlocked(root: Path, *, strict: bool = False) -> dict[str, Any]:
    graph = _collect_graph(root)
    slugs = sorted(graph["pages"])
    unreferenced = [slug for slug in slugs if not graph["incoming"][slug]]
    dead_ends = [slug for slug in slugs if not graph["outgoing"][slug]]
    isolated = [
        slug
        for slug in slugs
        if not graph["incoming"][slug] and not graph["outgoing"][slug]
    ]
    health_findings = bool(unreferenced or dead_ends or isolated)
    return {
        "ok": not graph["errors"] and not (strict and health_findings),
        "errors": graph["errors"],
        "broken_links": graph["broken_links"],
        "unreferenced": unreferenced,
        "dead_ends": dead_ends,
        "isolated": isolated,
        "counts": {
            "lessons": len(slugs),
            "typed_edges": len(graph["typed_edges"]),
            "untyped_edges": len(graph["untyped_edges"]),
            "broken_links": len(graph["broken_links"]),
        },
    }


def graph_report(vault: Path, *, strict: bool = False) -> dict[str, Any]:
    root = require_vault(vault)
    with workspace_lock(root):
        return _graph_report_unlocked(root, strict=strict)


def _export_graph_unlocked(root: Path) -> dict[str, Any]:
    graph = _collect_graph(root)
    if graph["errors"]:
        raise StateError(
            "cannot export invalid lesson graph: " + "; ".join(graph["errors"])
        )
    nodes = [
        {
            "id": slug,
            "title": graph["pages"][slug]["title"],
            "path": graph["pages"][slug]["path"],
        }
        for slug in sorted(graph["pages"])
    ]
    edges = sorted(
        graph["typed_edges"] + graph["untyped_edges"],
        key=lambda edge: (
            edge["source"],
            edge["target"],
            not edge["typed"],
            edge["type"],
        ),
    )
    return {"schema_version": 1, "directed": True, "nodes": nodes, "edges": edges}


def export_graph(vault: Path) -> dict[str, Any]:
    root = require_vault(vault)
    with workspace_lock(root):
        return _export_graph_unlocked(root)


def export_graph_file(vault: Path, destination: Path) -> Path:
    root = require_vault(vault)
    exports_root = safe_child(root, "exports")
    try:
        relative = destination.relative_to(exports_root)
    except ValueError as error:
        raise ValidationError(
            "graph exports must stay inside the exports directory"
        ) from error
    safe_destination = safe_child(exports_root, *relative.parts)
    if safe_destination.suffix.lower() != ".json":
        raise ValidationError("graph export path must end in .json")
    if safe_destination.exists() and not safe_destination.is_file():
        raise ValidationError("graph export destination must be a file")
    with workspace_lock(root):
        write_json(safe_destination, _export_graph_unlocked(root))
    return safe_destination
