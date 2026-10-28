#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One deterministic documentation check for CI and release.

The v2.1 postmortem listed the hand-editing defects this catches: version mismatch, stale test
counts, duplicate trigger words, literal `\\n` in prose, old `F:` install paths, broken rule
numbering, and scripts/assets that are not documented (or documented but absent).

Usage:
    python scripts/check_docs.py --root .
    python scripts/check_docs.py --root . --run-tests
    python scripts/check_docs.py --root . --test-count 196 --json

Exit codes:
    0  consistent
    1  issues found
    2  missing SKILL.md / README.md / unusable input
"""
import argparse
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEXT_DOCS = ("SKILL.md", "README.md")


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def front_matter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    block = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)
    data = {}
    for key in ("version", "updated", "description"):
        match = re.search(r"^\s*" + key + r":\s*(.+?)\s*$", "\n".join(block), re.M)
        if match:
            data[key] = match.group(1)
    return data


def readme_version(text):
    match = re.search(r"^- 版本：\s*([0-9][^\s（(]*)\s*[（(]([0-9-]+)[)）]", text, re.M)
    return (match.group(1), match.group(2)) if match else (None, None)


def changelog_rows(text):
    rows, header = [], None
    inside = False
    for line in text.splitlines():
        if re.match(r"^##\s+更新日志", line):
            inside = True
            continue
        if inside and re.match(r"^##\s+", line):
            break
        if not inside or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if cells and cells[0] in ("版本", "version"):
                header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        padded = cells + [""] * (3 - len(cells))
        rows.append({"version": padded[0], "date": padded[1],
                     "change": " | ".join(padded[2:]).strip()})
    return rows


def trigger_words(description):
    match = re.search(r"触发词[：:]\s*(.+)$", description)
    if not match:
        return []
    raw = match.group(1).strip().rstrip("。.")
    return [word.strip() for word in re.split(r"[、,，]", raw) if word.strip()]


def rule_numbers(text):
    numbers, inside = [], False
    for line in text.splitlines():
        if re.match(r"^##\s+Non-negotiables", line):
            inside = True
            continue
        if inside and re.match(r"^##\s+", line):
            break
        if not inside:
            continue
        match = re.match(r"^(\d+)\.\s+\*\*", line)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _without_code(text):
    """Blank fenced code and inline code so `\\n` inside examples is not flagged."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(out)


