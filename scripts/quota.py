#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read Codex plan quota (5 h primary + 7 d secondary) and pick the routing tier.

Sources, in order:
    1. live   - `codex app-server` + JSON-RPC `account/rateLimits/read`
                (authoritative, ~2.5 s, no --ephemeral involvement);
    2. log    - newest `rate_limits` record in
                %USERPROFILE%\\.codex\\sessions\\**\\rollout-*.jsonl (fallback only,
                and only while that record still belongs to the current window);
    3. manual - `--remaining <pct> --minutes <h>`.

A sample from an **expired** window is never extrapolated: the script reports
`remaining=null`, tier `U` (unknown), and refuses to claim a surplus. Fabricating
100% here is what made an earlier version route aggressively at 11% real usage.

Usage:
    python scripts/quota.py                       # live first, log fallback
    python scripts/quota.py --source log          # history only, no app-server
    python scripts/quota.py --json                # machine-readable
    python scripts/quota.py --remaining 62 --minutes 210
    python scripts/quota.py --note "before draft" # append to _work/quota_ledger.md

Exit codes:
    0  live or in-window reading (or manual override)
    1  neither source produced anything
    2  only an expired/stale sample: quota unknown, do not plan a large batch
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import queue
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SESSION_ROOT = os.environ.get("CODEX_SESSIONS_ROOT") or os.path.join(
    os.path.expanduser("~"), ".codex", "sessions")
TAIL_BYTES = 4_000_000          # rate_limits sit near the end of a rollout
MAX_FILES = 24
LIVE_TIMEOUT = 25

# (tier, minimum remaining %, label)
TIERS = [
    ("S", 65.0, "surplus"),
    ("A", 50.0, "normal"),
    ("B", 30.0, "conserve"),
    ("C", 12.0, "vision-only"),
    ("R", 0.0, "reserve"),
]

PLAN = {
    "S": {
        "lead": "integrate, run deterministic checks, arbitrate, deliver",
        "gpt": "production + all gates + optional second pass",
        "batch": "one call per artifact (quality over amortization)",
        "next": "queue production work now - this quota expires at reset",
    },
    "A": {
        "lead": "produce deterministic parts; GPT-facing handoffs stay clean",
        "gpt": "quality-sensitive production + every gate",
        "batch": "one call per artifact group",
        "next": "keep gates intact; no speculative passes",
    },
    "B": {
        "lead": "all production, integration, scripted checks",
        "gpt": "gates only, batched; no exploratory passes",
        "batch": "batch artifacts and pages; one call per group",
        "next": "reserve >=8% for the delivery visual gate",
    },
    "C": {
        "lead": "all text / code / format work",
        "gpt": "vision and layout judgement only, batched",
        "batch": "batch every image into one call",
        "next": "defer text review until after reset",
    },
    "R": {
        "lead": "everything",
        "gpt": "at most the single delivery visual gate, if not yet run",
        "batch": "one call, then stop",
        "next": "do not start GPT-dependent work; report remaining budget",
    },
    "U": {
        "lead": "all production; assume no GPT budget until a reading exists",
        "gpt": "one gate call at a time, if the delivery needs it",
        "batch": "one call, then re-read",
        "next": "quota unknown: read live or pass --remaining before any batch",
    },
}

CODEX_HINT = ("no codex.exe under %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\; "
              "install or repair the Codex app")


