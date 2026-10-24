#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Validate a review result before it enters the ledger. No third-party dependencies.

Checks, in order:
    1. JSON Schema (draft-07 subset used by assets/result.schema.json);
    2. envelope cross-check: request_id, artifact_hash, artifact_mtime must echo;
    3. coverage: every page in the envelope's page_map must appear in title_confirmed;
    4. origin accounting: independent vs advisory-derived findings (advisory-derived
       chapters are not independently reviewed, whatever the score says).

Usage:
    python scripts/validate_result.py --result _work/review/result_v6.json
    python scripts/validate_result.py --result r.json --envelope e.yaml
    python scripts/validate_result.py --result r.json --quiet     # exit code only

Exit codes: 0 valid, 1 invalid, 2 unusable input.
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(HERE, "..", "assets", "result.schema.json")
TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def type_ok(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES.get(name, object))


def check(node, schema, path, errors):
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if t:
        names = t if isinstance(t, list) else [t]
        if not any(type_ok(node, n) for n in names):
            errors.append(f"{path}: expected {'/'.join(names)}, got {type(node).__name__}")
            return
    if "enum" in schema and node not in schema["enum"]:
        errors.append(f"{path}: {node!r} not in {schema['enum']}")
    if isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} chars")
        if "pattern" in schema and not re.search(schema["pattern"], node):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                errors.append(f"{path}: missing required field '{key}'")
        props = schema.get("properties", {})
        for key, value in node.items():
            if key in props:
                check(value, props[key], f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field '{key}'")
    if isinstance(node, list):
        if "minItems" in schema and len(node) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(node):
                check(item, schema["items"], f"{path}[{i}]", errors)


def load_envelope(path):
    """Flat envelope: pyyaml when present, regex fallback otherwise."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    def norm(value):
        # pyyaml turns ISO timestamps into datetime; compare as strings
        return value.isoformat() if hasattr(value, "isoformat") else value

    try:
        import yaml  # noqa: F401
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            return {k: norm(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    out = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z_][\w]*):\s*(.+?)\s*$', line)
        if m and not m.group(1).startswith("page"):
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def envelope_pages(env):
    pages = env.get("page_map") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    result = []
    for item in pages if isinstance(pages, list) else []:
        if isinstance(item, dict) and isinstance(item.get("page"), int):
            result.append(item["page"])
    return result


def validate_files(result_path, schema_path=DEFAULT_SCHEMA, envelope_path=None):
    """Return (result, errors, warnings); raises OSError/ValueError on unreadable input."""
    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)

    errors, warnings = [], []
    check(result, schema, "$", errors)

    if envelope_path:
        env = load_envelope(envelope_path)
        for field, key in (("request_id", "request_id"),
                           ("artifact_hash", "version_hash"),
                           ("artifact_mtime", "artifact_mtime")):
            want, got = env.get(key), result.get(field)
            if want and got and str(want).strip() != str(got).strip():
                errors.append(f"$.{field}: {got!r} != envelope {key} {want!r}")
        wanted_pages = envelope_pages(env)
        confirmed = {t.get("page") for t in result.get("title_confirmed") or []
                     if isinstance(t, dict)}
        for page in wanted_pages:
            if page not in confirmed:
                errors.append(f"$.title_confirmed: page {page} from page_map was not confirmed")
        seen = set(result.get("pages_seen") or [])
        missing = [p for p in wanted_pages if p not in seen]
        if missing:
            errors.append(f"$.pages_seen: envelope pages not reported as seen: {missing}")

    findings = result.get("findings") if isinstance(result, dict) else None
    if isinstance(findings, list):
        advisory = sum(1 for f in findings
                       if isinstance(f, dict) and f.get("origin") == "advisory-derived")
        if advisory:
            warnings.append(
                f"{advisory} finding(s) marked advisory-derived: those chapters are NOT "
                f"independently reviewed - say so in the delivery note")
    return result, errors, warnings


def main():
    ap = argparse.ArgumentParser(description="validate a review result")
    ap.add_argument("--result", required=True)
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--envelope")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        result, errors, warnings = validate_files(args.result, args.schema, args.envelope)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot read input: {exc}")
        return 2

    findings = result.get("findings") if isinstance(result, dict) else None
    if not args.quiet:
        print(f"result : {os.path.abspath(args.result)}")
        print(f"schema : {os.path.abspath(args.schema)}")
        if isinstance(findings, list):
            advisory = sum(1 for f in findings
                           if isinstance(f, dict) and f.get("origin") == "advisory-derived")
            notice = sum(1 for f in findings
                         if isinstance(f, dict) and f.get("severity") == "notice")
            print(f"findings: {len(findings)} (independent {len(findings) - advisory}, "
                  f"advisory-derived {advisory}, notice {notice})")
        for w in warnings:
            print(f"[WARN] {w}")
        for e in errors[:25]:
            print(f"[FAIL] {e}")
        if len(errors) > 25:
            print(f"[FAIL] ... {len(errors) - 25} more")
        print("verdict:", "valid" if not errors else f"invalid ({len(errors)} problem(s))")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
