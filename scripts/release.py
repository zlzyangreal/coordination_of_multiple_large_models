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
import hashlib
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_docs  # noqa: E402
import gate_common as common  # noqa: E402

SKILL = "SKILL.md"
README = "README.md"
CANDIDATE = os.path.join("_work", "iteration", "candidate.json")
RELEASE = os.path.join("_work", "iteration", "release.json")


def candidate_context(root):
    """Candidate identity written by iteration.py init; None in a normal stable release."""
    try:
        with open(os.path.join(root, CANDIDATE), encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("role") != "candidate":
        return None
    return data


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


def run_candidate_evidence(root, timeout=1800):
    """Run the suite again at the release commit and bind the evidence to that commit.

    The runner comes from the stable copy named by `candidate.json` when it exists, so a
    candidate cannot substitute its own evidence tool.
    """
    tool_root = root
    try:
        with open(os.path.join(root, CANDIDATE), encoding="utf-8-sig") as fh:
            identity = json.load(fh)
        stable_root = str(identity.get("stable_root") or "")
        if stable_root and os.path.isfile(os.path.join(stable_root, "scripts",
                                                       "test_evidence.py")):
            tool_root = stable_root
    except (OSError, ValueError):
        pass
    path = os.path.join(tool_root, "scripts", "test_evidence.py")
    if not os.path.isfile(path):
        raise RuntimeError("scripts/test_evidence.py not found")
    done = subprocess.run([sys.executable, path, "record", "--name", "release-candidate",
                           "--project", root, "--", sys.executable,
                           "scripts/selftest_models.py"],
                          cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    record_path = os.path.join(root, "_work", "evidence", "release-candidate.json")
    try:
        with open(record_path, encoding="utf-8-sig") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        raise RuntimeError("test_evidence wrote no record:\n" + (done.stdout or "")
                           + (done.stderr or ""))
    return record_path, record, done.returncode


def finish_candidate(root, args, candidate, count, parent):
    """Commit docs, prove the exact release commit, tag it, and record the candidate release."""
    added = git(root, "add", "--", SKILL, README)
    if added.returncode != 0:
        git(root, "reset", "--hard", parent)
        print("[FAIL] staging failed; candidate restored", file=sys.stderr)
        return 1
    commit = git(root, "commit", "-m", f"release: v{args.version}")
    if commit.returncode != 0:
        git(root, "reset", "--hard", parent)
        print("[FAIL] commit failed; candidate restored", file=sys.stderr)
        print((commit.stderr or commit.stdout).strip(), file=sys.stderr)
        return 1
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    changed = set(git(root, "diff", "--name-only", parent, head).stdout.splitlines())
    if changed - {SKILL, README}:
        git(root, "reset", "--hard", parent)
        print("[FAIL] release commit changed more than the version docs: "
              + ", ".join(sorted(changed - {SKILL, README})), file=sys.stderr)
        return 1
    try:
        record_path, record, code = run_candidate_evidence(root)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        git(root, "reset", "--hard", parent)
        print(f"[FAIL] post-commit evidence failed; candidate restored: {exc}", file=sys.stderr)
        return 1
    if code != 0 or record.get("exit_code") != 0 or record.get("git_head") != head:
        git(root, "reset", "--hard", parent)
        print("[FAIL] the release commit does not pass the suite; candidate restored", file=sys.stderr)
        print(f"       evidence: {record_path} (exit {record.get('exit_code')}, "
              f"head {record.get('git_head')})", file=sys.stderr)
        return 1
    if record.get("tests_passed") is not None and record["tests_passed"] != count:
        git(root, "reset", "--hard", parent)
        print(f"[FAIL] test count changed after the docs edit ({count} -> "
              f"{record['tests_passed']}); candidate restored", file=sys.stderr)
        return 1
    tag = git(root, "tag", f"v{args.version}")
    if tag.returncode != 0:
        print(f"[FAIL] tag v{args.version} failed; the release commit is kept. "
              f"Recover with: git tag v{args.version}", file=sys.stderr)
        return 1
    with open(record_path, "rb") as fh:
        evidence_sha = hashlib.sha256(fh.read()).hexdigest()
    release = {
        "schema": 1,
        "iteration_id": candidate.get("iteration_id"),
        "version": args.version,
        "commit": head,
        "tag": f"v{args.version}",
        "evidence": {"path": os.path.join("_work", "evidence", "release-candidate.json")
                     .replace(os.sep, "/"),
                     "sha256": evidence_sha},
        "tests_passed": count,
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.join(root, os.path.dirname(RELEASE)), exist_ok=True)
    with open(os.path.join(root, RELEASE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(release, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    if args.push:
        for ref in ("HEAD", f"v{args.version}"):
            pushed = git(root, "push", "origin", ref)
            if pushed.returncode != 0:
                print(f"[WARN] push {ref} failed; local tag stands", file=sys.stderr)
    print(f"[OK] candidate released v{args.version} at {head[:12]} with {count} tests; "
          f"evidence {record_path}")
    print("     stable promotion still needs: iteration.py compare -> approve -> promote")
    return 0


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
    ap.add_argument("--push", action="store_true",
                    help="push branch and tag (candidate releases never push by default)")
    ap.add_argument("--no-iteration", action="store_true",
                    help="ignore a candidate identity (stable release path)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"[FAIL] --version must be X.Y.Z: {args.version!r}", file=sys.stderr)
        return 2
    for name in (SKILL, README):
        if not os.path.isfile(os.path.join(root, name)):
            print(f"[FAIL] {name} not found under {root}", file=sys.stderr)
            return 2

    candidate = None if args.no_iteration else candidate_context(root)
    if candidate:
        if args.push:
            print("[FAIL] a candidate release cannot push; the stable promotion is the only "
                  "push boundary", file=sys.stderr)
            return 4
        if args.version != candidate.get("target_version"):
            print(f"[FAIL] candidate release is v{candidate.get('target_version')}, "
                  f"not {args.version}", file=sys.stderr)
            return 2
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if candidate.get("branch") and branch != candidate["branch"]:
            print(f"[FAIL] candidate branch is {branch!r}, expected "
                  f"{candidate['branch']!r}", file=sys.stderr)
            return 2
    elif not args.dry_run:
        try:
            import authority  # noqa: PLC0415
            gcode, ginfo = authority.guard(root, "release", subject=args.version)
        except ImportError:
            markers = common.governance_markers(root)
            if markers.get("enabled") or markers.get("any_file"):
                print("[FAIL] governance is enabled but scripts/authority.py is "
                      "unavailable", file=sys.stderr)
                return 4
            gcode, ginfo = 0, {}
        if gcode != 0:
            print(f"[FAIL] authority ({gcode}): "
                  f"{ginfo.get('reason') or ginfo.get('message')}", file=sys.stderr)
            return 4
        if not args.no_push:
            try:
                gcode, ginfo = authority.guard(root, "push", subject=args.version)
            except ImportError:
                markers = common.governance_markers(root)
                if markers.get("enabled") or markers.get("any_file"):
                    print("[FAIL] governance is enabled but scripts/authority.py is "
                          "unavailable", file=sys.stderr)
                    return 4
                gcode, ginfo = 0, {}
            if gcode != 0:
                print(f"[FAIL] authority ({gcode}): "
                      f"{ginfo.get('reason') or ginfo.get('message')}", file=sys.stderr)
                return 4

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
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", SKILL, README)
        for issue in post:
            print(f"[FAIL] {issue['code']}: {issue['message']}", file=sys.stderr)
        print("[FAIL] post-edit check failed; SKILL.md/README.md restored", file=sys.stderr)
        return 1

    if candidate:
        parent = git(root, "rev-parse", "HEAD").stdout.strip()
        return finish_candidate(root, args, candidate, count, parent)

    added = git(root, "add", "--", SKILL, README)
    if added.returncode != 0:
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", SKILL, README)
        print("[FAIL] staging failed; docs restored", file=sys.stderr)
        print((added.stderr or added.stdout).strip(), file=sys.stderr)
        return 1
    commit = git(root, "commit", "-m", f"release: v{args.version}")
    if commit.returncode != 0:
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", SKILL, README)
        print("[FAIL] commit failed; docs and index restored (feature work untouched)",
              file=sys.stderr)
        print((commit.stderr or commit.stdout).strip(), file=sys.stderr)
        return 1
    tag = git(root, "tag", f"v{args.version}")
    if tag.returncode != 0:
        print(f"[FAIL] tag v{args.version} failed; the release commit is kept. "
              f"Recover with: git tag v{args.version}", file=sys.stderr)
        return 1
    if not args.no_push:
        try:
            import authority  # noqa: PLC0415
            gcode, ginfo = authority.guard(root, "push", subject=args.version)
        except ImportError:
            markers = common.governance_markers(root)
            if markers.get("enabled") or markers.get("any_file"):
                print("[FAIL] governance is enabled but scripts/authority.py is "
                      "unavailable", file=sys.stderr)
                return 4
            gcode, ginfo = 0, {}
        if gcode != 0:
            print(f"[FAIL] authority ({gcode}): "
                  f"{ginfo.get('reason') or ginfo.get('message')}", file=sys.stderr)
            return 4
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
