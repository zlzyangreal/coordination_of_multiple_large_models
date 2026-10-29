#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Review budget, production accounting, and the delivery gate.

One event log (`_work/gates/events.jsonl`) records every deviation, raise, minute notice,
adoption and tier waiver. Review attempts are counted from call records plus an atomic
pending marker written before launch, so a crashed process is still counted and two
concurrent reviews cannot both pass on the same prior count.

Usage:
    python scripts/review_budget.py check --milestone M1 --json
    python scripts/review_budget.py raise --to 3 --reason "third round needed for X"
    python scripts/review_budget.py raise --to 3 --milestone M1 --reason ...
    python scripts/review_budget.py note-minutes --milestone M1 --reason "over by 12 min"
    python scripts/review_budget.py adopt --request-id R... --decision adopted --reason ...
    python scripts/review_budget.py waive --tier S --approved-by user \
        --approval-ref "..." --reason "..."
    python scripts/review_budget.py report [--json]
    python scripts/review_budget.py deliver [--tier S|A|B|C|R|U] [--json]

Exit codes:
    0  ok
    2  bad input / missing facts / gate refused before launch
    3  third review round needs --why-unclosed
    4  review budget overrun needs --budget-change or a raise
    5  delivery gate blocked
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facts_pack  # noqa: E402
import gate_common as common  # noqa: E402

REVIEW = common.REVIEW_ROLES
TIER_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "R": 1, "U": 0}


def _facts_path(project):
    return os.path.join(project, "_work", "facts_pack.md")


def _facts(project):
    path = _facts_path(project)
    code, message, info = facts_pack.check(path)
    return code, message, info


def _prior(project, milestone, request_id=None):
    attempts = []
    for attempt in common.all_attempts(project):
        if attempt.get("role") not in REVIEW:
            continue
        if attempt.get("milestone") != milestone:
            continue
        if request_id and attempt.get("request_id") == request_id:
            continue
        attempts.append(attempt)
    return attempts


def _events(project, kind=None, milestone=None):
    events = common.read_events(project)
    if kind:
        events = [event for event in events if event.get("kind") == kind]
    if milestone:
        events = [event for event in events if event.get("milestone") == milestone]
    return events


def _evaluate(project, milestone, request_id=None, why_unclosed=None, budget_change=None,
              facts=None):
    """Return (code, info). Does not write anything."""
    if not milestone:
        return 2, {"message": "--milestone is mandatory for a review call"}
    code, message, info = facts or _facts(project)
    if code != 0:
        return 2, {"message": f"facts pack is not usable: {message}"}
    prior = _prior(project, milestone, request_id)
    count = len(prior)
    minutes = round(sum(float(item.get("duration_s") or 0) for item in prior) / 60.0, 1)
    budget_calls = info["budgets"].get("review_calls", 2)
    budget_minutes = info["budgets"].get("review_minutes", 30)
    data = {
        "milestone": milestone,
        "request_id": request_id,
        "prior_attempts": count,
        "prior_minutes": minutes,
        "review_calls": budget_calls,
        "review_minutes": budget_minutes,
        "why_unclosed": why_unclosed,
        "budget_change": budget_change,
        "attempts": [item["request_id"] for item in prior],
    }
    if count >= 2 and not why_unclosed:
        return 3, dict(data, message=(f"{count} review rounds already ran for {milestone}; "
                                      f"pass --why-unclosed \"why rounds 1-2 did not close\" "
                                      f"before round {count + 1}"))
    if count >= budget_calls and not budget_change:
        return 4, dict(data, message=(f"{count} review calls >= budget {budget_calls} for "
                                      f"{milestone}; pass --budget-change \"reason\" (one-off) "
                                      f"or run `review_budget.py raise --to N --reason ...` "
                                      f"(changes the facts pack)"))
    return 0, data


def _pending_path(project, request_id):
    return os.path.join(project, common.PENDING, f"{request_id}.json")


