#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Plan review gate: production dispatch requires a reviewed, hash-bound plan.

The gate looks for one chain, not a claim:

    plan.md  <-envelope artifact hash/mtime-  result.json  <-call record-  request_id
              planner.json (producer)          reviewer family != producer family

`--envelope` is mandatory. A v2 envelope (or a call pinned at >= 2.2.0) must have the full
v2 binding in its call record (`protocol_version`, review role, envelope/result/artifact
hashes). Legacy records are accepted only for calls pinned below 2.2.0 with a v1 envelope.
An unknown family never proves independence, and there is no override flag.

Usage:
    python scripts/plan_gate.py check --envelope _work/plan/env_plan.yaml
    python scripts/plan_gate.py check --plan _work/plan/plan.md \
        --result _work/plan/plan.review.json --envelope _work/plan/env_plan.yaml \
        --ledger _work/plan/review_ledger.md --json
    python scripts/plan_gate.py status --envelope _work/plan/env_plan.yaml

Exit codes:
    0  pass
    2  bad input / missing envelope
    3  no review or no call record
    4  invalid result, stale plan, or mixed envelope/result/call evidence
    5  a high/medium finding is still open
    6  not independent, unknown family, or wrong protocol
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402
import validate_result  # noqa: E402

DEFAULT_PLAN = os.path.join("_work", "plan", "plan.md")
DEFAULT_RESULT = os.path.join("_work", "plan", "plan.review.json")
DEFAULT_ENVELOPE = os.path.join("_work", "plan", "env_plan.yaml")
DEFAULT_LEDGER = os.path.join("_work", "plan", "review_ledger.md")


def _same(a, b):
    return common.same_path(a, b)


def _as_text(value):
    return "" if value is None else str(value).strip()


def check_plan(project=".", plan=None, result=None, envelope=None, ledger=None):
    """Return (exit_code, report dict). `project` is the root holding _work/."""
    project = os.path.abspath(project)
    plan = os.path.join(project, plan or DEFAULT_PLAN)
    result = os.path.join(project, result or DEFAULT_RESULT)
    envelope = os.path.join(project, envelope or DEFAULT_ENVELOPE)
    ledger = os.path.join(project, ledger or DEFAULT_LEDGER)
    report = {"project": project, "plan": plan, "result": result, "envelope": envelope,
              "checks": [], "code": 0, "ok": False, "message": ""}

    def fail(code, message):
        report["code"] = code
        report["message"] = message
        report["checks"].append({"ok": False, "code": code, "message": message})
        return code, report

    def ok(message):
        report["checks"].append({"ok": True, "message": message})

    for path, label in ((plan, "plan"), (envelope, "envelope")):
        if not os.path.isfile(path):
            return fail(2, f"{label} not found: {path}")
    if not os.path.isfile(result):
        return fail(3, f"no plan review result: {result} (run the independent review first)")
    if not os.path.isfile(ledger):
        return fail(2, f"plan review ledger not found: {ledger}")

    plan_hash = common.sha256_file(plan)
    plan_mtime = common.mtime_iso(plan)
    report.update({"plan_hash": plan_hash, "plan_mtime": plan_mtime})

    try:
        data, errors, _warnings = validate_result.validate_files(
            result, common.SCHEMA, envelope)
    except (OSError, ValueError) as exc:
        return fail(4, f"cannot read result/envelope: {exc}")
    if errors:
        return fail(4, "review result is invalid: " + "; ".join(errors[:4]))

    env = validate_result.load_envelope(envelope)
    env_artifact = _as_text(env.get("artifact"))
    if env_artifact and not _same(env_artifact, plan):
        return fail(4, f"envelope reviews {env_artifact}, not this plan ({plan})")
    if _as_text(env.get("version_hash")) != plan_hash:
        return fail(4, "envelope version_hash does not match the current plan")
    if _as_text(env.get("artifact_mtime")) != plan_mtime:
        return fail(4, "the plan changed after the envelope was built (mtime mismatch)")
    if _as_text(data.get("artifact_hash")) != plan_hash:
        return fail(4, "result artifact_hash does not match the current plan")
    if _as_text(data.get("artifact_mtime")) != plan_mtime:
        return fail(4, "result artifact_mtime does not match the current plan")
    ok("plan hash + mtime match the envelope and the result")

    request_id = _as_text(data.get("request_id"))
    attempt = common.find_attempt(project, request_id)
    if not attempt:
        return fail(3, f"no call record for {request_id}; a review without a record is "
                       f"not evidence")
    report["request_id"] = request_id
    report["reviewer_model"] = attempt.get("model")
    if attempt.get("status") != "ok":
        return fail(4, f"call {request_id} has status {attempt.get('status')!r}, not 'ok'")
    if attempt.get("ephemeral"):
        return fail(4, f"call {request_id} is ephemeral; review evidence must persist")
    if attempt.get("schema_valid") is False:
        return fail(4, f"call {request_id} is schema-invalid")
    ok(f"call record {request_id} is ok")

    env_version = _as_text(env.get("envelope_version"))
    pinned = _as_text((attempt.get("contract") or {}).get("pinned")) or \
        _as_text(env.get("skill_version"))
    is_v2 = env_version == "2" or (pinned and not common.version_lt(pinned, "2.2.0"))
    report["protocol"] = {"envelope_version": env_version or "1", "pinned": pinned,
                          "v2": bool(is_v2)}
    if is_v2:
        if _as_text(attempt.get("protocol_version")) != "2":
            return fail(4, "v2 evidence requires protocol_version: 2 in the call record")
        if attempt.get("role") not in common.REVIEW_ROLES:
            return fail(4, f"v2 review call has role {attempt.get('role')!r}; expected one "
                           f"of {common.REVIEW_ROLES}")
        required = ("envelope_sha256", "result_path", "result_sha256", "artifact",
                    "artifact_sha256")
        missing = [field for field in required if not attempt.get(field)]
        if missing:
            return fail(4, f"v2 call record is missing binding field(s): {', '.join(missing)}")
        if attempt["envelope_sha256"] != common.sha256_file(envelope):
            return fail(4, "call record envelope_sha256 does not match the envelope file")
        if not _same(attempt["result_path"], result):
            return fail(4, f"call record result_path {attempt['result_path']} is not this "
                           f"result")
        if attempt["result_sha256"] != common.sha256_file(result):
            return fail(4, "the result file changed after the call (sha256 mismatch)")
        if not _same(attempt["artifact"], plan):
            return fail(4, f"call record artifact {attempt['artifact']} is not this plan")
        if attempt["artifact_sha256"] != plan_hash:
            return fail(4, "call record artifact_sha256 does not match the plan")
        ok("v2 call record binds envelope, result and plan")
    else:
        if env_version not in ("", "1"):
            return fail(6, f"unknown envelope version {env_version!r}")
        if pinned and not common.version_lt(pinned, "2.2.0"):
            return fail(6, "a call pinned at >= 2.2.0 must use a v2 envelope and record")
        ok("legacy v1 evidence accepted (pinned below 2.2.0)")

    # Producer identity -----------------------------------------------------
    producer_id = "dsh:auto"
    planner_path = os.path.join(project, "_work", "plan", "planner.json")
    if os.path.isfile(planner_path):
        planner = common.read_json(planner_path, {}) or {}
        producer_id = planner.get("planner") or planner.get("planner_id") or "dsh:auto"
        recorded_hash = _as_text(planner.get("plan_sha256"))
        recorded_mtime = _as_text(planner.get("plan_mtime"))
        if not recorded_hash or recorded_hash != plan_hash:
            return fail(4, "planner.json does not bind the current plan hash; re-plan or "
                           "remove the stale record")
        if recorded_mtime and recorded_mtime != plan_mtime:
            return fail(4, "planner.json plan_mtime does not match the current plan")
        planner_request = _as_text(planner.get("requestId") or planner.get("request_id"))
        planner_attempt = common.find_attempt(project, planner_request) if planner_request \
            else None
        if planner_request and not planner_attempt:
            return fail(4, f"planner call {planner_request} has no call record")
        if planner_attempt and not _model_matches(producer_id, planner_attempt.get("model"),
                                                  project):
            return fail(4, f"planner.json says {producer_id}, but call {planner_request} "
                           f"used {planner_attempt.get('model')}")
        report["planner_request"] = planner_request
    report["producer"] = producer_id

    producer = common.family_of(producer_id, project)
    reviewer = common.family_of(attempt.get("model"), project)
    same, message, producer_info, reviewer_info = common.independent(producer, reviewer)
    report["families"] = {"producer": producer_info, "reviewer": reviewer_info}
    if not same:
        return fail(6, message)
    ok(f"independent families: producer {producer_info['family']} / reviewer "
       f"{reviewer_info['family']}")

    # Open blocking findings -------------------------------------------------
    closed = common.closed_finding_ids(ledger)
    open_items = []
    for finding in data.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") not in ("high", "medium"):
            continue
        finding_id = _as_text(finding.get("id"))
        if not finding_id:
            open_items.append("<finding without id>")
        elif finding_id not in closed:
            open_items.append(finding_id)
    if open_items:
        return fail(5, "plan review has unresolved high/medium finding(s): " +
                    ", ".join(open_items[:8]))
    ok("every high/medium finding is closed in the plan ledger")

    report["ok"] = True
    report["code"] = 0
    report["message"] = "plan review gate passed"
    return 0, report


