#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Human-governed decision authority for multi-model orchestration.

The module is intentionally stdlib-only and keeps its durable state below
``_work/governance``.  Integrations use ``guard``, ``governance_block`` and
``work_package``; the command line exposes the same primitives for operators.

Exit codes:
    0  allowed / successful
    2  bad input
    3  corrupt or inconsistent governance state
    4  authority denied the operation
"""
import argparse
import contextlib
import fnmatch
import hashlib
import hmac
import json
import os
import re
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402


GOV = os.path.join("_work", "governance")
ENABLED = os.path.join(GOV, "ENABLED")
MANDATE = os.path.join(GOV, "mandate.json")
CONTROL = os.path.join(GOV, "control.json")
DECISIONS = os.path.join(GOV, "decisions.jsonl")
GRANTS = os.path.join(GOV, "grants.jsonl")
USAGE = os.path.join(GOV, "usage.jsonl")
AGENTS = os.path.join(GOV, "agents.json")
REQUESTS = os.path.join(GOV, "requests")
REQUESTS_MD = os.path.join(GOV, "requests.md")
CHECKPOINT = os.path.join(GOV, "checkpoint.json")
APPROVALS = os.path.join(GOV, "approvals")
HALT = os.path.join(GOV, "HALT")
LOCK = os.path.join(GOV, ".usage.lock")
PIN = os.path.join("_work", "skill_pin.json")

SCHEMA = 1
RULES_VERSION = "2.4.0"
LEVEL_RANK = {"L3": 1, "L2": 2, "L1": 3}

BASE = {
    "target": "L1", "constraint": "L1", "scope": "L1",
    "permission": "L1", "release": "L1", "promote": "L1",
    "push": "L1", "acceptance": "L1", "budget-overrun": "L1",
    "model-add": "L1", "plugin-add": "L1", "unknown": "L1",
    "direction": "L2", "architecture-change": "L2",
    "stack-change": "L2", "interface-change": "L2",
    "budget-change": "L2", "goal-conflict": "L2",
    "ambiguity": "L2", "deviation": "L2", "plan-change": "L2",
    "research": "L3", "candidate": "L3", "plan": "L3",
    "task-split": "L3", "architecture-initial": "L3",
    "stack-initial": "L3", "interface-initial": "L3",
    "implementation": "L3", "test": "L3", "fix": "L3",
    "optimization": "L3", "model-selection": "L3",
    "scheduling": "L3", "produce": "L3", "review": "L3",
    "plugin-use": "L3", "model-use": "L3", "dependency-add": "L3",
}

L1_OPS = {
    "release", "promote", "push", "target-change", "constraint-change",
    "scope-change", "permission", "plugin-add", "model-add",
}
L2_OPS = {
    "direction", "architecture-change", "stack-change", "interface-change",
    "budget-change", "goal-conflict", "ambiguity", "deviation", "plan-change",
}
PATH_OPS = {"produce", "write"}
WRITABLE_OPS = {"produce", "write", "release", "promote", "push"}
KNOWN_OPS = set(BASE) | L1_OPS | L2_OPS | {
    "write", "subagent", "target-change", "constraint-change", "scope-change",
}


class GovernanceError(Exception):
    """A user, state, or authorization error with a stable process code."""

    def __init__(self, code, message=None):
        if message is None:
            message = str(code)
            code = 3
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)


def _path(project, relative):
    return os.path.join(os.path.abspath(project), relative)


def atomic_json(path, data):
    """Atomically replace a JSON file with an indented UTF-8 document."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
    os.replace(temporary, path)
    return path


def append_jsonl(path, record):
    """Append one compact, canonical JSON object."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n")
        fh.flush()
    return path


def read_jsonl(path):
    """Read a JSONL file and fail closed on any malformed row."""
    records = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError as exc:
                    raise GovernanceError(3, "%s:%d is invalid JSON: %s" %
                                          (path, number, exc))
                if not isinstance(value, dict):
                    raise GovernanceError(3, "%s:%d is not an object" % (path, number))
                records.append(value)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise GovernanceError(3, "cannot read %s: %s" % (path, exc))
    return records


def canonical(obj):
    """Canonical UTF-8 bytes used by hashes and HMAC-independent records."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _hash_body(record):
    body = dict(record)
    body.pop("hash", None)
    return hashlib.sha256(canonical(body)).hexdigest()


def chain_append(path, record):
    """Append a record to a seq/prev/hash JSONL chain."""
    records = read_jsonl(path)
    last = records[-1] if records else {}
    body = dict(record)
    body["seq"] = int(last.get("seq", 0)) + 1
    body["prev"] = last.get("hash", "")
    body.pop("hash", None)
    body["hash"] = _hash_body(body)
    append_jsonl(path, body)
    return body


def chain_check(path):
    """Return human-readable problems for an append-only chain."""
    problems = []
    try:
        records = read_jsonl(path)
    except GovernanceError as exc:
        return [exc.message]
    previous = ""
    for expected, record in enumerate(records, 1):
        if record.get("seq") != expected:
            problems.append("%s: seq %r, expected %d" %
                            (path, record.get("seq"), expected))
        if record.get("prev", "") != previous:
            problems.append("%s: seq %d prev mismatch" % (path, expected))
        if record.get("hash") != _hash_body(record):
            problems.append("%s: seq %d hash mismatch" % (path, expected))
        previous = record.get("hash", "")
    return problems


