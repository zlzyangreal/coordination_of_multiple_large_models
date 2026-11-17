"""Model family resolution for the workflow panel and the independence gate.

Order of authority (highest first):
  1. the project's ``models.config.json`` override ``overrides.<model>.family``;
  2. the shipped rule table ``assets/families.json`` (prefix/regex -> family);
  3. unknown - callers must treat an unknown family as "not independent".

The module is importable (``family_of``) and runnable:

    python scripts/families.py --model deepseek-account/deepseek-flash --json
    python scripts/families.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RULES_PATH = os.path.join(ROOT, "assets", "families.json")

_RULES = None


def load_rules(path: str | None = None) -> dict:
    global _RULES
    if _RULES is not None and path is None:
        return _RULES
    target = path or RULES_PATH
    try:
        with open(target, "r", encoding="utf-8-sig") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict) or not isinstance(loaded.get("rules"), list):
            loaded = {"schema": 1, "rules": []}
    except Exception:  # noqa: BLE001 - a missing table degrades to "unknown", never crashes
        loaded = {"schema": 1, "rules": []}
    if path is None:
        _RULES = loaded
    return loaded


def _override_family(project: str | None, model_id: str) -> str | None:
    """Read ``overrides.<model>.family`` from the project's models config."""
    if not project:
        return None
    candidates = [os.path.join(project, "models.config.json"),
                  os.path.join(project, "_work", "models.config.json")]
    for file in candidates:
        if not os.path.isfile(file):
            continue
        try:
            with open(file, "r", encoding="utf-8-sig") as handle:
                config = json.load(handle)
        except Exception:  # noqa: BLE001
            continue
        overrides = config.get("overrides")
        if not isinstance(overrides, dict):
            continue
        for key in (model_id, _short_slug(model_id)):
            entry = overrides.get(key)
            if isinstance(entry, dict) and entry.get("family"):
                return str(entry["family"])
            if isinstance(entry, str) and entry:
                return entry
    return None


def _short_slug(model_id: str) -> str:
    """``provider/<vendor>/<model>`` -> ``<model>`` and ``provider/<model>`` -> ``<model>``."""
    parts = [piece for piece in str(model_id or "").split("/") if piece]
    return parts[-1] if parts else ""


def family_of(model_id: str | None, project: str | None = None) -> dict:
    """Resolve ``{id, family, known, source}`` for a route id or bare slug."""
    raw = str(model_id or "").strip()
    if not raw:
        return {"id": None, "family": None, "known": False, "source": "missing"}
    override = _override_family(project, raw)
    if override:
        return {"id": raw, "family": override, "known": True, "source": "override"}
    for rule in load_rules().get("rules", []):
        pattern = rule.get("pattern")
        family = rule.get("family")
        if not pattern or not family:
            continue
        try:
            if re.search(pattern, raw, re.IGNORECASE):
                return {"id": raw, "family": str(family), "known": True, "source": "builtin"}
        except re.error:
            continue
    return {"id": raw, "family": None, "known": False, "source": "unknown"}


def self_test(extra_models: list[str] | None = None) -> int:
    models = list(extra_models or [])
    if not models:
        models = [
            "deepseek-account/deepseek-flash",
            "deepseek-account/deepseek-v4-pro",
            "our-free-model/big-pickle",
            "our-free-model/mimo-v2.6-flash-free",
            "our-free-model/deepseek-v4.1-flash-free",
            "our-free-model/glm-5.3-flash-free",
            "our-free-model/longcat-2.5-free",
            "our-free-model/iceman-free",
            "our-free-model/inkling-2-small-free",
            "our-free-model/beam-free",
            "our-free-model/flint-40b-free",
            "our-free-model/nemotron-3.5-super-vl-free",
            "our-free-model/hy4-flash-free",
            "our-free-model/amd/Qwen3.8-27B",
            "our-free-model/amd/DeepSeek-V4.1-Flash",
            "our-free-model/amd/MiMo-V2.6-Flash",
            "our-free-model/amd/GLM-5.3-Flash",
            "our-free-model/amd/MiniCPM-V-4.7-35B-A3B",
            "our-free-model/amd/MiniCPM5-2B",
            "our-free-model/amd/Gemma-4-31B-IT",
            "our-free-model/amd/MiniCPM-V-4.7-1B",
            "our-free-model/minimaxai/minimax-m3.1-flash",
            "our-free-model/z-ai/glm-5.3-flash",
            "our-free-model/stepfun-ai/step-5-preview",
            "our-free-model/openai/gpt-oss-20b",
            "our-free-model/opencode/glm-5.3-flash-free",
            "our-free-model/opencode/deepseek-v4.1-flash-free",
        ]
    unknown = [model for model in models if not family_of(model)["known"]]
    problems = []
    if unknown:
        problems.append(f"unknown family for {len(unknown)}: {', '.join(unknown[:5])}")
    deepseek = {family_of(model)["family"] for model in models if "deepseek" in model.lower()}
    if len(deepseek) != 1 or None in deepseek:
        problems.append(f"deepseek slug family mismatch: {sorted(map(str, deepseek))}")
    if family_of("deepseek-account/deepseek-flash")["family"] == \
            family_of("our-free-model/glm-5.3-flash-free")["family"]:
        problems.append("deepseek and glm resolved to the same family")
    if problems:
        print("[FAIL] " + "; ".join(problems))
        return 1
    print(f"[OK] {len(models)} models resolved")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a model family")
    parser.add_argument("--model")
    parser.add_argument("--project")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.model:
        parser.error("--model is required unless --self-test is used")
    info = family_of(args.model, args.project)
    if args.json:
        print(json.dumps(info, ensure_ascii=True))
    else:
        print(f"{info['id']}: {info['family'] or 'unknown'} ({info['source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
