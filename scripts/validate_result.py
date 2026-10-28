#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Validate a review result before it enters the ledger. No third-party dependencies.

Checks, in order:
    1. JSON Schema (draft-07 subset used by assets/result.schema.json);
    2. envelope cross-check: request_id, artifact_hash, artifact_mtime must echo;
    3. coverage: every unit in the envelope's units must appear in units_confirmed;
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
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402

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


def _parse_evidence_block(text):
    """Parse the `test_evidence:` list without PyYAML (the runtime has no dependency)."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^test_evidence\s*:", line):
            start = index
            break
    if start is None:
        return []
    head = lines[start].split(":", 1)[1].strip()
    if head == "[]":
        return []
    items, current = [], None
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        item = re.match(r"^\s*-\s+id:\s*(.+?)\s*$", line)
        if item:
            if current:
                items.append(current)
            current = {"id": _unquote(item.group(1))}
            continue
        if current is None:
            continue
        field = re.match(r"^\s+(command|exit_code|at|log_path|log_sha256|summary):\s*(.*)$", line)
        if field:
            key, raw = field.group(1), field.group(2).strip()
            if key == "exit_code":
                try:
                    current[key] = int(raw)
                except ValueError:
                    current[key] = raw
            elif raw.startswith('"'):
                try:
                    current[key] = json.loads(raw)
                except ValueError:
                    current[key] = _unquote(raw)
            else:
                current[key] = _unquote(raw)
    if current:
        items.append(current)
    return items


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_envelope(path):
    """Flat envelope + test_evidence: pyyaml when present, regex fallback otherwise."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()

    def norm(value):
        # pyyaml turns ISO timestamps into datetime; compare as strings
        return value.isoformat() if hasattr(value, "isoformat") else value

    try:
        import yaml  # noqa: F401
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict):
            out = {k: norm(v) for k, v in data.items()}
            out["test_evidence"] = data.get("test_evidence") or []
            return out
    except Exception:  # noqa: BLE001
        pass
    out = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z_][\w]*):\s*(.+?)\s*$', line)
        if m and m.group(1) not in ("units", "prior_findings", "acceptance", "scope",
                                   "test_evidence"):
            out[m.group(1)] = m.group(2).strip().strip('"')
    out["test_evidence"] = _parse_evidence_block(text)
    return out


def envelope_units(env):
    units = env.get("units") or []
    if isinstance(units, dict):
        units = list(units.values())
    return [str(u["id"]) for u in units if isinstance(u, dict) and u.get("id")]


def _abs_path(path):
    path = str(path or "")
    return path if path and os.path.isabs(path) else os.path.abspath(path)


def check_facts(env, result, errors):
    """A v2 envelope's facts pack must still hash to the value it recorded."""
    if str(env.get("envelope_version") or "") != "2":
        return
    declared_version = env.get("facts_version")
    declared_hash = env.get("facts_hash")
    facts_path = env.get("facts_path")
    if not declared_version or not declared_hash or not facts_path:
        errors.append("$.facts_version: a v2 envelope must carry facts_path/version/hash")
        return
    if str(result.get("facts_version") or "") != str(declared_version):
        errors.append(f"$.facts_version: {result.get('facts_version')!r} != envelope "
                      f"{declared_version!r}")
    info = common.facts_info(_abs_path(facts_path))
    if not info["exists"]:
        errors.append(f"facts pack not found: {facts_path}")
        return
    if info["actual_hash"] != str(declared_hash):
        errors.append("facts pack changed after the envelope was built; re-freeze and "
                      "rebuild the envelope")


def check_test_refs(env, result, errors, warnings):
    """Evidence exists -> the result must quote a log line the envelope summary lacks."""
    evidence = env.get("test_evidence") or []
    refs = result.get("test_refs")
    if not evidence:
        if refs:
            warnings.append("test_refs present without test_evidence in the envelope")
        return
    if not isinstance(refs, list) or not refs:
        errors.append("$.test_refs: the envelope carries test evidence; cite at least one "
                      "entry in test_refs")
        return
    by_id = {str(item.get("id")): item for item in evidence if isinstance(item, dict)}
    for index, ref in enumerate(refs):
        path = f"$.test_refs[{index}]"
        if not isinstance(ref, dict):
            errors.append(f"{path}: expected an object")
            continue
        ref_id = str(ref.get("id") or "")
        item = by_id.get(ref_id)
        if item is None:
            errors.append(f"{path}: unknown evidence id {ref_id!r}")
            continue
        if str(ref.get("command") or "") != str(item.get("command") or ""):
            errors.append(f"{path}.command: does not match evidence {ref_id}")
        if ref.get("exit_code") != item.get("exit_code"):
            errors.append(f"{path}.exit_code: {ref.get('exit_code')!r} != "
                          f"evidence {item.get('exit_code')!r}")
        if str(ref.get("log_sha256") or "") != str(item.get("log_sha256") or ""):
            errors.append(f"{path}.log_sha256: does not match evidence {ref_id}")
        observed = str(ref.get("observed") or "")
        if len(observed) < 20:
            errors.append(f"{path}.observed: needs at least 20 characters from the log")
            continue
        log_path = _abs_path(item.get("log_path"))
        try:
            with open(log_path, encoding="utf-8-sig", errors="replace") as fh:
                log_text = fh.read()
        except OSError:
            errors.append(f"{path}: evidence log not readable: {item.get('log_path')}")
            continue
        if observed not in log_text:
            errors.append(f"{path}.observed: the excerpt is not in the evidence log")
            continue
        summary = str(item.get("summary") or "")
        command = str(item.get("command") or "")
        if observed in summary or observed in command:
            errors.append(f"{path}.observed: the excerpt is already in the envelope summary; "
                          f"quote a line that only exists in the log")


def validate_files(result_path, schema_path=DEFAULT_SCHEMA, envelope_path=None):
    """Return (result, errors, warnings); raises OSError/ValueError on unreadable input."""
    with open(result_path, encoding="utf-8-sig") as fh:
        result = json.load(fh)
    with open(schema_path, encoding="utf-8-sig") as fh:
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
        wanted = envelope_units(env)
        confirmed = {str(t.get("unit")) for t in result.get("units_confirmed") or []
                     if isinstance(t, dict)}
        for unit in wanted:
            if unit not in confirmed:
                errors.append(f"$.units_confirmed: unit '{unit}' from the envelope was not confirmed")
        seen = {str(u) for u in result.get("units_seen") or []}
        missing = [u for u in wanted if u not in seen]
        if missing:
            errors.append(f"$.units_seen: envelope units not reported as seen: {missing}")
        check_facts(env, result, errors)
        check_test_refs(env, result, errors, warnings)

    findings = result.get("findings") if isinstance(result, dict) else None
    if isinstance(findings, list):
        advisory = sum(1 for f in findings
                       if isinstance(f, dict) and f.get("origin") == "advisory-derived")
        if advisory:
            warnings.append(
                f"{advisory} finding(s) marked advisory-derived: those units are NOT "
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