@contextlib.contextmanager
def lock(project):
    """Acquire the reservation lock, failing closed after five seconds."""
    path = _path(project, LOCK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor = None
    for _attempt in range(100):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            descriptor = None
            break
        except FileExistsError:
            time.sleep(0.05)
        except OSError as exc:
            raise GovernanceError(3, "cannot acquire usage lock: %s" % exc)
    else:
        raise GovernanceError(3, "timed out acquiring usage lock")
    try:
        yield
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GovernanceError(3, "cannot release usage lock: %s" % exc)


def normalize_topic(text):
    """Normalize an explicit topic without attempting semantic matching."""
    value = re.sub(r"\s+", " ", str(text or "").strip().lower())
    value = value.rstrip("\u3002\uff01\uff1f!?.,;: ")
    if not value:
        raise GovernanceError(2, "topic or subject must not be empty")
    return value


def mandate_hash(data):
    body = dict(data)
    body.pop("mandate_hash", None)
    return hashlib.sha256(canonical(body)).hexdigest()


def key_for(kind, subject, mandate_version):
    return "%s:%s:%s" % (mandate_version, _kind(kind), normalize_topic(subject))


def direction_key(kind, subject):
    return "%s:%s" % (_kind(kind), normalize_topic(subject))


def _kind(value):
    return str(value or "unknown").strip().lower().replace("_", "-") or "unknown"


def _split_values(values):
    """Accept both repeated flags and comma-separated values (ops/paths/models)."""
    result = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result


def _new_id(prefix):
    return "%s-%d-%d" % (prefix, int(time.time() * 1000), time.time_ns() % 1000000)


def _read_json_strict(path, label):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            value = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GovernanceError(3, "%s is unreadable: %s" % (label, exc))
    if not isinstance(value, dict):
        raise GovernanceError(3, "%s must be a JSON object" % label)
    return value


def pin_governance(project, enabled, hash_value):
    """Write the second governance marker in the project pin."""
    path = _path(project, PIN)
    pin = {}
    if os.path.isfile(path):
        try:
            pin = _read_json_strict(path, "skill pin")
        except GovernanceError as exc:
            raise GovernanceError(3, exc.message)
    pin["governance"] = {
        "enabled": bool(enabled),
        "mandate_hash": hash_value,
        "updated_at": common.now_iso(),
    }
    atomic_json(path, pin)
    return True


def _governance_files(project):
    root = _path(project, GOV)
    found = []
    if not os.path.isdir(root):
        return found
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name == os.path.basename(LOCK):
                continue
            found.append(os.path.join(current, name))
    return found


def load_governance(project):
    """Return ``(mandate, control, problem)``; ``None`` values mean disabled."""
    project = os.path.abspath(project)
    enabled_path = _path(project, ENABLED)
    mandate_path = _path(project, MANDATE)
    control_path = _path(project, CONTROL)
    pin_path = _path(project, PIN)
    enabled_file = os.path.isfile(enabled_path)
    pin = None
    pin_marker = None
    if os.path.isfile(pin_path):
        try:
            pin = _read_json_strict(pin_path, "skill pin")
        except GovernanceError as exc:
            return None, None, exc.message
        if "governance" in pin:
            if not isinstance(pin.get("governance"), dict):
                return None, None, "skill pin governance marker is invalid"
            pin_marker = pin.get("governance")

    files = _governance_files(project)
    if not enabled_file:
        if pin_marker and pin_marker.get("enabled"):
            return None, None, "governance enabled/deleted but the sentinel is missing"
        if files:
            return None, None, "governance enabled/deleted but the sentinel is missing"
        return None, None, None

    try:
        sentinel = _read_json_strict(enabled_path, "governance sentinel")
    except GovernanceError as exc:
        return None, None, exc.message
    if not sentinel.get("schema") or not sentinel.get("rules_version"):
        return None, None, "governance sentinel is incomplete"
    if not os.path.isfile(mandate_path) or not os.path.isfile(control_path):
        missing = []
        if not os.path.isfile(mandate_path):
            missing.append("mandate.json")
        if not os.path.isfile(control_path):
            missing.append("control.json")
        return None, None, "enabled but missing %s" % ", ".join(missing)
    for relative in (DECISIONS, GRANTS, USAGE, AGENTS):
        if not os.path.isfile(_path(project, relative)):
            return None, None, "enabled but missing %s" % os.path.basename(relative)
    try:
        mandate = _read_json_strict(mandate_path, "mandate")
        control = _read_json_strict(control_path, "control")
    except GovernanceError as exc:
        return None, None, exc.message
    if not str(mandate.get("goal") or "").strip():
        return None, None, "mandate goal is missing"
    try:
        version = int(mandate.get("version"))
    except (TypeError, ValueError):
        return None, None, "mandate version is invalid"
    if version < 1:
        return None, None, "mandate version is invalid"
    for key in ("constraints", "scope", "authority"):
        if not isinstance(mandate.get(key), dict):
            return None, None, "mandate %s is invalid" % key
    actual = mandate_hash(mandate)
    if mandate.get("mandate_hash") != actual:
        return None, None, "mandate hash mismatch"
    if control.get("mandate_hash") != actual:
        return None, None, "control and mandate hashes disagree"
    if not isinstance(control.get("enabled"), bool):
        return None, None, "control enabled flag is invalid"
    if not isinstance(control.get("paused"), bool):
        return None, None, "control paused flag is invalid"
    if control.get("enabled") is False and control.get("mode") != "suspended":
        return None, None, "disabled control must use suspended mode"
    if control.get("enabled") is not False and control.get("mode") not in ("enforce", "suspended"):
        return None, None, "control mode is invalid"
    if pin_marker is None or pin_marker.get("enabled") is not True:
        return None, None, "enabled but the skill pin has no enabled governance marker"
    pinned_hash = pin_marker.get("mandate_hash")
    if pinned_hash != actual:
        return None, None, "pin and mandate hashes disagree"
    for relative in (DECISIONS, GRANTS):
        problems = chain_check(_path(project, relative))
        if problems:
            return None, None, problems[0]
    try:
        read_jsonl(_path(project, USAGE))
    except GovernanceError as exc:
        return None, None, exc.message
    try:
        agents = _read_json_strict(_path(project, AGENTS), "agents")
    except GovernanceError as exc:
        return None, None, exc.message
    if not isinstance(agents.get("agents"), dict) or not isinstance(agents.get("plugins"), dict):
        return None, None, "agents.json is malformed"
    return mandate, control, None


def _require_state(project):
    mandate, control, problem = load_governance(project)
    if problem:
        raise GovernanceError(3, problem)
    if mandate is None:
        raise GovernanceError(4, "governance is disabled")
    return mandate, control


def classify(kind, strictness="normal", effect="none"):
    """Classify a declared decision kind into L1, L2, or L3."""
    normalized = _kind(kind)
    strictness = str(strictness or "normal").lower()
    effect = str(effect or "none").lower()
    if strictness not in ("low", "normal", "high"):
        raise GovernanceError(2, "strictness must be low, normal, or high")
    if effect not in ("network", "permission", "cost", "license", "none"):
        raise GovernanceError(2, "invalid dependency effect")
    level = BASE.get(normalized, "L1")
    reason = "base classifier"
    if normalized not in BASE:
        reason = "unclassified kind: conservative"
    elif normalized == "dependency-add" and effect != "none":
        level = "L2"
        reason = "dependency effect %s requires negotiation" % effect
    elif strictness == "high" and normalized in ("architecture-initial", "dependency-add"):
        level = "L2"
        reason = "high strictness raises %s" % normalized
    return {
        "kind": normalized,
        "level": level,
        "strictness": strictness,
        "effect": effect,
        "reason": reason,
    }


def fold_decisions(records):
    """Fold propose/decide/outcome chain records by decision id."""
    folded = {}
    order = []
    for record in sorted(records, key=lambda row: int(row.get("seq", 0))):
        identifier = record.get("id")
        if not identifier:
            continue
        if identifier not in folded:
            folded[identifier] = {"id": identifier, "outcomes": []}
            order.append(identifier)
        item = folded[identifier]
        event = record.get("event")
        if event == "outcome":
            item.setdefault("outcomes", []).append(dict(record))
            continue
        for key, value in record.items():
            if key not in ("hash", "prev"):
                item[key] = value
        item["last_seq"] = record.get("seq", item.get("last_seq", 0))
    for identifier in order:
        item = folded[identifier]
        supersedes = item.get("supersedes")
        if supersedes:
            values = supersedes if isinstance(supersedes, list) else [supersedes]
            for old_id in values:
                if old_id in folded:
                    folded[old_id]["superseded_by"] = identifier
    return [folded[identifier] for identifier in order]


def find_by_key(decisions, key, include_superseded=False):
    matches = []
    for item in decisions:
        if item.get("key") != key:
            continue
        if not include_superseded and item.get("superseded_by"):
            continue
        if item.get("status") in ("accepted", "rejected", "modified"):
            matches.append(item)
    return matches[-1] if matches else None


def _accepted(item):
    return item and item.get("status") in ("accepted", "modified")


def _blocked_directions(decisions):
    """Return durable rejected or pending-rejection directions."""
    accepted_lifts = []
    for item in decisions:
        if _accepted(item):
            accepted_lifts.append(item)
    blocked = []
    for item in decisions:
        key = item.get("direction_key")
        pending_reject = (item.get("status") == "open"
                          and item.get("verdict") in ("reject", "modify"))
        if not key or (item.get("status") != "rejected" and not pending_reject):
            continue
        lifted = False
        for later in accepted_lifts:
            if int(later.get("last_seq", 0)) <= int(item.get("last_seq", 0)):
                continue
            # Only a user L1/L2 decision may lift a rejection, and it must name the
            # rejected record. L3/self-authored accepts never clear it (IR-04).
            if later.get("decided_by") != "user" or later.get("level") not in ("L1", "L2"):
                continue
            supersedes = later.get("supersedes")
            supersedes = supersedes if isinstance(supersedes, list) else [supersedes]
            if item.get("id") in supersedes:
                lifted = True
                break
        if not lifted and not item.get("superseded_by"):
            blocked.append(item)
    return blocked


def _requests(project):
    root = _path(project, REQUESTS)
    values = []
    if not os.path.isdir(root):
        return values
    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(root, name)
        try:
            value = _read_json_strict(path, "request %s" % name)
        except GovernanceError:
            raise
        values.append(value)
    return values


def _request_path(project, identifier):
    value = str(identifier or "")
    if value.endswith(".json"):
        value = value[:-5]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise GovernanceError(2, "invalid request id")
    return _path(project, os.path.join(REQUESTS, value + ".json"))


def rebuild_requests_md(project):
    requests = _requests(project)
    lines = ["# Governance requests", ""]
    for level in ("L1", "L2", "L3"):
        rows = [item for item in requests
                if item.get("status") == "open" and item.get("level") == level]
        if not rows:
            continue
        lines.extend(["## %s" % level, "", "| id | kind | subject | proposal |", 
                      "|---|---|---|---|"])
        for item in rows:
            cells = []
            for key in ("id", "kind", "subject", "proposal"):
                cells.append(str(item.get(key, "")).replace("|", "\\|"))
            lines.append("| %s | %s | %s | %s |" % tuple(cells))
        lines.append("")
    path = _path(project, REQUESTS_MD)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    return path


def record_approval(project, ref, token=None, label="approval"):
    """Copy an approval and optionally authenticate it with a host HMAC secret."""
    if not ref:
        raise GovernanceError(2, "an approval reference is required")
    source = ref if os.path.isabs(ref) else os.path.join(project, ref)
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise GovernanceError(2, "approval reference does not exist: %s" % ref)
    try:
        with open(source, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise GovernanceError(2, "cannot read approval reference: %s" % exc)
    secret = os.environ.get("DSH_GOVERNANCE_SECRET")
    if secret:
        if not token:
            raise GovernanceError(2, "approval token is required by the host")
        expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(token), expected):
            raise GovernanceError(4, "approval token did not authenticate")
        mode = "authenticated"
    else:
        mode = "unauthenticated"
        print("[NOTICE] approval is an unauthenticated user attestation", file=sys.stderr)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(label)).strip("-.") or "approval"
    if not safe.lower().endswith(".md"):
        safe += ".md"
    destination = _path(project, os.path.join(APPROVALS, safe))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.abspath(destination) != source:
        if os.path.isfile(destination):
            with open(destination, "rb") as fh:
                existing = fh.read()
            if existing != raw:
                safe = safe[:-3] + "-%s.md" % hashlib.sha256(raw).hexdigest()[:12]
                destination = _path(project, os.path.join(APPROVALS, safe))
        if not os.path.isfile(destination):
            with open(destination, "wb") as fh:
                fh.write(raw)
    relative = os.path.relpath(destination, os.path.abspath(project)).replace(os.sep, "/")
    return {
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": mode,
    }


def _event(project, kind, mandate=None, **fields):
    try:
        common.append_event(project, kind,
                            mandate_hash=(mandate or {}).get("mandate_hash"), **fields)
    except OSError:
        pass


def _constraints(mandate):
    constraints = mandate.get("constraints") or {}
    return constraints if isinstance(constraints, dict) else {}


def _scope(mandate):
    scope = mandate.get("scope") or {}
    return scope if isinstance(scope, dict) else {}


def governance_block(project):
    """Return the compact governance envelope or ``None`` when never enabled."""
    mandate, control, problem = load_governance(project)
    if problem:
        raise GovernanceError(3, problem)
    if mandate is None:
        return None
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    accepted = []
    for item in decisions:
        if not _accepted(item) or item.get("superseded_by"):
            continue
        if item.get("mandate_version") != mandate.get("version"):
            continue
        accepted.append({key: item.get(key) for key in
                         ("id", "kind", "topic", "decision")})
    rejected = [{"key": item.get("direction_key"), "id": item.get("id"),
                 "topic": item.get("topic")} for item in _blocked_directions(decisions)]
    open_requests = [{"id": item.get("id"), "level": item.get("level"),
                      "topic": item.get("topic")} for item in _requests(project)
                     if item.get("status") == "open"]
    constraints = _constraints(mandate)
    authority = mandate.get("authority") or {}
    return {
        "enabled": bool(control.get("enabled")),
        "mode": control.get("mode"),
        "paused": bool(control.get("paused")),
        "mandate_version": mandate.get("version"),
        "mandate_hash": mandate.get("mandate_hash"),
        "goal": mandate.get("goal"),
        "hard_constraints": constraints.get("hard", []),
        "preferences": constraints.get("preferences", []),
        "strictness": authority.get("strictness", "normal"),
        "accepted_decisions": accepted,
        "rejected_directions": rejected,
        "open_requests": open_requests,
    }


