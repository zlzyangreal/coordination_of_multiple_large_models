#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read Codex plan quota (5 h primary + 7 d secondary) and pick the routing tier.

Source: %USERPROFILE%\\.codex\\sessions\\**\\rollout-*.jsonl, the
`event_msg/token_count` records carry `rate_limits.primary|secondary`
(used_percent, window_minutes, resets_at). No network, no credentials.

Usage:
    python scripts/quota.py                       # human-readable verdict
    python scripts/quota.py --json                # machine-readable
    python scripts/quota.py --remaining 62 --minutes 210   # manual override
    python scripts/quota.py --note "before draft"          # append to ledger

Rules for the numbers:
    * newest sample wins; samples older than the current window are ignored
      once that window has reset (remaining is then assumed 100%);
    * `resets_at` makes the percentage usable: 40% left with 30 min to reset
      is a surplus, 40% left with 4 h to reset is a shortage;
    * weekly (7 d) remaining can veto a generous 5 h window.

Exit codes:
    0  quota readable (or manual override given)
    1  no usable sample and no override -> ask the user for --remaining
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SESSION_ROOT = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
TAIL_BYTES = 4_000_000          # rate_limits sit near the end of a rollout
MAX_FILES = 24

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
        "effort": "high / xhigh",
        "batch": "one call per artifact (quality over amortization)",
        "next": "queue production work now - this quota expires at reset",
    },
    "A": {
        "lead": "produce deterministic parts; GPT-facing handoffs stay clean",
        "gpt": "quality-sensitive production + every gate",
        "effort": "medium / high",
        "batch": "one call per artifact group",
        "next": "keep gates intact; no speculative passes",
    },
    "B": {
        "lead": "all production, integration, scripted checks",
        "gpt": "gates only, batched; no exploratory passes",
        "effort": "low / medium",
        "batch": "batch artifacts and pages; one call per group",
        "next": "reserve >=8% for the delivery visual gate",
    },
    "C": {
        "lead": "all text / code / format work",
        "gpt": "vision and layout judgement only, batched",
        "effort": "low",
        "batch": "batch every image into one call",
        "next": "defer text review until after reset",
    },
    "R": {
        "lead": "everything",
        "gpt": "at most the single delivery visual gate, if not yet run",
        "effort": "low",
        "batch": "one call, then stop",
        "next": "do not start GPT-dependent work; report remaining budget",
    },
}


def local(ts):
    return ts.astimezone()


def tail_lines(path, max_bytes=TAIL_BYTES):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()          # drop the partial line
        return fh.read().decode("utf-8", "replace").splitlines()


def newest_samples(limit=MAX_FILES):
    files = glob.glob(os.path.join(SESSION_ROOT, "**", "rollout-*.jsonl"), recursive=True)
    files.sort(key=os.path.getmtime, reverse=True)
    out = []
    for path in files[:limit]:
        try:
            for line in tail_lines(path):
                if '"rate_limits"' not in line:
                    continue
                row = json.loads(line)
                rl = (row.get("payload") or {}).get("rate_limits")
                if not rl:
                    continue
                ts = row.get("timestamp")
                out.append((ts, rl))
        except Exception:
            continue
    # keep the latest record per window signature
    out.sort(key=lambda x: x[0] or "")
    return out


def pick(samples):
    """Return the most recent sample, preferring the newest window."""
    best = None
    for ts, rl in samples:
        if not ts:
            continue
        keep = (ts, rl)
        if best is None or keep[0] > best[0]:
            best = keep
    return best


def parse_iso(ts):
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def window_state(used_pct, window_min, resets_at, now):
    """Return (remaining%, hours_left, end_dt, assumed_fresh)."""
    if resets_at is None:
        return 100.0 - used_pct, None, None, False
    end = datetime.fromtimestamp(resets_at, tz=timezone.utc)
    if now < end:
        return max(0.0, 100.0 - used_pct), (end - now).total_seconds() / 3600.0, end, False
    step = timedelta(minutes=window_min or 300)
    k = 0
    while end + step * k <= now:
        k += 1
    return 100.0, ((end + step * k) - now).total_seconds() / 3600.0, end + step * k, True


def classify(remaining, hours_left, weekly_remaining):
    idx = next(i for i, (_, floor, _) in enumerate(TIERS) if remaining >= floor) \
        if remaining >= 0 else len(TIERS) - 1
    if hours_left is not None:
        pace = remaining / hours_left if hours_left > 0 else 999.0
        if hours_left <= 1.0 and remaining >= 15:
            idx = 0                                   # burn window: it expires
        elif hours_left >= 2.0 and pace >= 18:
            idx = max(0, idx - 1)                     # too much slack per hour
        elif hours_left >= 2.0 and pace < 8 and idx < 3:
            idx += 1                                  # too little slack per hour;
                                                      # never demote past vision-only
    else:
        pace = None
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