def finalize(project, request_id):
    path = _pending_path(project, request_id)
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def preflight(project, milestone, role, request_id, why_unclosed=None, budget_change=None):
    """Check the gates, then reserve this attempt atomically. Returns (code, info)."""
    if role not in REVIEW:
        return 2, {"message": f"role {role!r} is not a review role"}
    if not request_id:
        return 2, {"message": "a review reservation needs --request-id"}
    sys.path.insert(0, HERE)
    try:
        import model_config as cfg_mod  # noqa: PLC0415
        lock = cfg_mod.project_lock(project)
    except Exception:  # noqa: BLE001
        lock = None
    context = lock if lock else _null_context()
    with context:
        existing = common.find_attempt(project, request_id)
        if existing:
            return 2, {"message": (f"request id {request_id} was already used "
                                   f"({existing.get('status')}); a retry needs a new "
                                   f"request id and a new envelope")}
        code, data = _evaluate(project, milestone, request_id, why_unclosed, budget_change)
        if code != 0:
            return code, data
        os.makedirs(os.path.join(project, common.PENDING), exist_ok=True)
        common.write_json(_pending_path(project, request_id), {
            "request_id": request_id,
            "status": "pending",
            "role": role,
            "milestone": milestone,
            "protocol_version": 2,
            "started_at": common.now_iso(),
            "cwd": project,
            "why_unclosed": why_unclosed,
            "budget_change": budget_change,
        })
        if why_unclosed:
            common.append_event(project, "why", milestone=milestone, request_id=request_id,
                                reason=why_unclosed)
        if budget_change:
            common.append_event(project, "override", milestone=milestone,
                                request_id=request_id, reason=budget_change,
                                before=data["prior_attempts"],
                                after=data["review_calls"])
        data["reserved"] = True
        return 0, data


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def check_gate(project, milestone, request_id=None, why_unclosed=None, budget_change=None):
    return _evaluate(project, milestone, request_id, why_unclosed, budget_change)


def raise_budget(project, to, reason, milestone=None):
    code, message, info = _facts(project)
    if code != 0:
        return 2, {"message": f"facts pack is not usable: {message}"}
    if to <= 0:
        return 2, {"message": "--to must be positive"}
    before = info["budgets"].get("review_calls", 2)
    try:
        new_info = facts_pack.set_budget(_facts_path(project), "review_calls", to,
                                         f"review_calls {before}->{to}: {reason}")
    except (OSError, ValueError) as exc:
        return 2, {"message": str(exc)}
    common.append_event(project, "raise", milestone=milestone or "*", reason=reason,
                        before=before, after=to)
    return 0, {"before": before, "after": to, "facts_version": new_info.get("version"),
               "message": f"review_calls {before}->{to}; facts pack re-frozen"}


def note_minutes(project, milestone, reason, minutes=None):
    if not milestone or not reason.strip():
        return 2, {"message": "note-minutes needs --milestone and --reason"}
    if minutes is None:
        minutes = round(sum(float(item.get("duration_s") or 0) for item in
                            _prior(project, milestone)) / 60.0, 1)
    common.append_event(project, "minutes", milestone=milestone, reason=reason,
                        after=minutes)
    return 0, {"milestone": milestone, "minutes": minutes, "message": "minutes notice logged"}


def adopt(project, request_id, decision, reason):
    if decision not in ("adopted", "rejected", "pending"):
        return 2, {"message": "decision must be adopted / rejected / pending"}
    if not reason.strip():
        return 2, {"message": "adopt needs --reason"}
    if not common.find_attempt(project, request_id):
        return 2, {"message": f"no call record for {request_id}"}
    common.append_event(project, "adopt", request_id=request_id, reason=reason,
                        after=decision)
    return 0, {"request_id": request_id, "decision": decision,
               "message": f"recorded {decision}"}


def waive(project, tier, approved_by, approval_ref, reason):
    if tier not in TIER_ORDER or tier == "U":
        return 2, {"message": "--tier must be S/A/B/C/R"}
    if approved_by != "user":
        return 2, {"message": "an S/A deviation must be approved by the user"}
    reference = os.path.abspath(approval_ref)
    if not os.path.isfile(reference):
        return 2, {"message": f"--approval-ref must be an existing stored approval file: "
                              f"{reference}"}
    if not reason.strip():
        return 2, {"message": "waive needs --reason"}
    digest = common.sha256_file(reference)
    common.append_event(project, "deviation", reason=reason, after=tier,
                        approved_by=approved_by, approval_ref=reference,
                        approval_sha256=digest)
    return 0, {"tier": tier, "approved_by": approved_by, "approval_ref": reference,
               "approval_sha256": digest, "message": "deviation recorded"}


def _adoption(project, request_id):
    events = [event for event in _events(project, "adopt")
              if event.get("request_id") == request_id]
    return events[-1].get("after") if events else None