def _model_matches(planner_id, attempt_model, project):
    if not attempt_model:
        return False
    if planner_id == attempt_model:
        return True
    if planner_id.startswith("codex:"):
        slug = planner_id.split(":", 1)[1]
        if slug in ("auto", "") or slug == attempt_model:
            return True
    left = common.family_of(planner_id, project)
    right = common.family_of(attempt_model, project)
    return bool(left.get("known") and right.get("known")
                and left.get("family") == right.get("family"))


def render(report):
    print(f"plan    : {report['plan']}")
    if report.get("plan_hash"):
        print(f"hash    : {report['plan_hash'][:16]}...")
    if report.get("request_id"):
        print(f"request : {report['request_id']}")
    if report.get("families"):
        prod = report["families"]["producer"]
        rev = report["families"]["reviewer"]
        print(f"producer: {prod.get('id')} [{prod.get('family') or 'unknown'}]")
        print(f"reviewer: {rev.get('id')} [{rev.get('family') or 'unknown'}]")
    for check in report["checks"]:
        print(("  [OK] " if check["ok"] else "  [FAIL] ") + check["message"])
    print("verdict :", "PASS" if report["ok"] else f"BLOCKED ({report['code']}) - "
          f"{report['message']}")


def main():
    ap = argparse.ArgumentParser(description="plan review gate")
    sub = ap.add_subparsers(dest="action", required=True)
    for name in ("check", "status"):
        child = sub.add_parser(name)
        child.add_argument("--project", default=".")
        child.add_argument("--plan", default=DEFAULT_PLAN)
        child.add_argument("--result", default=DEFAULT_RESULT)
        child.add_argument("--envelope", default=DEFAULT_ENVELOPE)
        child.add_argument("--ledger", default=DEFAULT_LEDGER)
        child.add_argument("--json", action="store_true")
    args = ap.parse_args()
    code, report = check_plan(args.project, args.plan, args.result, args.envelope, args.ledger)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        render(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
