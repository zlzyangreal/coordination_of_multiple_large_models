"""Rule-based assignability for workflow nodes.

The independence rules live in the Python gate (``gate_common.independent``); this CLI is
the thin, batch-shaped projection the workflow panel calls, so the UI never re-implements a
rule. It answers, for one node of one board:

    is this route allowed as the assignee, and if not, which rule forbids it and why?

Rules (first failure wins):
    producer_unassigned  a review node whose producer has no model yet
    family_unknown       reviewer or producer family cannot be established (rule 19)
    same_model           reviewer is exactly the producer's model (rule 2)
    same_family          reviewer shares the producer's family (rule 19)
    team_declined        the channel was refused during the team scan (rule 20)

Usage:
    python scripts/eligibility.py --kind review --producer deepseek-account/deepseek-flash ^
        --routes-file routes.json

Exit codes: 0 ok, 2 bad input. The verdicts are always in the JSON body.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import families  # noqa: E402


def _route_id(route: dict) -> str:
    provider = str(route.get("provider") or "").strip()
    model = str(route.get("model") or "").strip()
    if provider and model:
        return f"{provider}/{model}"
    return model or provider


def _declined_providers(project: str | None) -> set[str]:
    """Providers refused by the team scan, if the project recorded one."""
    if not project:
        return set()
    file = os.path.join(project, "_work", "models", "team_scan.json")
    if not os.path.isfile(file):
        return set()
    try:
        with open(file, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001
        return set()
    declined = set()
    for entry in data.get("declined") or data.get("refused") or []:
        if isinstance(entry, str):
            declined.add(entry)
        elif isinstance(entry, dict):
            value = entry.get("provider") or entry.get("id") or entry.get("channel")
            if value:
                declined.add(str(value))
    return declined


def judge(routes: list[dict], kind: str, producer: str | None, project: str | None = None,
          declined: set[str] | None = None) -> list[dict]:
    declined = declined if declined is not None else _declined_providers(project)
    is_review = str(kind or "") == "review"
    producer_info = families.family_of(producer, project) if producer else {
        "id": None, "family": None, "known": False, "source": "missing"}
    producer_id = str(producer or "")
    producer_short = producer_id.split("/")[-1] if producer_id else ""
    options = []
    for route in routes:
        item = dict(route)
        route_id = _route_id(route)
        info = families.family_of(route_id, project)
        item.update({
            "family": info["family"],
            "familyKnown": bool(info["known"]),
            "familySource": info["source"],
            "eligible": True,
            "code": None,
            "rule": None,
            "reason": None,
        })
        model_short = str(route.get("model") or "").split("/")[-1]
        if is_review:
            if not producer:
                item.update({
                    "eligible": False, "code": "producer_unassigned", "rule": "2",
                    "reason": "产出方还没分配模型，评审暂不可指派",
                })
            elif not producer_info["known"]:
                item.update({
                    "eligible": False, "code": "family_unknown", "rule": "19",
                    "reason": (f"产出方家族未知：请在 models.config.json 声明 "
                               f"overrides.{producer}.family（规则 19：家族未知不算独立评审）"),
                })
            elif not info["known"]:
                item.update({
                    "eligible": False, "code": "family_unknown", "rule": "19",
                    "reason": (f"家族未知：请在 models.config.json 声明 "
                               f"overrides.{route_id}.family（规则 19：家族未知不算独立评审）"),
                })
            elif producer_id == route_id or (producer_short and producer_short == model_short):
                item.update({
                    "eligible": False, "code": "same_model", "rule": "2",
                    "reason": f"与产出方同一模型（{model_short}）——规则 2：生产不能自审",
                })
            elif producer_info["family"] == info["family"]:
                item.update({
                    "eligible": False, "code": "same_family", "rule": "19",
                    "reason": f"与产出方同族（{info['family']}）——规则 19：评审必须来自不同家族",
                })
        if item["eligible"] and declined:
            provider = str(route.get("provider") or "")
            if provider in declined:
                item.update({
                    "eligible": False, "code": "team_declined", "rule": "20",
                    "reason": f"该通道（{provider}）已在团队扫描中被拒绝（规则 20）",
                })
        options.append(item)
    return options


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rule-based workflow assignability")
    parser.add_argument("--kind", default="work", choices=["work", "review", "integrate"])
    parser.add_argument("--producer")
    parser.add_argument("--routes-json")
    parser.add_argument("--routes-file")
    parser.add_argument("--project")
    args = parser.parse_args(argv)
    try:
        if args.routes_file:
            with open(args.routes_file, "r", encoding="utf-8-sig") as handle:
                routes = json.load(handle)
        else:
            routes = json.loads(args.routes_json or "[]")
        if not isinstance(routes, list):
            raise ValueError("routes must be a list")
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "bad_input", "message": str(error)}))
        return 2
    options = judge(routes, args.kind, args.producer, args.project)
    payload = {
        "ok": True,
        "kind": args.kind,
        "producer": families.family_of(args.producer, args.project) if args.producer else None,
        "options": options,
    }
    print(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
