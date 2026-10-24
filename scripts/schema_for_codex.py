#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Derive a strict structured-output schema for codex `--output-schema`.

`codex exec --output-schema` forwards the file to the Responses API with
`strict: true`, which rejects ordinary draft-07:
    * every object needs `additionalProperties: false`;
    * **every** property must be listed in `required`;
    * `pattern`, `minLength`, `format` are not part of the strict subset.

So the hand-written contract (`assets/result.schema.json`) cannot be passed verbatim:
keep it for local validation, and pass this derived schema to codex. Optional fields are
made required (the model emits empty arrays/strings); unsupported keywords are dropped so
local validation stays the strict one.

Usage:
    python scripts/schema_for_codex.py --schema assets/result.schema.json --out _work/x.schema.json
    python scripts/schema_for_codex.py --schema assets/result.schema.json --stdout
Exit codes: 0 written, 2 unusable schema.
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KEEP = {"type", "properties", "required", "additionalProperties", "items", "enum",
        "anyOf", "oneOf", "allOf", "description", "title", "default"}


def resolve(node, root, seen):
    """Inline $ref against #/definitions or #/$defs."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen or not ref.startswith("#/"):
            return {"type": "object", "additionalProperties": False, "properties": {}}
        seen = seen | {ref}
        target = root
        for part in ref.lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node = dict(target)
    return node


def strict(node, root, seen=frozenset()):
    node = resolve(node, root, seen)
    if isinstance(node, list):
        return [strict(item, root, seen) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k in KEEP}
    if "properties" in out:
        out["properties"] = {k: strict(v, root, seen) for k, v in out["properties"].items()}
        out["required"] = sorted(out["properties"])
        out["additionalProperties"] = False
    if "items" in out:
        out["items"] = strict(out["items"], root, seen)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in out:
            out[key] = [strict(item, root, seen) for item in out[key]]
    return out


def derive(schema):
    root = schema
    body = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "definitions", "$defs")}
    return strict(body, root)


def main():
    ap = argparse.ArgumentParser(description="derive a strict codex output schema")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out", help="write here (default: alongside, *.strict.json)")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()
    try:
        with open(args.schema, encoding="utf-8-sig") as fh:
            schema = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot read schema: {exc}")
        return 2
    if not isinstance(schema, dict) or schema.get("type") != "object":
        print("[FAIL] the top level of a codex output schema must be an object")
        return 2
    derived = derive(schema)
    text = json.dumps(derived, ensure_ascii=False, indent=2)
    if args.stdout:
        print(text)
        return 0
    out = args.out or os.path.splitext(os.path.abspath(args.schema))[0] + ".strict.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"[OK] strict schema: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
