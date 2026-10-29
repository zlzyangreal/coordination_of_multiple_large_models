#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared helpers for the v2.2 gates.

Pure functions, no side effects on import. Every gate reads its evidence from files written by
the call path (call records, envelopes, result JSON, facts pack, pin); this module only
normalises those files so the gates agree on what "the same request", "the same family" and
"the same facts" mean.

Exit codes are owned by the individual scripts, not here.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SKILL = os.path.join(ROOT, "SKILL.md")
SCHEMA = os.path.join(ROOT, "assets", "result.schema.json")
CALLS = os.path.join("_work", "calls")
PENDING = os.path.join(CALLS, "pending")
PLAN_DIR = os.path.join("_work", "plan")
GATES = os.path.join("_work", "gates")
EVENTS = os.path.join(GATES, "events.jsonl")
FACTS = os.path.join("_work", "facts_pack.md")
REVIEW_ROLES = ("review", "plan-review")
CALL_ROLES = ("plan", "produce", "review", "plan-review", "e2e")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).astimezone() \
        .isoformat(timespec="seconds")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def read_text(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def parse_table(path):
    """Parse the first Markdown table with a header row.

    Returns a list of dicts keyed by the header cells; separator rows are skipped. Rows
    shorter than the header are padded with empty strings. No third-party YAML needed.
    """
    rows, header = [], None
    try:
        text = read_text(path)
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            if cells and cells[0].lower() in ("id", "request_id", "version", "at", "budget"):
                header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells[:len(header)])))
    return rows


def ledger_rows(path):
    """Rows of a review ledger keyed by id/lower-case headers."""
    return [row for row in parse_table(path) if row.get("id")]


def closed_finding_ids(path):
    """Finding ids with a closed disposition (anything except open/empty)."""
    closed = set()
    for row in ledger_rows(path):
        status = (row.get("status") or "").strip().lower()
        if status and status != "open":
            closed.add(row["id"].strip())
    return closed


def norm_path(path):
    return os.path.normcase(os.path.abspath(path)) if path else None


def same_path(a, b):
    return bool(a and b and norm_path(a) == norm_path(b))


# --------------------------------------------------------------------------- workspace identity
def workspace_identity(root="."):
    root = os.path.abspath(root)
    drive, _ = os.path.splitdrive(root)
    try:
        volume = os.stat(root).st_dev
    except OSError:
        volume = None
    git_head = None
    try:
        done = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=8)
        if done.returncode == 0:
            git_head = done.stdout.strip() or None
    except Exception:  # noqa: BLE001
        pass
    return {
        "root": root,
        "drive": drive or os.sep,
        "volume": volume,
        "git_head": git_head,
        "host": socket.gethostname(),
    }


def load_pin(root="."):
    return read_json(os.path.join(root, "_work", "skill_pin.json"))


def identity_mismatches(pin, root="."):
    """Empty list = match; a list of human-readable mismatches otherwise.

    A pin without a workspace block returns ["legacy pin"]. The caller decides whether that
    is a hard failure (startup) or a warning (an old session being inspected).
    """
    if not isinstance(pin, dict):
        return ["no pin"]
    pinned = pin.get("workspace")
    if not isinstance(pinned, dict) or not pinned.get("root"):
        return ["legacy pin without workspace identity"]
    current = workspace_identity(root)
    problems = []
    for key in ("root", "drive", "volume"):
        want, got = pinned.get(key), current.get(key)
        if key == "root":
            if norm_path(want) != norm_path(got):
                problems.append(f"root {got!r} != pinned {want!r}")
        elif str(want) != str(got):
            problems.append(f"{key} {got!r} != pinned {want!r}")
    return problems


