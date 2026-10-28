#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One-command release: check docs, run the real suite, write the version/count/changelog,
commit, tag, push.

The v2.1 postmortem's release defects were all hand edits: version drift, stale test counts,
duplicate trigger words, a literal `\\n`, an old `F:` path. This script makes the checks and the
edits one path, and refuses to start on a dirty tree or an existing tag.

Usage:
    python scripts/release.py --version 2.2.0 --note "executable gates ..."
    python scripts/release.py --version 2.2.0 --note "..." --dry-run
    python scripts/release.py --version 2.2.0 --note "..." --no-push

Exit codes:
    0  released (or dry-run clean)
    1  check/test/git failure
    2  bad invocation
"""
import argparse
import datetime
import difflib
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_docs  # noqa: E402

SKILL = "SKILL.md"
README = "README.md"


def read(root, name):
    with open(os.path.join(root, name), encoding="utf-8-sig") as fh:
        return fh.read()


def write(root, name, text):
    with open(os.path.join(root, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def git(root, *args, check=False):
    done = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "git failed").strip())
    return done


def parse_count(output):
    match = re.search(r"(\d+) passed, (\d+) failed", output or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def run_selftest(root):
    path = os.path.join(root, "scripts", "selftest_models.py")
    if not os.path.isfile(path):
        raise RuntimeError("scripts/selftest_models.py not found")
    done = subprocess.run([sys.executable, path], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800)
    parsed = parse_count((done.stdout or "") + (done.stderr or ""))
    if not parsed:
        raise RuntimeError("cannot read the selftest count")
    passed, failed = parsed
    if failed:
        raise RuntimeError(f"selftest reports {failed} failure(s)")
    return passed


def set_skill_meta(text, version, date):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md has no front matter")
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise ValueError("SKILL.md front matter is not closed")
    for index in range(1, end):
        if re.match(r"^\s*version:\s*", lines[index]):
            indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f"{indent}version: {version}"
        elif re.match(r"^\s*updated:\s*", lines[index]):
            indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f"{indent}updated: {date}"
    return "\n".join(lines) + "\n"


def set_readme_version(text, version, date):
    text = re.sub(r"^- 版本：.*$", f"- 版本：{version}（{date}）", text, count=1, flags=re.M)
    if not re.search(r"^- 当前离线自测：", text, re.M):
        text = re.sub(r"^(- 版本：.*)$", r"\1\n- 当前离线自测：TBD 项（mock providers，不联网）",
                      text, count=1, flags=re.M)
    return text


def set_canonical_count(text, count):
    if re.search(r"^- 当前离线自测：", text, re.M):
        return re.sub(r"^- 当前离线自测：.*$",
                      f"- 当前离线自测：{count} 项（mock providers，不联网）",
                      text, count=1, flags=re.M)
    return re.sub(r"^(- 版本：.*)$",
                  r"\1\n- 当前离线自测：" + str(count) + " 项（mock providers，不联网）",
                  text, count=1, flags=re.M)


def _change_text(note, count):
    note = " ".join((note or "").split()).replace("|", "/").strip()
    if re.search(r"\d+\s*项离线自测", note):
        return note
    return f"{note}；{count} 项离线自测"


def set_changelog(text, version, date, note, count):
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines)
                  if re.match(r"^##\s+更新日志", line)), None)
    if start is None:
        raise ValueError("README.md has no ## 更新日志 section")
    end = next((index for index in range(start + 1, len(lines))
                if lines[index].startswith("## ")), len(lines))
    header = None
    for index in range(start + 1, end):
        line = lines[index].strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header is None:
            if cells and cells[0] in ("版本", "version"):
                header = index
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        if cells and cells[0] == version:
            lines[index] = f"| {version} | {date} | {_change_text(note, count)} |"
            return "\n".join(lines) + "\n"
    if header is None:
        raise ValueError("README.md changelog table not found")
    insert_at = header + 2
    lines.insert(insert_at, f"| {version} | {date} | {_change_text(note, count)} |")
    return "\n".join(lines) + "\n"


def check_all(root, count=None):
    return check_docs.collect_issues(root, count)


def main():
    ap = argparse.ArgumentParser(description="release the skill")
    ap.add_argument("--root", default=".")
    ap.add_argument("--version", required=True)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--test-count", type=int, default=None)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"[FAIL] --version must be X.Y.Z: {args.version!r}", file=sys.stderr)
        return 2
    for name in (SKILL, README):
        if not os.path.isfile(os.path.join(root, name)):
            print(f"[FAIL] {name} not found under {root}", file=sys.stderr)
            return 2

    skill_text = read(root, SKILL)
    readme_text = read(root, README)
    current = check_docs.front_matter(skill_text).get("version")
    if current and tuple(int(x) for x in args.version.split(".")) < \
            tuple(int(x) for x in current.split(".")):
        print(f"[FAIL] {args.version} is older than the current {current}", file=sys.stderr)
        return 2

    status = git(root, "status", "--porcelain")
    if status.returncode != 0:
        print("[FAIL] not a git repository (git status failed)", file=sys.stderr)
        return 1
    if status.stdout.strip():
        print("[FAIL] working tree is not clean; commit the feature work first:", file=sys.stderr)
        for line in status.stdout.strip().splitlines()[:20]:
            print("  " + line, file=sys.stderr)
        return 1
    if git(root, "tag", "--list", f"v{args.version}").stdout.strip():
        print(f"[FAIL] tag v{args.version} already exists", file=sys.stderr)
        return 1

    pre = check_all(root)
    if pre:
        for issue in pre:
            print(f"[FAIL] {issue['code']}: {issue['message']}", file=sys.stderr)
        return 1

    if args.skip_tests:
        if args.test_count is None:
            print("[FAIL] --skip-tests needs --test-count", file=sys.stderr)
            return 2
        count = args.test_count
    elif args.test_count is not None:
        count = args.test_count
    else:
        try:
            count = run_selftest(root)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        print(f"[OK] selftest: {count} passed, 0 failed")

    new_skill = set_skill_meta(skill_text, args.version, args.date)
    new_readme = set_readme_version(readme_text, args.version, args.date)
    new_readme = set_canonical_count(new_readme, count)
    try:
        new_readme = set_changelog(new_readme, args.version, args.date, args.note, count)
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        for name, before, after in ((SKILL, skill_text, new_skill),
                                    (README, readme_text, new_readme)):
            diff = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                        fromfile=f"{name} (current)",
                                        tofile=f"{name} (release {args.version})", lineterm="")
            print("\n".join(diff) or f"# {name}: no change")
        print(f"[DRY-RUN] would release v{args.version} with {count} tests; no files changed")
        return 0

    write(root, SKILL, new_skill)
    write(root, README, new_readme)
    post = check_all(root, count)
    if post:
        git(root, "restore", "--source=HEAD", "--", SKILL, README)
        for issue in post:
            print(f"[FAIL] {issue['code']}: {issue['message']}", file=sys.stderr)
        print("[FAIL] post-edit check failed; SKILL.md/README.md restored", file=sys.stderr)
        return 1

    git(root, "add", "--", SKILL, README, check=True)
    commit = git(root, "commit", "-m", f"release: v{args.version}")
    if commit.returncode != 0:
        git(root, "restore", "--source=HEAD", "--", SKILL, README)
        print("[FAIL] commit failed; docs restored (feature work untouched)", file=sys.stderr)
        print((commit.stderr or commit.stdout).strip(), file=sys.stderr)
        return 1
    tag = git(root, "tag", f"v{args.version}")
    if tag.returncode != 0:
        print(f"[FAIL] tag v{args.version} failed; the release commit is kept. "
              f"Recover with: git tag v{args.version}", file=sys.stderr)
        return 1
    if not args.no_push:
        push = git(root, "push")
        if push.returncode != 0:
            print("[FAIL] branch push failed; local commit and tag are kept. "
                  "Recover with: git push", file=sys.stderr)
            return 1
        push_tag = git(root, "push", "origin", f"v{args.version}")
        if push_tag.returncode != 0:
            print(f"[FAIL] tag push failed; local tag is kept. "
                  f"Recover with: git push origin v{args.version}", file=sys.stderr)
            return 1
    print(f"[OK] released v{args.version} with {count} tests"
          f"{' (local only)' if args.no_push else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