def _write_empty(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n"):
        pass


def _preference(value, number):
    text = str(value)
    if ":" in text:
        key, rest = text.split(":", 1)
        if key.strip() and rest.strip():
            return {"id": "PREF-%d" % number, "key": key.strip(), "text": rest.strip()}
    return {"id": "PREF-%d" % number, "key": "preference-%d" % number,
            "text": text.strip()}


def command_init(project, args):
    project = os.path.abspath(project)
    if not str(args.goal or "").strip():
        raise GovernanceError(2, "--goal is required")
    sentinel_path = _path(project, ENABLED)
    if os.path.exists(sentinel_path) and not args.force:
        raise GovernanceError(4, "governance is already initialized; use --force deliberately")
    if args.gpt_calls is not None and args.gpt_calls < 0:
        raise GovernanceError(2, "--gpt-calls must be non-negative")
    if args.minutes is not None and args.minutes < 0:
        raise GovernanceError(2, "--minutes must be non-negative")
    hard = [{"id": "HC-%d" % (index + 1), "text": value}
            for index, value in enumerate(args.hard_constraint)]
    preferences = [_preference(value, index + 1)
                   for index, value in enumerate(args.preference)]
    scope = {
        "paths": _split_values(args.path) or ["."],
        "deny_paths": _split_values(args.deny_path),
        "models": _split_values(args.model),
        "plugins": _split_values(args.plugin),
        "ops": _split_values(args.op),
        "budgets": {"gpt_calls": args.gpt_calls, "minutes": args.minutes},
    }
    mandate = {
        "schema": SCHEMA,
        "version": 1,
        "goal": args.goal.strip(),
        "deliverable": args.deliverable or "",
        "ideas": [{"id": "IDEA-%d" % (index + 1), "text": value}
                  for index, value in enumerate(args.idea)],
        "constraints": {"hard": hard, "preferences": preferences},
        "scope": scope,
        "authority": {"default": "agent", "strictness": args.strictness},
        "created_at": common.now_iso(),
    }
    mandate["mandate_hash"] = mandate_hash(mandate)
    os.makedirs(_path(project, GOV), exist_ok=True)
    pin_governance(project, True, mandate["mandate_hash"])
    atomic_json(sentinel_path, {
        "schema": SCHEMA,
        "enabled_at": common.now_iso(),
        "rules_version": RULES_VERSION,
    })
    if args.force:
        for relative in (DECISIONS, GRANTS, USAGE, REQUESTS_MD, CHECKPOINT, HALT):
            try:
                os.remove(_path(project, relative))
            except FileNotFoundError:
                pass
        requests_root = _path(project, REQUESTS)
        if os.path.isdir(requests_root):
            for name in os.listdir(requests_root):
                if name.endswith(".json"):
                    os.remove(os.path.join(requests_root, name))
    atomic_json(_path(project, MANDATE), mandate)
    control = {
        "schema": SCHEMA,
        "enabled": True,
        "paused": False,
        "mode": "enforce",
        "mandate_hash": mandate["mandate_hash"],
        "updated_at": common.now_iso(),
        "updated_by": "user",
        "reason": "initialized",
        "safe_stop": None,
        "rules_version": RULES_VERSION,
    }
    atomic_json(_path(project, CONTROL), control)
    atomic_json(_path(project, AGENTS), {
        "schema": SCHEMA,
        "agents": {"lead": {"id": "lead", "parent": None,
                              "capabilities": ["orchestrate"]}},
        "plugins": {},
    })
    for relative in (DECISIONS, GRANTS, USAGE):
        if not os.path.exists(_path(project, relative)):
            _write_empty(_path(project, relative))
    os.makedirs(_path(project, REQUESTS), exist_ok=True)
    os.makedirs(_path(project, APPROVALS), exist_ok=True)
    rebuild_requests_md(project)
    _event(project, "authority-mode", mandate, action="init", enabled=True)
    return {"message": "governance initialized", "mandate": mandate,
            "control": control}


def _validate_mandate_shape(data):
    if not isinstance(data, dict):
        raise GovernanceError(2, "mandate file must contain an object")
    if not str(data.get("goal") or "").strip():
        raise GovernanceError(2, "mandate goal is required")
    try:
        version = int(data.get("version"))
    except (TypeError, ValueError):
        raise GovernanceError(2, "mandate version must be an integer")
    if version < 1:
        raise GovernanceError(2, "mandate version must be positive")
    for key in ("constraints", "scope", "authority"):
        if key in data and not isinstance(data.get(key), dict):
            raise GovernanceError(2, "mandate %s must be an object" % key)


def command_mandate_set(project, args):
    current, control = _require_state(project)
    if args.by != "user":
        raise GovernanceError(4, "only an attested user may replace the mandate")
    source = args.file if os.path.isabs(args.file) else os.path.join(project, args.file)
    try:
        with open(source, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GovernanceError(2, "cannot read new mandate: %s" % exc)
    _validate_mandate_shape(data)
    if int(data["version"]) <= int(current["version"]):
        raise GovernanceError(2, "new mandate version must increase")
    approval = record_approval(project, args.approval_ref, args.approval_token,
                               "mandate-v%s" % data["version"])
    data = dict(data)
    data.setdefault("updated_at", common.now_iso())
    data["mandate_hash"] = mandate_hash(data)
    atomic_json(_path(project, MANDATE), data)
    control.update({
        "mandate_hash": data["mandate_hash"],
        "updated_at": common.now_iso(),
        "updated_by": "user",
        "reason": args.reason,
    })
    atomic_json(_path(project, CONTROL), control)
    pin_governance(project, True, data["mandate_hash"])
    identifier = _new_id("D")
    record = {
        "at": common.now_iso(), "event": "decide", "id": identifier,
        "kind": "mandate", "topic": "mandate v%s" % data["version"],
        "subject": str(data["version"]),
        "key": key_for("mandate", str(data["version"]), data["version"]),
        "level": "L1", "status": "accepted", "decision": "accept",
        "decided_by": "user", "reason": args.reason,
        "approval_ref": approval, "identity": "attested",
        "artifact": os.path.abspath(source),
        "artifact_sha256": common.sha256_file(source),
        "mandate_version": data["version"],
    }
    chain_append(_path(project, DECISIONS), record)
    _event(project, "authority-decision", data, decision_id=identifier,
           decision_kind="mandate")
    return {"message": "mandate updated", "mandate": data, "decision_id": identifier}


def command_mandate(project, args):
    if args.mandate_action == "set":
        return command_mandate_set(project, args)
    mandate, _control = _require_state(project)
    if args.mandate_action == "check":
        actual = mandate_hash(mandate)
        if mandate.get("mandate_hash") != actual:
            raise GovernanceError(3, "mandate hash mismatch")
        return {"message": "mandate is valid", "version": mandate.get("version"),
                "mandate_hash": actual}
    return mandate


def _verify_evidence_ref(project, value):
    if value.startswith("call:"):
        request_id = value[5:]
        attempt = common.find_attempt(project, request_id)
        if not attempt:
            raise GovernanceError(4, "unknown call evidence: %s" % request_id)
        if attempt.get("status") != "ok":
            raise GovernanceError(4, "call evidence is not successful: %s" % request_id)
        reviewer = common.family_of(attempt.get("model"), project)
        lead = common.family_of("dsh:auto", project)
        independent = (reviewer.get("known") and lead.get("known")
                       and reviewer.get("family") != lead.get("family"))
        return ("independent" if independent else "self",
                "%s %s != %s" % (request_id, reviewer.get("family"), lead.get("family"))
                if independent else "%s is not independently identified" % request_id)
    if value.startswith("log:"):
        name = value[4:]
        path = name if os.path.isabs(name) else os.path.join(project, name)
        if not os.path.isfile(path):
            raise GovernanceError(4, "unknown log evidence: %s" % name)
        return "self", "log:%s" % os.path.abspath(path)
    raise GovernanceError(2, "evidence refs must use call: or log:")


def command_intake(project, args):
    mandate, _control = _require_state(project)
    if args.verdict == "reject":
        if not args.evidence or not args.evidence_ref or not args.alternative:
            raise GovernanceError(2, "reject requires evidence, evidence-ref, and alternative")
    if args.verdict == "modify" and not args.alternative:
        raise GovernanceError(2, "modify requires an alternative")
    labels = []
    bases = []
    for ref in args.evidence_ref:
        label, basis = _verify_evidence_ref(project, ref)
        labels.append(label)
        bases.append(basis)
    if "independent" in labels:
        independence = "independent"
    elif args.user_response != "none":
        independence = "user"
    else:
        independence = "self"
    subject = normalize_topic(args.subject or args.summary)
    identifier = _new_id("D")
    level = "L3" if args.verdict == "adopt" else "L2"
    key = key_for("intake", subject, mandate["version"])
    dkey = args.direction_key or direction_key(args.kind, subject)
    if args.verdict == "adopt":
        status, decision = "accepted", "accept"
    elif args.user_response == "accept":
        status, decision = "accepted", "accept"
    elif args.user_response == "modify":
        status, decision = "modified", "modify"
    elif args.user_response == "reject":
        status, decision = "rejected", "reject"
    else:
        status, decision = "open", "none"
    base = {
        "at": common.now_iso(), "event": "propose", "id": identifier,
        "kind": "intake", "intake_kind": args.kind, "level": level,
        "key": key, "topic": args.summary, "subject": subject,
        "idea_id": args.idea_id or _new_id("IDEA"), "summary": args.summary,
        "verdict": args.verdict, "assessment": args.assessment,
        "evidence": args.evidence, "evidence_refs": args.evidence_ref,
        "independence": independence, "independence_basis": "; ".join(bases),
        "alternatives": args.alternative, "user_response": args.user_response,
        "direction_key": dkey, "mandate_version": mandate["version"],
        "status": "open" if status == "open" else status,
    }
    chain_append(_path(project, DECISIONS), base)
    decided = dict(base)
    decided.update({
        "at": common.now_iso(), "event": "decide", "status": status,
        "decision": decision,
        "decided_by": "user" if args.user_response != "none" else "agent",
        "reason": args.assessment,
        "approval_mode": "chat" if args.user_response == "accept" else None,
    })
    chain_append(_path(project, DECISIONS), decided)
    _event(project, "authority-intake", mandate, decision_id=identifier,
           verdict=args.verdict, status=status)
    return {"message": "intake recorded", "id": identifier, "status": status,
            "level": level, "independence": independence, "direction_key": dkey}


def _parse_option(value):
    parts = str(value).split(":", 2)
    if len(parts) == 3:
        return {"id": parts[0], "text": parts[1], "impact": parts[2]}
    if len(parts) == 2:
        return {"id": parts[0], "text": parts[1], "impact": ""}
    return {"id": str(value), "text": str(value), "impact": ""}


def command_request(project, args):
    mandate, _control = _require_state(project)
    strictness = (mandate.get("authority") or {}).get("strictness", "normal")
    result = classify(args.kind, strictness, args.effect)
    level = result["level"]
    if args.level != "auto":
        if LEVEL_RANK[args.level] < LEVEL_RANK[level]:
            raise GovernanceError(2, "explicit level cannot lower the classifier")
        level = args.level
    if level == "L3":
        raise GovernanceError(2, "L3 matters are autonomous; do the work and optionally decide")
    if not args.subject:
        raise GovernanceError(2, "--subject is required for L1/L2 requests")
    subject = normalize_topic(args.subject)
    key = key_for(result["kind"], subject, mandate["version"])
    for item in _requests(project):
        if item.get("status") == "open" and item.get("key") == key:
            duplicate = dict(item)
            duplicate["duplicate"] = True
            duplicate["message"] = "existing request returned"
            return duplicate
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    settled = find_by_key(decisions, key)
    supersedes = None
    if settled:
        if not args.reopen:
            raise GovernanceError(4, "the canonical decision key is already settled")
        if not args.reason or not args.evidence_ref:
            raise GovernanceError(2, "reopen requires --reason and --evidence-ref")
        for reference in args.evidence_ref:
            _verify_evidence_ref(project, reference)
        prior_refs = set(settled.get("evidence_refs") or [])
        for item in _requests(project):
            if item.get("decision_id") == settled.get("id"):
                prior_refs.update(item.get("evidence_refs") or [])
        if not any(reference not in prior_refs for reference in args.evidence_ref):
            raise GovernanceError(4, "reopen needs evidence not already on the settled record")
        level = settled.get("level") or level
        supersedes = settled.get("id")
    elif args.reopen:
        raise GovernanceError(4, "there is no settled decision to reopen")
    identifier = _new_id("Q")
    request = {
        "id": identifier, "at": common.now_iso(), "level": level,
        "kind": result["kind"], "key": key, "subject": subject,
        "topic": args.topic, "proposal": args.proposal,
        "options": [_parse_option(value) for value in args.option],
        "affected": args.affected, "status": "open", "decision_id": None,
        "mandate_version": mandate["version"], "supersedes": supersedes,
        "evidence_refs": args.evidence_ref, "reopen_reason": args.reason,
    }
    atomic_json(_request_path(project, identifier), request)
    rebuild_requests_md(project)
    _event(project, "authority-request", mandate, request_id=identifier,
           level=level, key=key, batch=bool(args.batch))
    request["message"] = "request created"
    return request


def command_pending(project, args):
    _require_state(project)
    rows = [item for item in _requests(project) if item.get("status") == "open"]
    if args.level:
        rows = [item for item in rows if item.get("level") == args.level]
    return {"message": "%d open request(s)" % len(rows), "requests": rows,
            "count": len(rows)}


def _decision_records(project, proposal, decided):
    chain_append(_path(project, DECISIONS), proposal)
    return chain_append(_path(project, DECISIONS), decided)


def command_resolve(project, args):
    mandate, _control = _require_state(project)
    path = _request_path(project, args.request)
    if not os.path.isfile(path):
        raise GovernanceError(4, "request does not exist")
    request = _read_json_strict(path, "request")
    if request.get("status") != "open":
        raise GovernanceError(4, "request is not open")
    level = request.get("level")
    if level in ("L1", "L2") and args.by != "user":
        raise GovernanceError(4, "%s decisions require an attested user" % level)
    if args.decision == "modify" and not args.note:
        raise GovernanceError(2, "modify requires --note")
    identifier = _new_id("D")
    subject = normalize_topic(args.subject or request.get("subject"))
    status = {"accept": "accepted", "reject": "rejected",
              "modify": "modified"}[args.decision]
    dkey = args.direction_key or direction_key(request.get("kind"), subject)
    artifact = None
    artifact_hash = None
    if args.artifact:
        artifact = args.artifact if os.path.isabs(args.artifact) else os.path.join(project,
                                                                                  args.artifact)
        artifact = os.path.abspath(artifact)
        if not os.path.isfile(artifact):
            raise GovernanceError(2, "artifact does not exist")
        artifact_hash = common.sha256_file(artifact)
    binding_kinds = {"release", "scope", "constraint", "target",
                     "scope-change", "constraint-change", "target-change"}
    if (level == "L1" and status in ("accepted", "modified")
            and request.get("kind") in binding_kinds and not artifact and not args.commit):
        raise GovernanceError(2, "accepted L1 decision requires --artifact or --commit")
    approval = None
    if level == "L1":
        approval = record_approval(project, args.approval_ref, args.approval_token,
                                   "resolve-%s" % request["id"])
    proposal = dict(request)
    proposal.update({
        "at": common.now_iso(), "event": "propose", "id": identifier,
        "request_id": request.get("id"), "status": "open",
    })
    decided = dict(proposal)
    decided.update({
        "at": common.now_iso(), "event": "decide", "status": status,
        "decision": args.note if args.decision == "modify" else args.decision,
        "decision_type": args.decision, "decided_by": args.by,
        "reason": args.note, "approval_ref": approval,
        "identity": "attested" if args.by == "user" else None,
        "direction_key": dkey, "supersedes": request.get("supersedes"),
        "mandate_version": mandate["version"], "artifact": artifact,
        "artifact_sha256": artifact_hash, "commit": args.commit,
    })
    _decision_records(project, proposal, decided)
    request["status"] = "resolved"
    request["decision_id"] = identifier
    request["resolved_at"] = common.now_iso()
    atomic_json(path, request)
    rebuild_requests_md(project)
    _event(project, "authority-decision", mandate, decision_id=identifier,
           request_id=request.get("id"), status=status)
    return {"message": "request resolved", "id": identifier, "status": status,
            "key": request.get("key"), "direction_key": dkey}


def _effective_level(base_level, explicit):
    if explicit in (None, "auto"):
        return base_level
    if LEVEL_RANK[explicit] < LEVEL_RANK[base_level]:
        raise GovernanceError(2, "explicit level cannot lower the classifier")
    return explicit


def command_decide(project, args):
    mandate, _control = _require_state(project)
    strictness = (mandate.get("authority") or {}).get("strictness", "normal")
    classified = classify(args.kind, strictness, args.effect)
    level = _effective_level(classified["level"], args.level)
    if level in ("L1", "L2") and not args.subject:
        raise GovernanceError(2, "--subject is required for L1/L2 decisions")
    if level in ("L1", "L2") and args.by != "user":
        raise GovernanceError(4, "%s decisions require an attested user" % level)
    if args.decision == "modify" and not args.reason:
        raise GovernanceError(2, "modify requires a reason")
    subject = normalize_topic(args.subject or args.topic)
    key = key_for(classified["kind"], subject, mandate["version"])
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    previous = find_by_key(decisions, key)
    supersedes = args.supersedes
    if previous and not supersedes:
        if args.decision == "accept" and previous.get("status") == "rejected":
            if args.by != "user" or level not in ("L1", "L2"):
                raise GovernanceError(4, "only a user L1/L2 decision can lift a rejection")
            supersedes = previous.get("id")
        else:
            raise GovernanceError(4, "the canonical decision key is already settled")
    if supersedes and (args.by != "user" or level not in ("L1", "L2")):
        raise GovernanceError(4, "only a user L1/L2 decision can supersede a prior decision")
    artifact = None
    artifact_hash = None
    if args.artifact:
        artifact = args.artifact if os.path.isabs(args.artifact) else os.path.join(project,
                                                                                  args.artifact)
        artifact = os.path.abspath(artifact)
        if not os.path.isfile(artifact):
            raise GovernanceError(2, "artifact does not exist")
        artifact_hash = common.sha256_file(artifact)
    binding_kinds = {"release", "scope", "constraint", "target",
                     "scope-change", "constraint-change", "target-change"}
    if (level == "L1" and args.decision in ("accept", "modify")
            and classified["kind"] in binding_kinds
            and not artifact and not args.commit):
        raise GovernanceError(2, "accepted L1 decision requires --artifact or --commit")
    approval = None
    if level == "L1":
        approval = record_approval(project, args.approval_ref, args.approval_token,
                                   "decision-%s" % _new_id("A"))
    identifier = _new_id("D")
    status = {"accept": "accepted", "reject": "rejected",
              "modify": "modified"}[args.decision]
    dkey = args.direction_key or direction_key(classified["kind"], subject)
    proposal = {
        "at": common.now_iso(), "event": "propose", "id": identifier,
        "kind": classified["kind"], "topic": args.topic, "proposal": args.proposal,
        "proposed_by": args.by, "subject": subject, "key": key, "level": level,
        "direction_key": dkey, "mandate_version": mandate["version"], "status": "open",
    }
    decided = dict(proposal)
    decided.update({
        "at": common.now_iso(), "event": "decide", "status": status,
        "decision": args.decision, "decided_by": args.by, "reason": args.reason,
        "approval_ref": approval, "identity": "attested" if args.by == "user" else None,
        "artifact": artifact, "artifact_sha256": artifact_hash, "commit": args.commit,
        "supersedes": supersedes,
    })
    _decision_records(project, proposal, decided)
    _event(project, "authority-decision", mandate, decision_id=identifier,
           level=level, decision_kind=classified["kind"], status=status)
    return {"message": "decision recorded", "id": identifier, "status": status,
            "level": level, "key": key, "direction_key": dkey}


def command_explain(project, args):
    _require_state(project)
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    item = next((row for row in decisions if row.get("id") == args.id), None)
    related = [row for row in _requests(project)
               if row.get("decision_id") == args.id or row.get("supersedes") == args.id]
    if item:
        return {"message": "decision fold", "decision": item,
                "related_requests": related}
    request = next((row for row in _requests(project) if row.get("id") == args.id), None)
    if request:
        return {"message": "request", "request": request}
    raise GovernanceError(4, "decision or request id not found")


def command_outcome(project, args):
    mandate, _control = _require_state(project)
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    if not any(row.get("id") == args.id for row in decisions):
        raise GovernanceError(4, "decision id not found")
    record = {
        "at": common.now_iso(), "event": "outcome", "id": args.id,
        "verdict": args.verdict, "observed": args.observed,
        "model": args.model, "mandate_version": mandate["version"],
    }
    chain_append(_path(project, DECISIONS), record)
    _event(project, "authority-outcome", mandate, decision_id=args.id,
           verdict=args.verdict, model=args.model)
    return {"message": "outcome recorded", "id": args.id, "verdict": args.verdict}


def _agents(project):
    path = _path(project, AGENTS)
    if not os.path.isfile(path):
        raise GovernanceError(3, "agents.json is missing")
    data = _read_json_strict(path, "agents")
    if not isinstance(data.get("agents"), dict) or not isinstance(data.get("plugins"), dict):
        raise GovernanceError(3, "agents.json is malformed")
    return data


def command_register(project, args):
    mandate, _control = _require_state(project)
    data = _agents(project)
    identifier = args.id
    if identifier in data["agents"]:
        raise GovernanceError(4, "agent is already registered")
    if args.parent and args.parent not in data["agents"]:
        raise GovernanceError(4, "parent agent is not registered")
    data["agents"][identifier] = {
        "id": identifier, "parent": args.parent,
        "capabilities": args.capability, "registered_at": common.now_iso(),
    }
    atomic_json(_path(project, AGENTS), data)
    _event(project, "authority-agent", mandate, action="register", agent=identifier)
    return {"message": "agent registered", "agent": data["agents"][identifier]}


def command_plugin_add(project, args):
    mandate, _control = _require_state(project)
    if args.by != "user":
        raise GovernanceError(4, "plugin registration requires an attested user")
    scope_plugins = _scope(mandate).get("plugins") or []
    if scope_plugins and args.id not in scope_plugins:
        raise GovernanceError(4, "plugin is outside the mandate")
    data = _agents(project)
    if args.id in data["plugins"]:
        raise GovernanceError(4, "plugin is already registered")
    approval = record_approval(project, args.approval_ref, args.approval_token,
                               "plugin-%s" % args.id)
    data["plugins"][args.id] = {
        "id": args.id, "ops": args.op, "capabilities": args.capability,
        "registered_at": common.now_iso(), "approval_ref": approval,
    }
    atomic_json(_path(project, AGENTS), data)
    identifier = _new_id("D")
    record = {
        "at": common.now_iso(), "event": "decide", "id": identifier,
        "kind": "plugin-add", "topic": args.id, "subject": args.id,
        "key": key_for("plugin-add", args.id, mandate["version"]),
        "level": "L1", "status": "accepted", "decision": "accept",
        "decided_by": "user", "reason": args.reason, "approval_ref": approval,
        "identity": "attested", "mandate_version": mandate["version"],
        "direction_key": direction_key("plugin-add", args.id),
    }
    chain_append(_path(project, DECISIONS), record)
    _event(project, "authority-agent", mandate, action="plugin-add", plugin=args.id)
    return {"message": "plugin registered", "plugin": data["plugins"][args.id],
            "decision_id": identifier}


def command_plugin(project, args):
    if args.plugin_action == "add":
        return command_plugin_add(project, args)
    _require_state(project)
    plugins = _agents(project)["plugins"]
    return {"message": "%d plugin(s)" % len(plugins), "plugins": plugins}


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise GovernanceError(2, "invalid ISO timestamp: %s" % value)


def fold_grants(records):
    folded = {}
    order = []
    for record in sorted(records, key=lambda row: int(row.get("seq", 0))):
        identifier = record.get("id")
        if not identifier:
            continue
        if record.get("event") == "grant":
            folded[identifier] = dict(record)
            folded[identifier]["revoked"] = False
            order.append(identifier)
        elif record.get("event") == "revoke" and identifier in folded:
            folded[identifier]["revoked"] = True
            folded[identifier]["revoked_at"] = record.get("at")
            folded[identifier]["revoke_reason"] = record.get("reason")
    return [folded[identifier] for identifier in order]


def _grant_map(project):
    return {row["id"]: row for row in
            fold_grants(read_jsonl(_path(project, GRANTS)))}


def _expired(grant):
    expires = _parse_iso(grant.get("expires_at")) if grant.get("expires_at") else None
    if not expires:
        return False
    now = datetime.now().astimezone()
    if expires.tzinfo is None:
        expires = expires.astimezone()
    return expires <= now


def _grant_chain(grants, identifier):
    chain = []
    seen = set()
    current = grants.get(identifier)
    while current:
        if current["id"] in seen:
            raise GovernanceError(3, "grant parent cycle detected")
        seen.add(current["id"])
        chain.append(current)
        parent = current.get("parent")
        if not parent:
            break
        current = grants.get(parent)
        if current is None:
            raise GovernanceError(3, "grant parent is missing: %s" % parent)
    return chain


def _active_chain(grants, identifier):
    chain = _grant_chain(grants, identifier)
    for item in chain:
        if item.get("revoked"):
            raise GovernanceError(4, "grant %s is revoked" % item["id"])
        if _expired(item):
            raise GovernanceError(4, "grant %s is expired" % item["id"])
    return chain


def _is_under(path, root):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:
        return False


def _iteration_roots(project):
    project = os.path.realpath(os.path.abspath(project))
    summary = common.iteration_summary(project)
    candidate = None
    if summary:
        candidate = (summary.get("target") or {}).get("root")
        candidate = os.path.realpath(os.path.abspath(candidate)) if candidate else None
    return project, candidate


def _resolve_path(project, value):
    project_root, candidate_root = _iteration_roots(project)
    raw = str(value or ".")
    active = os.path.realpath(os.path.abspath(os.getcwd()))
    base = project_root
    if candidate_root and _is_under(active, candidate_root):
        base = candidate_root
    elif _is_under(active, project_root):
        base = project_root
    absolute = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    root = candidate_root if candidate_root and _is_under(absolute, candidate_root) else project_root
    if not _is_under(absolute, root):
        raise GovernanceError(4, "path escapes the logical workspace root")
    relative = os.path.relpath(absolute, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise GovernanceError(4, "path traversal escapes the logical root")
    return absolute, root, relative.replace(os.sep, "/")


def _path_subset(project, child, parents):
    child_path, child_root, _rel = _resolve_path(project, child)
    for parent in parents:
        if os.path.isabs(str(parent)):
            parent_path, parent_root, _ = _resolve_path(project, parent)
        else:
            parent_root = child_root
            parent_path = os.path.realpath(os.path.join(child_root, str(parent)))
        if (os.path.normcase(child_root) == os.path.normcase(parent_root)
                and _is_under(parent_path, parent_root) and _is_under(child_path, parent_path)):
            return True
    return False


def _pattern_subset(child, parents):
    for parent in parents:
        if parent == "*" or child == parent:
            return True
        if parent.endswith("*") and child.startswith(parent[:-1]):
            return True
    return False


def command_grant(project, args):
    mandate, _control = _require_state(project)
    if args.by != "user":
        raise GovernanceError(4, "grants require an attested user")
    if args.max_calls is not None and args.max_calls < 0:
        raise GovernanceError(2, "--max-calls must be non-negative")
    agents = _agents(project)["agents"]
    if args.to != "lead" and args.to not in agents:
        raise GovernanceError(4, "grant target is not registered")
    grants = _grant_map(project)
    parent = None
    parent_paths = None
    parent_models = None
    parent_ops = None
    max_calls = args.max_calls
    expires = args.expires
    if args.parent:
        if args.parent not in grants:
            raise GovernanceError(4, "parent grant does not exist")
        _active_chain(grants, args.parent)
        parent = grants[args.parent]
        parent_paths = parent.get("paths") or ["."]
        parent_models = parent.get("models") or []
        parent_ops = parent.get("ops") or []
        if max_calls is None:
            max_calls = parent.get("max_calls")
        if expires is None:
            expires = parent.get("expires_at")
        if parent.get("max_calls") is not None and (max_calls is None
                                                     or max_calls > parent["max_calls"]):
            raise GovernanceError(4, "child call budget widens its parent")
        if parent.get("expires_at"):
            if not expires or _parse_iso(expires) > _parse_iso(parent["expires_at"]):
                raise GovernanceError(4, "child expiry widens its parent")
    else:
        scope = _scope(mandate)
        parent_paths = scope.get("paths") or ["."]
        parent_models = scope.get("models") or []
        parent_ops = scope.get("ops") or []
        budget = (scope.get("budgets") or {}).get("gpt_calls")
        if budget is not None and max_calls is not None and max_calls > budget:
            raise GovernanceError(4, "grant call budget exceeds the mandate")
    paths = _split_values(args.paths) or list(parent_paths)
    models = _split_values(args.models) or list(parent_models)
    ops = _split_values(args.ops) or list(parent_ops)
    for value in paths:
        if not _path_subset(project, value, parent_paths):
            raise GovernanceError(4, "grant path widens its parent or mandate: %s" % value)
    if parent_models:
        for value in models:
            if not _pattern_subset(value, parent_models):
                raise GovernanceError(4, "grant model widens its parent or mandate: %s" % value)
    if parent_ops and not set(ops).issubset(set(parent_ops)):
        raise GovernanceError(4, "grant operations widen the parent or mandate")
    if expires:
        _parse_iso(expires)
    approval = record_approval(project, args.approval_ref, args.approval_token,
                               "grant-%s" % _new_id("A"))
    identifier = _new_id("G")
    record = {
        "at": common.now_iso(), "event": "grant", "id": identifier,
        "to": args.to, "parent": args.parent, "ops": ops, "paths": paths,
        "models": models, "max_calls": max_calls, "expires_at": expires,
        "by": "user", "reason": args.reason, "approval_ref": approval,
        "identity": "attested", "mandate_version": mandate["version"],
    }
    chain_append(_path(project, GRANTS), record)
    _event(project, "authority-grant", mandate, grant_id=identifier, to=args.to)
    return {"message": "grant created", "grant": record}


def command_revoke(project, args):
    mandate, _control = _require_state(project)
    if args.by != "user":
        raise GovernanceError(4, "revocation requires an attested user")
    grants = _grant_map(project)
    grant = grants.get(args.id)
    if not grant:
        raise GovernanceError(4, "grant does not exist")
    if grant.get("revoked"):
        raise GovernanceError(4, "grant is already revoked")
    record = {
        "at": common.now_iso(), "event": "revoke", "id": args.id,
        "by": "user", "reason": args.reason, "identity": "attested",
        "mandate_version": mandate["version"],
    }
    chain_append(_path(project, GRANTS), record)
    _event(project, "authority-grant", mandate, action="revoke", grant_id=args.id)
    return {"message": "grant revoked", "id": args.id}


def _model_allowed(model, patterns):
    if not patterns or not model:
        return True
    return any(fnmatch.fnmatchcase(model, pattern) for pattern in patterns)


def _path_allowed(absolute, project, roots):
    return _path_subset(project, absolute, roots or ["."])


def _select_grant(project, agent, op, absolute_path, model):
    grants = _grant_map(project)
    mandate, _control = _require_state(project)
    actor = [grant for grant in grants.values() if grant.get("to") == agent]
    if not actor:
        if agent == "lead":
            return [], False
        raise GovernanceError(4, "registered subagent has no active grant")
    denial = None
    for grant in reversed(actor):
        if grant.get("mandate_version") not in (None, mandate["version"]):
            denial = GovernanceError(4, "grant predates the current mandate; regrant")
            continue
        try:
            chain = _active_chain(grants, grant["id"])
        except GovernanceError as exc:
            denial = exc
            continue
        if any(item.get("mandate_version") not in (None, mandate["version"])
               for item in chain):
            denial = GovernanceError(4, "grant chain predates the current mandate")
            continue
        if grant.get("ops") and op not in grant.get("ops", []):
            continue
        if absolute_path is not None and not _path_allowed(absolute_path, project,
                                                           grant.get("paths") or ["."]):
            continue
        if not _model_allowed(model, grant.get("models") or []):
            continue
        return [item["id"] for item in chain], True
    if denial:
        raise denial
    raise GovernanceError(4, "no active grant covers the operation")


def reserve(project, agent, grant_chain, calls, request_id, op):
    """Atomically reserve calls against every ancestor and the mandate."""
    if calls < 1:
        raise GovernanceError(2, "calls must be at least one")
    with lock(project):
        mandate, _control = _require_state(project)
        grants = _grant_map(project)
        usage = read_jsonl(_path(project, USAGE))
        for identifier in grant_chain:
            grant = grants.get(identifier)
            if not grant:
                raise GovernanceError(3, "reservation references a missing grant")
            maximum = grant.get("max_calls")
            if maximum is None:
                continue
            used = sum(int(row.get("calls", 0)) for row in usage
                       if identifier in (row.get("grant_chain") or []))
            if used + calls > int(maximum):
                raise GovernanceError(4, "call budget exhausted for grant %s" % identifier)
        mandate_max = ((_scope(mandate).get("budgets") or {}).get("gpt_calls"))
        total = sum(int(row.get("calls", 0)) for row in usage)
        if mandate_max is not None and total + calls > int(mandate_max):
            raise GovernanceError(4, "mandate call budget is exhausted")
        minute_max = ((_scope(mandate).get("budgets") or {}).get("minutes"))
        if minute_max is not None:
            started = _parse_iso(mandate.get("created_at") or mandate.get("updated_at"))
            if started:
                if started.tzinfo is None:
                    started = started.astimezone()
                elapsed = (datetime.now().astimezone() - started).total_seconds() / 60.0
                if elapsed >= float(minute_max):
                    raise GovernanceError(4, "mandate minute budget is exhausted")
        identifier = _new_id("U")
        row = {
            "seq": len(usage) + 1, "at": common.now_iso(), "id": identifier,
            "request_id": request_id, "agent": agent,
            "grant_chain": grant_chain or ["mandate"], "calls": calls, "op": op,
        }
        append_jsonl(_path(project, USAGE), row)
    _event(project, "authority-usage", mandate, reservation_id=identifier,
           request_id=request_id, agent=agent, calls=calls, op=op)
    return identifier


def command_usage(project, args):
    mandate, _control = _require_state(project)
    rows = read_jsonl(_path(project, USAGE))
    totals = {}
    for row in rows:
        for identifier in row.get("grant_chain") or ["mandate"]:
            totals[identifier] = totals.get(identifier, 0) + int(row.get("calls", 0))
    total = sum(int(row.get("calls", 0)) for row in rows)
    budget = ((_scope(mandate).get("budgets") or {}).get("gpt_calls"))
    return {"message": "%d call(s) reserved" % total, "total": total,
            "mandate_budget": budget, "by_grant": totals, "reservations": rows}


def _plan_path(project):
    local = _path(project, os.path.join("_work", "plan", "plan.md"))
    if os.path.isfile(local):
        return local
    summary = common.iteration_summary(project)
    stable = (summary or {}).get("orchestrator") or {}
    root = stable.get("root")
    if root:
        candidate = os.path.join(os.path.abspath(root), "_work", "plan", "plan.md")
        if os.path.isfile(candidate):
            return candidate
    return local


def work_package(project, wp_id):
    """Return a reviewed work-package output/direction, or ``None``."""
    path = _plan_path(project)
    for row in common.parse_table(path):
        if str(row.get("id", "")).strip() == str(wp_id).strip():
            output = str(row.get("output", "")).strip().strip("`")
            direction = str(row.get("direction", "")).strip().strip("`")
            return {"output": output, "direction": direction or "wp:%s" % wp_id}
    return None


def _package_outputs(package):
    raw = str((package or {}).get("output") or "")
    return [item.strip().strip("`") for item in raw.split(",")
            if item.strip().strip("`")]


def _decision_for(project, mandate, kinds, subject):
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    normalized_subject = normalize_topic(subject)
    candidates = []
    for item in decisions:
        if item.get("kind") not in kinds or normalize_topic(item.get("subject", "")) != normalized_subject:
            continue
        if item.get("mandate_version") != mandate.get("version"):
            continue
        if _accepted(item) and not item.get("superseded_by"):
            candidates.append(item)
    return candidates[-1] if candidates else None


def _guard_result(code, reason, **fields):
    data = {
        "allowed": code == 0, "code": code, "reason": reason,
        "level": fields.pop("level", None), "key": fields.pop("key", None),
        "decision_id": fields.pop("decision_id", None),
        "scope_root": fields.pop("scope_root", None),
        "mode": fields.pop("mode", None),
        "mandate_hash": fields.pop("mandate_hash", None),
        "reservation_id": fields.pop("reservation_id", None),
    }
    data.update(fields)
    return data


def _approval_exists(project, ref):
    if not ref:
        return False
    if isinstance(ref, dict):
        ref = ref.get("path")
    path = ref if os.path.isabs(str(ref)) else os.path.join(project, str(ref))
    return os.path.isfile(path)


def _guard_impl(project, op, path=None, model=None, agent="lead", plugin=None,
                work_package=None, change=None, direction_key=None, calls=1,
                subject=None, approval_ref=None, quiet=False, request_id=None,
                effect="none"):
    project = os.path.abspath(project)
    mandate, control, problem = load_governance(project)
    if problem:
        return 3, _guard_result(3, problem)
    if mandate is None:
        return 0, _guard_result(0, "governance disabled", disabled=True)
    normalized_op = _kind(op)
    if normalized_op not in KNOWN_OPS:
        return 2, _guard_result(2, "unknown operation: %s" % normalized_op,
                                mandate_hash=mandate["mandate_hash"], mode=control.get("mode"))
    try:
        calls = int(calls)
    except (TypeError, ValueError):
        raise GovernanceError(2, "calls must be an integer")
    if calls < 1:
        raise GovernanceError(2, "calls must be at least one")
    if (os.path.exists(_path(project, HALT)) or control.get("paused")) \
            and normalized_op in WRITABLE_OPS:
        raise GovernanceError(4, "paused by user")
    registry = _agents(project)
    if agent != "lead" and agent not in registry["agents"]:
        raise GovernanceError(4, "agent is not registered")
    if normalized_op == "plugin-use":
        plugin_id = plugin or subject
        entry = registry["plugins"].get(plugin_id)
        if not entry:
            raise GovernanceError(4, "plugin is not registered")
        capability = subject or plugin
        if entry.get("ops") and capability not in entry.get("ops", []):
            raise GovernanceError(4, "plugin capability is not authorized")

    strictness = (mandate.get("authority") or {}).get("strictness", "normal")
    if normalized_op in L1_OPS:
        level = "L1"
    elif normalized_op in L2_OPS:
        level = "L2"
    else:
        level = classify(normalized_op, strictness, effect)["level"]
    package = None
    package_direction = None
    if work_package:
        package = globals()["work_package"](project, work_package)
        if not package:
            raise GovernanceError(4, "work package is not in the reviewed plan")
        package_direction = package["direction"]
    elif normalized_op in ("produce", "write") and not change:
        raise GovernanceError(2, "produce/write requires --work-package or --change")

    decision = None
    change_kind = None
    key = None
    if change:
        change_kind = _kind(change)
        classified = classify(change_kind, strictness, effect)
        if LEVEL_RANK[classified["level"]] > LEVEL_RANK[level]:
            level = classified["level"]
        if not subject:
            raise GovernanceError(2, "an explicit change must name --subject")
        key = key_for(change_kind, subject, mandate["version"])
        if level in ("L1", "L2"):
            decision = _decision_for(project, mandate, {change_kind}, subject)
            if not decision:
                if level == "L2" and control.get("mode") == "suspended":
                    _event(project, "authority-notice", mandate,
                           message="suspended: L2 logged", kind=change_kind,
                           subject=subject)
                else:
                    raise GovernanceError(4, "accepted %s decision is required" % change_kind)

    if level == "L1" and not change:
        if not subject:
            raise GovernanceError(2, "%s requires --subject" % normalized_op)
        aliases = {normalized_op}
        aliases.update({
            "target-change": "target", "constraint-change": "constraint",
            "scope-change": "scope",
        }.get(normalized_op, "") for _unused in [0])
        aliases.discard("")
        if normalized_op == "promote":
            if not _approval_exists(project, approval_ref):
                raise GovernanceError(4, "promote requires the iteration approval reference")
        else:
            decision = _decision_for(project, mandate, aliases, subject)
            if not decision:
                raise GovernanceError(4, "accepted L1 decision is required")
        key = key_for(normalized_op, subject, mandate["version"])
    elif level == "L2" and not change:
        if not subject:
            raise GovernanceError(2, "%s requires --subject" % normalized_op)
        decision = _decision_for(project, mandate, {normalized_op}, subject)
        if not decision and control.get("mode") != "suspended":
            raise GovernanceError(4, "accepted L2 decision is required")
        key = key_for(normalized_op, subject, mandate["version"])

    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    blocked_keys = {item.get("direction_key"): item for item in _blocked_directions(decisions)}
    dkey = direction_key or package_direction
    if not dkey and change:
        dkey = globals()["direction_key"](change_kind, subject)
    for candidate in (dkey, "wp:%s" % work_package if work_package else None):
        if candidate and candidate in blocked_keys:
            raise GovernanceError(4, "direction is rejected: %s" % candidate)

    absolute = None
    scope_root = os.path.realpath(project)
    relative = None
    if normalized_op in PATH_OPS:
        if not path:
            raise GovernanceError(2, "%s requires --path" % normalized_op)
        absolute, scope_root, relative = _resolve_path(project, path)
        governance_root = os.path.realpath(os.path.join(scope_root, GOV))
        iteration_root = os.path.realpath(os.path.join(scope_root, "_work", "iteration"))
        if _is_under(absolute, governance_root) or _is_under(absolute, iteration_root):
            raise GovernanceError(4, "protected governance/iteration state is never writable")
        scope = _scope(mandate)
        deny = scope.get("deny_paths") or []
        if any(_path_subset(project, path, [value]) for value in deny):
            raise GovernanceError(4, "path is denied by the mandate")
        if package:
            outputs = _package_outputs(package)
            if not outputs or not _path_subset(project, path, outputs):
                raise GovernanceError(4, "path is outside the work-package output")

    control_op = normalized_op in L1_OPS or normalized_op in L2_OPS
    if control_op:
        grant_chain, has_grant = [], False
    else:
        grant_chain, has_grant = _select_grant(project, agent, normalized_op, absolute, model)
    # The current mandate is re-applied even when a grant exists: a mandate narrowing must
    # invalidate the wider parts of an older grant (IR-05).
    scope = _scope(mandate)
    if absolute is not None and not _path_allowed(absolute, project,
                                                  scope.get("paths") or ["."]):
        raise GovernanceError(4, "path is outside the mandate")
    if not _model_allowed(model, scope.get("models") or []):
        raise GovernanceError(4, "model is outside the mandate")
    allowed_ops = scope.get("ops") or []
    if allowed_ops and normalized_op not in allowed_ops:
        raise GovernanceError(4, "operation is outside the mandate")

    reservation = reserve(project, agent, grant_chain, calls,
                          request_id or _new_id("CALL"), normalized_op)
    info = _guard_result(
        0, "allowed", level=level, key=key,
        decision_id=decision.get("id") if decision else None,
        scope_root=scope_root, mode=control.get("mode"),
        mandate_hash=mandate["mandate_hash"], reservation_id=reservation,
        work_package=work_package, direction_key=dkey, path=relative,
    )
    _event(project, "authority-guard", mandate, op=normalized_op, allowed=True,
           code=0, level=level, key=key, decision_id=info.get("decision_id"),
           reservation_id=reservation, agent=agent, model=model)
    return 0, info


def guard(project, op, **kwargs):
    """Integration guard that always returns ``(code, info)``."""
    try:
        return _guard_impl(project, op, **kwargs)
    except GovernanceError as exc:
        mandate, control, _problem = load_governance(project)
        info = _guard_result(exc.code, exc.message,
                             mode=(control or {}).get("mode"),
                             mandate_hash=(mandate or {}).get("mandate_hash"))
        if mandate is not None:
            _event(project, "authority-guard", mandate, op=_kind(op), allowed=False,
                   code=exc.code, reason=exc.message)
            _event(project, "authority-block", mandate, op=_kind(op), code=exc.code,
                   reason=exc.message)
        return exc.code, info
    except Exception as exc:  # noqa: BLE001
        info = _guard_result(3, "authority internal/state failure: %s" % exc)
        return 3, info


def record_call(project, info, request_id):
    """Record integration metadata without changing a prior reservation."""
    mandate, _control, problem = load_governance(project)
    if problem:
        raise GovernanceError(3, problem)
    if mandate is not None:
        _event(project, "authority-guard", mandate, request_id=request_id,
               reservation_id=(info or {}).get("reservation_id"), recorded=True)


def _open_requests(project):
    return [item for item in _requests(project) if item.get("status") == "open"]


def _withdraw_open(project):
    count = 0
    for item in _open_requests(project):
        item["status"] = "withdrawn"
        item["withdrawn_at"] = common.now_iso()
        atomic_json(_request_path(project, item["id"]), item)
        count += 1
    rebuild_requests_md(project)
    return count


def _mode_status(project):
    mandate, control, problem = load_governance(project)
    if problem:
        raise GovernanceError(3, problem)
    if mandate is None:
        return {"message": "governance disabled", "disabled": True,
                "enabled": False, "paused": False, "mode": "disabled"}
    grants = fold_grants(read_jsonl(_path(project, GRANTS)))
    active = []
    for item in grants:
        try:
            _active_chain({row["id"]: row for row in grants}, item["id"])
            active.append(item["id"])
        except GovernanceError:
            pass
    checkpoint = common.read_json(_path(project, CHECKPOINT))
    return {
        "message": "governance status",
        "enabled": bool(control.get("enabled")), "paused": bool(control.get("paused")),
        "mode": control.get("mode"), "mandate_version": mandate.get("version"),
        "mandate_hash": mandate.get("mandate_hash"),
        "open_requests": [row["id"] for row in _open_requests(project)],
        "active_grants": active, "checkpoint": checkpoint,
    }


def command_mode(project, args):
    if args.mode_action == "status":
        return _mode_status(project)
    mandate, control = _require_state(project)
    if args.by != "user":
        raise GovernanceError(4, "only an attested user may change governance mode")
    if not args.reason:
        raise GovernanceError(2, "--reason is required")
    approval = None
    if args.mode_action != "off" and args.approval_ref:
        approval = record_approval(project, args.approval_ref, args.approval_token,
                                   "mode-%s-%s" % (args.mode_action, _new_id("A")))
    if args.mode_action == "off":
        if not args.safe_stop:
            raise GovernanceError(2, "off requires --safe-stop")
        if args.safe_stop == "complete" and _open_requests(project):
            raise GovernanceError(4, "safe-stop complete requires no open requests")
        approval = record_approval(project, args.approval_ref, args.approval_token,
                                   "mode-off-%s" % _new_id("A"))
        if args.safe_stop == "abort":
            _withdraw_open(project)
        control.update({"enabled": False, "paused": args.safe_stop == "pause",
                        "mode": "suspended", "safe_stop": args.safe_stop})
        if args.safe_stop == "pause":
            _write_halt(project, args.reason)
    elif args.mode_action == "pause":
        control.update({"enabled": True, "paused": True, "mode": "enforce",
                        "safe_stop": "pause"})
        _write_halt(project, args.reason)
    elif args.mode_action in ("resume", "on"):
        control.update({"enabled": True, "paused": False, "mode": "enforce",
                        "safe_stop": None})
        try:
            os.remove(_path(project, HALT))
        except FileNotFoundError:
            pass
    control.update({"updated_at": common.now_iso(), "updated_by": "user",
                    "reason": args.reason, "approval_ref": approval,
                    "rules_version": RULES_VERSION})
    atomic_json(_path(project, CONTROL), control)
    pin_governance(project, True, mandate["mandate_hash"])
    _event(project, "authority-mode", mandate, action=args.mode_action,
           safe_stop=args.safe_stop, identity="attested")
    return {"message": "mode %s" % args.mode_action, "control": control}


def _write_halt(project, reason):
    path = _path(project, HALT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("paused_at: %s\nreason: %s\n" % (common.now_iso(), reason))


def command_checkpoint(project, args):
    mandate, _control = _require_state(project)
    plan = _plan_path(project)
    plan_hash = common.sha256_file(plan) if os.path.isfile(plan) else None
    grants = fold_grants(read_jsonl(_path(project, GRANTS)))
    active = []
    grant_map = {row["id"]: row for row in grants}
    for item in grants:
        try:
            _active_chain(grant_map, item["id"])
            active.append(item["id"])
        except GovernanceError:
            pass
    usage = read_jsonl(_path(project, USAGE))
    value = {
        "at": common.now_iso(), "phase": args.phase, "note": args.note,
        "mandate_hash": mandate["mandate_hash"], "plan_hash": plan_hash,
        "open_requests": [row["id"] for row in _open_requests(project)],
        "grants": active, "last_call": usage[-1].get("request_id") if usage else None,
    }
    atomic_json(_path(project, CHECKPOINT), value)
    return {"message": "checkpoint recorded", "checkpoint": value}


def command_resume(project, args):
    mandate, _control = _require_state(project)
    path = _path(project, CHECKPOINT)
    if not os.path.isfile(path):
        raise GovernanceError(4, "checkpoint does not exist")
    value = _read_json_strict(path, "checkpoint")
    if value.get("mandate_hash") != mandate.get("mandate_hash"):
        raise GovernanceError(3, "checkpoint mandate hash is stale")
    plan = _plan_path(project)
    if value.get("plan_hash"):
        if not os.path.isfile(plan) or common.sha256_file(plan) != value.get("plan_hash"):
            raise GovernanceError(3, "checkpoint plan hash is stale")
    phases = ["research", "plan", "produce", "review", "verify", "release"]
    phase = value.get("phase")
    if phase not in phases:
        raise GovernanceError(3, "checkpoint phase is invalid")
    if args.check:
        current_open = sorted(row["id"] for row in _open_requests(project))
        recorded_open = sorted(value.get("open_requests") or [])
        if current_open != recorded_open:
            raise GovernanceError(3, "checkpoint open requests changed; add a new checkpoint")
        usage = read_jsonl(_path(project, USAGE))
        current_last = usage[-1].get("request_id") if usage else None
        if current_last != value.get("last_call"):
            raise GovernanceError(3, "checkpoint call state changed; add a new checkpoint")
    index = phases.index(phase) + 1
    next_phase = phases[index] if index < len(phases) else "done"
    return {"message": "resume at %s" % next_phase, "next_phase": next_phase,
            "checkpoint": value, "checked": bool(args.check)}


def _approval_problems(project, ref, label):
    if not ref:
        return []
    if not isinstance(ref, dict):
        return ["%s approval reference is malformed" % label]
    path = ref.get("path")
    if not path:
        return ["%s approval path is missing" % label]
    absolute = path if os.path.isabs(path) else os.path.join(project, path)
    if not os.path.isfile(absolute):
        return ["%s approval file is missing" % label]
    if ref.get("sha256") and common.sha256_file(absolute) != ref.get("sha256"):
        return ["%s approval hash mismatch" % label]
    return []


def command_check(project, _args=None):
    mandate, control, problem = load_governance(project)
    if problem:
        raise GovernanceError(3, problem)
    if mandate is None:
        return {"message": "governance disabled", "disabled": True,
                "boundary": "deleting all governance state and the pin is outside enforcement"}
    problems = []
    notices = []
    _unused = control
    try:
        _validate_mandate_shape(mandate)
    except GovernanceError as exc:
        problems.append(exc.message)
    for relative in (DECISIONS, GRANTS):
        problems.extend(chain_check(_path(project, relative)))
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    grants = fold_grants(read_jsonl(_path(project, GRANTS)))
    grant_map = {row["id"]: row for row in grants}
    for item in grants:
        try:
            _grant_chain(grant_map, item["id"])
        except GovernanceError as exc:
            problems.append(exc.message)
        problems.extend(_approval_problems(project, item.get("approval_ref"),
                                           "grant %s" % item.get("id")))
        if (item.get("approval_ref") or {}).get("mode") == "unauthenticated":
            notices.append("grant %s uses unauthenticated attestation" % item.get("id"))
    decision_ids = {row.get("id") for row in decisions}
    for item in decisions:
        problems.extend(_approval_problems(project, item.get("approval_ref"),
                                           "decision %s" % item.get("id")))
        if (item.get("approval_ref") or {}).get("mode") == "unauthenticated":
            notices.append("decision %s uses unauthenticated attestation" % item.get("id"))
        if (item.get("level") == "L1" and _accepted(item)
                and item.get("kind") in ("release", "scope", "constraint", "target")
                and not item.get("artifact") and not item.get("commit")):
            problems.append("decision %s lacks artifact/commit binding" % item.get("id"))
    for request in _requests(project):
        if request.get("status") == "resolved" and request.get("decision_id") not in decision_ids:
            problems.append("request %s has a missing decision link" % request.get("id"))
    try:
        _agents(project)
    except GovernanceError as exc:
        problems.append(exc.message)
    if problems:
        raise GovernanceError(3, "; ".join(problems[:12]))
    return {"message": "governance state is valid", "problems": [],
            "notices": notices, "mandate_version": mandate["version"],
            "mandate_hash": mandate["mandate_hash"]}


def _similar_subjects(decisions):
    pairs = []
    values = []
    for item in decisions:
        subject = str(item.get("subject") or "").strip()
        if not subject:
            continue
        tokens = set(re.findall(r"[a-z0-9]+", subject.lower()))
        values.append((item.get("id"), subject, tokens))
    for index, left in enumerate(values):
        for right in values[index + 1:]:
            if left[0] == right[0] or not left[2] or not right[2]:
                continue
            overlap = len(left[2] & right[2]) / float(len(left[2] | right[2]))
            compact_left = re.sub(r"[^a-z0-9]", "", left[1].lower())
            compact_right = re.sub(r"[^a-z0-9]", "", right[1].lower())
            if overlap >= 0.5 or compact_left == compact_right:
                pairs.append({"left_id": left[0], "left": left[1],
                              "right_id": right[0], "right": right[1],
                              "score": round(overlap, 3)})
    return pairs


def _report(project):
    mandate, control = _require_state(project)
    decisions = fold_decisions(read_jsonl(_path(project, DECISIONS)))
    grants = fold_grants(read_jsonl(_path(project, GRANTS)))
    usage = read_jsonl(_path(project, USAGE))
    counts = {"level": {}, "status": {}, "kind": {}}
    independence = {}
    outcomes = 0
    for item in decisions:
        for bucket, value in (("level", item.get("level")),
                              ("status", item.get("status")),
                              ("kind", item.get("kind"))):
            if value:
                counts[bucket][value] = counts[bucket].get(value, 0) + 1
        if item.get("independence"):
            value = item["independence"]
            independence[value] = independence.get(value, 0) + 1
        outcomes += len(item.get("outcomes") or [])
    active, expired = [], []
    grant_map = {row["id"]: row for row in grants}
    for item in grants:
        try:
            _active_chain(grant_map, item["id"])
            active.append(item["id"])
        except GovernanceError:
            expired.append(item["id"])
    events = common.read_events(project)
    blocked = [row for row in events if row.get("kind") == "authority-block"]
    by_model = {}
    modes = {}
    for row in events:
        model = row.get("model")
        if model:
            by_model[model] = by_model.get(model, 0) + 1
        if row.get("kind") == "authority-mode":
            action = row.get("action") or "unknown"
            modes[action] = modes.get(action, 0) + 1
    return {
        "message": "governance report", "mandate_version": mandate["version"],
        "mandate_hash": mandate["mandate_hash"], "mode": control.get("mode"),
        "counts": counts, "accepted": counts["status"].get("accepted", 0),
        "rejected": counts["status"].get("rejected", 0),
        "modified": counts["status"].get("modified", 0),
        "intake": counts["kind"].get("intake", 0), "independence": independence,
        "rejected_directions": [{"id": row.get("id"),
                                  "key": row.get("direction_key")}
                                 for row in _blocked_directions(decisions)],
        "outcomes": outcomes, "open_requests": _open_requests(project),
        "active_grants": active, "inactive_grants": expired,
        "blocked_events": len(blocked),
        "usage": {"calls": sum(int(row.get("calls", 0)) for row in usage),
                  "reservations": len(usage)},
        "by_model": by_model, "modes": modes,
        "similar_subjects": _similar_subjects(decisions),
        "boundary": [
            "Host tool, shell, and non-cooperating plugin calls are not intercepted.",
            "Semantic paraphrases are reported heuristically, not prevented.",
            "Cost and token budgets are recorded when available but not enforced.",
            "Pause does not kill an in-flight call.",
        ],
    }


def _report_markdown(data):
    lines = ["# Governance report", "", "- Mandate version: %s" % data["mandate_version"],
             "- Mode: %s" % data["mode"],
             "- Open requests: %d" % len(data["open_requests"]),
             "- Active grants: %d" % len(data["active_grants"]),
             "- Reserved calls: %d" % data["usage"]["calls"], "",
             "## Decision counts", "", "| dimension | value | count |",
             "|---|---|---:|"]
    for dimension in ("level", "status", "kind"):
        for value, count in sorted(data["counts"][dimension].items()):
            lines.append("| %s | %s | %s |" % (dimension, value, count))
    lines.extend(["", "## Boundary", ""])
    lines.extend("- " + item for item in data["boundary"])
    return "\n".join(lines) + "\n"


def command_report(project, args):
    data = _report(project)
    if args.out:
        destination = args.out if os.path.isabs(args.out) else os.path.join(project, args.out)
        os.makedirs(os.path.dirname(os.path.abspath(destination)) or ".", exist_ok=True)
        with open(destination, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_report_markdown(data))
        data["out"] = os.path.abspath(destination)
    return data


def command_export(project, args):
    block = governance_block(project)
    if block is None:
        return {"disabled": True}
    if args.format == "json":
        return block
    lines = ["governance:"]
    for key in ("enabled", "mode", "paused", "mandate_version", "mandate_hash", "goal"):
        lines.append("  %s: %s" % (key, json.dumps(block.get(key), ensure_ascii=False)))
    for key in ("hard_constraints", "preferences", "accepted_decisions",
                "rejected_directions", "open_requests"):
        lines.append("  %s: %s" % (key, json.dumps(block.get(key), ensure_ascii=False)))
    return {"_text": "\n".join(lines) + "\n", "governance": block}


def command_approve_file(project, args):
    _require_state(project)
    label = os.path.basename(args.file) or _new_id("approval")
    approval = record_approval(project, args.file, args.token, label)
    return {"message": "approval recorded", "approval": approval}


def _add_json(parser):
    parser.add_argument("--json", action="store_true")


def build_parser():
    ap = argparse.ArgumentParser(description="human-governed decision authority")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("init", help="initialize governance state")
    p.add_argument("--goal", required=True)
    p.add_argument("--deliverable", default="")
    p.add_argument("--idea", action="extend", nargs="+", default=[])
    p.add_argument("--hard-constraint", "--constraint", "--hard", dest="hard_constraint",
                   action="extend", nargs="+", default=[])
    p.add_argument("--preference", action="extend", nargs="+", default=[])
    p.add_argument("--path", action="extend", nargs="+", default=[])
    p.add_argument("--deny-path", action="extend", nargs="+", default=[])
    p.add_argument("--model", action="extend", nargs="+", default=[])
    p.add_argument("--plugin", action="extend", nargs="+", default=[])
    p.add_argument("--op", action="extend", nargs="+", default=[])
    p.add_argument("--gpt-calls", type=int, default=None)
    p.add_argument("--minutes", type=int, default=None)
    p.add_argument("--strictness", choices=["low", "normal", "high"], default="normal")
    p.add_argument("--force", action="store_true")
    _add_json(p)

    p = sub.add_parser("mandate", help="show, check, or replace the mandate")
    nested = p.add_subparsers(dest="mandate_action", required=True)
    child = nested.add_parser("show")
    _add_json(child)
    child = nested.add_parser("check")
    _add_json(child)
    child = nested.add_parser("set")
    child.add_argument("--file", required=True)
    child.add_argument("--reason", required=True)
    child.add_argument("--by", required=True)
    child.add_argument("--approval-ref", required=True)
    child.add_argument("--approval-token", default=None)
    _add_json(child)

    p = sub.add_parser("intake", help="evaluate a human idea")
    p.add_argument("--summary", required=True)
    p.add_argument("--subject", default=None)
    p.add_argument("--idea-id", default=None)
    p.add_argument("--kind", choices=["idea", "direction", "requirement"], required=True)
    p.add_argument("--verdict", choices=["adopt", "modify", "reject"], required=True)
    p.add_argument("--assessment", required=True)
    p.add_argument("--evidence", action="extend", nargs="+", default=[])
    p.add_argument("--evidence-ref", action="extend", nargs="+", default=[])
    p.add_argument("--alternative", action="extend", nargs="+", default=[])
    p.add_argument("--direction-key", default=None)
    p.add_argument("--user-response", choices=["accept", "reject", "modify", "none"],
                   default="none")
    _add_json(p)

    p = sub.add_parser("classify", help="classify a declared decision kind")
    p.add_argument("--kind", required=True)
    p.add_argument("--strictness", choices=["low", "normal", "high"], default="normal")
    p.add_argument("--effect", choices=["network", "permission", "cost", "license", "none"],
                   default="none")
    _add_json(p)

    p = sub.add_parser("request", help="create a human decision request")
    p.add_argument("--kind", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--proposal", required=True)
    p.add_argument("--option", action="extend", nargs="+", default=[])
    p.add_argument("--subject", default=None)
    p.add_argument("--level", choices=["auto", "L1", "L2"], default="auto")
    p.add_argument("--effect", choices=["network", "permission", "cost", "license", "none"],
                   default="none")
    p.add_argument("--affected", action="extend", nargs="+", default=[])
    p.add_argument("--reopen", action="store_true")
    p.add_argument("--reason", default=None)
    p.add_argument("--evidence-ref", action="extend", nargs="+", default=[])
    p.add_argument("--batch", action="store_true")
    _add_json(p)

    p = sub.add_parser("pending", help="list open requests")
    p.add_argument("--level", choices=["L1", "L2", "L3"], default=None)
    _add_json(p)

    p = sub.add_parser("resolve", help="resolve an open request")
    p.add_argument("--request", required=True)
    p.add_argument("--decision", choices=["accept", "reject", "modify"], required=True)
    p.add_argument("--by", choices=["user", "agent"], required=True)
    p.add_argument("--note", default="")
    p.add_argument("--subject", default=None)
    p.add_argument("--direction-key", default=None)
    p.add_argument("--approval-ref", default=None)
    p.add_argument("--approval-token", default=None)
    p.add_argument("--artifact", default=None)
    p.add_argument("--commit", default=None)
    _add_json(p)

    p = sub.add_parser("decide", help="record a direct decision")
    p.add_argument("--kind", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--proposal", required=True)
    p.add_argument("--decision", choices=["accept", "reject", "modify"], required=True)
    p.add_argument("--by", choices=["user", "agent"], required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--subject", default=None)
    p.add_argument("--level", choices=["auto", "L1", "L2", "L3"], default="auto")
    p.add_argument("--effect", choices=["network", "permission", "cost", "license", "none"],
                   default="none")
    p.add_argument("--direction-key", default=None)
    p.add_argument("--supersedes", default=None)
    p.add_argument("--artifact", default=None)
    p.add_argument("--commit", default=None)
    p.add_argument("--approval-ref", default=None)
    p.add_argument("--approval-token", default=None)
    _add_json(p)

    p = sub.add_parser("explain", help="show a decision fold")
    p.add_argument("--id", required=True)
    _add_json(p)

    p = sub.add_parser("outcome", help="attach an observed result")
    p.add_argument("--id", required=True)
    p.add_argument("--verdict", choices=["met", "partial", "missed", "infeasible"],
                   required=True)
    p.add_argument("--observed", required=True)
    p.add_argument("--model", default=None)
    _add_json(p)

    p = sub.add_parser("grant", help="create a scoped authorization grant")
    p.add_argument("--to", required=True)
    p.add_argument("--ops", "--op", action="extend", nargs="+", default=[])
    p.add_argument("--paths", "--path", action="extend", nargs="+", default=[])
    p.add_argument("--models", "--model", action="extend", nargs="+", default=[])
    p.add_argument("--max-calls", type=int, default=None)
    p.add_argument("--expires", default=None)
    p.add_argument("--parent", default=None)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--approval-ref", required=True)
    p.add_argument("--approval-token", default=None)
    _add_json(p)

    p = sub.add_parser("revoke", help="revoke a grant")
    p.add_argument("--id", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    _add_json(p)

    p = sub.add_parser("register", help="register an agent")
    p.add_argument("--id", "--agent", dest="id", required=True)
    p.add_argument("--parent", default="lead")
    p.add_argument("--capability", action="append", default=[])
    _add_json(p)

    p = sub.add_parser("plugin", help="register or list cooperative plugins")
    nested = p.add_subparsers(dest="plugin_action", required=True)
    child = nested.add_parser("add")
    child.add_argument("--id", "--name", "--plugin", dest="id", required=True)
    child.add_argument("--op", action="append", default=[])
    child.add_argument("--capability", action="append", default=[])
    child.add_argument("--by", required=True)
    child.add_argument("--reason", required=True)
    child.add_argument("--approval-ref", required=True)
    child.add_argument("--approval-token", default=None)
    _add_json(child)
    child = nested.add_parser("list")
    _add_json(child)

    p = sub.add_parser("usage", help="show call reservations")
    _add_json(p)

    p = sub.add_parser("guard", help="authorize a controlled operation")
    p.add_argument("--op", required=True)
    p.add_argument("--path", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--agent", default="lead")
    p.add_argument("--plugin", default=None)
    p.add_argument("--work-package", default=None)
    p.add_argument("--change", default=None)
    p.add_argument("--direction-key", default=None)
    p.add_argument("--calls", type=int, default=1)
    p.add_argument("--subject", default=None)
    p.add_argument("--approval-ref", default=None)
    p.add_argument("--request-id", default=None)
    p.add_argument("--effect", choices=["network", "permission", "cost", "license", "none"],
                   default="none")
    p.add_argument("--quiet", action="store_true")
    _add_json(p)

    p = sub.add_parser("mode", help="inspect or change control mode")
    nested = p.add_subparsers(dest="mode_action", required=True)
    child = nested.add_parser("status")
    _add_json(child)
    for name in ("on", "pause", "resume", "off"):
        child = nested.add_parser(name)
        child.add_argument("--by", required=True)
        child.add_argument("--reason", required=True)
        child.add_argument("--approval-ref", default=None)
        child.add_argument("--approval-token", default=None)
        child.add_argument("--safe-stop", choices=["pause", "abort", "complete"], default=None)
        _add_json(child)

    p = sub.add_parser("checkpoint", help="save resumable governance state")
    p.add_argument("--phase", choices=["research", "plan", "produce", "review", "verify",
                                       "release"], required=True)
    p.add_argument("--note", default="")
    _add_json(p)

    p = sub.add_parser("resume", help="validate a checkpoint and show the next phase")
    p.add_argument("--check", action="store_true")
    _add_json(p)

    p = sub.add_parser("approve-file", help="store an approval artifact")
    p.add_argument("--file", required=True)
    p.add_argument("--token", default=None)
    _add_json(p)

    p = sub.add_parser("check", help="validate all governance state")
    _add_json(p)

    p = sub.add_parser("report", help="summarize decisions and authority usage")
    p.add_argument("--out", default=None)
    _add_json(p)

    p = sub.add_parser("export", help="export the governance envelope")
    p.add_argument("--format", choices=["json", "text"], default="json")
    _add_json(p)

    p = sub.add_parser("status", help="show compact governance status")
    _add_json(p)
    return ap


def _print_result(data, as_json=False):
    if isinstance(data, dict) and "_text" in data and not as_json:
        print(data["_text"], end="")
    elif as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, dict):
        if "message" not in data:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        print("[OK] %s" % data.get("message", "ok"))
        for key in ("enabled", "paused", "mode", "mandate_version", "mandate_hash",
                    "next_phase", "count", "total"):
            if key in data:
                print("%s: %s" % (key, data[key]))
        if "requests" in data:
            for item in data["requests"]:
                print("%s %s %s" % (item.get("id"), item.get("level"),
                                     item.get("topic") or item.get("subject")))
    else:
        print(data)


def main(argv=None):
    parser = build_parser()
    values = list(sys.argv[1:] if argv is None else argv)
    project_args = []
    index = 0
    while index < len(values):
        if values[index] == "--project" and index + 1 < len(values):
            project_args = values[index:index + 2]
            del values[index:index + 2]
            continue
        if values[index].startswith("--project="):
            project_args = [values.pop(index)]
            continue
        index += 1
    if project_args:
        values = project_args + values
    args = parser.parse_args(values)
    project = os.path.abspath(args.project)
    try:
        if args.action == "init":
            data = command_init(project, args)
        elif args.action == "mandate":
            data = command_mandate(project, args)
        elif args.action == "intake":
            data = command_intake(project, args)
        elif args.action == "classify":
            data = classify(args.kind, args.strictness, args.effect)
            data["message"] = "%s is %s" % (data["kind"], data["level"])
        elif args.action == "request":
            data = command_request(project, args)
        elif args.action == "pending":
            data = command_pending(project, args)
        elif args.action == "resolve":
            data = command_resolve(project, args)
        elif args.action == "decide":
            data = command_decide(project, args)
        elif args.action == "explain":
            data = command_explain(project, args)
        elif args.action == "outcome":
            data = command_outcome(project, args)
        elif args.action == "grant":
            data = command_grant(project, args)
        elif args.action == "revoke":
            data = command_revoke(project, args)
        elif args.action == "register":
            data = command_register(project, args)
        elif args.action == "plugin":
            data = command_plugin(project, args)
        elif args.action == "usage":
            data = command_usage(project, args)
        elif args.action == "guard":
            code, data = guard(project, args.op, path=args.path, model=args.model,
                               agent=args.agent, plugin=args.plugin,
                               work_package=args.work_package, change=args.change,
                               direction_key=args.direction_key, calls=args.calls,
                               subject=args.subject, approval_ref=args.approval_ref,
                               quiet=args.quiet, request_id=args.request_id,
                               effect=args.effect)
            if not args.quiet:
                if args.json:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
                else:
                    print("%s (%d): %s" % ("allow" if code == 0 else "deny",
                                            code, data.get("reason")))
            return code
        elif args.action == "mode":
            data = command_mode(project, args)
        elif args.action == "checkpoint":
            data = command_checkpoint(project, args)
        elif args.action == "resume":
            data = command_resume(project, args)
        elif args.action == "approve-file":
            data = command_approve_file(project, args)
        elif args.action == "check":
            data = command_check(project, args)
        elif args.action == "report":
            data = command_report(project, args)
        elif args.action == "export":
            data = command_export(project, args)
        else:
            data = _mode_status(project)
        detailed = args.action in ("explain",)
        _print_result(data, getattr(args, "json", False) or detailed)
        return 0
    except GovernanceError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"allowed": False, "code": exc.code, "reason": exc.message},
                             ensure_ascii=False, indent=2))
        else:
            print("[FAIL] %s" % exc.message, file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
