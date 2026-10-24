# Contracts, execution, quality gates

## Task contract (one per artifact)

- Target file: absolute path + hash/page count.
- Scope: which pages/sections; everything else untouched.
- "Format" means: content structure / layout / chart style — state which.
- DoD.
- Baseline: which file is authoritative; where older versions live.

No contract, no work.

## Task board (conditional)

Create when: ≥2 models, or ≥3 artifacts, or work spans sessions.
Otherwise write three lines: task / output / DoD.

Columns: task | producer | reviewer | output | DoD | depends on | status.

## Facts pack

One file shared by all models: goal, audience, hard constraints, banned words/numbers, data definitions, asset paths, output format, DoD.

Freeze before parallel production; if a fact changes, update the pack first, then the artifact.

## Execution (8 steps with gates)

| # | Action | Gate |
|---|---|---|
| 0 | Back up originals, record SHA256 | no edit without backup |
| 1 | Capability + quota check | no routing before check |
| 2 | Decompose, board, contract | no board, no start |
| 3 | Facts pack | no parallel work before freeze |
| 4 | Produce + independent review | producer never self-reviews |
| 5 | Integrate (doc/ppt/chart) | single owner per file |
| 6 | Cross-model acceptance: visual + compliance + facts | open items block delivery |
| 7 | Deliver + postmortem | no postmortem, not done |

## Working discipline

1. **Single writer per file**; confirm the other party finished before editing.
2. **Version artifacts** (`_v1`, `_v2`); diff before merging; no silent overwrite.
3. **Directory split**: `_work/` intermediates, `fist_output/` finals, `原文件备份/` originals.
4. **Never overwrite originals**; copy, then edit; keep checksums.
5. **Structured reviews only** (location + issue + fix + priority).
6. **Reviewers do not ghostwrite**; they report findings.
7. **Log conflicts**; the lead decides and records why.
8. **Never hide failures**: stalls, timeouts, quota, rejections go in the report.
9. **Permission pre-check**: writable tasks never run read-only.
10. **Escalate immediately** on permission/file-lock/overwrite/network issues; ≤2 silent retries.
11. **Three paths differ** (artifact, payload, result).
12. **Delete temp files** at stage end.
13. **Change order**: when a requirement flips, reply with four lines — file/pages, conflict with previous, which rule wins, how it is verified; then edit.

## Definition of Done

- **Content**: every requirement and finding addressed; banned words/numbers zero; key figures sourced or marked "pending"; counts match; no "already completed" phrasing in a proposal.
- **Visual**: no overflow, clipping, edge collision, overlap, misalignment; minimum font size; no stretched/cropped images; continuous page numbering; visual review by a non-producer model.
- **Document**: header row repeats across pages; figure/table numbering continuous; no heading orphaned at page bottom; consistent font/spacing/indent; no blank or half-empty pages.
- **Delivery**: source + PDF + backup + note; version and date in filename; originals untouched; postmortem updated.
- **Regression** (after any edit): rerun the same scripted checks (banned words, citation numbering, page count, table count); diff against the previous build; layout passes ≤3.
- **Full-text extraction**: assert on paragraphs + all table cells + headers/footers + text boxes. Paragraph-only checks miss table content.

## Role separation

- **Producer**: edits, builds, integrates; never signs off.
- **Reviewer**: findings only; never edits production files; never both advises and accepts.
- **Scripts**: banned words, counts, hashes — deterministic, no model memory.
- **Lead**: arbitrates, accepts, owns delivery.

Every critical artifact passes two gates: **model review + scripted compliance**.
