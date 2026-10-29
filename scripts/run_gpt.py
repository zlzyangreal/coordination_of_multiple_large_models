#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""The single entry point for GPT (Codex) calls. Use this instead of ad-hoc `codex exec`.

Why a runner:
    * **persistence** - production calls must write a rollout (no `--ephemeral`), or the
      call is invisible to the quota reader, the Codex UI and every postmortem;
    * **observability** - one JSON record per call: thread_id, rollout path, model, effort,
      sandbox, duration, usage, quota before/after;
    * **cleanup** - on timeout the turn is closed as `interrupted` instead of staying
      `inProgress` forever;
    * **runtime paths** - codex is resolved from the newest `bin/*/codex.exe`, never a
      hardcoded hash.

Usage:
    python scripts/run_gpt.py --prompt-file prompt.txt --cd work --request-id R01
    python scripts/run_gpt.py --prompt "reply ok" --probe          # connectivity only
    python scripts/run_gpt.py --prompt-file brief.md --sandbox read-only \
        --schema assets/result.schema.json --output _work/payload.json \
        --result-path _work/result.json --envelope _work/env.yaml  # review call

Exit codes: 0 ok, 1 model/tool failure, 2 bad invocation, 3 schema-invalid, 124 timeout.

Record: `_work/calls/<request_id>.json` (+ `.events.jsonl`). Payload stays in `output`
(`-o`), the result is copied to `result_path`, and both stay distinct from the artifact.
One quota-ledger row is appended per call; `--no-ledger` skips it.
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import quota as quota_mod
except Exception:  # noqa: BLE001
    quota_mod = None
try:
    import reap_turns
except Exception:  # noqa: BLE001
    reap_turns = None
try:
    import gate_common as common
except Exception:  # noqa: BLE001
    common = None
try:
    import plan_gate
except Exception:  # noqa: BLE001
    plan_gate = None
try:
    import review_budget
except Exception:  # noqa: BLE001
    review_budget = None
try:
    import facts_pack
except Exception:  # noqa: BLE001
    facts_pack = None
try:
    from validate_result import load_envelope
except Exception:  # noqa: BLE001
    load_envelope = None

SKILL_VERSION = "2.2.0"
CALL_DIR = os.path.join("_work", "calls")


def resolve_codex():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin")
    hits = glob.glob(os.path.join(base, "*", "codex.exe"))
    return max(hits, key=os.path.getmtime) if hits else None


def config_value(key, default=None):
    path = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*' + re.escape(key) + r'\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return default


def codex_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def quota_snapshot():
    if not quota_mod:
        return {"source": "unavailable"}
    try:
        data = quota_mod.collect("auto")
    except Exception as exc:  # noqa: BLE001
        return {"source": "error", "error": str(exc)[:160]}
    if not data.get("source"):
        return {"source": "unavailable"}
    keys = ("source", "used", "remaining", "hours_left", "weekly_used",
            "weekly_remaining", "reset_local", "plan_type", "stale")
    snap = {k: data.get(k) for k in keys}
    tier, _, _ = quota_mod.classify(data.get("remaining"), data.get("hours_left"),
                                    data.get("weekly_remaining"))
    snap["tier"] = tier[0]
    return snap


def find_rollout(thread_id):
    if not thread_id:
        return None
    pattern = os.path.join(os.path.expanduser("~"), ".codex", "sessions", "**",
                           f"*{thread_id}*.jsonl")
    hits = glob.glob(pattern, recursive=True)
    return hits[0] if hits else None


def new_request_id():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tail = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"R{stamp}-{tail}"


