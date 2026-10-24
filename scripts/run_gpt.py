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

SKILL_VERSION = "1.6.0"
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


def main():
    ap = argparse.ArgumentParser(description="single entry point for Codex calls")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt", help="inline prompt (short)")
    src.add_argument("--prompt-file", help="file with the prompt (preferred)")
    ap.add_argument("--cd", default=os.getcwd())
    ap.add_argument("--request-id", default=None)
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
    if args.probe:
        cmd += ["--ephemeral"]
    feed_prompt = len(prompt) > 6000
    if not feed_prompt:
        cmd.append(prompt)

    before = quota_snapshot()
    started = time.time()
    started_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"[run] {request_id} model={model} effort={effort} sandbox={args.sandbox}"
          f"{' ephemeral(PROBE)' if args.probe else ''}", file=sys.stderr)
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
    if status in ("timeout", "failed", "incomplete") and not args.probe:
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

    delta = None
    if isinstance(before.get("used"), (int, float)) and isinstance(after.get("used"), (int, float)):
        delta = round(after["used"] - before["used"], 1)

    contract = contract_info()
    ledger_path = None
    if not args.no_ledger and quota_mod:
        try:
            delta_text = f"{delta:+.1f}pt" if delta is not None else "delta ?"
            note = f"{request_id} {status} {before.get('used')}->{after.get('used')}% {delta_text}"
            ledger_path = quota_mod.append_ledger(
                args.ledger, dict(after, now=finished_iso), note, after.get("tier"))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] ledger append failed: {exc}", file=sys.stderr)

    record = {
        "request_id": request_id,
        "status": status,
        "ephemeral": bool(args.probe),
        "started_at": started_iso,
        "finished_at": finished_iso,
        "duration_s": round(time.time() - started, 1),
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "sandbox": args.sandbox,
        "codex": {"path": codex, "version": codex_version(codex)},
        "contract": contract,
        "thread_id": thread_id,
        "rollout_path": find_rollout(thread_id),
        "events_path": os.path.abspath(events_path),
        "output_path": args.output,
        "result_path": args.result_path,
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
