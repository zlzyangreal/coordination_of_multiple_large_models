#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Stable-drives-candidate iteration controller (v2.3.0).

One released tag V(N) stays the execution end. The next version V(N+1) is developed in an
independent git worktree, tested there, compared with a baseline recorded from V(N), accepted
by the user, and only then promoted. Promotion and rollback are commands with exit codes, not
a hand-edited version number.

The authoritative manifest lives in the *stable* workspace (`_work/iteration/iteration.json`).
The candidate carries only a one-way identity file (`_work/iteration/candidate.json`, written
by `init`); candidate code never writes the stable workspace.

Commands:
    python scripts/iteration.py init --candidate ../proj-candidate [--stable v2.2.0]
    python scripts/iteration.py status [--json]
    python scripts/iteration.py guard --path <p> --expect candidate
    python scripts/iteration.py baseline [-- python scripts/selftest_models.py]
    python scripts/iteration.py evidence [-- python scripts/selftest_models.py]
    python scripts/iteration.py compare [--accept-fewer-tests N --reason ...]
    python scripts/iteration.py approve --by <user> --note "..."
    python scripts/iteration.py promote [--smoke -- <cmd>] [--push]
    python scripts/iteration.py rollback [--to <ref>] [--yes]
    python scripts/iteration.py verify

Exit codes:
    0  ok
    1  runtime/command failure
    2  bad invocation or missing iteration/candidate evidence
    3  workspace identity, isolation or executor-snapshot violation
    4  a gate blocked the action (plan, evidence, comparison, approval, tag, dirty tree)
    5  promotion/rollback smoke failed; the previous stable state was restored
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402

ITER_DIR = os.path.join("_work", "iteration")
MANIFEST = os.path.join(ITER_DIR, "iteration.json")
STATE = os.path.join(ITER_DIR, "state.json")
BASELINE = os.path.join(ITER_DIR, "baseline.json")
COMPARISON = os.path.join(ITER_DIR, "comparison.json")
APPROVAL = os.path.join(ITER_DIR, "approval.json")
SNAPSHOTS = os.path.join(ITER_DIR, "snapshots")
SKILL = "SKILL.md"
README = "README.md"
VERSION_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
SNAPSHOT_FILES = (
    "_work/models/config.json",
    "_work/models/team.json",
    "_work/skill_pin.json",
    "_work/plan/planner.json",
    "_work/iteration/state.json",
    "_work/iteration/baseline.json",
    "_work/iteration/comparison.json",
    "_work/iteration/approval.json",
)


# --------------------------------------------------------------------------- small helpers
def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(cmd, cwd, timeout=1800):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def git(root, *args, timeout=60):
    return run(["git", "-C", root] + list(args), cwd=root, timeout=timeout)


def split_command(text):
    """Split a --smoke-command string, keeping quoted Python snippets intact."""
    import shlex  # noqa: PLC0415
    parts = shlex.split(text, posix=False)
    return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "'\""
            else part for part in parts]


def git_ok(root, *args):
    done = git(root, *args)
    return (done.stdout or "").strip() if done.returncode == 0 else None


def git_head(root):
    return git_ok(root, "rev-parse", "HEAD")


def git_branch(root):
    return git_ok(root, "rev-parse", "--abbrev-ref", "HEAD")


def is_dirty(root):
    out = git_ok(root, "status", "--porcelain")
    return bool(out)


def norm(path):
    # realpath resolves junctions/symlinks, so a junction inside the candidate cannot
    # masquerade as a separate workspace.
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def within(child, parent):
    child, parent = norm(child), norm(parent)
    return child == parent or child.startswith(parent + os.sep)


def trusted(root):
    """Gates must run from the stable workspace's own scripts, never the candidate's copy."""
    if norm(HERE) != norm(os.path.join(root, "scripts")):
        print("[FAIL] run scripts/iteration.py from the stable workspace, not the candidate")
        print(f"       executing: {HERE}")
        print(f"       stable   : {os.path.join(root, 'scripts')}")
        return False
    return True


def rel_or_abs(path, base):
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:  # different drives on Windows
        return os.path.abspath(path).replace(os.sep, "/")


def version_tuple(value):
    parts = []
    for piece in str(value or "").split("."):
        match = re.match(r"(\d+)", piece)
        parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def bump_minor(version):
    major, minor, _patch = version_tuple(version)
    return f"{major}.{minor + 1}.0"


def parse_skill_version(text):
    match = re.search(r"^\s*version:\s*(\S+)\s*$", text, re.M)
    return match.group(1) if match else None


def parse_rule_count(text):
    numbers = []
    inside = False
    for line in text.splitlines():
        if re.match(r"^##\s+Non-negotiables", line):
            inside = True
            continue
        if inside and re.match(r"^##\s+", line):
            break
        if inside:
            match = re.match(r"^(\d+)\.\s+\*\*", line)
            if match:
                numbers.append(int(match.group(1)))
    return max(numbers) if numbers else 0