def run(cmd, cwd, prompt, events_path, timeout, feed_prompt):
    """Run codex exec; return dict(thread_id, usage, exit_code, final_message, timed_out)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    info = {"thread_id": None, "usage": None, "exit_code": None,
            "final_message": None, "timed_out": False, "stderr_tail": []}

    def feed():
        try:
            if feed_prompt:
                proc.stdin.write(prompt)
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    def read_stdout():
        with open(events_path, "w", encoding="utf-8") as fh:
            for line in proc.stdout:
                fh.write(line)
                fh.flush()
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                kind = obj.get("type")
                if kind == "thread.started":
                    info["thread_id"] = obj.get("thread_id")
                elif kind == "turn.completed":
                    info["usage"] = obj.get("usage")
                elif kind == "item.completed":
                    item = obj.get("item") or {}
                    if item.get("type") == "agent_message":
                        info["final_message"] = item.get("text")
                elif kind in ("error", "turn.failed"):
                    info["stderr_tail"].append(json.dumps(obj, ensure_ascii=False)[:300])

    def read_stderr():
        for line in proc.stderr:
            info["stderr_tail"].append(line.strip()[:300])
            del info["stderr_tail"][:-6]

    threading.Thread(target=feed, daemon=True).start()
    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    try:
        info["exit_code"] = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        info["timed_out"] = True
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        info["exit_code"] = 124
    return info


def close_turn(thread_id, started_ts, backup_dir, attempts=6, delay=1.5):
    """Mark the killed turn interrupted; returns a small report or None.

    The row is written by the dying process, so poll briefly instead of racing it.
    """
    if not (reap_turns and thread_id):
        return None
    for _ in range(attempts):
        try:
            rows = reap_turns.stale_turns(thread=thread_id, older_than_min=None)
        except Exception as exc:  # noqa: BLE001
            return {"thread_id": thread_id, "reaped": False, "error": str(exc)[:200]}
        rows = [r for r in rows if (r.get("started_at") or 0) >= int(started_ts) - 5]
        if rows:
            try:
                backup, updated = reap_turns.mark_interrupted(
                    rows, backup_dir=os.path.join(backup_dir, "turns"), reason="timeout")
                return {"thread_id": thread_id, "reaped": bool(updated), "rows": updated,
                        "backup": backup, "turns": [r["turn_id"] for r in rows]}
            except Exception as exc:  # noqa: BLE001
                return {"thread_id": thread_id, "reaped": False, "error": str(exc)[:200]}
        time.sleep(delay)
    return {"thread_id": thread_id, "reaped": False,
            "reason": f"no inProgress row after {attempts * delay:.0f}s"}


def contract_info():
    """Which contract this call runs under, and whether the repo moved on."""
    skill = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "SKILL.md"))
    current, current_mtime = None, None
    try:
        with open(skill, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^\s*version:\s*(\S+)\s*$", text, re.M)
        current = m.group(1) if m else None
        current_mtime = datetime.fromtimestamp(os.path.getmtime(skill)).astimezone() \
            .isoformat(timespec="seconds")
    except OSError:
        pass
    pinned = None
    try:
        with open(os.path.join("_work", "skill_pin.json"), encoding="utf-8") as fh:
            pinned = json.load(fh)
    except (OSError, ValueError):
        pass
    return {
        "current": current,
        "current_mtime": current_mtime,
        "pinned": pinned.get("version") if pinned else None,
        "pinned_at": pinned.get("pinned_at") if pinned else None,
        "drift": bool(pinned and current and pinned.get("version") != current),
    }


REVIEW_ROLES = ("review", "plan-review")


def resolve_role(args):
    """A call with no semantic role is production: the safe default is the gate."""
    if args.probe:
        return "probe", "forced"
    if args.role:
        return args.role, "explicit"
    return ("review" if args.schema else "produce"), "inferred"


def workspace_guard(project):
    """Return an error string when the pin belongs to another path/disk."""
    if not common:
        return None
    pin = common.load_pin(project)
    if not pin or not pin.get("workspace"):
        return None
    problems = common.identity_mismatches(pin, project)
    return "; ".join(problems) if problems else None


def facts_guard(envelope_path):
    """Rehash the facts pack named by the envelope right before spending quota."""
    if not envelope_path:
        return None
    if not (load_envelope and common):
        return "validator/facts helpers unavailable"
    try:
        env = load_envelope(envelope_path)
    except (OSError, ValueError) as exc:
        return f"cannot read envelope: {exc}"
    version = str(env.get("envelope_version") or "")
    facts_path = env.get("facts_path")
    declared = env.get("facts_hash")
    if version == "2" and (not facts_path or not declared):
        return "a v2 envelope must carry facts_path and facts_hash"
    if not facts_path:
        return None
    path = facts_path if os.path.isabs(str(facts_path)) else os.path.abspath(str(facts_path))
    if not facts_pack:
        return "facts_pack helper unavailable"
    code, message, info = facts_pack.check(path)
    if code != 0:
        return message
    if str(declared) != str(info.get("declared_hash")):
        return (f"facts pack hash header changed after the envelope was built ({path}); "
                f"re-freeze and rebuild the envelope")
    if str(env.get("facts_version")) != str(info.get("version")):
        return (f"facts pack version changed ({info.get('version')}); rebuild the envelope")
    return None


def plan_guard(project):
    if not plan_gate:
        return "plan_gate helper unavailable"
    code, report = plan_gate.check_plan(project)
    if code == 0:
        return None
    return f"plan gate blocked ({code}): {report.get('message')}"


def main():
    ap = argparse.ArgumentParser(description="single entry point for Codex calls")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt", help="inline prompt (short)")
    src.add_argument("--prompt-file", help="file with the prompt (preferred)")
    ap.add_argument("--cd", default=os.getcwd())
    ap.add_argument("--request-id", default=None)
    ap.add_argument("--role", default=None,
                    choices=("plan", "produce", "review", "plan-review", "probe"),
                    help="semantic call role; omitted = review if --schema else produce")
    ap.add_argument("--milestone", default=None,
                    help="review milestone (mandatory for review/plan-review)")
    ap.add_argument("--work-package", default=None,
                    help="work package id from the reviewed plan (produce)")
    ap.add_argument("--produce-artifact", default=None,
                    help="path of the artifact this produce call is expected to create")
    ap.add_argument("--why-unclosed", default=None,
                    help="why the previous two review rounds did not close (round 3+)")
    ap.add_argument("--budget-change", default=None,
                    help="one-off reason for exceeding the review-call budget")
    ap.add_argument("--model", default=None,
                    help="default: model from ~/.codex/config.toml")
    ap.add_argument("--effort", default=None,
                    help="default: model_reasoning_effort from config (never tier-driven)")
    ap.add_argument("--sandbox", default="read-only",
                    choices=("read-only", "workspace-write", "danger-full-access"))
    ap.add_argument("--output", help="payload path (-o); never the artifact")
    ap.add_argument("--result-path", help="copy the captured payload here on success")
    ap.add_argument("--schema", help="JSON schema; required for review calls (see calling-gpt.md)")
    ap.add_argument("--schema-raw", action="store_true",
                    help="pass --schema to codex verbatim (it must already be strict; usually wrong)")
    ap.add_argument("--envelope", help="envelope yaml; enables the echo/coverage cross-check")
    ap.add_argument("--timeout", type=int, default=1200, help="seconds (default 1200)")
    ap.add_argument("--probe", action="store_true",
                    help="ephemeral connectivity test only; never for production")
    ap.add_argument("--calls-dir", default=CALL_DIR)
    ap.add_argument("--ledger", default=os.path.join("_work", "quota_ledger.md"),
                    help="one row is appended per call (use --no-ledger to skip)")
    ap.add_argument("--no-ledger", action="store_true")
    args = ap.parse_args()

    project = os.path.abspath(os.getcwd())
    role, role_source = resolve_role(args)

    identity_problem = workspace_guard(project)
    if identity_problem:
        print(f"[FAIL] workspace identity mismatch: {identity_problem}", file=sys.stderr)
        print("       re-pin with `python scripts/session.py pin --force` in this root",
              file=sys.stderr)
        return 2

    env_info = {}
    if args.envelope:
        if not os.path.isfile(args.envelope):
            print(f"[FAIL] envelope not found: {args.envelope}", file=sys.stderr)
            return 2
        env_info = load_envelope(args.envelope) if load_envelope else {}

    if role in REVIEW_ROLES:
        missing = [flag for flag, value in (("--schema", args.schema),
                                            ("--envelope", args.envelope),
                                            ("--result-path", args.result_path),
                                            ("--milestone", args.milestone)) if not value]
        if missing:
            print(f"[FAIL] a {role} call needs {', '.join(missing)}; a review must be "
                  f"adoptable, not just text", file=sys.stderr)
            return 2
        if args.sandbox != "read-only":
            print("[FAIL] a review call must run --sandbox read-only (zero-write)", file=sys.stderr)
            return 2
        problem = facts_guard(args.envelope)
        if problem:
            print(f"[FAIL] {problem}", file=sys.stderr)
            return 2
    if role == "produce":
        if not args.work_package:
            print("[FAIL] a produce call needs --work-package from the reviewed plan",
                  file=sys.stderr)
            return 2
        problem = plan_guard(project)
        if problem:
            print(f"[FAIL] {problem}", file=sys.stderr)
            return 2
    if role == "probe":
        forbidden = [flag for flag, value in (("--output", args.output),
                                             ("--result-path", args.result_path),
                                             ("--schema", args.schema),
                                             ("--envelope", args.envelope),
                                             ("--work-package", args.work_package),
                                             ("--produce-artifact", args.produce_artifact))
                     if value]
        if forbidden:
            print(f"[FAIL] a probe call is text-only and cannot use "
                  f"{', '.join(forbidden)}; use --role produce (with a reviewed plan) or "
                  f"--role review", file=sys.stderr)
            return 2

    codex = resolve_codex()
    if not codex:
        print("[FAIL] codex.exe not found under %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\",
              file=sys.stderr)
        return 2
    model = args.model or config_value("model", "gpt-5.6-sol")
    effort = args.effort or config_value("model_reasoning_effort", "medium")
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8-sig") as fh:
            prompt = fh.read()
    else:
        prompt = args.prompt
    cwd = os.path.abspath(args.cd)
    if not os.path.isdir(cwd):
        print(f"[FAIL] --cd is not a directory: {cwd}", file=sys.stderr)
        return 2

    request_id = args.request_id or new_request_id()
    env_request = str(env_info.get("request_id") or "").strip() if env_info else ""
    if role in REVIEW_ROLES and env_request:
        if args.request_id and args.request_id != env_request:
            print(f"[FAIL] --request-id {args.request_id} does not match the envelope "
                  f"request_id {env_request}", file=sys.stderr)
            return 2
        request_id = env_request
    os.makedirs(args.calls_dir, exist_ok=True)
    events_path = os.path.join(args.calls_dir, f"{request_id}.events.jsonl")
    record_path = os.path.join(args.calls_dir, f"{request_id}.json")
    if args.output:
        args.output = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    if args.result_path:
        if not args.output:
            print("[FAIL] --result-path needs --output (the payload the runner captures)",
                  file=sys.stderr)
            return 2
        args.result_path = os.path.abspath(args.result_path)
        os.makedirs(os.path.dirname(args.result_path) or ".", exist_ok=True)

    cmd = [codex, "exec", "--json", "--skip-git-repo-check", "-C", cwd,
           "-m", model, "-c", f"model_reasoning_effort={effort}",
           "-s", args.sandbox]
    if args.output:
        cmd += ["-o", args.output]
    codex_schema = None
    if args.schema:
        # codex forwards --output-schema to the Responses API with strict=true; plain
        # draft-07 is rejected (additionalProperties / all-required). Derive a strict copy.
        codex_schema = os.path.abspath(args.schema)
        if not args.schema_raw:
            try:
                from schema_for_codex import derive
                with open(args.schema, encoding="utf-8") as fh:
                    codex_schema = os.path.abspath(
                        os.path.join(args.calls_dir, f"{request_id}.codex-schema.json"))
                    os.makedirs(os.path.dirname(codex_schema), exist_ok=True)
                    with open(codex_schema, "w", encoding="utf-8") as out:
                        json.dump(derive(json.load(fh)), out, ensure_ascii=False, indent=2)
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] cannot derive strict schema: {exc}", file=sys.stderr)
                return 2
        cmd += ["--output-schema", codex_schema]
    if args.probe or role == "probe":
        cmd += ["--ephemeral"]
    feed_prompt = len(prompt) > 6000
    if not feed_prompt:
        cmd.append(prompt)

    budget_info = None
    reserved = False
    if role in REVIEW_ROLES:
        if not review_budget:
            print("[FAIL] review_budget helper unavailable", file=sys.stderr)
            return 2
        code, budget_info = review_budget.preflight(
            project, args.milestone, role, request_id, args.why_unclosed,
            args.budget_change)
        if code != 0:
            print(f"[FAIL] review budget: {budget_info.get('message')}", file=sys.stderr)
            return 2
        reserved = True
        print(f"[run] review reservation {args.milestone}: "
              f"{budget_info.get('prior_attempts')}/{budget_info.get('review_calls')} prior "
              f"calls, {budget_info.get('prior_minutes')}/"
              f"{budget_info.get('review_minutes')} prior minutes", file=sys.stderr)

    envelope_sha = common.sha256_file(args.envelope) if (common and args.envelope) else None
    artifact_path = (env_info or {}).get("artifact")
    artifact_hash = (env_info or {}).get("version_hash")
    produce_artifact = args.produce_artifact
    if produce_artifact:
        produce_artifact = os.path.abspath(produce_artifact)

    before = quota_snapshot()
    started = time.time()
    started_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"[run] {request_id} role={role} model={model} effort={effort} sandbox={args.sandbox}"
          f"{' ephemeral(PROBE)' if args.probe or role == 'probe' else ''}", file=sys.stderr)
    print(f"[run] quota before: {before.get('used')}% used "
          f"(tier {before.get('tier')}, source {before.get('source')})", file=sys.stderr)

    info = run(cmd, cwd, prompt, events_path, args.timeout, feed_prompt)
    after = quota_snapshot()
    finished_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    thread_id = info.get("thread_id")

    status = "ok"
    if info["timed_out"]:
        status = "timeout"
    elif info.get("exit_code") not in (0, None):
        status = "failed"
    elif not info.get("usage"):
        status = "incomplete"

    cleanup = None
    if status in ("timeout", "failed", "incomplete") and not (args.probe or role == "probe"):
        cleanup = close_turn(thread_id, started, os.path.dirname(os.path.abspath(record_path)))

    # Result capture + contract check (BUG-1 / BUG-3): the reviewer writes nothing,
    # the runner copies the captured payload and validates it before the lead adopts it.
    schema_valid, schema_errors = None, []
    if args.schema and status == "ok":
        captured = args.output
        if not captured or not os.path.isfile(captured):
            schema_valid, schema_errors = False, ["no payload captured; pass --output"]
        else:
            if args.result_path:
                shutil.copyfile(captured, args.result_path)
                captured = args.result_path
            try:
                from validate_result import validate_files
                _, schema_errors, _ = validate_files(captured, args.schema, args.envelope)
                schema_valid = not schema_errors
            except Exception as exc:  # noqa: BLE001
                schema_valid, schema_errors = False, [f"validator error: {exc}"]
        if not schema_valid:
            status = "schema-invalid"

    call_duration = round(time.time() - started, 1)
    if role in REVIEW_ROLES and budget_info:
        total_minutes = round(float(budget_info.get("prior_minutes") or 0)
                              + call_duration / 60.0, 1)
        over = total_minutes > float(budget_info.get("review_minutes") or 30)
        budget_info["minutes_total"] = total_minutes
        budget_info["minutes_over"] = over
        if over:
            review_budget.note_minutes(
                project, args.milestone,
                f"auto: {total_minutes} min > {budget_info.get('review_minutes')} min",
                minutes=total_minutes)

    delta = None
    if isinstance(before.get("used"), (int, float)) and isinstance(after.get("used"), (int, float)):
        delta = round(after["used"] - before["used"], 1)

    contract = contract_info()
    ledger_path = None
    if not args.no_ledger and quota_mod:
        try:
            delta_text = f"{delta:+.1f}pt" if delta is not None else "delta ?"
            prefix = [f"role={role}"]
            if args.milestone:
                prefix.append(f"M={args.milestone}")
            if args.work_package:
                prefix.append(f"WP={args.work_package}")
            note = " ".join(prefix + [f"{request_id} {status} {before.get('used')}->"
                                      f"{after.get('used')}% {delta_text}"])
            note = note.replace("|", "/")
            ledger_path = quota_mod.append_ledger(
                args.ledger, dict(after, now=finished_iso), note, after.get("tier"))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] ledger append failed: {exc}", file=sys.stderr)

    result_hash = None
    if args.result_path and os.path.isfile(args.result_path):
        result_hash = common.sha256_file(args.result_path) if common else None
    produced_hash = None
    if produce_artifact and os.path.isfile(produce_artifact):
        produced_hash = common.sha256_file(produce_artifact) if common else None
    workspace = common.workspace_identity(project) if common else None
    record = {
        "request_id": request_id,
        "protocol_version": 2,
        "status": status,
        "ephemeral": bool(args.probe or role == "probe"),
        "role": role,
        "role_source": role_source,
        "milestone": args.milestone,
        "work_package": args.work_package,
        "produce_artifact": produce_artifact,
        "produce_artifact_hash": produced_hash,
        "routing_tier": before.get("tier"),
        "tier_source": "quota" if before.get("tier") else "unavailable",
        "started_at": started_iso,
        "finished_at": finished_iso,
        "duration_s": call_duration,
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "sandbox": args.sandbox,
        "codex": {"path": codex, "version": codex_version(codex)},
        "contract": contract,
        "workspace": workspace,
        "thread_id": thread_id,
        "rollout_path": find_rollout(thread_id),
        "events_path": os.path.abspath(events_path),
        "output_path": args.output,
        "result_path": args.result_path,
        "result_sha256": result_hash,
        "envelope_path": os.path.abspath(args.envelope) if args.envelope else None,
        "envelope_sha256": envelope_sha,
        "artifact": artifact_path,
        "artifact_sha256": artifact_hash,
        "budget": budget_info,
        "schema_path": os.path.abspath(args.schema) if args.schema else None,
        "codex_schema_path": codex_schema,
        "schema_valid": schema_valid,
        "schema_errors": schema_errors[:20],
        "exit_code": info.get("exit_code"),
        "usage": info.get("usage"),
        "final_message": info.get("final_message"),
        "quota_before": before,
        "quota_after": after,
        "quota_delta_pts": delta,
        "quota_ledger": ledger_path,
        "turn_cleanup": cleanup,
        "stderr_tail": info.get("stderr_tail")[-3:],
    }
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    if reserved and review_budget:
        review_budget.finalize(project, request_id)
    print(json.dumps(record, ensure_ascii=False))
    print(f"[run] {status} in {record['duration_s']}s, thread {thread_id}, "
          f"quota {before.get('used')}->{after.get('used')}% ({delta}), record {record_path}",
          file=sys.stderr)
    if contract["drift"]:
        print(f"[WARN] contract drift: repo is v{contract['current']}, session pinned "
              f"v{contract['pinned']}; this call used the pinned contract - record the deviation",
              file=sys.stderr)
    if status == "timeout":
        return 124
    if status == "schema-invalid":
        print(f"[FAIL] result failed schema validation ({len(schema_errors)} problem(s)); "
              f"see {record_path}", file=sys.stderr)
        return 3
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