def collect(manual_remaining=None, manual_minutes=None):
    now = datetime.now(timezone.utc)
    samples = newest_samples()
    best = pick(samples)
    data = {
        "now": local(now).isoformat(timespec="seconds"),
        "sample_time": None,
        "sample_age_min": None,
        "stale": False,
        "assumed_fresh": False,
        "remaining": None,
        "used": None,
        "hours_left": None,
        "reset_local": None,
        "pace": None,
        "weekly_remaining": None,
        "weekly_used": None,
        "weekly_reset_local": None,
        "source": "session-log",
    }
    if best:
        ts, rl = best
        sample_dt = parse_iso(ts)
        age = (now - sample_dt).total_seconds() / 60.0
        p = rl.get("primary") or {}
        s = rl.get("secondary") or {}
        remaining, hours_left, end, assumed = window_state(
            float(p.get("used_percent") or 0.0), p.get("window_minutes") or 300,
            p.get("resets_at"), now)
        w_remaining, _, w_end, w_assumed = window_state(
            float(s.get("used_percent") or 0.0), s.get("window_minutes") or 10080,
            s.get("resets_at"), now)
        data.update({
            "sample_time": local(sample_dt).strftime("%Y-%m-%d %H:%M"),
            "sample_age_min": round(age, 1),
            "stale": age > 30 and not assumed,
            "assumed_fresh": assumed,
            "remaining": round(remaining, 1),
            "used": round(100.0 - remaining, 1),
            "hours_left": round(hours_left, 2) if hours_left is not None else None,
            "reset_local": local(end).strftime("%Y-%m-%d %H:%M") if end else None,
            "weekly_remaining": round(w_remaining, 1),
            "weekly_used": round(100.0 - w_remaining, 1),
            "weekly_reset_local": local(w_end).strftime("%Y-%m-%d %H:%M") if w_end else None,
        })
        if w_assumed and w_end:
            data["weekly_remaining"] = 100.0
    if manual_remaining is not None:
        data["remaining"] = float(manual_remaining)
        data["used"] = round(100.0 - float(manual_remaining), 1)
        data["source"] = "manual"
        if manual_minutes is not None:
            data["hours_left"] = round(float(manual_minutes) / 60.0, 2)
            data["reset_local"] = local(now + timedelta(minutes=float(manual_minutes))).strftime(
                "%Y-%m-%d %H:%M")
        data["stale"] = False
        data["assumed_fresh"] = False
    return data


def main():
    ap = argparse.ArgumentParser(description="Codex quota -> routing tier")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--remaining", type=float, help="manual remaining %% of the 5 h window")
    ap.add_argument("--minutes", type=float, help="minutes until the 5 h window resets")
    ap.add_argument("--note", help="label this reading; appends to the ledger")
    ap.add_argument("--ledger", default=os.path.join("_work", "quota_ledger.md"))
    args = ap.parse_args()

    data = collect(args.remaining, args.minutes)
    if data["remaining"] is None:
        print("[FAIL] no rate-limit sample found under", SESSION_ROOT, file=sys.stderr)
        print("       run one codex call, or pass --remaining <pct> --minutes <m>",
              file=sys.stderr)
        return 1

    tier, pace, cap = classify(data["remaining"], data["hours_left"], data["weekly_remaining"])
    week_capped = cap is not None and data["weekly_remaining"] is not None and \
        data["weekly_remaining"] < 25
    data.update({
        "tier": tier[0],
        "tier_label": tier[2],
        "tier_band": f">={int(tier[1])}% remaining",
        "pace": round(pace, 1) if pace else None,
        "weekly_capped": week_capped,
        "plan": PLAN[tier[0]],
    })

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        d = data
        print(f"== codex quota {d['now'][:16]} ==")
        print(f"primary 5h : used {d['used']:5.1f}%  remaining {d['remaining']:5.1f}%"
              f"  resets {d['reset_local'] or 'unknown'}  (in {fmt_hours(d['hours_left'])})")
        print(f"weekly  7d : used {d['weekly_used']:5.1f}%  remaining {d['weekly_remaining']:5.1f}%"
              f"  resets {d['weekly_reset_local'] or 'unknown'}")
        src = d["source"]
        extra = []
        if d["sample_time"] and src == "session-log":
            extra.append(f"sample {d['sample_time']} ({d['sample_age_min']:.0f} min old)")
        if d["assumed_fresh"]:
            extra.append("window already reset -> assuming 0% used")
        if d["stale"]:
            extra.append("STALE: usage since the sample is invisible")
        print(f"source     : {src}" + ("  |  " + "; ".join(extra) if extra else ""))
        if d["pace"] is not None:
            print(f"pace       : {d['pace']:.1f} pts/h available (healthy 8-18)")
        print(f"tier       : {d['tier']} - {d['tier_label']} ({d['tier_band']})"
              + ("  [capped by weekly quota]" if d["weekly_capped"] else ""))
        print(f"lead       : {d['plan']['lead']}")
        print(f"gpt        : {d['plan']['gpt']}")
        print(f"effort     : {d['plan']['effort']}")
        print(f"batching   : {d['plan']['batch']}")
        print(f"next       : {d['plan']['next']}")
        if d["stale"] or d["assumed_fresh"]:
            print("warning    : confirm in Codex /status before a large batch")

    if args.note:
        path = os.path.abspath(args.ledger)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as fh:
            if new:
                fh.write("# quota ledger\n\n"
                         "| time | tier | 5h left | weekly left | resets in | note |\n"
                         "|---|---|---|---|---|---|\n")
            fh.write(f"| {data['now'][:16].replace('T', ' ')} | {data['tier']} | {data['remaining']:.0f}% | "
                     f"{data['weekly_remaining']:.0f}% | {fmt_hours(data['hours_left'])} | "
                     f"{args.note} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
