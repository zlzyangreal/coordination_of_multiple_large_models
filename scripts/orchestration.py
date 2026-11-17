#!/usr/bin/env python3
"""Deterministic orchestration board for DSH multi-model collaboration.

The module deliberately uses only the Python standard library.  Its public helpers are
``load_board``, ``write_board``, ``apply_op`` and ``snapshot``; the command-line interface is
the sole integration surface used by the host bridge.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, NoReturn


SCHEMA = 1
BOARD_DIR = os.path.join("_work", "orchestration")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NODE_KINDS = {"work", "review", "integrate"}
NODE_STATUSES = {"pending", "ready", "running", "review", "rework", "done", "failed"}
LANES = {"dsh", "codex", "http"}
BLOCKING_SEVERITIES = {"high", "medium"}
CLOSED_FINDING_STATUSES = {"fixed", "accepted"}
MUTATING_OPS = {"init", "plan", "assign", "pin", "start", "result", "review", "deliver"}


class BoardError(Exception):
    """An expected CLI failure carrying the required process exit code."""

    def __init__(self, code: int, message: str, error: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.error = message if error is None else error


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser which reports bad invocations through the JSON protocol."""

    def error(self, message: str) -> NoReturn:
        raise BoardError(2, message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_bytes(value: Any) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return text.encode("utf-8")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((_norm(path), _norm(root))) == _norm(root)
    except ValueError:
        return False


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id) or task_id in {".", ".."}:
        raise BoardError(2, "task id must be a safe filename component")


def _project_root(project: str) -> str:
    if not isinstance(project, str) or not project.strip():
        raise BoardError(2, "--project must not be empty")
    root = _norm(project)
    if not os.path.isdir(root):
        raise BoardError(2, f"project directory does not exist: {project}")
    return root


def _board_path(project: str, task_id: str) -> str:
    _validate_task_id(task_id)
    root = _project_root(project)
    path = os.path.join(root, BOARD_DIR, task_id + ".json")
    if not _within(path, root):
        raise BoardError(2, "board path escapes the project")
    return path


def _input_path(project: str, value: str) -> str:
    root = _project_root(project)
    path = value if os.path.isabs(value) else os.path.join(root, value)
    path = _norm(path)
    if not _within(path, root):
        raise BoardError(2, "input file must be inside --project")
    return path