def parse_counts(text):
    match = re.search(r"(\d+) passed,\s*(\d+) failed", text or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def repo_root(start):
    out = git_ok(os.path.abspath(start), "rev-parse", "--show-toplevel")
    return out if out else os.path.abspath(start)


def load_manifest(root):
    data = read_json(os.path.join(root, MANIFEST))
    return data if isinstance(data, dict) else None


def save_manifest(root, manifest):
    manifest["updated_at"] = now()
    write_json(os.path.join(root, MANIFEST), manifest)


def load_json_file(root, rel, default=None):
    return read_json(os.path.join(root, rel), default)


def state_default(root):
    state = load_json_file(root, STATE, {}) or {}
    return state.get("default_executor") or {}


def write_state(root, version, commit, tag, reason=""):
    state = load_json_file(root, STATE, {}) or {}
    state.update({
        "schema": 1,
        "default_executor": {"version": version, "commit": commit, "tag": tag},
        "updated_at": now(),
        "reason": reason,
    })
    write_json(os.path.join(root, STATE), state)


def executor_snapshot(root):
    trees = {}
    for folder, suffix in (("scripts", ".py"), ("references", ".md"),
                           ("assets", (".json", ".md", ".yaml", ".yml"))):
        files = {}
        directory = os.path.join(root, folder)
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                if name.endswith(suffix) and os.path.isfile(path):
                    files[name] = sha256_file(path)
        trees[folder] = files
    config = os.path.join(root, "_work", "models", "config.json")
    skill = os.path.join(root, SKILL)
    readme = os.path.join(root, README)

    def digest(path):
        return sha256_file(path) if os.path.isfile(path) else None

    return {
        "skill_sha256": digest(skill),
        "readme_sha256": digest(readme),
        "scripts": trees["scripts"],
        "references": trees["references"],
        "assets": trees["assets"],
        "config_sha256": digest(config),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def sync_orchestrator(manifest, root, version, tag, commit):
    """Point the manifest at the code that is actually live in the stable root."""
    snapshot = executor_snapshot(root)
    try:
        rules = parse_rule_count(read_text(os.path.join(root, SKILL)))
    except OSError:
        rules = None
    orchestrator = manifest.setdefault("orchestrator", {})
    orchestrator.update({
        "role": "stable",
        "version": version,
        "tag": tag,
        "commit": commit,
        "root": root,
        "workspace": common.workspace_identity(root),
        "executor": snapshot,
        "capabilities": sorted(snapshot.get("scripts") or {}),
        "rules": rules,
    })
    return orchestrator


def changed_executor(manifest, root):
    """Files that changed in the stable executor since `init` (empty = still pinned)."""
    pinned = (manifest.get("orchestrator") or {}).get("executor") or {}
    current = executor_snapshot(root)
    changed = []
    for key, label in (("skill_sha256", SKILL), ("readme_sha256", README)):
        if pinned.get(key) != current.get(key):
            changed.append(label + ("" if current.get(key) else " (missing or removed)"))
    for folder in ("scripts", "references", "assets"):
        before = pinned.get(folder) or {}
        after = current.get(folder) or {}
        for name, digest in before.items():
            if after.get(name) is None:
                changed.append(f"{folder}/{name} (removed)")
            elif after.get(name) != digest:
                changed.append(f"{folder}/{name}")
        for name in after:
            if name not in before:
                changed.append(f"{folder}/{name} (new)")
    if pinned.get("config_sha256") and pinned["config_sha256"] != current["config_sha256"]:
        changed.append("_work/models/config.json")
    return sorted(set(changed))


def candidate_root(manifest):
    return (manifest.get("target") or {}).get("root")


def locate(start="."):
    """(stable_root, manifest) when an iteration exists, else (None, None)."""
    start = os.path.abspath(start)
    manifest = load_manifest(start)
    if manifest:
        return start, manifest
    record = common.candidate_record(start)
    if isinstance(record, dict) and record.get("stable_root"):
        stable = os.path.abspath(record["stable_root"])
        manifest = load_manifest(stable)
        if manifest:
            return stable, manifest
    return None, None


def identity_problems(root, manifest):
    """Workspace/executor checks that must hold before any gate or promotion step."""
    problems = []
    orch = manifest.get("orchestrator") or {}
    if norm((orch.get("root") or "")) != norm(root):
        problems.append(f"manifest belongs to {orch.get('root')}, not {root}")
    current = common.workspace_identity(root)
    if str((orch.get("workspace") or {}).get("volume")) != str(current.get("volume")):
        problems.append("stable volume changed since init")
    stage = manifest.get("stage")
    head = git_head(root)
    if stage == "development":
        if head != orch.get("commit"):
            problems.append(f"stable HEAD {str(head)[:12]} != pinned {str(orch.get('commit'))[:12]} "
                            f"during development (no hot switch)")
        changed = changed_executor(manifest, root)
        if changed:
            problems.append("stable executor changed after init: " + ", ".join(changed[:8]))
    elif stage == "promoted":
        want = (manifest.get("target") or {}).get("commit")
        if want and head != want:
            problems.append(f"stable HEAD {str(head)[:12]} != promoted {str(want)[:12]}")
    target = manifest.get("target") or {}
    cand = target.get("root")
    if not cand:
        problems.append("target root missing from the manifest")
        return problems
    if within(cand, root) or within(root, cand):
        problems.append("candidate and stable workspaces overlap")
    if not os.path.isdir(cand):
        problems.append(f"candidate workspace missing: {cand}")
        return problems
    cand_head = git_head(cand)
    if not cand_head:
        problems.append(f"candidate is not a git worktree: {cand}")
    elif common.git_common_dir(cand) != common.git_common_dir(root):
        problems.append("candidate does not share the stable repository")
    branch = git_branch(cand)
    if target.get("branch") and branch != target["branch"]:
        problems.append(f"candidate branch {branch!r} != {target['branch']!r}")
    record = common.candidate_record(cand)
    if not record or record.get("iteration_id") != manifest.get("iteration_id"):
        problems.append("candidate identity file is missing or for another iteration")
    return problems


def die(messages, code=3):
    for message in messages:
        print(f"[FAIL] {message}", file=sys.stderr)
    return code


def render_manifest(manifest):
    orch = manifest.get("orchestrator") or {}
    target = manifest.get("target") or {}
    cand = target.get("root") or "?"
    cand_head = git_head(cand) if os.path.isdir(cand) else None
    stage = manifest.get("stage")
    print(f"iteration : {manifest.get('iteration_id')} ({stage})")
    print(f"stable    : v{orch.get('version')} @ {str(orch.get('commit'))[:12]} "
          f"tag {orch.get('tag')} root {orch.get('root')}")
    print(f"candidate : v{target.get('version')} @ {str(cand_head)[:12]} "
          f"base {str(target.get('base_commit'))[:12]} branch {target.get('branch')}")
    print(f"target    : {cand}")
    gates = manifest.get("gates") or {}
    summary = []
    for name in ("baseline", "evidence", "comparison", "plan"):
        gate = gates.get(name)
        if not gate:
            summary.append(f"{name}=pending")
        elif name == "comparison":
            summary.append(f"{name}={gate.get('verdict')}")
        else:
            summary.append(f"{name}={'ok' if gate.get('ok') else 'blocked'}")
    if os.path.isfile(os.path.join((orch.get("root") or "."), APPROVAL)):
        summary.append("approval=recorded")
    else:
        summary.append("approval=pending")
    print("gates     : " + " | ".join(summary))


# --------------------------------------------------------------------------- init
def cmd_init(args):
    root = repo_root(args.root)
    if not trusted(root):
        return 2
    if load_manifest(root) and not args.force:
        print(f"[FAIL] iteration manifest already exists: {os.path.join(root, MANIFEST)}")
        print("       use status/verify to inspect it, or --force to re-initialise")
        return 2
    if is_dirty(root):
        print("[FAIL] stable working tree is not clean; commit or stash before init")
        return 4
    stable = args.stable
    head = git_head(root)
    if not head:
        print("[FAIL] not a git repository")
        return 2
    if stable:
        if not VERSION_TAG.match(stable):
            stable = "v" + stable
        if not VERSION_TAG.match(stable):
            print(f"[FAIL] --stable must be a version tag like v2.2.0: {stable!r}")
            return 2
        stable_commit = git_ok(root, "rev-parse", f"refs/tags/{stable}^{{commit}}")
        if not stable_commit:
            print(f"[FAIL] tag {stable} does not exist")
            return 2
        tag = stable
    else:
        candidates = []
        for tag in (git_ok(root, "tag", "--points-at", "HEAD") or "").splitlines():
            match = VERSION_TAG.match(tag.strip())
            if match:
                candidates.append((version_tuple(match.group(1)), match.group(1), tag.strip()))
        if not candidates:
            print("[FAIL] HEAD is not a released version tag; pass --stable vX.Y.Z")
            return 2
        _value, version, tag = max(candidates)
        stable_commit = head
    version = tag[1:] if tag.startswith("v") else tag
    stable_commit = stable_commit or head
    ancestor = git(root, "merge-base", "--is-ancestor", stable_commit, head)
    if ancestor.returncode != 0:
        print(f"[FAIL] {tag} is not an ancestor of HEAD; the stable end must be released")
        return 4
    if head != stable_commit:
        print(f"[FAIL] stable workspace HEAD is {str(head)[:12]}, not {tag} "
              f"({str(stable_commit)[:12]})")
        print("       check out the released tag before init; the stable end must not move")
        return 4
    skill_text = git_ok(root, "show", f"{stable_commit}:{SKILL}")
    if skill_text is None:
        print(f"[FAIL] {SKILL} is not in {tag}")
        return 2
    declared = parse_skill_version(skill_text)
    if declared and version_tuple(declared) != version_tuple(version):
        print(f"[FAIL] tag {tag} says v{version}, {SKILL} at that commit says v{declared}")
        return 4

    target_version = args.version or bump_minor(version)
    if version_tuple(target_version) <= version_tuple(version):
        print(f"[FAIL] target version {target_version} must be newer than {version}")
        return 2
    branch = args.branch or f"iteration/v{target_version}"
    cand = os.path.abspath(args.candidate)
    if not cand:
        print("[FAIL] --candidate is required (an independent worktree path)")
        return 2
    if within(cand, root) or within(root, cand):
        print("[FAIL] candidate must be a sibling workspace, not inside the stable tree")
        return 2

    created = False
    if os.path.isdir(cand) and os.listdir(cand):
        cand_head = git_head(cand)
        if cand_head is None:
            print(f"[FAIL] candidate exists and is not a git worktree: {cand}")
            return 2
        if common.git_common_dir(cand) != common.git_common_dir(root):
            print("[FAIL] candidate belongs to a different repository")
            return 2
        if git_branch(cand) != branch:
            print(f"[FAIL] candidate branch {git_branch(cand)!r} != {branch!r}")
            return 2
        if cand_head != stable_commit and not args.reuse:
            print(f"[FAIL] existing candidate is at {cand_head[:12]}, not {tag}; "
                  f"pass --reuse if that is intentional")
            return 2
    else:
        if os.path.isdir(cand):
            os.rmdir(cand)  # git worktree add wants a missing or empty path
        os.makedirs(os.path.dirname(cand), exist_ok=True)
        if git_ok(root, "rev-parse", "--verify", f"refs/heads/{branch}"):
            done = git(root, "worktree", "add", cand, branch)
            if done.returncode == 0:
                cand_head = git_ok(cand, "rev-parse", "HEAD")
                if cand_head != stable_commit and not args.reuse:
                    git(root, "worktree", "remove", "--force", cand)
                    print(f"[FAIL] branch {branch} is not at {tag}; pass --reuse to reuse it")
                    return 2
        else:
            done = git(root, "worktree", "add", "-b", branch, cand, stable_commit)
        if done.returncode != 0:
            print("[FAIL] git worktree add failed:")
            print((done.stderr or done.stdout).strip(), file=sys.stderr)
            return 1
        created = True

    executor = executor_snapshot(root)
    workspace = common.workspace_identity(root)
    target_workspace = common.workspace_identity(cand)
    iteration_id = args.id or ("ITER-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    manifest = {
        "schema": 1,
        "iteration_id": iteration_id,
        "created_at": now(),
        "stage": "development",
        "orchestrator": {
            "role": "stable",
            "version": version,
            "tag": tag,
            "commit": stable_commit,
            "root": root,
            "workspace": workspace,
            "executor": executor,
            "capabilities": sorted(executor["scripts"]),
            "rules": parse_rule_count(skill_text),
        },
        "target": {
            "role": "candidate",
            "version": target_version,
            "branch": branch,
            "base_commit": stable_commit,
            "root": cand,
            "workspace": target_workspace,
            "created_worktree": created,
        },
        "gates": {},
    }
    write_json(os.path.join(root, MANIFEST), manifest)
    write_json(os.path.join(cand, common.CANDIDATE_RECORD), {
        "schema": 1,
        "iteration_id": iteration_id,
        "role": "candidate",
        "stable_root": root,
        "stable_version": version,
        "stable_commit": stable_commit,
        "target_version": target_version,
        "base_commit": stable_commit,
        "created_at": now(),
    })
    write_state(root, version, stable_commit, tag, "iteration init")
    common.append_event(root, "iteration-init", iteration=iteration_id, stable=stable_commit,
                        candidate=cand, branch=branch, target_version=target_version)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"[OK] iteration {iteration_id}: stable v{version} ({tag}) -> "
              f"candidate v{target_version}")
        print(f"     stable    : {root}")
        print(f"     candidate : {cand} ({branch})")
        print(f"     next      : develop in the candidate, then `iteration.py evidence`, "
              f"`compare`, `approve`, `promote`")
    return 0


# --------------------------------------------------------------------------- status / guard
def cmd_status(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest under _work/iteration/")
        return 2
    problems = identity_problems(root, manifest)
    if args.json:
        print(json.dumps({"root": root, "manifest": manifest, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        render_manifest(manifest)
        for problem in problems:
            print(f"[FAIL] {problem}")
        if not problems:
            print("identity  : stable and candidate pinned; executor unchanged")
    return 3 if problems else 0


def cmd_verify(args):
    return cmd_status(args)


def cmd_guard(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    path = os.path.abspath(args.path)
    target = candidate_root(manifest)
    stable = (manifest.get("orchestrator") or {}).get("root") or root
    owner = None
    if target and within(path, target):
        owner = "candidate"
    elif within(path, stable):
        owner = "stable"
    if owner is None:
        print(f"[FAIL] {path} belongs to neither workspace")
        return 4
    if args.expect and owner != args.expect:
        print(f"[FAIL] {path} is in the {owner} workspace, expected {args.expect}")
        return 4
    print(f"[OK] {path} -> {owner}")
    return 0


# --------------------------------------------------------------------------- evidence
def _metrics(root):
    attempts = common.all_attempts(root)
    durations, ok = [], 0
    tokens = 0
    for item in attempts:
        if item.get("status") == "ok":
            ok += 1
        try:
            duration = float(item.get("duration_s"))
        except (TypeError, ValueError):
            continue
        if duration >= 0:
            durations.append(duration)
        raw = item.get("raw") or {}
        usage = raw.get("usage") or raw.get("tokens") or {}
        if isinstance(usage, dict):
            value = (usage.get("total_tokens") or usage.get("total")
                     or usage.get("output_tokens"))
            if isinstance(value, (int, float)):
                tokens += int(value)
    durations.sort()

    def percentile(fraction):
        if not durations:
            return None
        index = min(len(durations) - 1, int(round((len(durations) - 1) * fraction)))
        return round(durations[index], 3)

    return {
        "calls": len(attempts),
        "success_rate": round(ok / len(attempts), 3) if attempts else None,
        "p50_duration_s": percentile(0.5),
        "p95_duration_s": percentile(0.95),
        "tokens": tokens or None,
        "cost": "unknown",
    }


def _run_and_log(project, argv, log_path, timeout=1800):
    started = now()
    began = time.time()
    try:
        done = run(argv, project, timeout=timeout)
        code, stdout, stderr = done.returncode, done.stdout or "", done.stderr or ""
    except OSError as exc:
        code, stdout, stderr = 127, "", f"{exc}\n"
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = f"timeout after {exc.timeout}s\n"
    duration = round(time.time() - began, 2)
    combined = stdout + ("\n[stderr]\n" + stderr if stderr else "")
    write_text(os.path.join(project, log_path), combined)
    passed, failed = parse_counts(combined)
    return {
        "command": " ".join(argv),
        "argv": argv,
        "exit_code": code,
        "duration_s": duration,
        "started_at": started,
        "finished_at": now(),
        "passed": passed,
        "failed": failed,
        "log_path": log_path.replace(os.sep, "/"),
        "log_sha256": sha256_file(os.path.join(project, log_path)),
    }


def cmd_baseline(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    problems = identity_problems(root, manifest)
    if problems:
        return die(problems)
    if is_dirty(root):
        print("[FAIL] stable tree is dirty; record the baseline from the exact stable commit")
        return 4
    argv = args.command or [sys.executable, "scripts/selftest_models.py"]
    result = _run_and_log(root, argv, os.path.join(ITER_DIR, "baseline.log"))
    record = {
        "schema": 1,
        "kind": "baseline",
        "iteration_id": manifest.get("iteration_id"),
        "created_at": now(),
        "stable": {"version": (manifest.get("orchestrator") or {}).get("version"),
                   "tag": (manifest.get("orchestrator") or {}).get("tag"),
                   "commit": (manifest.get("orchestrator") or {}).get("commit")},
        "tests": result,
        "metrics": _metrics(root),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "drive": (manifest.get("orchestrator") or {}).get("workspace", {})
                        .get("drive")},
        "capabilities": (manifest.get("orchestrator") or {}).get("capabilities") or [],
        "unknown": ["resource_usage", "cost"],
    }
    write_json(os.path.join(root, BASELINE), record)
    manifest.setdefault("gates", {})["baseline"] = {
        "ok": result["exit_code"] == 0 and result["failed"] == 0,
        "path": BASELINE, "sha256": sha256_file(os.path.join(root, BASELINE)),
        "commit": record["stable"]["commit"], "at": now(),
    }
    save_manifest(root, manifest)
    common.append_event(root, "iteration-baseline", iteration=manifest.get("iteration_id"),
                        commit=record["stable"]["commit"], passed=result["passed"],
                        exit_code=result["exit_code"])
    print(f"[{'OK' if manifest['gates']['baseline']['ok'] else 'FAIL'}] baseline: "
          f"{result['passed']} passed, {result['failed']} failed "
          f"(exit {result['exit_code']}) -> {os.path.join(root, BASELINE)}")
    return 0 if manifest["gates"]["baseline"]["ok"] else 1


def cmd_evidence(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    problems = identity_problems(root, manifest)
    if problems:
        return die(problems)
    cand = candidate_root(manifest)
    if is_dirty(cand):
        print("[FAIL] candidate tree is dirty; commit the tested state before recording evidence")
        print("       (evidence must name the exact commit it tested)")
        return 4
    name = args.name
    command = args.command or [sys.executable, "scripts/selftest_models.py"]
    record_rel = os.path.join("_work", "evidence", f"{name}.json")
    head_before = git_head(cand)
    tool = os.path.join(HERE, "test_evidence.py")
    argv = [sys.executable, tool, "record", "--name", name, "--project", cand,
            "--"] + command
    done = run(argv, cand)
    print((done.stdout or "").strip())
    if done.returncode != 0:
        print((done.stderr or "").strip(), file=sys.stderr)
    record_path = os.path.join(cand, record_rel)
    record = read_json(record_path)
    if not isinstance(record, dict):
        print(f"[FAIL] test_evidence did not write {record_path}", file=sys.stderr)
        return 1
    head = git_head(cand)
    ok = (done.returncode == 0 and record.get("exit_code") == 0
          and record.get("tests_failed") in (0, None)
          and str(record.get("git_head") or "") == str(head_before)
          and record.get("git_head_after", head_before) == head_before
          and head == head_before and not record.get("git_dirty")
          and not is_dirty(cand))
    manifest.setdefault("gates", {})["evidence"] = {
        "ok": ok,
        "path": rel_or_abs(record_path, cand),
        "sha256": sha256_file(record_path),
        "commit": head_before,
        "passed": record.get("tests_passed"),
        "at": now(),
    }
    save_manifest(root, manifest)
    common.append_event(root, "iteration-evidence", iteration=manifest.get("iteration_id"),
                        commit=head, ok=ok)
    if not ok:
        print(f"[FAIL] evidence does not bind candidate HEAD {str(head)[:12]}")
        return 4
    print(f"[OK] evidence {name}: candidate {str(head)[:12]} -> {record_path}")
    return 0


# --------------------------------------------------------------------------- comparison
def _comparison_body(report):
    body = dict(report)
    body.pop("report_sha256", None)
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seconds(value):
    if isinstance(value, (int, float)):
        return f"{value:.2f}s"
    return "unknown"


def _render_report(report):
    delta = report["tests"]["delta"]
    delta_text = f"{delta:+d}" if isinstance(delta, int) else "unknown"
    lines = [
        f"# Iteration comparison — {report['iteration_id']}",
        "",
        f"- verdict: **{report['verdict']}**",
        f"- stable: v{report['stable']['version']} @ {report['stable']['commit'][:12]} "
        f"({report['stable']['tag']})",
        f"- candidate: v{report['candidate']['version']} @ "
        f"{report['candidate']['commit'][:12]}",
        f"- tests: baseline {report['tests']['baseline']} -> candidate "
        f"{report['tests']['candidate']} ({delta_text})",
        f"- duration: {_seconds(report['tests']['candidate_duration_s'])} (baseline "
        f"{_seconds(report['tests']['baseline_duration_s'])})",        "",
        "## Capabilities",
        "",
        f"- added: {', '.join(report['capabilities']['added']) or '-'}",
        f"- removed: {', '.join(report['capabilities']['removed']) or '-'}",
        f"- changed: {', '.join(report['capabilities']['changed']) or '-'}",
        "",
        "## Gates",
        "",
        f"- docs: {'ok' if report['docs']['ok'] else 'blocked'} "
        f"({len(report['docs']['issues'])} issue(s))",
        f"- evidence commit: {report['candidate']['evidence_commit'][:12]}",
        f"- model metrics: {report['models']['status']}",
        "",
        "## Blocking reasons",
        "",
    ]
    lines += [f"- {reason}" for reason in report["reasons"]] or ["- none"]
    lines += ["", "## Warnings", ""]
    lines += [f"- {warning}" for warning in report["warnings"]] or ["- none"]
    lines += ["", "## Unknown metrics", ""]
    lines += [f"- {item}" for item in report["unknown"]] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def cmd_compare(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    problems = identity_problems(root, manifest)
    if problems:
        return die(problems)
    cand = candidate_root(manifest)
    target = manifest.get("target") or {}
    head = git_head(cand)
    baseline = read_json(os.path.join(root, args.baseline or BASELINE))
    if not isinstance(baseline, dict):
        print("[FAIL] no baseline report; run `iteration.py baseline` in the stable workspace")
        return 2
    baseline_gate = (manifest.get("gates") or {}).get("baseline") or {}
    baseline_file = os.path.join(root, args.baseline or BASELINE)
    if not baseline_gate.get("ok"):
        print("[FAIL] the baseline gate is missing or red; re-run `iteration.py baseline`")
        return 4
    if not os.path.isfile(baseline_file):
        print(f"[FAIL] baseline report not found: {baseline_file}")
        return 2
    if baseline_gate.get("sha256") != sha256_file(baseline_file):
        print("[FAIL] the baseline report changed after it was recorded")
        return 4
    if baseline.get("iteration_id") != manifest.get("iteration_id"):
        print("[FAIL] the baseline belongs to another iteration")
        return 4
    if (baseline.get("stable") or {}).get("commit") != \
            (manifest.get("orchestrator") or {}).get("commit"):
        print("[FAIL] the baseline was recorded on another stable commit")
        return 4
    if (baseline.get("tests") or {}).get("exit_code") != 0 or \
            (baseline.get("tests") or {}).get("failed") not in (0, None):
        print("[FAIL] the baseline suite is not green; fix the stable end first")
        return 4
    gate = (manifest.get("gates") or {}).get("evidence") or {}
    if not gate.get("ok") or not gate.get("path"):
        print("[FAIL] no trusted candidate evidence recorded by `iteration.py evidence`")
        print("       a file the candidate wrote itself is not evidence")
        return 2
    recorded = gate["path"]
    evidence_path = recorded if os.path.isabs(str(recorded)) \
        else os.path.join(cand, recorded)
    if args.evidence and norm(args.evidence) != norm(evidence_path):
        print("[FAIL] --evidence does not match the evidence the stable gate recorded")
        return 4
    if not os.path.isfile(evidence_path) or sha256_file(evidence_path) != gate.get("sha256"):
        print("[FAIL] the recorded evidence file changed or disappeared")
        return 4
    if gate.get("commit") != head:
        print(f"[FAIL] the trusted evidence names {str(gate.get('commit'))[:12]}, "
              f"candidate HEAD is {str(head)[:12]}")
        return 4
    evidence = read_json(evidence_path)
    if not isinstance(evidence, dict):
        print(f"[FAIL] no candidate evidence: {evidence_path}")
        return 2
    evidence_commit = str(evidence.get("git_head") or "")
    evidence_log = os.path.join(cand, str(evidence.get("log_path") or ""))
    reasons, warnings = [], []
    if evidence_commit != head:
        print(f"[FAIL] evidence names {evidence_commit[:12]}, candidate HEAD is {str(head)[:12]}")
        print("       re-record evidence for the current commit")
        return 4
    if evidence.get("git_dirty") or evidence.get("git_head_changed"):
        reasons.append("candidate evidence was recorded on a dirty or moving tree")
    if os.path.isfile(evidence_log) and \
            sha256_file(evidence_log) != str(evidence.get("log_sha256") or ""):
        reasons.append("candidate evidence log changed after it was recorded")
    if evidence.get("exit_code") != 0:
        reasons.append(f"candidate tests exited {evidence.get('exit_code')}")
    tests_passed, tests_failed = parse_counts(read_text(evidence_log)) if \
        os.path.isfile(evidence_log) else (None, None)
    if tests_failed:
        reasons.append(f"candidate suite reports {tests_failed} failure(s)")
    baseline_tests = baseline.get("tests") or {}
    baseline_passed = baseline_tests.get("passed")
    if baseline_passed is None:
        reasons.append("baseline has no parsed test count")
    if tests_passed is None:
        reasons.append("candidate evidence has no parsed test count")
    accepted_fewer = args.accept_fewer_tests
    if (baseline_passed is not None and tests_passed is not None
            and tests_passed < baseline_passed):
        if accepted_fewer is not None and tests_passed == accepted_fewer and args.reason:
            warnings.append(f"user-accepted test-count drop {baseline_passed} -> "
                            f"{tests_passed}: {args.reason}")
        else:
            reasons.append(f"test count regressed {baseline_passed} -> {tests_passed} "
                           f"(accept explicitly with --accept-fewer-tests {tests_passed} "
                           f"--reason ...)")

    candidate_scripts = {}
    directory = os.path.join(cand, "scripts")
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if name.endswith(".py") and os.path.isfile(path):
                candidate_scripts[name] = sha256_file(path)
    stable_scripts = (manifest.get("orchestrator") or {}).get("executor", {}).get("scripts") or {}
    added = sorted(set(candidate_scripts) - set(stable_scripts))
    removed = sorted(set(stable_scripts) - set(candidate_scripts))
    changed = sorted(name for name in set(candidate_scripts) & set(stable_scripts)
                     if candidate_scripts[name] != stable_scripts[name])
    accepted_removals = []
    for name in args.accept_removal:
        if name in removed and args.reason:
            accepted_removals.append(name)
            warnings.append(f"user-accepted removal of scripts/{name}: {args.reason}")
    remaining = [name for name in removed if name not in accepted_removals]
    if remaining:
        reasons.append("capabilities removed without a declared migration: " +
                       ", ".join(f"scripts/{name}" for name in remaining))

    skill_path = os.path.join(cand, SKILL)
    candidate_version = parse_skill_version(read_text(skill_path)) if \
        os.path.isfile(skill_path) else None
    if candidate_version != target.get("version"):
        reasons.append(f"candidate SKILL version {candidate_version!r} != target "
                       f"{target.get('version')!r}")

    import check_docs  # noqa: PLC0415
    doc_issues = check_docs.collect_issues(cand)
    doc_ok = not doc_issues
    for issue in doc_issues[:8]:
        reasons.append(f"docs {issue['code']}: {issue['message']}")

    baseline_duration = baseline_tests.get("duration_s")
    evidence_duration = evidence.get("duration_s")
    if isinstance(baseline_duration, (int, float)) and isinstance(evidence_duration, (int, float)) \
            and baseline_duration > 0 and evidence_duration > baseline_duration * 2 \
            and evidence_duration - baseline_duration > 5:
        warnings.append(f"candidate suite is {evidence_duration / baseline_duration:.1f}x "
                        f"slower ({baseline_duration:.1f}s -> {evidence_duration:.1f}s)")

    candidate_calls = _metrics(cand)
    models = {"status": "unknown", "candidate": candidate_calls,
              "baseline": baseline.get("metrics")}
    if candidate_calls.get("calls") and (baseline.get("metrics") or {}).get("calls"):
        models["status"] = "recorded"
        if candidate_calls.get("success_rate") is not None and \
                candidate_calls["success_rate"] < 1.0:
            warnings.append(f"candidate call success rate {candidate_calls['success_rate']}")
    report = {
        "schema": 1,
        "kind": "comparison",
        "iteration_id": manifest.get("iteration_id"),
        "created_at": now(),
        "verdict": "pass" if not reasons else "blocked",
        "stable": {"version": baseline.get("stable", {}).get("version"),
                   "commit": baseline.get("stable", {}).get("commit"),
                   "tag": baseline.get("stable", {}).get("tag")},
        "candidate": {"version": candidate_version, "commit": head,
                      "evidence": rel_or_abs(evidence_path, cand),
                      "evidence_commit": evidence_commit},
        "tests": {"baseline": baseline_passed, "candidate": tests_passed,
                  "delta": (tests_passed - baseline_passed)
                  if None not in (tests_passed, baseline_passed) else None,
                  "baseline_duration_s": baseline_duration,
                  "candidate_duration_s": evidence_duration},
        "capabilities": {"added": added, "removed": remaining, "changed": changed,
                         "accepted_removals": accepted_removals},
        "docs": {"ok": doc_ok, "issues": doc_issues},
        "models": models,
        "compatibility": {
            "python_same": (baseline.get("environment") or {}).get("python")
            == platform.python_version(),
            "platform_same": (baseline.get("environment") or {}).get("platform")
            == platform.platform(),
        },
        "reasons": reasons,
        "warnings": warnings,
        "accepted": {"fewer_tests": accepted_fewer, "removals": accepted_removals,
                     "reason": args.reason},
        "unknown": ["resource_usage", "cost"],
    }
    report["report_sha256"] = hashlib.sha256(_comparison_body(report).encode("utf-8")) \
        .hexdigest()
    out = os.path.join(root, COMPARISON)
    write_json(out, report)
    write_text(os.path.join(root, ITER_DIR, "comparison.md"), _render_report(report))
    manifest.setdefault("gates", {})["comparison"] = {
        "verdict": report["verdict"],
        "path": COMPARISON,
        "sha256": sha256_file(out),
        "candidate_commit": head,
        "at": now(),
    }
    save_manifest(root, manifest)
    common.append_event(root, "iteration-compare", iteration=manifest.get("iteration_id"),
                        commit=head, verdict=report["verdict"], reasons=len(reasons))
    if accepted_fewer is not None or accepted_removals:
        common.append_event(root, "iteration-override",
                            iteration=manifest.get("iteration_id"), commit=head,
                            fewer_tests=accepted_fewer, removals=accepted_removals,
                            reason=args.reason)
    print(_render_report(report))
    print(f"report: {out}")
    return 0 if report["verdict"] == "pass" else 4


# --------------------------------------------------------------------------- decisions / approve
def cmd_decide(args):
    """Record a major decision (options, reasons, who decided) instead of re-asking."""
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    if not str(args.topic).strip() or not str(args.decision).strip() or not str(args.by).strip():
        print("[FAIL] --topic, --decision and --by are required")
        return 2
    record = {
        "at": now(),
        "iteration_id": manifest.get("iteration_id"),
        "candidate_commit": git_head(candidate_root(manifest)),
        "topic": args.topic,
        "options": args.option,
        "decision": args.decision,
        "reason": args.reason,
        "by": args.by,
    }
    path = os.path.join(root, ITER_DIR, "decisions.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    common.append_event(root, "iteration-decision", iteration=manifest.get("iteration_id"),
                        topic=args.topic, by=args.by, decision=args.decision)
    print(f"[OK] decision recorded for {manifest.get('iteration_id')}: {args.topic} -> "
          f"{args.decision} ({args.by})")
    return 0


def cmd_approve(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    problems = identity_problems(root, manifest)
    if problems:
        return die(problems)
    comparison = read_json(os.path.join(root, COMPARISON))
    if not isinstance(comparison, dict) or comparison.get("verdict") != "pass":
        print("[FAIL] no passing comparison report; the user cannot accept an untested candidate")
        return 4
    head = git_head(candidate_root(manifest))
    if comparison.get("candidate", {}).get("commit") != head:
        print("[FAIL] comparison was computed for another candidate commit; re-run compare")
        return 4
    if not args.by or not str(args.by).strip():
        print("[FAIL] --by is required: record who accepted the candidate")
        return 2
    if str(args.by).strip() != "user":
        print("[FAIL] acceptance is an L1 user decision; `--by user` is the only accepted "
              "identity")
        return 4
    approval = {
        "schema": 1,
        "iteration_id": manifest.get("iteration_id"),
        "candidate_commit": head,
        "comparison_sha256": sha256_file(os.path.join(root, COMPARISON)),
        "by": str(args.by).strip(),
        "note": args.note,
        "at": now(),
    }
    write_json(os.path.join(root, APPROVAL), approval)
    manifest.setdefault("gates", {})["approval"] = {
        "ok": True, "by": approval["by"], "commit": head,
        "comparison_sha256": approval["comparison_sha256"], "at": now(),
    }
    save_manifest(root, manifest)
    common.append_event(root, "iteration-approve", iteration=manifest.get("iteration_id"),
                        commit=head, by=approval["by"])
    print(f"[OK] candidate {head[:12]} accepted by {approval['by']}")
    return 0


def _snapshot(root, manifest):
    iteration_id = manifest.get("iteration_id")
    directory = os.path.join(root, SNAPSHOTS, iteration_id)
    os.makedirs(os.path.join(directory, "files"), exist_ok=True)
    files = {}
    for rel in SNAPSHOT_FILES:
        source = os.path.join(root, rel)
        if not os.path.isfile(source):
            continue
        backup = os.path.join(directory, "files", rel)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copy2(source, backup)
        files[rel] = {"sha256": sha256_file(source), "backup": backup}
    stable = manifest.get("orchestrator") or {}
    data = {"schema": 1, "iteration_id": iteration_id, "created_at": now(),
            "stable": {"version": stable.get("version"), "tag": stable.get("tag"),
                       "commit": stable.get("commit")},
            "files": files}
    write_json(os.path.join(directory, "snapshot.json"), data)
    tag = "iteration-snapshot-" + re.sub(r"[^A-Za-z0-9_.-]", "-", str(iteration_id))
    if not git_ok(root, "rev-parse", "--verify", f"refs/tags/{tag}"):
        git(root, "tag", tag, stable.get("commit"))
    data["tag"] = tag
    write_json(os.path.join(directory, "snapshot.json"), data)
    return directory, data


def _restore_files(root, snapshot):
    for rel, info in (snapshot.get("files") or {}).items():
        backup = info.get("backup")
        if backup and os.path.isfile(backup):
            target = os.path.join(root, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(backup, target)


ROLLBACK_TEMPLATE = '''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generated by iteration.py; restores the pre-promotion stable state.

It uses only the standard library and never imports the promoted code, so it still works when
the new version is broken. Run: python <this file> --yes
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = __ROOT__
COMMIT = __COMMIT__
TAG = __TAG__
VERSION = __VERSION__
STATE_PATH = __STATE_PATH__
STATE = __STATE__
FILES = __FILES__
MANIFEST = __MANIFEST__
ITERATION = __ITERATION__


def main():
    if "--yes" not in sys.argv:
        print("refusing without --yes; this resets the stable tree to", COMMIT[:12])
        return 2
    status = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"],
                            capture_output=True, text=True)
    if status.stdout.strip():
        print("[FAIL] stable tree is dirty; commit or discard local changes first")
        return 4
    subprocess.run(["git", "-C", ROOT, "tag", "-f", "iteration-rollback-safety", "HEAD"],
                   capture_output=True, text=True)
    done = subprocess.run(["git", "-C", ROOT, "reset", "--hard", COMMIT],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print("[FAIL] git reset failed:", done.stderr.strip())
        return 1
    for rel, backup in FILES.items():
        if os.path.isfile(backup):
            target = os.path.join(ROOT, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(backup, target)
    with open(os.path.join(ROOT, STATE_PATH), "w", encoding="utf-8", newline="\\n") as fh:
        json.dump(STATE, fh, ensure_ascii=False, indent=2)
        fh.write("\\n")
    path = os.path.join(ROOT, MANIFEST)
    try:
        with open(path, encoding="utf-8-sig") as fh:
            manifest = json.load(fh)
        manifest["stage"] = "rolled_back"
        manifest["rolled_back_at"] = __NOW__
        manifest["rollback"] = {"by": "generated-shim", "to": COMMIT, "tag": TAG}
        orchestrator = manifest.setdefault("orchestrator", {})
        orchestrator["version"] = VERSION
        orchestrator["tag"] = TAG
        orchestrator["commit"] = COMMIT
        with open(path, "w", encoding="utf-8", newline="\\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\\n")
    except (OSError, ValueError):
        pass
    print("[OK] stable restored to", COMMIT[:12], "with state", STATE_PATH, "for", ITERATION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _rollback_shim(root, manifest, snapshot):
    stable = manifest.get("orchestrator") or {}
    iteration_id = manifest.get("iteration_id")
    files = {rel: info["backup"] for rel, info in (snapshot.get("files") or {}).items()
             if info.get("backup")}
    state = {"schema": 1,
             "default_executor": {"version": stable.get("version"),
                                  "commit": stable.get("commit"),
                                  "tag": stable.get("tag")},
             "updated_at": now(), "reason": "rollback shim"}
    text = (ROLLBACK_TEMPLATE
            .replace("__ROOT__", json.dumps(root))
            .replace("__COMMIT__", json.dumps(stable.get("commit")))
            .replace("__TAG__", json.dumps(stable.get("tag")))
            .replace("__VERSION__", json.dumps(stable.get("version")))
            .replace("__STATE_PATH__", json.dumps(STATE))
            .replace("__STATE__", json.dumps(state, ensure_ascii=False))
            .replace("__FILES__", json.dumps(files, ensure_ascii=False))
            .replace("__MANIFEST__", json.dumps(MANIFEST))
            .replace("__ITERATION__", json.dumps(iteration_id))
            .replace("__NOW__", json.dumps(now())))
    path = os.path.join(root, ITER_DIR,
                        f"rollback-{re.sub(r'[^A-Za-z0-9_.-]', '-', str(iteration_id))}.py")
    write_text(path, text)
    return path


def _smoke(root, argv, timeout=1800):
    try:
        done = run(argv, root, timeout=timeout)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except OSError as exc:
        return 127, f"{exc}\n"
    except subprocess.TimeoutExpired as exc:
        return 124, f"timeout after {exc.timeout}s\n"


def cmd_promote(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    problems = identity_problems(root, manifest)
    if problems:
        return die(problems)
    if manifest.get("stage") not in ("development", "rolled_back"):
        print(f"[FAIL] iteration stage is {manifest.get('stage')!r}, not promotable")
        return 4
    cand = candidate_root(manifest)
    target = manifest.get("target") or {}
    stable = manifest.get("orchestrator") or {}
    head = git_head(cand)
    if is_dirty(cand):
        print("[FAIL] candidate tree is dirty; commit everything the tests covered")
        return 4
    if is_dirty(root):
        print("[FAIL] stable tree is dirty; promotion would mix unrelated changes")
        return 4
    if git_head(root) != stable.get("commit"):
        print("[FAIL] stable HEAD moved during development; restore it before promotion")
        return 4

    approval = read_json(os.path.join(root, APPROVAL))
    comparison = read_json(os.path.join(root, COMPARISON))
    if not isinstance(comparison, dict) or comparison.get("verdict") != "pass":
        print("[FAIL] no passing comparison; run compare and close every blocking reason")
        return 4
    if comparison.get("candidate", {}).get("commit") != head:
        print("[FAIL] comparison does not cover the current candidate commit")
        return 4
    if not isinstance(approval, dict):
        print("[FAIL] the user has not accepted this candidate (`iteration.py approve --by ...`)")
        return 4
    if approval.get("by") != "user":
        print("[FAIL] the stored approval is not a user attestation; promotion is refused")
        return 4
    if approval.get("candidate_commit") != head or \
            approval.get("comparison_sha256") != sha256_file(os.path.join(root, COMPARISON)):
        print("[FAIL] approval does not match the current comparison/candidate")
        return 4
    evidence_gate = (manifest.get("gates") or {}).get("evidence") or {}
    recorded = evidence_gate.get("path")
    evidence_file = recorded if recorded and os.path.isabs(str(recorded)) \
        else os.path.join(cand, recorded or "")
    if not evidence_gate.get("ok") or evidence_gate.get("commit") != head \
            or not os.path.isfile(evidence_file) \
            or sha256_file(evidence_file) != evidence_gate.get("sha256"):
        print("[FAIL] the trusted candidate evidence is missing, stale or changed")
        return 4

    version = target.get("version")
    tag = f"v{version}"
    tag_commit = git_ok(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if not tag_commit:
        print(f"[FAIL] tag {tag} does not exist; release the tested candidate first:")
        print(f"       (in the candidate) python scripts/release.py --version {version} "
              f"--note \"...\" --no-push")
        return 4
    if tag_commit != head:
        print(f"[FAIL] tag {tag} points at {tag_commit[:12]}, candidate HEAD is {head[:12]}")
        return 4

    try:
        import authority  # noqa: PLC0415
        gcode, ginfo = authority.guard(root, "promote", subject=version,
                                       approval_ref=os.path.join(root, APPROVAL))
    except ImportError:
        markers = common.governance_markers(root)
        if markers.get("enabled") or markers.get("any_file"):
            print("[FAIL] governance is enabled but scripts/authority.py is unavailable")
            return 4
        gcode, ginfo = 0, {}
    if gcode != 0:
        print(f"[FAIL] authority ({gcode}): "
              f"{ginfo.get('reason') or ginfo.get('message')}")
        return 4

    import plan_gate  # noqa: PLC0415
    code, report = plan_gate.check_plan(root)
    if code != 0:
        print(f"[FAIL] plan gate blocked promotion ({code}): {report.get('message')}")
        return 4
    manifest.setdefault("gates", {})["plan"] = {
        "ok": True, "code": 0, "request_id": report.get("request_id"),
        "plan_hash": report.get("plan_hash"), "at": now(),
    }

    try:
        smoke_argv = split_command(args.smoke_command) if args.smoke_command \
            else [sys.executable, "scripts/selftest_models.py"]
    except ValueError as exc:
        print(f"[FAIL] cannot parse --smoke-command: {exc}")
        return 2
    snapshot_dir, snapshot = _snapshot(root, manifest)
    done = git(root, "merge", "--ff-only", head)
    if done.returncode != 0:
        print("[FAIL] fast-forward merge failed; stable state is unchanged")
        print((done.stderr or done.stdout).strip(), file=sys.stderr)
        return 4
    previous_orchestrator = dict(manifest.get("orchestrator") or {})
    try:
        smoke_code, smoke_output = _smoke(root, smoke_argv)
        smoke_error = None
    except Exception as exc:  # noqa: BLE001
        smoke_code, smoke_output, smoke_error = 127, "", str(exc)
    if smoke_code != 0:
        git(root, "reset", "--hard", stable.get("commit"))
        _restore_files(root, snapshot)
        manifest["orchestrator"] = previous_orchestrator
        write_state(root, stable.get("version"), stable.get("commit"), stable.get("tag"),
                    "promotion smoke failed; rolled back")
        manifest["stage"] = "rolled_back"
        manifest["rolled_back_at"] = now()
        manifest["rollback"] = {"by": "promote", "to": stable.get("commit"),
                                "reason": "post-promotion smoke failed"}
        save_manifest(root, manifest)
        common.append_event(root, "iteration-promote-failed",
                            iteration=manifest.get("iteration_id"), commit=head,
                            smoke_exit=smoke_code, error=smoke_error)
        print(f"[FAIL] promotion smoke failed ({smoke_code}); stable restored to "
              f"v{stable.get('version')} @ {str(stable.get('commit'))[:12]}")
        print(smoke_output[-2000:])
        if smoke_error:
            print(smoke_error)
        return 5

    try:
        write_state(root, version, head, tag, "iteration promotion")
        manifest["stage"] = "promoted"
        manifest["promoted_at"] = now()
        manifest["promotion"] = {
            "from": {"version": stable.get("version"), "tag": stable.get("tag"),
                     "commit": stable.get("commit")},
            "to": {"version": version, "commit": head, "tag": tag},
            "snapshot": rel_or_abs(snapshot_dir, root),
            "smoke": {"command": " ".join(smoke_argv), "exit_code": smoke_code},
        }
        shim = _rollback_shim(root, manifest, snapshot)
        sync_orchestrator(manifest, root, version, tag, head)
        (manifest.get("target") or {})["commit"] = head
        save_manifest(root, manifest)
        common.append_event(root, "iteration-promote", iteration=manifest.get("iteration_id"),
                            commit=head, version=version, snapshot=snapshot.get("tag"))
    except Exception as exc:  # noqa: BLE001
        git(root, "reset", "--hard", stable.get("commit"))
        _restore_files(root, snapshot)
        manifest["orchestrator"] = previous_orchestrator
        write_state(root, stable.get("version"), stable.get("commit"), stable.get("tag"),
                    "promotion post-step failed; rolled back")
        manifest["stage"] = "rolled_back"
        manifest["rolled_back_at"] = now()
        manifest["rollback"] = {"by": "promote", "to": stable.get("commit"),
                                "reason": f"post-promotion step failed: {exc}"}
        save_manifest(root, manifest)
        common.append_event(root, "iteration-promote-failed",
                            iteration=manifest.get("iteration_id"), commit=head,
                            error=str(exc))
        print(f"[FAIL] promotion post-step failed ({exc}); stable restored to "
              f"v{stable.get('version')} @ {str(stable.get('commit'))[:12]}")
        return 5
    print(f"[OK] promoted v{version} @ {head[:12]}; default executor is now v{version}")
    print(f"     snapshot : {snapshot_dir}")
    print(f"     rollback : python scripts/iteration.py rollback --yes")
    print(f"                or (standalone) python {shim}")
    if args.push:
        try:
            import authority  # noqa: PLC0415
            gcode, ginfo = authority.guard(root, "push", subject=version)
        except ImportError:
            markers = common.governance_markers(root)
            if markers.get("enabled") or markers.get("any_file"):
                print("[FAIL] governance is enabled but scripts/authority.py is "
                      "unavailable")
                return 4
            gcode, ginfo = 0, {}
        if gcode != 0:
            print(f"[FAIL] authority ({gcode}): "
                  f"{ginfo.get('reason') or ginfo.get('message')}")
            return 4
        for ref in ("HEAD", tag):
            pushed = git(root, "push", "origin", ref)
            if pushed.returncode != 0:
                print(f"[WARN] push {ref} failed; local promotion stands")
                print((pushed.stderr or pushed.stdout).strip(), file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- rollback
def cmd_rollback(args):
    root, manifest = locate(args.root)
    if not manifest:
        print("[FAIL] no iteration manifest")
        return 2
    stable = manifest.get("orchestrator") or {}
    promotion = manifest.get("promotion") or {}
    previous = promotion.get("from") or {}
    target_commit = None
    snapshot_dir = None
    if args.to:
        target_commit = git_ok(root, "rev-parse", f"{args.to}^{{commit}}")
        if not target_commit:
            print(f"[FAIL] cannot resolve --to {args.to!r}")
            return 2
    else:
        snapshot_rel = promotion.get("snapshot")
        if snapshot_rel:
            snapshot_dir = os.path.join(root, snapshot_rel)
            snapshot = read_json(os.path.join(snapshot_dir, "snapshot.json"))
            if isinstance(snapshot, dict):
                target_commit = (snapshot.get("stable") or {}).get("commit")
        target_commit = target_commit or previous.get("commit") or stable.get("commit")
    if not target_commit:
        print("[FAIL] no rollback target; pass --to <ref>")
        return 2
    if is_dirty(root) and not args.force:
        print("[FAIL] stable tree is dirty; commit/discard first or pass --force")
        return 4
    if not args.yes and not args.force:
        print("[FAIL] rollback resets the stable tree; re-run with --yes when that is intended")
        return 2
    git(root, "tag", "-f", "iteration-rollback-safety", "HEAD")
    done = git(root, "reset", "--hard", target_commit)
    if done.returncode != 0:
        print("[FAIL] git reset failed; state unchanged")
        print((done.stderr or done.stdout).strip(), file=sys.stderr)
        return 1
    if snapshot_dir:
        snapshot = read_json(os.path.join(snapshot_dir, "snapshot.json"))
        if isinstance(snapshot, dict) and \
                (snapshot.get("stable") or {}).get("commit") == target_commit:
            _restore_files(root, snapshot)
    old_version = previous.get("version")
    old_tag = previous.get("tag")
    if not old_version:
        try:
            old_version = parse_skill_version(read_text(os.path.join(root, SKILL)))
        except OSError:
            old_version = stable.get("version")
    if not old_tag:
        exact = git_ok(root, "describe", "--tags", "--exact-match", target_commit)
        old_tag = exact or stable.get("tag")
    sync_orchestrator(manifest, root, old_version, old_tag, target_commit)
    (manifest.get("target") or {})["commit"] = target_commit
    write_state(root, old_version, target_commit, old_tag, "iteration rollback")
    manifest["stage"] = "rolled_back"
    manifest["rolled_back_at"] = now()
    manifest["rollback"] = {"to": target_commit, "by": args.by or "iteration.py"}
    save_manifest(root, manifest)
    common.append_event(root, "iteration-rollback", iteration=manifest.get("iteration_id"),
                        to=target_commit, by=args.by or "iteration.py")
    if not args.skip_smoke:
        smoke_argv = split_command(args.smoke_command) if args.smoke_command \
            else [sys.executable, "scripts/selftest_models.py"]
        code, output = _smoke(root, smoke_argv)
        if code != 0:
            print(f"[FAIL] restored stable smoke failed ({code})")
            print(output[-2000:])
            return 5
    print(f"[OK] rolled back to {target_commit[:12]}; default executor restored to "
          f"v{old_version}")
    return 0


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="stable-drives-candidate iteration controller")
    sub = ap.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init")
    init.add_argument("--root", default=".")
    init.add_argument("--candidate", required=True)
    init.add_argument("--stable", default=None, help="released tag vX.Y.Z (default: tag at HEAD)")
    init.add_argument("--version", default=None)
    init.add_argument("--branch", default=None)
    init.add_argument("--id", default=None)
    init.add_argument("--force", action="store_true")
    init.add_argument("--reuse", action="store_true")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=cmd_init)

    for name, func in (("status", cmd_status), ("verify", cmd_verify)):
        child = sub.add_parser(name)
        child.add_argument("--root", default=".")
        child.add_argument("--json", action="store_true")
        child.set_defaults(func=func)

    guard = sub.add_parser("guard")
    guard.add_argument("--root", default=".")
    guard.add_argument("--path", required=True)
    guard.add_argument("--expect", choices=["stable", "candidate"], default=None)
    guard.set_defaults(func=cmd_guard)

    baseline = sub.add_parser("baseline")
    baseline.add_argument("--root", default=".")
    baseline.add_argument("command", nargs=argparse.REMAINDER)
    baseline.set_defaults(func=cmd_baseline)

    evidence = sub.add_parser("evidence")
    evidence.add_argument("--root", default=".")
    evidence.add_argument("--name", default="iteration-candidate")
    evidence.add_argument("command", nargs=argparse.REMAINDER)
    evidence.set_defaults(func=cmd_evidence)

    compare = sub.add_parser("compare")
    compare.add_argument("--root", default=".")
    compare.add_argument("--baseline", default=None)
    compare.add_argument("--evidence", default=None)
    compare.add_argument("--accept-fewer-tests", type=int, default=None)
    compare.add_argument("--accept-removal", action="append", default=[])
    compare.add_argument("--reason", default="")
    compare.set_defaults(func=cmd_compare)

    approve = sub.add_parser("approve")
    approve.add_argument("--root", default=".")
    approve.add_argument("--by", required=True)
    approve.add_argument("--note", default="")
    approve.set_defaults(func=cmd_approve)

    decide = sub.add_parser("decide")
    decide.add_argument("--root", default=".")
    decide.add_argument("--topic", required=True)
    decide.add_argument("--decision", required=True)
    decide.add_argument("--by", required=True)
    decide.add_argument("--reason", default="")
    decide.add_argument("--option", action="append", default=[])
    decide.set_defaults(func=cmd_decide)

    promote = sub.add_parser("promote")
    promote.add_argument("--root", default=".")
    promote.add_argument("--smoke-command", default=None,
                         help="command run in the promoted stable tree (default: selftest)")
    promote.add_argument("--push", action="store_true")
    promote.set_defaults(func=cmd_promote)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--root", default=".")
    rollback.add_argument("--to", default=None)
    rollback.add_argument("--smoke-command", default=None)
    rollback.add_argument("--skip-smoke", action="store_true")
    rollback.add_argument("--force", action="store_true")
    rollback.add_argument("--yes", action="store_true",
                          help="non-interactive confirmation (the command is already explicit)")
    rollback.add_argument("--by", default="")
    rollback.set_defaults(func=cmd_rollback)

    args = ap.parse_args()
    for name in ("command", "smoke"):
        value = getattr(args, name, None)
        if isinstance(value, list) and value and value[0] == "--":
            setattr(args, name, value[1:])
    if args.cmd != "init":
        root, manifest = locate(getattr(args, "root", "."))
        if manifest and not trusted(root):
            return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