def _plan_work_packages(project):
    """Map work-package id -> declared output path from the reviewed plan table."""
    path = os.path.join(project, common.PLAN_DIR, "plan.md")
    if not os.path.isfile(path):
        return None
    rows = common.parse_table(path)
    packages = {}
    for row in rows:
        package_id = (row.get("id") or "").strip()
        output = (row.get("output") or "").strip().strip("`")
        if package_id and output and "dod" in row:
            packages[package_id] = output
    return packages or None


def _output_matches(artifact, output, project):
    artifact_path = common.norm_path(artifact)
    declared = output.strip().strip("`")
    declared_path = declared if os.path.isabs(declared) \
        else os.path.join(project, declared)
    declared_path = os.path.abspath(declared_path)
    if artifact_path == common.norm_path(declared_path):
        return True
    if declared.endswith(("/", "\\")) or os.path.isdir(declared_path):
        prefix = common.norm_path(declared_path) + os.sep
        return artifact_path.startswith(prefix)
    return False


def _tier(project, explicit=None):
    observed = [item.get("routing_tier") for item in common.all_attempts(project)
                if item.get("routing_tier") in TIER_ORDER]
    highest = max(observed, key=lambda tier: TIER_ORDER[tier]) if observed else None
    if explicit:
        if explicit not in TIER_ORDER:
            return None, f"unknown tier {explicit!r}"
        if highest and TIER_ORDER[highest] > TIER_ORDER[explicit]:
            return None, (f"--tier {explicit} is lower than a recorded tier {highest}; "
                          f"fail closed")
        return explicit, None
    if not highest:
        return None, ("no routing tier is recorded; pass --tier S|A|B|C|R|U explicitly "
                      "(U means no GPT budget evidence)")
    return highest, None


def deliver(project, tier=None):
    resolved, error = _tier(project, tier)
    if error:
        return 5, {"message": error}
    attempts = common.all_attempts(project)
    events = common.read_events(project)
    problems = []
    notes = []

    produce = [item for item in attempts if item.get("role") == "produce"
               and item.get("status") == "ok"]
    valid = []
    for item in produce:
        request_id = item["request_id"]
        wp = (item.get("work_package") or "").strip()
        artifact = item.get("produce_artifact")
        if not wp:
            problems.append(f"{request_id}: produce call has no work package")
            continue
        if not artifact or not os.path.isfile(artifact):
            problems.append(f"{request_id}: produced artifact is missing ({artifact})")
            continue
        if not item.get("produce_artifact_hash"):
            problems.append(f"{request_id}: produce call has no artifact hash")
            continue
        if item["produce_artifact_hash"] != common.sha256_file(artifact):
            problems.append(f"{request_id}: artifact hash no longer matches")
            continue
        decision = _adoption(project, request_id)
        if decision not in ("adopted", "rejected"):
            problems.append(f"{request_id}: adoption is {decision or 'pending'}")
            continue
        valid.append(item)

    if resolved in ("S", "A") and not valid:
        deviation = [event for event in events if event.get("kind") == "deviation"
                     and event.get("after") in ("S", "A")
                     and event.get("approval_ref")
                     and os.path.isfile(str(event.get("approval_ref")))
                     and event.get("approval_sha256")
                     == common.sha256_file(str(event.get("approval_ref")))]
        if deviation:
            notes.append(f"tier {resolved}: no GPT-produced unit; user-approved deviation "
                         f"recorded ({deviation[-1]['approval_ref']})")
        else:
            problems.append(f"tier {resolved}: no GPT-produced work package with an "
                            f"adoption decision (record a stored-approval deviation with "
                            f"`review_budget.py waive`)")

    if valid:
        packages = _plan_work_packages(project)
        for item in valid:
            wp = item["work_package"]
            if not packages:
                problems.append("no reviewed plan work-package table found for produced units")
                break
            if wp not in packages:
                problems.append(f"{item['request_id']}: work package {wp!r} is not in the "
                                f"reviewed plan table")
                continue
            if not _output_matches(item["produce_artifact"], packages[wp], project):
                problems.append(f"{item['request_id']}: artifact {item['produce_artifact']} "
                                f"does not match the declared output for {wp} "
                                f"({packages[wp]})")

    by_milestone = {}
    for item in attempts:
        if item.get("role") in REVIEW and item.get("milestone"):
            by_milestone.setdefault(item["milestone"], []).append(item)
    for milestone, items in by_milestone.items():
        code, message, info = _facts(project)
        if code != 0:
            problems.append(f"{milestone}: {message}")
            continue
        budget_calls = info["budgets"].get("review_calls", 2)
        budget_minutes = info["budgets"].get("review_minutes", 30)
        extra = max(0, len(items) - budget_calls)
        overrides = len([event for event in events
                         if event.get("kind") == "override"
                         and event.get("milestone") == milestone])
        if extra > overrides:
            problems.append(f"{milestone}: {len(items)} review calls exceed budget "
                            f"{budget_calls} without {extra} override row(s)")
        minutes = sum(float(item.get("duration_s") or 0) for item in items) / 60.0
        if minutes > budget_minutes and not [event for event in events
                                             if event.get("kind") == "minutes"
                                             and event.get("milestone") == milestone]:
            problems.append(f"{milestone}: review minutes {minutes:.0f} exceed "
                            f"{budget_minutes} without a minutes notice")

    summary = {
        "tier": resolved,
        "attempts": len(attempts),
        "review_calls": sum(1 for item in attempts if item.get("role") in REVIEW),
        "produce_calls": len(produce),
        "valid_produce_units": len(valid),
        "notes": notes,
        "problems": problems,
    }
    if problems:
        return 5, dict(summary, message="; ".join(problems[:6]))
    return 0, dict(summary, message="delivery gate passed")