def _script_mentions(text, require_prefix=True):
    if require_prefix:
        return set(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", text))
    return {name for name in re.findall(r"([A-Za-z0-9_]+\.py)", text)}


def _script_names(text):
    return set(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", text))


def _md_files(root):
    paths = []
    for name in TEXT_DOCS:
        full = os.path.join(root, name)
        if os.path.isfile(full):
            paths.append(full)
    refs = os.path.join(root, "references")
    if os.path.isdir(refs):
        for name in sorted(os.listdir(refs)):
            if name.endswith(".md"):
                paths.append(os.path.join(refs, name))
    return paths


def _issue(code, message, path=None):
    display = None
    if path:
        try:
            display = os.path.relpath(path, os.getcwd())
        except ValueError:
            display = os.path.abspath(path)
    return {"code": code, "message": message, "file": display}


def collect_issues(root=".", test_count=None):
    root = os.path.abspath(root)
    skill_path = os.path.join(root, "SKILL.md")
    readme_path = os.path.join(root, "README.md")
    if not os.path.isfile(skill_path) or not os.path.isfile(readme_path):
        missing = [name for name in ("SKILL.md", "README.md")
                   if not os.path.isfile(os.path.join(root, name))]
        return [_issue("missing-doc", f"missing {', '.join(missing)}", root)]
    skill = _read(skill_path)
    readme = _read(readme_path)
    meta = front_matter(skill)
    issues = []

    version = meta.get("version")
    readme_ver, readme_date = readme_version(readme)
    rows = changelog_rows(readme)
    top = rows[0].get("version") if rows else None
    if not version:
        issues.append(_issue("version", "SKILL.md metadata has no version", skill_path))
    if readme_ver != version:
        issues.append(_issue("version", f"README version {readme_ver!r} != SKILL {version!r}",
                             readme_path))
    if top != version:
        issues.append(_issue("version", f"top changelog row {top!r} != SKILL {version!r}",
                             readme_path))
    if meta.get("updated") and readme_date and meta["updated"] != readme_date:
        issues.append(_issue("version", f"updated date {meta['updated']} != README "
                                        f"{readme_date}", readme_path))

    canonical = re.search(r"^- 当前离线自测：\s*(\d+)\s*项", readme, re.M)
    current_row = next((row for row in rows if row.get("version") == version), None)
    row_count = None
    if current_row:
        match = re.search(r"(\d+)\s*项离线自测", current_row.get("change") or "")
        row_count = int(match.group(1)) if match else None
    if canonical and row_count is not None and int(canonical.group(1)) != row_count:
        issues.append(_issue("test-count", f"README count {canonical.group(1)} != current "
                                           f"changelog {row_count}", readme_path))
    if test_count is not None:
        if not canonical:
            issues.append(_issue("test-count", "missing canonical line "
                                               "`- 当前离线自测：N 项`", readme_path))
        elif int(canonical.group(1)) != test_count:
            issues.append(_issue("test-count", f"README count {canonical.group(1)} != "
                                               f"actual {test_count}", readme_path))
        if row_count is None:
            issues.append(_issue("test-count", f"current changelog row for {version} has no "
                                               f"`N 项离线自测`", readme_path))
        elif row_count != test_count:
            issues.append(_issue("test-count", f"changelog count {row_count} != actual "
                                               f"{test_count}", readme_path))

    words = trigger_words(meta.get("description", ""))
    duplicates = sorted({word for word in words if words.count(word) > 1})
    if duplicates:
        issues.append(_issue("trigger", "duplicate trigger word(s): " +
                             ", ".join(duplicates), skill_path))

    numbers = rule_numbers(skill)
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        issues.append(_issue("rules", f"hard-rule numbering is {numbers}, expected "
                                      f"{expected}", skill_path))

    for path in _md_files(root):
        text = _read(path) if path != skill_path and path != readme_path else \
            (skill if path == skill_path else readme)
        cleaned = _without_code(text)
        if re.search(r"\\n", cleaned):
            issues.append(_issue("literal-newline", "literal `\\n` in prose", path))
        if re.search(r"(?i)(^|[^A-Za-z0-9])F:[\\/]", text) or "file:///F:" in text:
            issues.append(_issue("old-path", "old F: path in prose", path))

    scripts_dir = os.path.join(root, "scripts")
    existing = set()
    if os.path.isdir(scripts_dir):
        existing = {name for name in os.listdir(scripts_dir)
                    if name.endswith(".py") and name != "__init__.py"}
    for target, label, require_prefix in ((skill_path, "SKILL.md", True),
                                          (readme_path, "README.md", False)):
        text = _read(target)
        mentioned = _script_mentions(text, require_prefix)
        missing = sorted(existing - mentioned)
        if missing:
            issues.append(_issue("script-doc", f"{label} does not mention script(s): "
                                               f"{', '.join(missing)}", target))
        if require_prefix:
            absent = sorted(mentioned - existing)
            if absent:
                issues.append(_issue("script-doc", f"{label} references missing script(s): "
                                                   f"{', '.join(absent)}", target))

    assets = set()
    for path in _md_files(root):
        assets |= set(re.findall(r"assets/([A-Za-z0-9_.-]+\.(?:json|md|ya?ml))", _read(path)))
    for name in sorted(assets):
        if not os.path.isfile(os.path.join(root, "assets", name)):
            issues.append(_issue("asset", f"documented asset is missing: assets/{name}",
                                 skill_path))
    return issues


def run_tests(root):
    path = os.path.join(root, "scripts", "selftest_models.py")
    if not os.path.isfile(path):
        return None, "selftest_models.py not found"
    try:
        done = subprocess.run([sys.executable, path], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"selftest failed to run: {exc}"
    match = re.search(r"(\d+) passed, (\d+) failed", (done.stdout or "") + (done.stderr or ""))
    if not match:
        return None, "cannot read the selftest count"
    passed, failed = int(match.group(1)), int(match.group(2))
    if failed:
        return None, f"selftest reports {failed} failure(s)"
    return passed, None


def main():
    ap = argparse.ArgumentParser(description="documentation consistency gate")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--test-count", type=int, default=None)
    ap.add_argument("--run-tests", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isfile(os.path.join(root, "SKILL.md")):
        print("[FAIL] missing SKILL.md under " + root, file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(root, "README.md")):
        print("[FAIL] missing README.md under " + root, file=sys.stderr)
        return 2
    count = args.test_count
    if args.run_tests:
        count, error = run_tests(root)
        if error:
            print(f"[FAIL] {error}", file=sys.stderr)
            return 1
    issues = collect_issues(root, count)
    if args.json:
        print(json.dumps({"root": root, "test_count": count, "issues": issues},
                         ensure_ascii=False, indent=2))
    elif issues:
        for issue in issues:
            location = f" ({issue['file']})" if issue.get("file") else ""
            print(f"[FAIL] {issue['code']}: {issue['message']}{location}")
    elif not args.quiet:
        suffix = f", {count} tests" if count is not None else ""
        print(f"[OK] docs consistent{suffix}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