# --------------------------------------------------------------------------- call evidence
def _normalise(record):
    """One shape for runner records, HTTP JSONL rows and pending markers."""
    if not isinstance(record, dict):
        return None
    request_id = record.get("request_id") or record.get("requestId")
    if not request_id:
        return None
    return {
        "request_id": str(request_id),
        "status": record.get("status"),
        "lane": record.get("lane"),
        "model": record.get("model"),
        "role": record.get("role"),
        "role_source": record.get("role_source") or record.get("roleSource"),
        "milestone": record.get("milestone"),
        "work_package": record.get("work_package") or record.get("workPackage"),
        "produce_artifact": record.get("produce_artifact") or record.get("produceArtifact"),
        "produce_artifact_hash": record.get("produce_artifact_hash")
        or record.get("produceArtifactHash"),
        "routing_tier": record.get("routing_tier") or record.get("routingTier"),
        "tier_source": record.get("tier_source") or record.get("tierSource"),
        "protocol_version": record.get("protocol_version") or record.get("protocolVersion"),
        "envelope_path": record.get("envelope_path") or record.get("envelopePath"),
        "envelope_sha256": record.get("envelope_sha256") or record.get("envelopeSha256"),
        "artifact": record.get("artifact"),
        "artifact_sha256": record.get("artifact_sha256") or record.get("artifactSha256"),
        "result_path": record.get("result_path") or record.get("resultPath"),
        "result_sha256": record.get("result_sha256") or record.get("resultSha256"),
        "schema_valid": record.get("schema_valid") if "schema_valid" in record
        else record.get("schemaValid"),
        "ephemeral": bool(record.get("ephemeral")),
        "started_at": record.get("started_at") or record.get("ts"),
        "finished_at": record.get("finished_at"),
        "duration_s": record.get("duration_s") or record.get("latencySeconds"),
        "contract": record.get("contract") or {},
        "workspace": record.get("workspace"),
        "source": record.get("_source"),
        "raw": record,
    }


def runner_records(calls_dir=CALLS):
    """Completed runner records, excluding the strict codex schema copies."""
    out = []
    if not os.path.isdir(calls_dir):
        return out
    for name in sorted(os.listdir(calls_dir)):
        if not name.endswith(".json") or name.endswith(".codex-schema.json"):
            continue
        record = read_json(os.path.join(calls_dir, name))
        if isinstance(record, dict):
            record["_source"] = "runner"
            normalised = _normalise(record)
            if normalised:
                out.append(normalised)
    return out


def pending_records(calls_dir=CALLS):
    out = []
    pending_dir = os.path.join(calls_dir, "pending")
    if not os.path.isdir(pending_dir):
        return out
    for name in sorted(os.listdir(pending_dir)):
        if not name.endswith(".json"):
            continue
        record = read_json(os.path.join(pending_dir, name))
        if isinstance(record, dict):
            record.setdefault("status", "pending")
            record["_source"] = "pending"
            normalised = _normalise(record)
            if normalised:
                out.append(normalised)
    return out


