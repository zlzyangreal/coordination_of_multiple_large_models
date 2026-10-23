#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pre-flight environment check: Codex path/version, processes, proxy, egress, writability.

Usage:
    python scripts/check_env.py [--workdir .] [--host chatgpt.com] [--short-call]

Exit codes:
    0  ready (warnings allowed)
    1  blocked (no codex, workdir not writable, or short call failed)
"""
import argparse
import glob
import os
import socket
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def resolve_codex():
    """Never hardcode bin/<hash>/; return the newest codex.exe under the install root."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenAI", "Codex", "bin")
    hits = glob.glob(os.path.join(base, "*", "codex.exe"))
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def codex_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        return f"[version failed] {exc}"


def process_running(names):
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout.lower()
        return [n for n in names if n.lower() in out]
    except Exception:
        return []


def tcp_ok(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def writable(path):
    probe = os.path.join(path, ".dsh_write_probe")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--host", default="chatgpt.com")
    ap.add_argument("--short-call", action="store_true",
                    help="run a real codex call; the only reliable egress verdict")
    ap.add_argument("--short-timeout", type=int, default=240,
                    help="seconds before the short call is considered failed")
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir)
    blockers = []
    print(f"== env check {datetime.now().isoformat(timespec='seconds')} ==")
    print(f"workdir: {workdir}")

    codex = resolve_codex()
    if codex:
        print(f"[OK] codex: {codex}")
        print(f"     version: {codex_version(codex)}")
    else:
        print("[FAIL] codex.exe not found under %LOCALAPPDATA%/OpenAI/Codex/bin/*/")
        blockers.append("codex missing")

    procs = process_running(["Codex.exe", "ChatGPT.exe", "codex.exe"])
    print(f"[{'OK' if procs else 'WARN'}] processes: "
          f"{', '.join(procs) if procs else 'none found'}")

    proxy_vars = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
                  if os.environ.get(k)}
    print(f"[{'OK' if proxy_vars else 'WARN'}] proxy vars: "
          f"{proxy_vars or 'unset (Clash system-proxy/TUN may not reach child processes)'}")

    if tcp_ok(args.host, 443):
        print(f"[OK] TCP {args.host}:443 reachable")
    elif proxy_vars:
        print(f"[WARN] TCP {args.host}:443 refused; proxy set, trust the short call")
    else:
        print(f"[WARN] TCP {args.host}:443 refused; direct probes are unreliable behind TUN/proxy")

    ok_dir = writable(workdir)
    print(f"[{'OK' if ok_dir else 'FAIL'}] workdir writable")
    if not ok_dir:
        blockers.append("workdir not writable")

    if args.short_call and codex:
        try:
            r = subprocess.run([codex, "exec", "--ephemeral", "--skip-git-repo-check",
                                "-s", "read-only", "-c", "approval_policy=never",
                                "-C", workdir, "ok"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=args.short_timeout)
            lines = ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines()
            good = any(line.strip().lower().rstrip(".!") == "ok" for line in lines)
            print(f"[{'OK' if good else 'WARN'}] short call: "
                  f"{'passed' if good else 'failed'} (exit={r.returncode})")
            if not good:
                tail = (r.stderr or "").strip().splitlines()
                print("     " + (tail[-1][:160] if tail else "(no stderr)"))
                blockers.append("short call failed")
        except subprocess.TimeoutExpired:
            print(f"[FAIL] short call timed out after {args.short_timeout}s")
            blockers.append("short call timeout")

    print(f"[OK] python: {sys.executable} ({sys.version.split()[0]})")
    print("\nverdict:", "ready" if not blockers else f"blocked: {', '.join(blockers)}")
    if not args.short_call:
        print("hint: add --short-call to confirm egress with a real call.")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