def _receipt_path(project: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BoardError(2, "--receipts must not be empty")
    return _norm(value if os.path.isabs(value) else os.path.join(_project_root(project), value))


def _read_json(path: str, description: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise BoardError(2, f"{description} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoardError(2, f"cannot read {description}: {exc}") from exc


def load_board(project: str, task_id: str) -> dict[str, Any]:
    """Load and minimally validate one board from its project and task id."""

    path = _board_path(project, task_id)
    value = _read_json(path, "board")
    if not isinstance(value, dict):
        raise BoardError(4, "board root must be a JSON object")
    if value.get("schema") != SCHEMA or value.get("task_id") != task_id:
        raise BoardError(4, "board schema or task id is invalid")
    if not isinstance(value.get("revision"), int) or value["revision"] < 1:
        raise BoardError(4, "board revision is invalid")
    if not isinstance(value.get("nodes"), list):
        raise BoardError(4, "board nodes must be an array")
    if value.get("status") not in {"planning", "running", "reviewing", "delivered", "failed"}:
        raise BoardError(4, "board status is invalid")
    if not isinstance(value.get("delivery"), dict):
        raise BoardError(4, "board delivery must be an object")
    if not isinstance(value.get("history"), list):
        raise BoardError(4, "board history must be an array")
    if not isinstance(value.get("review_policy"), dict):
        raise BoardError(4, "board review_policy must be an object")
    return value


def write_board(project: str, board: dict[str, Any]) -> bytes:
    """Atomically write a board and return the exact bytes placed on disk."""

    task_id = board.get("task_id")
    if not isinstance(task_id, str):
        raise BoardError(4, "board has no valid task_id")
    path = _board_path(project, task_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    data = _canonical_bytes(board)
    temporary = path + ".tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
        raise BoardError(1, f"cannot write board: {exc}") from exc
    return data


def _node_map(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = board.get("nodes")
    if not isinstance(nodes, list):
        raise BoardError(4, "board nodes must be an array")
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise BoardError(4, "every node must have a non-empty string id")
        if node["id"] in result:
            raise BoardError(4, f"duplicate node id: {node['id']}")
        result[node["id"]] = node
    return result


def _get_node(board: dict[str, Any], node_id: str) -> dict[str, Any]:
    try:
        return _node_map(board)[node_id]
    except KeyError as exc:
        raise BoardError(2, f"unknown node: {node_id}") from exc


def _assignee_resolved(node: dict[str, Any]) -> bool:
    assignee = node.get("assignee")
    return (
        isinstance(assignee, dict)
        and isinstance(assignee.get("lane"), str)
        and bool(assignee["lane"].strip())
        and isinstance(assignee.get("model"), str)
        and bool(assignee["model"].strip())
    )


def _family(node: dict[str, Any]) -> str | None:
    assignee = node.get("assignee")
    if not isinstance(assignee, dict):
        return None
    lane = assignee.get("lane")
    if lane in {"codex", "dsh"}:
        return lane
    if lane == "http":
        provider = assignee.get("provider")
        return f"http:{provider}" if isinstance(provider, str) and provider else None
    return None


def _deps_done(node: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> bool:
    deps = node.get("depends_on", [])
    return isinstance(deps, list) and all(
        dep in nodes and nodes[dep].get("status") == "done" for dep in deps
    )


def _ready_condition(board: dict[str, Any], node: dict[str, Any]) -> bool:
    nodes = _node_map(board)
    if node.get("kind") in {"work", "integrate"}:
        return _deps_done(node, nodes)
    if node.get("kind") == "review":
        producer_id = node.get("reviews")
        producer = nodes.get(producer_id)
        deps = node.get("depends_on", [])
        return bool(
            producer
            and producer_id in deps
            and producer.get("status") == "review"
            and all(
                dep == producer_id or (dep in nodes and nodes[dep].get("status") == "done")
                for dep in deps
            )
        )
    return False


def _promote_ready_nodes(board: dict[str, Any], at: str) -> None:
    """Apply T1/T7 whenever a pending node's prerequisite condition becomes true."""

    changed = True
    while changed:
        changed = False
        for node in board.get("nodes", []):
            if node.get("status") == "pending" and _ready_condition(board, node):
                node["status"] = "ready"
                node["updated_at"] = at
                changed = True


def _recompute_status(board: dict[str, Any]) -> None:
    nodes = board.get("nodes", [])
    if board.get("delivery", {}).get("delivered_at"):
        board["status"] = "delivered"
    elif any(node.get("status") == "failed" for node in nodes):
        board["status"] = "failed"
    elif any(node.get("status") in {"review", "rework"} for node in nodes):
        board["status"] = "reviewing"
    elif any(node.get("status") in {"running", "ready"} for node in nodes):
        board["status"] = "running"
    else:
        board["status"] = "planning"


def _open_blocking_findings(board: dict[str, Any]) -> list[dict[str, Any]]:
    open_findings: list[dict[str, Any]] = []
    for node in board.get("nodes", []):
        review = node.get("review")
        if not isinstance(review, dict):
            continue
        findings = review.get("findings", [])
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if (
                isinstance(finding, dict)
                and finding.get("severity") in BLOCKING_SEVERITIES
                and finding.get("status") not in CLOSED_FINDING_STATUSES
            ):
                open_findings.append(finding)
    return open_findings


def _i7_ready(board: dict[str, Any]) -> bool:
    nodes = board.get("nodes", [])
    if not nodes:
        return False
    reviews = [node for node in nodes if node.get("kind") == "review"]
    producers = [node for node in nodes if node.get("kind") in {"work", "integrate"}]
    if not reviews or not producers:
        return False
    for producer in producers:
        covered = False
        for review in reviews:
            if review.get("reviews") != producer.get("id"):
                continue
            verdict = review.get("review") or {}
            if verdict.get("verdict") == "pass" and verdict.get("attempt") == producer.get("attempt"):
                covered = True
                break
        if not covered:
            return False
    return all(node.get("status") == "done" for node in nodes) and not _open_blocking_findings(board)


def _validate_invariants(board: dict[str, Any]) -> None:
    """Validate structural invariants I1, I2, I3 and I6."""

    nodes = _node_map(board)
    for node in nodes.values():
        if node.get("kind") not in NODE_KINDS:
            raise BoardError(4, f"invalid kind for node {node['id']}")
        if node.get("status") not in NODE_STATUSES:
            raise BoardError(4, f"invalid status for node {node['id']}")
        deps = node.get("depends_on")
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
            raise BoardError(4, f"invalid dependencies for node {node['id']}")
        if len(deps) != len(set(deps)):
            raise BoardError(4, f"duplicate dependency for node {node['id']}")
        if not isinstance(node.get("attempt"), int) or node["attempt"] < 1:
            raise BoardError(4, f"invalid attempt for node {node['id']}")
        assignee = node.get("assignee")
        if not isinstance(assignee, dict) or assignee.get("source") not in {"auto", "manual"}:
            raise BoardError(4, f"invalid assignee for node {node['id']}")
        for dep in deps:
            if dep not in nodes:
                raise BoardError(4, f"unknown dependency {dep} for node {node['id']}")

    # I1: dependencies and review edges both point from a node to its prerequisites.
    edges: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        targets = list(node.get("depends_on", []))
        review_target = node.get("reviews")
        if isinstance(review_target, str) and review_target not in targets:
            targets.append(review_target)
        edges[node_id] = targets
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise BoardError(4, "node graph is cyclic")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in edges[node_id]:
            if target not in nodes:
                raise BoardError(4, f"unknown edge target: {target}")
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)

    review_nodes = [node for node in nodes.values() if node.get("kind") == "review"]
    for reviewer in review_nodes:
        producer_id = reviewer.get("reviews")
        if not isinstance(producer_id, str) or producer_id not in nodes:
            raise BoardError(4, f"review node {reviewer['id']} must name one producer")
        producer = nodes[producer_id]
        if producer.get("kind") not in {"work", "integrate"}:
            raise BoardError(4, f"review node {reviewer['id']} targets a non-producer")
        if producer_id not in reviewer.get("depends_on", []):
            raise BoardError(4, f"review node {reviewer['id']} must depend on its producer")
        if reviewer["id"] in producer.get("depends_on", []):
            raise BoardError(4, f"producer {producer_id} must not depend on its review")
        reviewer_family = _family(reviewer)
        producer_family = _family(producer)
        # Unresolved assignments have no family yet; assign/pin will re-run this check.
        if reviewer_family is not None and producer_family is not None and reviewer_family == producer_family:
            raise BoardError(4, f"review node {reviewer['id']} is not family-independent")

    for node in nodes.values():
        if node.get("kind") not in {"work", "integrate"} or node.get("status") != "done":
            continue
        passed = False
        for reviewer in review_nodes:
            review = reviewer.get("review")
            if (
                reviewer.get("reviews") == node["id"]
                and isinstance(review, dict)
                and review.get("verdict") == "pass"
                and review.get("attempt") == node.get("attempt")
            ):
                passed = True
                break
        if not passed:
            raise BoardError(4, f"producer {node['id']} is done without a passing review")


def _read_receipts(path: str) -> list[dict[str, Any]] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return []
    if not lines:
        return None
    entries: list[dict[str, Any]] = []
    try:
        for line in lines:
            value = json.loads(line)
            if not isinstance(value, dict):
                return []
            entries.append(value)
    except json.JSONDecodeError:
        return []
    return entries


def _chain_status(receipts: str | None, board_bytes: bytes) -> str | None:
    if receipts is None:
        return None
    entries = _read_receipts(receipts)
    if entries is None:
        return "missing"
    if not entries:
        return "broken"
    previous: str | None = None
    for index, entry in enumerate(entries):
        if entry.get("prev_digest") != previous:
            return "broken"
        digest = entry.get("board_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "broken"
        if index == 0 and entry.get("prev_digest") is not None:
            return "broken"
        previous = digest
    current = hashlib.sha256(board_bytes).hexdigest()
    return "ok" if previous == current else "stale"


def _append_receipt(path: str, at: str, op: str, revision: int, board_bytes: bytes) -> None:
    entries = _read_receipts(path)
    previous = None
    if entries:
        last = entries[-1]
        value = last.get("board_digest")
        previous = value if isinstance(value, str) else None
    entry = {
        "at": at,
        "op": op,
        "revision": revision,
        "prev_digest": previous,
        "board_digest": hashlib.sha256(board_bytes).hexdigest(),
    }
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise BoardError(1, f"cannot append receipt: {exc}") from exc


def _board_bytes(project: str, board: dict[str, Any]) -> bytes:
    path = _board_path(project, board["task_id"])
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return _canonical_bytes(board)


def snapshot(
    board: dict[str, Any], receipts: str | None = None, board_bytes: bytes | None = None
) -> dict[str, Any]:
    """Return a detached board snapshot with all derived fields."""

    result = copy.deepcopy(board)
    for node in result.get("nodes", []):
        original = next(item for item in board.get("nodes", []) if item.get("id") == node.get("id"))
        node["ready"] = node.get("status") == "ready" or (
            node.get("status") == "pending" and _ready_condition(board, original)
        )
        node["family"] = _family(original)
    data = _canonical_bytes(board) if board_bytes is None else board_bytes
    raw_chain = _chain_status(receipts, data)
    # The read-only protocol intentionally collapses a structurally broken chain to stale.
    result["chain"] = "stale" if raw_chain == "broken" else raw_chain
    result["can_deliver"] = bool(
        not board.get("delivery", {}).get("delivered_at")
        and _i7_ready(board)
        and raw_chain == "ok"
    )
    nodes = board.get("nodes", [])
    result["progress"] = {
        "done": sum(1 for node in nodes if node.get("status") == "done"),
        "total": len(nodes),
    }
    return result


def _normal_assignee(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise BoardError(2, "node assignee must be an object")
    lane = value.get("lane", "")
    provider = value.get("provider", "")
    model = value.get("model", "")
    source = value.get("source", "auto")
    if not all(isinstance(item, str) for item in (lane, provider, model, source)):
        raise BoardError(2, "assignee lane, provider, model and source must be strings")
    if lane and lane not in LANES:
        raise BoardError(2, f"invalid assignee lane: {lane}")
    if source not in {"auto", "manual"}:
        raise BoardError(2, f"invalid assignee source: {source}")
    pinned_at = value.get("pinned_at")
    pinned_by = value.get("pinned_by")
    if pinned_at is not None and not isinstance(pinned_at, str):
        raise BoardError(2, "assignee pinned_at must be null or a string")
    if pinned_by is not None and not isinstance(pinned_by, str):
        raise BoardError(2, "assignee pinned_by must be null or a string")
    return {
        "lane": lane,
        "provider": provider,
        "model": model,
        "source": source,
        "pinned_at": pinned_at,
        "pinned_by": pinned_by,
    }


def _normal_nodes(value: Any, at: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise BoardError(2, "nodes file must contain a JSON array")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BoardError(2, f"node at index {index} must be an object")
        node_id = item.get("id")
        title = item.get("title")
        kind = item.get("kind")
        if not isinstance(node_id, str) or not node_id:
            raise BoardError(2, f"node at index {index} has no valid id")
        if node_id in seen:
            raise BoardError(4, f"duplicate node id: {node_id}")
        seen.add(node_id)
        if not isinstance(title, str) or not title:
            raise BoardError(2, f"node {node_id} has no valid title")
        if kind not in NODE_KINDS:
            raise BoardError(2, f"node {node_id} has invalid kind")
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) or not dep for dep in deps):
            raise BoardError(2, f"node {node_id} has invalid depends_on")
        reviews = item.get("reviews")
        if reviews is not None and not isinstance(reviews, str):
            raise BoardError(2, f"node {node_id} has invalid reviews target")
        if kind != "review" and reviews is not None:
            raise BoardError(4, f"non-review node {node_id} must not have reviews")
        nodes.append(
            {
                "id": node_id,
                "title": title,
                "kind": kind,
                "depends_on": list(deps),
                "reviews": reviews,
                "attempt": 1,
                "assignee": _normal_assignee(item.get("assignee")),
                "status": "pending",
                "result": None,
                "review": None,
                "updated_at": at,
            }
        )
    return nodes


def _normal_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise BoardError(2, "findings file must contain a JSON array")
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BoardError(2, f"finding at index {index} must be an object")
        finding: dict[str, str] = {}
        for field in ("id", "severity", "where", "status"):
            field_value = item.get(field)
            if not isinstance(field_value, str) or not field_value:
                raise BoardError(2, f"finding at index {index} has invalid {field}")
            finding[field] = field_value
        if finding["id"] in seen:
            raise BoardError(2, f"duplicate finding id: {finding['id']}")
        seen.add(finding["id"])
        findings.append(finding)
    return findings


def _set_assignment(
    node: dict[str, Any], lane: str, provider: str, model: str, source: str, at: str, by: str | None
) -> None:
    if lane not in LANES or not provider or not model:
        raise BoardError(2, "lane, provider and model must be non-empty and lane must be valid")
    node["assignee"] = {
        "lane": lane,
        "provider": provider,
        "model": model,
        "source": source,
        "pinned_at": at if source == "manual" else None,
        "pinned_by": by if source == "manual" else None,
    }
    node["updated_at"] = at


def _finish_mutation(board: dict[str, Any], op: str, detail: str, at: str) -> None:
    _promote_ready_nodes(board, at)
    _recompute_status(board)
    board["revision"] += 1
    board["updated_at"] = at
    board.setdefault("history", []).append(
        {"at": at, "op": op, "revision": board["revision"], "detail": detail}
    )
    _validate_invariants(board)


def apply_op(
    op: str,
    project: str,
    task: str,
    *,
    expected_revision: int | None,
    receipts: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Apply and persist one mutating operation, returning the new board."""

    if op not in MUTATING_OPS:
        raise BoardError(2, f"unknown mutating operation: {op}")
    if expected_revision is None or expected_revision < 0:
        raise BoardError(2, "--expected-revision must be a non-negative integer")
    root = _project_root(project)
    board_path = _board_path(root, task)
    receipt_path = _receipt_path(root, receipts)
    at = _now()

    if op == "init":
        if os.path.exists(board_path):
            existing = load_board(root, task)
            if existing["revision"] != expected_revision:
                raise BoardError(3, "revision mismatch")
            raise BoardError(3, "board already exists")
        if expected_revision != 0:
            raise BoardError(3, "revision mismatch")
        title = options.get("title")
        if not isinstance(title, str) or not title:
            raise BoardError(2, "--title must not be empty")
        board: dict[str, Any] = {
            "schema": SCHEMA,
            "task_id": task,
            "title": title,
            "status": "planning",
            "revision": 1,
            "created_at": at,
            "updated_at": at,
            "review_policy": {"independent_family": True, "block_on": ["high", "medium"]},
            "delivery": {"delivered_at": None, "commit": None},
            "nodes": [],
            "history": [{"at": at, "op": "init", "revision": 1, "detail": title}],
        }
        _validate_invariants(board)
    else:
        board = load_board(root, task)
        if board["revision"] != expected_revision:
            raise BoardError(3, "revision mismatch")
        _validate_invariants(board)
        if board.get("delivery", {}).get("delivered_at"):
            raise BoardError(3, "delivered boards are immutable")

        if op == "plan":
            if board.get("status") != "planning":
                raise BoardError(3, "plan is allowed only while the board is planning")
            path = _input_path(root, options.get("nodes_file", ""))
            planned = _normal_nodes(_read_json(path, "nodes file"), at)
            if not planned:
                raise BoardError(4, "a plan needs at least one node")
            if not any(node.get("kind") == "review" for node in planned):
                raise BoardError(4, "a plan needs at least one independent review node")
            board["nodes"] = planned
            _finish_mutation(board, op, f"nodes={len(board['nodes'])}", at)

        elif op in {"assign", "pin"}:
            node = _get_node(board, options.get("node", ""))
            if op == "assign" and node.get("assignee", {}).get("source") == "manual":
                raise BoardError(4, "manual assignment can only be changed by pin")
            by = options.get("by") if op == "pin" else None
            if op == "pin" and (not isinstance(by, str) or not by):
                raise BoardError(2, "--by must not be empty")
            _set_assignment(
                node,
                options.get("lane", ""),
                options.get("provider", ""),
                options.get("model", ""),
                "manual" if op == "pin" else "auto",
                at,
                by,
            )
            note = options.get("note")
            suffix = f" note={note}" if op == "pin" and note else ""
            _finish_mutation(board, op, f"node={node['id']}{suffix}", at)

        elif op == "start":
            node = _get_node(board, options.get("node", ""))
            kind = node.get("kind")
            status = node.get("status")
            if status == "pending" and _ready_condition(board, node):
                node["status"] = "ready"  # T1 or T7 immediately followed by T2 or T8.
                status = "ready"
            if kind in {"work", "integrate"}:
                if status == "ready":
                    if not _assignee_resolved(node):
                        raise BoardError(3, "assignee is unresolved")
                    node["status"] = "running"
                elif status in {"rework", "failed"}:
                    if not _deps_done(node, _node_map(board)) or not _assignee_resolved(node):
                        raise BoardError(3, "restart prerequisites are not satisfied")
                    node["attempt"] += 1
                    node["result"] = None
                    node["status"] = "running"
                else:
                    raise BoardError(3, f"invalid start transition for {kind}: {status}")
            elif kind == "review" and status == "ready":
                if not _assignee_resolved(node):
                    raise BoardError(3, "assignee is unresolved")
                producer = _node_map(board)[node["reviews"]]
                node["review"] = {
                    "verdict": None,
                    "attempt": producer["attempt"],
                    "findings": [],
                    "by": None,
                    "at": None,
                }
                node["status"] = "running"
            else:
                raise BoardError(3, f"invalid start transition for {kind}: {status}")
            node["updated_at"] = at
            _finish_mutation(board, op, f"node={node['id']} attempt={node['attempt']}", at)

        elif op == "result":
            node = _get_node(board, options.get("node", ""))
            if node.get("kind") not in {"work", "integrate"} or node.get("status") != "running":
                raise BoardError(3, "result requires a running work or integrate node")
            summary = options.get("summary")
            if not isinstance(summary, str) or not summary:
                raise BoardError(2, "--summary must not be empty")
            artifact = options.get("artifact")
            commit = options.get("commit")
            child_session = options.get("child_session")
            child_model = options.get("child_model")
            for field_name, field_value in (
                ("artifact", artifact),
                ("commit", commit),
                ("child_session", child_session),
                ("child_model", child_model),
            ):
                if field_value is not None and (
                    not isinstance(field_value, str) or not field_value
                ):
                    raise BoardError(4, f"result {field_name} must be a non-empty string")
            child_pair = bool(child_session and child_model)
            artifact_pair = bool(artifact and commit)
            if bool(child_session) != bool(child_model) or bool(artifact) != bool(commit):
                raise BoardError(4, "result receipt fields must be supplied in complete pairs")
            if child_pair:
                if child_model != node.get("assignee", {}).get("model"):
                    raise BoardError(4, "child model does not match the assigned model")
                verified = "child"
            elif options.get("no_subagent") and artifact_pair:
                verified = "artifact"
            else:
                raise BoardError(4, "result has no acceptable child or artifact receipt")
            node["result"] = {
                "summary": summary,
                "artifact": artifact,
                "commit": commit,
                "child_session": child_session,
                "child_model": child_model,
                "verified": verified,
            }
            node["status"] = "review"
            node["updated_at"] = at
            _finish_mutation(board, op, f"node={node['id']} verified={verified}", at)

        elif op == "review":
            node = _get_node(board, options.get("node", ""))
            if node.get("kind") != "review" or node.get("status") != "running":
                raise BoardError(3, "review verdict requires a running review node")
            verdict = options.get("verdict")
            if verdict not in {"pass", "fail"}:
                raise BoardError(2, "--verdict must be pass or fail")
            producer = _node_map(board)[node["reviews"]]
            active_review = node.get("review")
            if (
                producer.get("status") != "review"
                or not isinstance(active_review, dict)
                or active_review.get("attempt") != producer.get("attempt")
            ):
                raise BoardError(3, "review attempt does not match the producer")
            findings: list[dict[str, str]] = []
            findings_file = options.get("findings_file")
            if findings_file:
                path = _input_path(root, findings_file)
                findings = _normal_findings(_read_json(path, "findings file"))
            by = options.get("by") or node.get("assignee", {}).get("model") or _family(node)
            completed_review = {
                "verdict": verdict,
                "attempt": producer["attempt"],
                "findings": findings,
                "by": by,
                "at": at,
            }
            if verdict == "pass":
                node["review"] = completed_review
                node["status"] = "done"
                producer["status"] = "done"
            else:
                # T10 retains the failed verdict/findings but clears the active attempt marker.
                completed_review["attempt"] = None
                node["review"] = completed_review
                node["status"] = "pending"
                producer["status"] = "rework"
                producer["result"] = None
            node["updated_at"] = at
            producer["updated_at"] = at
            _finish_mutation(
                board,
                op,
                f"node={node['id']} producer={producer['id']} verdict={verdict} findings={len(findings)}",
                at,
            )

        elif op == "deliver":
            if receipt_path is None:
                raise BoardError(4, "delivery requires receipts", {"chain": "missing"})
            current_bytes = _board_bytes(root, board)
            chain = _chain_status(receipt_path, current_bytes)
            if chain != "ok":
                chain_error = "missing" if chain == "missing" else ("broken" if chain == "broken" else "stale")
                raise BoardError(4, f"receipt chain is {chain_error}", {"chain": chain_error})
            if not _i7_ready(board) or any(
                node.get("status") in {"review", "rework", "failed"} for node in board.get("nodes", [])
            ):
                raise BoardError(4, "delivery gate is not satisfied")
            commit = options.get("commit")
            if not isinstance(commit, str) or not commit:
                raise BoardError(2, "--commit must not be empty")
            board["delivery"] = {"delivered_at": at, "commit": commit}
            _finish_mutation(board, op, f"commit={commit}", at)

        else:  # pragma: no cover - guarded by MUTATING_OPS
            raise BoardError(2, f"unsupported operation: {op}")

    data = write_board(root, board)
    if receipt_path is not None:
        _append_receipt(receipt_path, at, op, board["revision"], data)
    return board


def _add_common(
    parser: argparse.ArgumentParser, *, mutating: bool, receipts_required: bool = False
) -> None:
    parser.add_argument("--project", default=".")
    parser.add_argument("--task", required=True)
    if mutating:
        parser.add_argument("--expected-revision", required=True, type=int)
        parser.add_argument("--receipts", required=receipts_required)
    parser.add_argument("--json", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="orchestration.py", add_help=True)
    subparsers = parser.add_subparsers(dest="op", required=True, parser_class=JsonArgumentParser)

    init = subparsers.add_parser("init")
    _add_common(init, mutating=True)
    init.add_argument("--title", required=True)

    plan = subparsers.add_parser("plan")
    _add_common(plan, mutating=True)
    plan.add_argument("--nodes-file", required=True)

    for name in ("assign", "pin"):
        command = subparsers.add_parser(name)
        _add_common(command, mutating=True)
        command.add_argument("--node", required=True)
        command.add_argument("--lane", required=True, choices=sorted(LANES))
        command.add_argument("--provider", required=True)
        command.add_argument("--model", required=True)
        if name == "pin":
            command.add_argument("--by", required=True)
            command.add_argument("--note")

    start = subparsers.add_parser("start")
    _add_common(start, mutating=True)
    start.add_argument("--node", required=True)

    result = subparsers.add_parser("result")
    _add_common(result, mutating=True)
    result.add_argument("--node", required=True)
    result.add_argument("--summary", required=True)
    result.add_argument("--artifact")
    result.add_argument("--commit")
    result.add_argument("--child-session")
    result.add_argument("--child-model")
    result.add_argument("--no-subagent", action="store_true")

    review = subparsers.add_parser("review")
    _add_common(review, mutating=True)
    review.add_argument("--node", required=True)
    review.add_argument("--verdict", required=True, choices=("pass", "fail"))
    review.add_argument("--findings-file")
    review.add_argument("--by")

    deliver = subparsers.add_parser("deliver")
    _add_common(deliver, mutating=True, receipts_required=True)
    deliver.add_argument("--commit", required=True)

    state = subparsers.add_parser("state")
    _add_common(state, mutating=False)
    state.add_argument("--receipts")
    return parser


def _emit(payload: dict[str, Any], *, stderr: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    stream = sys.stderr if stderr else sys.stdout
    stream.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = _parser().parse_args(argv)
        values = vars(args)
        op = values.pop("op")
        project = values.pop("project")
        task = values.pop("task")
        values.pop("json", None)
        if op == "state":
            receipt_value = values.pop("receipts", None)
            root = _project_root(project)
            board = load_board(root, task)
            _validate_invariants(board)
            receipt_path = _receipt_path(root, receipt_value)
            task_snapshot = snapshot(board, receipt_path, _board_bytes(root, board))
        else:
            expected = values.pop("expected_revision")
            receipt_value = values.pop("receipts", None)
            board = apply_op(
                op,
                project,
                task,
                expected_revision=expected,
                receipts=receipt_value,
                **values,
            )
            root = _project_root(project)
            receipt_path = _receipt_path(root, receipt_value)
            task_snapshot = snapshot(board, receipt_path, _board_bytes(root, board))
        _emit({"ok": True, "code": 0, "message": "ok", "task": task_snapshot})
        return 0
    except BoardError as exc:
        payload = {"ok": False, "code": exc.code, "message": exc.message, "error": exc.error}
        _emit(payload)
        _emit(payload, stderr=True)
        return exc.code
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        payload = {"ok": False, "code": 1, "message": str(exc), "error": str(exc)}
        _emit(payload)
        _emit(payload, stderr=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
