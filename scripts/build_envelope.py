#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a review request envelope: SHA256 + mtime + page_map skeleton, with path separation.

Usage:
    python scripts/build_envelope.py --artifact <file> --pages 4,8,13 --out <yaml>
        [--render-dir _work/renders] [--permission read-only] [--scope "..."]

Exit codes:
    0  written
    2  invalid input (missing artifact, or artifact/out/payload/result collide)
"""
import argparse
import hashlib
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(timespec="seconds")


def posix(path):
    return path.replace(os.sep, "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--pages", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--render-dir", default="_work/renders")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--permission", default="read-only",
                    choices=["read-only", "workspace-write", "danger-full-access"])
    ap.add_argument("--scope", default="")
    args = ap.parse_args()

    artifact = os.path.abspath(args.artifact)
    if not os.path.isfile(artifact):
        print(f"[FAIL] artifact not found: {artifact}")
        return 2

    out = os.path.abspath(args.out)
    payload = os.path.abspath(args.payload) if args.payload else \
        os.path.join(os.path.dirname(out), "payload.md")
    result = os.path.abspath(args.result) if args.result else \
        os.path.join(os.path.dirname(out), "result.json")

    if len({os.path.normcase(p) for p in (artifact, out, payload, result)}) < 4:
        print("[FAIL] artifact / out / payload / result must be four distinct paths")
        return 2

    pages = [int(p.strip()) for p in args.pages.split(",") if p.strip().isdigit()]
    rid = "R" + datetime.now().strftime("%Y%m%d-%H%M%S")

    lines = [
        f"request_id: {rid}",
        f"artifact: {artifact}",
        f"version_hash: {sha256(artifact)}",
        f"artifact_mtime: {mtime_iso(artifact)}",
        f"write_permission: {args.permission}",
        f"output_path: {posix(payload)}",
        f"result_path: {posix(result)}",
    ]
    if pages:
        lines.append("page_map:")
        for p in pages:
            img = posix(os.path.join(args.render_dir, f"p{p:03d}.png"))
            lines += [f"  - page: {p}", f"    image: {img}", '    expected_title: ""']
    lines.append(f'scope: "{args.scope}"')
    lines += ["acceptance:", '  - ""', "output_schema: findings[]"]

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"[OK] envelope written: {out}")
    print(f"     hash={sha256(artifact)[:12]}... mtime={mtime_iso(artifact)}")
    if pages:
        print(f"     fill {len(pages)} expected_title entries before sending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
