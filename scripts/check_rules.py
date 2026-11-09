#!/usr/bin/env python3
"""Verify (or generate) the forty-rule contract manifest and its rendered text.

The manifest is the machine-readable form of SKILL.md's numbered non-negotiables. Rules 1-38 are
the v2.4.0 baseline and must stay verbatim; rules 39-40 are the v3.0.0 additions. `--generate`
writes `lib/rule-manifest.json` and `lib/rules.md`; the default check fails if any clause drifts
from SKILL.md, if an id is missing, or if the v2.4.0 baseline hash changes.

Exit codes: 0 ok, 1 drift/missing, 2 bad invocation.
"""
import argparse
import hashlib
import json
import os
import re
import sys

RULE_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(.*)$")
MECHANISMS = {
    1: ["prompt"], 2: ["prompt", "python"], 3: ["python"], 4: ["python"], 5: ["python"],
    6: ["python"], 7: ["python"], 8: ["python"], 9: ["python"], 10: ["python", "native"],
    11: ["python", "prompt"], 12: ["python"], 13: ["native"], 14: ["native", "prompt"],
    15: ["python"], 16: ["prompt"], 17: ["python", "prompt"], 18: ["python"],
    19: ["python"], 20: ["python", "prompt"], 21: ["prompt", "python"], 22: ["python"],
    23: ["python"], 24: ["python"], 25: ["python"], 26: ["python"],
    27: ["python", "native"], 28: ["python"], 29: ["python"], 30: ["python", "native"],
    31: ["python", "prompt"], 32: ["python"], 33: ["python"], 34: ["python", "native"],
    35: ["python"], 36: ["python"], 37: ["native", "python"], 38: ["prompt", "docs"],
    39: ["native"], 40: ["native"],
}


def parse_skill(path):
    text = open(path, encoding="utf-8-sig").read()
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "## Non-negotiables":
            start = index + 1
            break
    if start is None:
        raise SystemExit("SKILL.md has no '## Non-negotiables' section")
    rules = {}
    current = None
    for line in lines[start:]:
        if line.startswith("## "):
            break
        match = RULE_RE.match(line)
        if match:
            if current is not None:
                rules[current[0]] = current
            current = [int(match.group(1)), match.group(2).strip(), [match.group(3).strip()]]
            continue
        if current is not None:
            if line.strip():
                current[2].append(line.strip())
    if current is not None:
        rules[current[0]] = current
    return {number: {"title": title, "text": " ".join(part for part in parts if part)}
            for number, title, parts in rules.values()}


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def digest(rules):
    body = "\n".join(normalize(rules[i]["text"]) for i in sorted(rules))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="verify the 40-rule contract")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--generate", action="store_true", help="write the manifest and rules.md")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    skill = os.path.join(root, "SKILL.md")
    manifest_path = os.path.join(root, "lib", "rule-manifest.json")
    rules_path = os.path.join(root, "lib", "rules.md")
    baseline_path = os.path.join(root, "assets", "rule-baseline.v24.sha256")
    if not os.path.isfile(skill):
        print(f"[FAIL] missing {skill}", file=sys.stderr)
        return 2
    rules = parse_skill(skill)
    missing = [i for i in range(1, 41) if i not in rules]
    if missing:
        print(f"[FAIL] SKILL.md is missing rule ids: {missing}", file=sys.stderr)
        return 1
    extra = [i for i in sorted(rules) if i > 40]
    if extra:
        print(f"[FAIL] SKILL.md has unexpected rule ids: {extra}", file=sys.stderr)
        return 1
    if args.generate:
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        manifest = {
            "version": "3.0.0",
            "source": "SKILL.md v3.0.0 (rules 1-38 verbatim from v2.4.0; 39-40 added)",
            "baseline_sha256": digest({i: rules[i] for i in range(1, 39)}),
            "rules": [
                {"id": i, "title": rules[i]["title"], "mechanism": MECHANISMS[i],
                 "text": normalize(rules[i]["text"])}
                for i in range(1, 41)
            ],
        }
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        with open(rules_path, "w", encoding="utf-8") as handle:
            handle.write("# Multi-model coordination contract (v3.0.0)\n\n")
            handle.write("Read this before acting. Then call "
                         "`coordination({action:\"contract\"})`; until then the host denies every "
                         "other tool call.\n\n")
            for rule in manifest["rules"]:
                handle.write(f"{rule['id']}. **{rule['title']}** {rule['text']}\n\n")
        print(f"[OK] generated {manifest_path} and {rules_path}")
    else:
        if not os.path.isfile(manifest_path):
            print(f"[FAIL] missing manifest: {manifest_path} (run --generate)", file=sys.stderr)
            return 1
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        entries = {int(row["id"]): row for row in manifest.get("rules", [])}
        problems = []
        for i in range(1, 41):
            if i not in entries:
                problems.append(f"rule {i} missing from manifest")
                continue
            if normalize(entries[i]["text"]) != normalize(rules[i]["text"]):
                problems.append(f"rule {i} differs from SKILL.md")
            if not entries[i].get("mechanism"):
                problems.append(f"rule {i} has no mechanism")
        baseline = digest({i: rules[i] for i in range(1, 39)})
        if manifest.get("baseline_sha256") != baseline:
            problems.append("v2.4.0 baseline hash changed (rules 1-38 must stay verbatim)")
        if os.path.isfile(baseline_path):
            frozen = open(baseline_path, encoding="utf-8").read().strip()
            if frozen and frozen != baseline:
                problems.append("rules 1-38 no longer match assets/rule-baseline.v24.sha256")
        if os.path.isfile(rules_path):
            rendered = open(rules_path, encoding="utf-8").read()
            if "coordination({action:\"contract\"})" not in rendered:
                problems.append("lib/rules.md has no first-action instruction")
            for i in range(1, 41):
                if f"\n{i}. " not in rendered:
                    problems.append(f"lib/rules.md is missing rule {i}")
        else:
            problems.append("lib/rules.md is missing")
        if problems:
            for problem in problems:
                print(f"[FAIL] {problem}", file=sys.stderr)
            return 1
        print(f"[OK] 40 rules verified; baseline {baseline[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