def resolve_codex():
    """Never hardcode bin/<hash>/; return the newest codex.exe."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin")
    hits = glob.glob(os.path.join(base, "*", "codex.exe"))
    return max(hits, key=os.path.getmtime) if hits else None


def configured_effort(path=None):
    """Configured model_reasoning_effort; the tier policy never changes it."""
    path = path or os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*model_reasoning_effort\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def configured_model(path=None):
    path = path or os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- live source

def live_rate_limits(timeout=LIVE_TIMEOUT):
    """One JSON-RPC round trip to a fresh app-server. None on any failure."""
    codex = resolve_codex()
    if not codex:
        return None
    try:
        proc = subprocess.Popen(
            [codex, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError:
        return None
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "coordination-skill", "version": "1.4.0"}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
    ]
    lines = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        except Exception:  # noqa: BLE001
            pass
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        for msg in messages:
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        while datetime.now(timezone.utc) < deadline:
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            try:
                line = lines.get(timeout=max(0.05, remaining))
            except queue.Empty:
                break
            if line is None:
                break
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("id") == 2:
                return (obj.get("result") or {})
    except (OSError, ValueError):
        return None
    finally:
        try:
            proc.kill()
        except OSError:
            pass
    return None


# ----------------------------------------------------------------- log source

def tail_lines(path, max_bytes=TAIL_BYTES):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()          # drop the partial line
        return fh.read().decode("utf-8", "replace").splitlines()


def newest_log_sample():
    """Newest (timestamp, rate_limits) record across recent rollouts."""
    files = glob.glob(os.path.join(SESSION_ROOT, "**", "rollout-*.jsonl"), recursive=True)
    files.sort(key=os.path.getmtime, reverse=True)
    best = None
    for path in files[:MAX_FILES]:
        try:
            for line in tail_lines(path):
                if '"rate_limits"' not in line:
                    continue
                row = json.loads(line)
                rl = (row.get("payload") or {}).get("rate_limits")
                ts = row.get("timestamp")
                if rl and ts and (best is None or ts > best[0]):
                    best = (ts, rl)
        except Exception:  # noqa: BLE001
            continue
    return best


def parse_iso(ts):
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


# ------------------------------------------------------------------- helpers

def window_state(used_pct, resets_at, now):
    """(remaining%, hours_left, end_dt) from an authoritative used_percent."""
    used = max(0.0, min(100.0, float(used_pct)))
    end = datetime.fromtimestamp(resets_at, tz=timezone.utc) if resets_at else None
    hours = max(0.0, (end - now).total_seconds() / 3600.0) if end else None
    return 100.0 - used, hours, end


def classify(remaining, hours_left, weekly_remaining):
    if remaining is None:
        return ("U", 0.0, "unknown"), None, "unknown"
    idx = next(i for i, (_, floor, _) in enumerate(TIERS) if remaining >= floor)
    pace = None
    if hours_left is not None:
        pace = remaining / hours_left if hours_left > 0 else 999.0
        if hours_left <= 1.0 and remaining >= 15:
            idx = 0                                   # burn window: it expires
        elif hours_left >= 2.0 and pace >= 18:
            idx = max(0, idx - 1)                     # too much slack per hour
        elif hours_left >= 2.0 and pace < 8 and idx < 3:
            idx += 1                                  # too little slack per hour;
                                                      # never demote past vision-only
    cap = None
    if weekly_remaining is not None:
        if weekly_remaining < 12:
            cap = next(i for i, t in enumerate(TIERS) if t[0] == "C")
        elif weekly_remaining < 25:
            cap = next(i for i, t in enumerate(TIERS) if t[0] == "B")
    if cap is not None:
        idx = max(idx, cap)
    if remaining <= 8:
        idx = next(i for i, t in enumerate(TIERS) if t[0] == "R")
    return TIERS[idx], pace, cap


def fmt_hours(h):
    if h is None:
        return "unknown"
    if h < 0:
        return "expired"
    total = int(round(h * 60))
    return f"{total // 60}h{total % 60:02d}m"


def _empty():
    return {
        "now": "",
        "source": None,
        "plan_type": None,
        "sample_time": None,
        "sample_age_min": None,
        "stale": False,
        "stale_reason": None,
        "remaining": None,
        "used": None,
        "hours_left": None,
        "reset_local": None,
        "pace": None,
        "weekly_remaining": None,
        "weekly_used": None,
        "weekly_reset_local": None,
        "limit_id": None,
    }


def from_live(payload, now):
    data = _empty()
    rl = payload.get("rateLimits") or {}
    by_id = payload.get("rateLimitsByLimitId") or {}
    if by_id.get("codex"):
        rl = by_id["codex"]
    primary = rl.get("primary") or {}
    secondary = rl.get("secondary") or {}
    if primary.get("usedPercent") is None:
        return None
    remaining, hours, end = window_state(primary.get("usedPercent"),
                                         primary.get("resetsAt"), now)
    w_remaining, _, w_end = window_state(secondary.get("usedPercent", 0.0),
                                         secondary.get("resetsAt"), now)
    data.update({
        "source": "app-server",
        "plan_type": rl.get("planType"),
        "limit_id": rl.get("limitId"),
        "remaining": round(remaining, 1),
        "used": round(100.0 - remaining, 1),
        "hours_left": round(hours, 2) if hours is not None else None,
        "reset_local": end.astimezone().strftime("%Y-%m-%d %H:%M") if end else None,
        "weekly_remaining": round(w_remaining, 1),
        "weekly_used": round(100.0 - w_remaining, 1),
        "weekly_reset_local": w_end.astimezone().strftime("%Y-%m-%d %H:%M") if w_end else None,
    })
    return data


def from_log(now):
    best = newest_log_sample()
    if not best:
        return None
    ts, rl = best
    sample_dt = parse_iso(ts)
    age = (now - sample_dt).total_seconds() / 60.0
    primary = rl.get("primary") or {}
    secondary = rl.get("secondary") or {}
    resets_at = primary.get("resets_at")
    data = _empty()
    data.update({
        "source": "session-log",
        "plan_type": rl.get("plan_type"),
        "limit_id": rl.get("limit_id"),
        "sample_time": sample_dt.astimezone().strftime("%Y-%m-%d %H:%M"),
        "sample_age_min": round(age, 1),
    })
    w_remaining, _, w_end = window_state(secondary.get("used_percent", 0.0),
                                         secondary.get("resets_at"), now)
    data.update({
        "weekly_remaining": round(w_remaining, 1),
        "weekly_used": round(100.0 - w_remaining, 1),
        "weekly_reset_local": w_end.astimezone().strftime("%Y-%m-%d %H:%M") if w_end else None,
    })
    if resets_at and datetime.fromtimestamp(resets_at, tz=timezone.utc) <= now:
        data.update({
            "stale": True,
            "stale_reason": "last sample belongs to an expired 5 h window",
        })
        return data
    remaining, hours, end = window_state(primary.get("used_percent", 0.0), resets_at, now)
    data.update({
        "remaining": round(remaining, 1),
        "used": round(100.0 - remaining, 1),
        "hours_left": round(hours, 2) if hours is not None else None,
        "reset_local": end.astimezone().strftime("%Y-%m-%d %H:%M") if end else None,
    })
    if age > 30:
        data["stale"] = True
        data["stale_reason"] = "sample older than 30 min; remaining is an upper bound"
    return data


def collect(source="auto", manual_remaining=None, manual_minutes=None):
    now = datetime.now(timezone.utc)
    data = _empty()
    data["now"] = now.astimezone().isoformat(timespec="seconds")
    if manual_remaining is not None:
        data.update({
            "source": "manual",
            "remaining": float(manual_remaining),
            "used": round(100.0 - float(manual_remaining), 1),
        })
        if manual_minutes is not None:
            data["hours_left"] = round(float(manual_minutes) / 60.0, 2)
            data["reset_local"] = (now + timedelta(minutes=float(manual_minutes))) \
                .astimezone().strftime("%Y-%m-%d %H:%M")
        return data
    if source in ("auto", "live"):
        payload = live_rate_limits()
        if payload:
            live = from_live(payload, now)
            if live:
                live["now"] = data["now"]
                return live
        if source == "live":
            return data
    logged = from_log(now)
    if logged:
        logged["now"] = data["now"]
    return logged or data


def main():
    ap = argparse.ArgumentParser(description="Codex quota -> routing tier")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--source", choices=("auto", "live", "log"), default="auto",
                    help="live = app-server only, log = rollouts only, auto = live then log")
    ap.add_argument("--remaining", type=float, help="manual remaining %% of the 5 h window")
    ap.add_argument("--minutes", type=float, help="minutes until the 5 h window resets")
    ap.add_argument("--note", help="label this reading; appends to the ledger")
    ap.add_argument("--ledger", default=os.path.join("_work", "quota_ledger.md"))
    args = ap.parse_args()

    data = collect(args.source, args.remaining, args.minutes)
    if data["source"] is None:
        print("[FAIL] no live reading and no rollout sample.", file=sys.stderr)
        print("       Is Codex installed and logged in? Or pass --remaining <pct> --minutes <m>",
              file=sys.stderr)
        return 1

    tier, pace, cap = classify(data["remaining"], data["hours_left"],
                               data["weekly_remaining"])
    week_capped = cap is not None and data["weekly_remaining"] is not None and \
        data["weekly_remaining"] < 25
    effort = configured_effort()
    effort_note = "held; tiers change scope and calls, not effort"
    if tier[0] == "R":
        effort_note = "one notch down only if a gate must still run below 8%, and record it"
    unknown = tier[0] == "U"
    data.update({
        "tier": tier[0],
        "tier_label": tier[2],
        "tier_band": None if unknown else f">={int(tier[1])}% remaining",
        "pace": round(pace, 1) if pace else None,
        "weekly_capped": week_capped,
        "plan": PLAN[tier[0]],
        "effort_configured": effort,
        "effort_changed": False,
        "effort_note": effort_note,
    })
    if not args.json:
        d = data
        print(f"== codex quota {d['now'][:16]} ==")
        if unknown:
            print(f"primary 5h : remaining unknown  ({d['stale_reason'] or 'no reading'})")
        else:
            print(f"primary 5h : used {d['used']:5.1f}%  remaining {d['remaining']:5.1f}%"
                  f"  resets {d['reset_local'] or 'unknown'}"
                  f"  (in {fmt_hours(d['hours_left'])})")
        if d["weekly_remaining"] is None:
            print("weekly  7d : unknown")
        else:
            print(f"weekly  7d : used {d['weekly_used']:5.1f}%"
                  f"  remaining {d['weekly_remaining']:5.1f}%"
                  f"  resets {d['weekly_reset_local'] or 'unknown'}")
        extra = []
        if d["sample_time"]:
            extra.append(f"sample {d['sample_time']} ({d['sample_age_min']:.0f} min old)")
        if d["plan_type"]:
            extra.append(f"plan {d['plan_type']}")
        if d["stale"]:
            extra.append(f"STALE: {d['stale_reason']}")
        print(f"source     : {d['source']}" + ("  |  " + "; ".join(extra) if extra else ""))
        if d["pace"] is not None:
            print(f"pace       : {d['pace']:.1f} pts/h available (healthy 8-18)")
        band = f" ({d['tier_band']})" if d["tier_band"] else ""
        print(f"tier       : {d['tier']} - {d['tier_label']}{band}"
              + ("  [capped by weekly quota]" if d["weekly_capped"] else ""))
        print(f"lead       : {d['plan']['lead']}")
        print(f"gpt        : {d['plan']['gpt']}")
        print(f"batching   : {d['plan']['batch']}")
        label = d["effort_configured"] or "as configured"
        print(f"effort     : {label} - {d['effort_note']}")
        print(f"next       : {d['plan']['next']}")
        if unknown:
            print("warning    : no quota number -> keep GPT work to gates until one exists")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))

    if args.note:
        path = os.path.abspath(args.ledger)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as fh:
            if new:
                fh.write("# quota ledger\n\n"
                         "| time | tier | 5h left | weekly left | resets in | note |\n"
                         "|---|---|---|---|---|---|\n")
            left5 = "unknown" if data["remaining"] is None else f"{data['remaining']:.0f}%"
            left7 = "unknown" if data["weekly_remaining"] is None \
                else f"{data['weekly_remaining']:.0f}%"
            fh.write(f"| {data['now'][:16].replace('T', ' ')} | {tier[0]} | {left5} | "
                     f"{left7} | {fmt_hours(data['hours_left'])} | {args.note} |\n")
    if unknown:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