def report(project, as_json=False):
    attempts = common.all_attempts(project)
    events = common.read_events(project)
    milestones = sorted({item.get("milestone") for item in attempts
                         if item.get("role") in REVIEW and item.get("milestone")})
    rows = []
    for milestone in milestones:
        items = [item for item in attempts if item.get("role") in REVIEW
                 and item.get("milestone") == milestone]
        rows.append({
            "milestone": milestone,
            "review_calls": len(items),
            "minutes": round(sum(float(item.get("duration_s") or 0)
                                for item in items) / 60.0, 1),
            "produce_calls": sum(1 for item in attempts if item.get("role") == "produce"
                                 and item.get("milestone") == milestone),
            "adoptions": len([event for event in events if event.get("kind") == "adopt"
                              and event.get("request_id") in
                              {item["request_id"] for item in attempts}]),
        })
    data = {"milestones": rows, "events": len(events),
            "attempts": len(attempts)}
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no review attempts recorded")
    else:
        print(f"{'milestone':<14}{'reviews':>8}{'minutes':>9}{'produce':>9}{'adopt':>7}")
        for row in rows:
            print(f"{row['milestone']:<14}{row['review_calls']:>8}{row['minutes']:>9.1f}"
                  f"{row['produce_calls']:>9}{row['adoptions']:>7}")
    print(f"events: {len(events)}; attempts: {len(attempts)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="review budget and production gate")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("check")
    p.add_argument("--milestone", required=True)
    p.add_argument("--request-id", default=None)
    p.add_argument("--why-unclosed", default=None)
    p.add_argument("--budget-change", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("raise")
    p.add_argument("--to", type=int, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--milestone", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("note-minutes")
    p.add_argument("--milestone", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("adopt")
    p.add_argument("--request-id", required=True)
    p.add_argument("--decision", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("waive")
    p.add_argument("--tier", required=True)
    p.add_argument("--approved-by", default="user")
    p.add_argument("--approval-ref", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("report")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("deliver")
    p.add_argument("--tier", default=None)
    p.add_argument("--json", action="store_true")

    args = ap.parse_args()
    project = os.path.abspath(args.project)
    if args.action == "check":
        code, data = check_gate(project, args.milestone, args.request_id,
                                args.why_unclosed, args.budget_change)
    elif args.action == "raise":
        code, data = raise_budget(project, args.to, args.reason, args.milestone)
    elif args.action == "note-minutes":
        code, data = note_minutes(project, args.milestone, args.reason)
    elif args.action == "adopt":
        code, data = adopt(project, args.request_id, args.decision, args.reason)
    elif args.action == "waive":
        code, data = waive(project, args.tier, args.approved_by, args.approval_ref,
                           args.reason)
    elif args.action == "deliver":
        code, data = deliver(project, args.tier)
    else:
        return report(project, getattr(args, "json", False))
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if code == 0 else "[FAIL] ") + str(data.get("message", "")))
        if code != 0 and data.get("problems"):
            for problem in data["problems"]:
                print(f"  - {problem}")
    return code


if __name__ == "__main__":
    sys.exit(main())
