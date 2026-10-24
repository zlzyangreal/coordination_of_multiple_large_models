# Contracts, execution, quality gates

## Task contract (one per artifact)

- Artifact: absolute path + hash; file, directory or diff.
- Scope: which units are in, which are out; everything else untouched.
- Constraints: the non-negotiables that apply (API/ABI, schema, style, security, banned
  deps/words, required behaviour).
- Budgets: size, calls, deadline — with defaults.
- DoD.
- Baseline: which version is authoritative; where older versions live.

No contract, no work.

## Task board (conditional)

Create when: ≥2 models, or ≥3 artifacts, or work spans sessions.
Otherwise write three lines: task / output / DoD.

Columns: task | producer | reviewer | output | DoD | depends on | status.

## Facts pack

One file shared by all models, in **two separate blocks** — never mix them, or a budget will
be litigated as a constraint:

| Block | Contents | Reviewer weight |
|---|---|---|
| **constraints** | interface/schema contract, required behaviour, security rules, style/lint rules, banned deps/words/numbers, mandatory content | violation → high/medium, rework |
| **budgets** | size (lines/files/pages/PR), number of calls, performance envelope, deadlines — each with default and change log | overrun → `notice`, never a veto |

- `constraints` are frozen. `budgets` are adjustable, but only before production and only with
  a recorded change (ledger `budget_change` row), never "after seeing the draft".
- Reviewer rule: a budget overrun is `severity: notice`; a constraint violation is
  `high`/`medium`. "The PR is 900 lines against a 600-line budget" does not block delivery.
- Template: `assets/facts-pack.example.md`.

## Execution (8 steps with gates)

| # | Action | Gate |
|---|---|---|
| 0 | Pin the contract (`session.py pin`), snapshot originals, record SHA256 | no pin, no start; no edit without backup |
| 1 | Capability + quota check | no routing before check |
| 2 | Decompose, board, contract | no board, no start |
| 3 | Facts pack (constraints vs budgets) | no parallel work before freeze |
| 4 | Produce + independent review (schema-enforced) | producer never self-reviews; advisory drafts count as production |
| 5 | Integrate / merge | single owner per file |
| 6 | Cross-model acceptance: behaviour + constraints + facts | open items block delivery |
| 7 | Deliver + postmortem | no postmortem, not done |

## Working discipline

1. **Single writer per file**; confirm the other party finished before editing.
2. **Version artifacts** (`_v1`, `_v2`, or commit SHAs); diff before merging; no silent overwrite.
3. **Directory split**: `_work/` intermediates, `output/` finals, `backup/` originals (adapt names to the project, keep the split).
4. **Never overwrite originals**; copy, then edit; keep checksums.
5. **Structured reviews only** (location + issue + fix + priority).
6. **Reviewers do not ghostwrite**; they report findings.
7. **Log conflicts**; the lead decides and records why.
8. **Never hide failures**: stalls, timeouts, quota, rejections go in the report.
9. **Permission pre-check**: writable tasks never run read-only.
10. **Escalate immediately** on permission/file-lock/overwrite/network issues; ≤2 silent retries.
11. **Three paths differ** (artifact, payload, result).
12. **Delete temp files** at stage end.
13. **Change order**: when a requirement flips, reply with four lines — artifact/units, conflict with previous, which rule wins, how it is verified; then edit.

## Definition of Done

- **Correctness**: every requirement and accepted finding addressed; tests/checks run and their
  command quoted; no unmarked TODOs; data and figures sourced or marked "pending"; counts match.
- **Contract**: constraints satisfied; budgets recorded; interface/schema/format conforms to the
  facts pack.
- **Review integrity**: every adopted finding has a schema-valid result and a call record; any
  `advisory-derived` unit is declared non-independent; no finding adopted without source verification.
- **Delivery**: artifact + supporting docs + backup + note; version in the filename or commit;
  originals untouched; postmortem updated. The note states `SKILL vX (pinned …), repository vY`
  and whether any gate was skipped.
- **Postmortem**: the fixed question "was the skill in force the whole session? where did it
  break?" is answered; any deviation is written down, not remembered.
- **Regression** (after any edit): rerun the same scripted checks (tests, lint, banned
  words/deps, counts); diff against the previous build; ≤3 formatting/restructure passes.
- **Artifact-specific checks** stay in the project, not in this skill: if the deliverable has a
  visual surface or a formal format, attach that checklist to the artifact's contract and point
  the review at it.

## Role separation

- **Producer**: edits, builds, integrates; never signs off. Advisory drafts (outlines, designs,
  scaffolds, patches that the lead adopts) count as production for the units they shape.
- **Reviewer**: findings only; never edits production files; never both advises and accepts;
  never reviews its own draft as if independent.
- **Scripts**: banned words, counts, hashes, schema validation — deterministic, no model memory.
- **Lead**: arbitrates, accepts, owns delivery.

Every critical artifact passes three gates: **model review + scripted checks + schema/source verification**.
