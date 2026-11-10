# Self-bootstrap iteration (stable drives candidate)

One released, accepted version is the execution end. The next version is a *candidate* in a
separate git worktree; the stable workspace drives it, reviews it, and only promotes it after a
baseline comparison, the user's acceptance, and a passing smoke test. Promotion is reversible
without trusting the candidate's code.

This reference is the detail behind hard rules 27–30 and workflow step 0/5b. It adds no new
policy: if the two disagree, `SKILL.md` wins.

## Roles and roots

| Role | Where | What it may write | Its identity |
|---|---|---|---|
| `stable` V(N) | the repository root, on the released tag commit | its own `_work` (plan, reviews, evidence, manifest) | version + tag + commit + executor hash snapshot |
| `candidate` V(N+1) | a sibling path created with `git worktree add` from the tag | only the candidate root and its `_work` | one-way `_work/iteration/candidate.json` written at init |

The stable manifest is authoritative: `_work/iteration/iteration.json`. It names both roots, both
commits, the capability/script/config snapshot and each gate result. Candidate scripts never edit
the stable root. The stable orchestrator reads candidate evidence; the candidate never reads or
writes stable state.

`session.py pin` copies a compact dual identity into `_work/skill_pin.json`; `session.py status`
prints both lines. `session.py check` refuses a moved stable HEAD during development and a
candidate that is not a worktree of the same repository.

## Init

```bash
# in the released stable workspace, clean tree
python scripts/iteration.py init --candidate ../myrepo-candidate --stable v2.2.0
```

`--stable` defaults to the release tag at HEAD. Init refuses:

- a dirty stable tree, a non-ancestor tag, or a tag whose `SKILL.md` version disagrees with it;
- a candidate inside/around the stable tree, a candidate from another repository, or the wrong
  branch;
- an existing manifest (use `status`; `--force` re-initialises deliberately).

It records `stable.tag/commit`, `version`, `workspace` (root, drive, volume), the hash of
`SKILL.md`, every `scripts/*.py`, the model config and the Python/platform, plus the target
version and branch. `write_state` makes this the default executor in
`_work/iteration/state.json`.

## Baseline, development, evidence

Baseline is recorded from the untouched stable end **before** candidate code changes matter:

```bash
python scripts/iteration.py baseline            # runs scripts/selftest_models.py, records count/duration
```

It also aggregates whatever call history exists (calls, success rate, p50/p95 duration, tokens);
cost and resource use are `unknown` when no instrumented source exists — never invented.

Development happens in the candidate, with `run_gpt.py --cd <candidate>`. During an iteration a
produce call in the stable root is refused unless `--allow-stable-produce` is passed, because
stable must stay the frozen execution end.

Release the candidate first, then record evidence on the tagged commit **from the stable
workspace**:

```bash
# candidate, after committing the work
python scripts/release.py --version 2.3.0 --note "..." --no-push
# stable root
python scripts/iteration.py evidence            # trusted test_evidence.py --project <candidate>
```

`test_evidence.py` stamps the commit *before* the run, the commit after it, both dirty flags,
workspace root/volume, iteration id and role. `iteration.py evidence` records the record's path,
sha256 and commit in the stable manifest; `compare` accepts only that record. A candidate-written
JSON, a dirty tree, or a run that moved HEAD is rejected: a test result is bound to the commit it
ran on, not to "the working copy".

## Compare

```bash
python scripts/iteration.py compare
```

The comparison report (`_work/iteration/comparison.json` + `.md`) blocks when:

- the baseline gate is missing/red, changed on disk, or belongs to another stable commit;
- the trusted evidence file changed, its commit is not current candidate HEAD, the log changed,
  or the run was dirty/HEAD moved;
- candidate tests are fewer than the baseline (an explicit user-accepted drop needs
  `--accept-fewer-tests N --reason ...`) or the suite failed;
- a stable script disappeared without `--accept-removal <name> --reason ...`;
- candidate `SKILL.md` is not the target version, or `check_docs.py` finds an inconsistency;
- the evidence cannot be parsed.

Warnings (do not block): a suite over 2× slower, a call success rate below 1.0, an accepted
trade-off. Capability additions and changed scripts are listed, not treated as regressions.
The report hash is what `approve` signs; editing it afterwards invalidates the approval.

## Accept, promote, roll back

A major divergence (goal, hard constraint, architecture direction, privacy boundary, risky
operation) is recorded once instead of being re-asked:

```bash
python scripts/iteration.py decide --topic "storage engine" \
    --option "sqlite: simple" --option "postgres: operational cost" \
    --decision "sqlite" --reason "single-node scope" --by <user>
```

The row lands in `_work/iteration/decisions.jsonl` with the candidate commit and an event; later
turns cite it rather than reopening a settled question.

```bash
# the user explicitly accepts this exact candidate
python scripts/iteration.py approve --by <name> --note "accepted after review"

# candidate release: docs checked, suite re-run at the release commit, tag created
#   (run inside the candidate; no push by default)
python scripts/release.py --version 2.3.0 --note "..." --no-push

# stable promotion
python scripts/iteration.py promote
```

`promote` requires, in order: stable identity unchanged; candidate clean; shipped HEAD equal to
the stable commit; a passing comparison; an approval whose commit and comparison hash match; tag
`v<target>` pointing at candidate HEAD; and a passing `plan_gate.py`. It then snapshots
code/config/pins/plan records, tags `iteration-snapshot-<id>`, fast-forwards, and runs the project
smoke (`scripts/selftest_models.py` by default; `--smoke-command "..."` for a project-specific
smoke). A failed smoke resets to the snapshot and leaves `stage: rolled_back` (exit 5). Only a
passing smoke writes the new default executor and `stage: promoted`.

`rollback` restores the snapshot commit and copied state, updates `state.json`, and smoke-tests
the restored stable end. The same run also generates a standalone
`_work/iteration/rollback-<id>.py`; it uses only the standard library and git, so it still works
when the promoted code is broken:

```bash
python scripts/iteration.py rollback --yes
python _work/iteration/rollback-<id>.py --yes
```

## Isolation invariants and exit codes

`iteration.py status` (also `verify`) fails closed when the manifest belongs to another root, the
stable volume changed, stable HEAD moved during development, an executor file changed after init,
the candidate is missing/overlapping/not a worktree, the branch differs, or the candidate identity
file is missing.

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | command/runtime failure |
| 2 | bad invocation or missing manifest/evidence |
| 3 | identity, isolation or executor-snapshot violation |
| 4 | a gate blocked the action (plan, evidence, comparison, approval, tag, dirty tree) |
| 5 | promotion/rollback smoke failed; previous stable restored |

Every init, baseline, evidence, comparison, approval, promotion, failed promotion and rollback is
appended to `_work/gates/events.jsonl`.

## Release and failure drill

`release.py` in a candidate workspace detects `_work/iteration/candidate.json`, checks the branch
and target version, runs the suite, commits only `SKILL.md`/`README.md`, then runs the suite again
**at the release commit** and binds that evidence before tagging. A changed test count, a failed
suite, or any extra file in the release commit restores the candidate to its pre-release state.

Before claiming the loop works, run one real drill:

1. `promote` with a deliberately failing `--smoke-command` (or a broken candidate) — stable must
   return to V(N), `state.json` must name V(N), and `status` must show `rolled_back`.
2. delete/break the promoted `scripts/iteration.py` and run the generated shim — the stable tree
   must return to V(N) without importing candidate code.
3. re-run `scripts/selftest_models.py` in the restored stable root.

A rollback that only edits a version string is not a rollback.