def http_records(project="."):
    """Rows written by models.py for HTTP calls (and its codex bookkeeping)."""
    path = os.path.join(project, "_work", "models", "model_calls.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    record["_source"] = "http"
                    normalised = _normalise(record)
                    if normalised:
                        out.append(normalised)
    except OSError:
        pass
    return out


def all_attempts(project="."):
    """Every attempt, deduped by request id; runner evidence wins over bookkeeping rows."""
    merged = {}
    for record in runner_records(os.path.join(project, CALLS)):
        merged[record["request_id"]] = record

    def binding_score(record):
        return sum(bool(record.get(key)) for key in
                   ("role", "milestone", "envelope_sha256", "result_sha256",
                    "artifact_sha256", "produce_artifact_hash", "protocol_version"))

    for record in http_records(project):
        current = merged.get(record["request_id"])
        if current is None or (current.get("source") != "runner"
                               and binding_score(record) > binding_score(current)):
            merged[record["request_id"]] = record
    for record in pending_records(os.path.join(project, CALLS)):
        current = merged.get(record["request_id"])
        if current is None or (current.get("source") == "pending"
                               and binding_score(record) > binding_score(current)):
            merged[record["request_id"]] = record
    return list(merged.values())


def find_attempt(project, request_id):
    for record in all_attempts(project):
        if record["request_id"] == request_id:
            return record
    return None


# --------------------------------------------------------------------------- families
def catalog(project="."):
    sys.path.insert(0, HERE)
    try:
        import model_config as cfg_mod  # noqa: PLC0415
        import model_route  # noqa: PLC0415
        cfg, _meta = cfg_mod.load_config(project)
        state = cfg_mod.load_state(project)
        return model_route.catalog(cfg, state, project)
    except Exception:  # noqa: BLE001
        return []


def family_of(model_id, project=".", entries=None):
    """Resolve (id, family, known, source) for a lane id, provider/model id or bare slug."""
    if not model_id:
        return {"id": None, "family": None, "known": False, "source": "missing"}
    entries = entries if entries is not None else catalog(project)
    by_id = {entry.get("id"): entry for entry in entries}
    candidates = [model_id]
    if "/" not in model_id and ":" not in model_id:
        candidates.append(f"codex:{model_id}")
    for candidate in candidates:
        entry = by_id.get(candidate)
        if entry:
            return {"id": entry.get("id"), "family": entry.get("family"),
                    "known": entry.get("familyKnown") is not False,
                    "source": entry.get("familySource") or "catalog"}
    sys.path.insert(0, HERE)
    try:
        import model_registry as registry  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        registry = None
    if model_id.startswith("codex:") and registry:
        return {"id": model_id, "family": registry.family("codex"), "known": True,
                "source": "lane"}
    if model_id.startswith("dsh:") and registry:
        return {"id": model_id, "family": registry.family("dsh"), "known": True,
                "source": "lane"}
    if "/" not in model_id and ":" not in model_id and registry:
        # A bare slug in a runner record is the codex lane (run_gpt is its only writer).
        return {"id": model_id, "family": registry.family("codex"), "known": True,
                "source": "lane"}
    return {"id": model_id, "family": None, "known": False, "source": "unknown"}


def independent(producer, reviewer):
    """(ok, message, producer_info, reviewer_info). Unknown family is never independent."""
    if not producer.get("id") or not reviewer.get("id"):
        return False, "producer or reviewer model id is missing", producer, reviewer
    if not producer.get("known"):
        return (False, f"producer family is unknown for {producer['id']}; declare "
                       f"overrides.<model>.family in the project config", producer, reviewer)
    if not reviewer.get("known"):
        return (False, f"reviewer family is unknown for {reviewer['id']}; declare "
                       f"overrides.<model>.family in the project config", producer, reviewer)
    if producer.get("family") == reviewer.get("family"):
        return (False, f"reviewer {reviewer['id']} is the same family "
                       f"({reviewer['family']}) as the producer {producer['id']}",
                producer, reviewer)
    return True, "independent", producer, reviewer


# --------------------------------------------------------------------------- versions / facts
def version_tuple(value):
    parts = []
    for piece in str(value or "").split("."):
        match = re.match(r"(\d+)", piece)
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def version_lt(value, target):
    return version_tuple(value) < version_tuple(target)


FACTS_RE = re.compile(r"^\s*(facts_version|facts_hash|frozen_at)\s*:\s*(.*?)\s*$")


def canonical_facts_body(text):
    """The exact bytes the facts hash covers: LF newlines, facts_hash line removed."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalised.split("\n")
    kept = [line for line in lines if not re.match(r"^\s*facts_hash\s*:", line)]
    return "\n".join(kept).rstrip("\n") + "\n"


def facts_info(path):
    info = {"path": os.path.abspath(path), "exists": os.path.isfile(path),
            "version": None, "declared_hash": None, "actual_hash": None, "frozen_at": None,
            "header_count": 0, "header_counts": {"facts_version": 0, "facts_hash": 0,
                                                 "frozen_at": 0},
            "stale": None, "budgets": {}}
    if not os.path.isfile(path):
        return info
    text = read_text(path)
    headers = []
    for line in text.splitlines():
        match = FACTS_RE.match(line)
        if match:
            headers.append((match.group(1), match.group(2).strip().strip('"')))
    for key, _value in headers:
        info["header_counts"][key] = info["header_counts"].get(key, 0) + 1
    info["header_count"] = info["header_counts"]["facts_hash"]
    for key, value in headers:
        if key == "facts_version":
            info["version"] = value
        elif key == "frozen_at":
            info["frozen_at"] = value
        elif key == "facts_hash" and info["declared_hash"] is None:
            info["declared_hash"] = value
    info["actual_hash"] = sha256_text(canonical_facts_body(text))
    info["stale"] = bool(info["declared_hash"] and
                         info["declared_hash"].lower() != info["actual_hash"])
    info["budgets"] = parse_budgets(text)
    return info


def parse_budgets(text):
    budgets = {"review_calls": 2, "review_minutes": 30, "raw": {}}
    section = []
    inside = False
    for line in text.splitlines():
        if re.match(r"^##\s+budgets\b", line, re.I):
            inside = True
            continue
        if inside and re.match(r"^##\s+", line):
            break
        if inside:
            section.append(line)
    header = None
    for line in section:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            if cells and cells[0].lower() == "budget":
                header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        row = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        key, value = row.get("budget", ""), row.get("value", "")
        if not key:
            continue
        budgets["raw"][key] = value
        match = re.search(r"\d+", value)
        if match:
            if key == "review_calls":
                budgets["review_calls"] = int(match.group(0))
            elif key == "review_minutes":
                budgets["review_minutes"] = int(match.group(0))
    return budgets


# --------------------------------------------------------------------------- events
def append_event(project, kind, **fields):
    path = os.path.join(project, EVENTS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {"at": now_iso(), "kind": kind}
    event.update({key: value for key, value in fields.items() if value is not None})
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


def read_events(project="."):
    path = os.path.join(project, EVENTS)
    events = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events
