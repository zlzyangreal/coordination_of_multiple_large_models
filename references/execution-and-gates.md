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
  a recorded change (ledger `budget_change` row / `review_budget.py raise`), never "after seeing
  the draft".
- Reviewer rule: a budget overrun is `severity: notice`; a constraint violation is
  `high`/`medium`. "The PR is 900 lines against a 600-line budget" does not block delivery.
- **Version and hash**: the pack carries exactly one `facts_version`, `facts_hash` and
  `frozen_at` header. `python scripts/facts_pack.py freeze` writes them; every scope or budget
  change uses `bump --reason`, which increments the version and appends a `## version log` row.
  `check` exits 3 when the body changed without a freeze. The envelope carries the values; the
  runner rehashes before launch and `validate_result.py` rechecks during validation.
- Review budget rows: `review_calls` (default 2 per milestone) and `review_minutes` (default 30).
- Template: `assets/facts-pack.example.md`.

## Execution (8 steps with gates)

| # | Action | Gate |
|---|---|---|
| 0 | Pin the contract **and root identity** (`session.py pin`), snapshot originals, record SHA256 | no pin/identity, no start; no edit without backup |
| 1 | Capability + quota check | no routing before check |
| 2 | Plan, then independent plan review + `plan_gate.py check` | no reviewed plan, no dispatch; different family; v2 record binding |
| 3 | Decompose, board, contract, facts pack frozen + versioned, review budget | no board/frozen facts/budget, no start |
| 4 | Produce + independent review (roles, schema, evidence) | producer never self-reviews; advisory drafts count as production; `probe` never counts |
| 5 | Integrate / merge | single owner per file |
| 6 | Cross-model acceptance + `review_budget.py deliver` | open items or missing S/A produce unit block delivery |
| 7 | Release checks + postmortem (`check_docs.py --run-tests`, `release.py`) | no checks/postmortem, not done |

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
- **Evidence**: when the envelope carries `test_evidence`, the result cites at least one
  `test_refs` entry with the log hash and a verbatim excerpt absent from the envelope summary;
  otherwise the review declares itself static-only. The facts pack was fresh at build, launch and
  validation.
- **Plan gate**: production was dispatched only after `plan_gate.py check` passed; any waiver is
  a recorded deviation, never a pass.
- **Delivery**: artifact + supporting docs + backup + note; version in the filename or commit;
  originals untouched; postmortem updated; `review_budget.py deliver` passed. The note states `SKILL vX (pinned …), repository vY`
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
