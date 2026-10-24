# Quota-adaptive routing: 5 h window, weekly cap

The scarce resource is not "GPT calls" but **Codex quota**: a fixed pool per 5 h window
(`primary`, `window_minutes=300`) plus a 7 d cap (`secondary`, `window_minutes=10080`).
Both are read from the CLI, not guessed.

## Principle

1. **Percent alone is the wrong input.** The same 40% means "use it now" 30 min before reset
   and "conserve" with 4 h left. Route by `remaining` **and** `hours_to_reset`.
2. **Quota is perishable.** Unused percent expires at reset. Idling at 80% while a backlog
   exists is the failure this policy exists to fix.
3. **Utilization ≠ token burn.** Spending quota on file ops, renames, scripts or formatting
   is waste, not utilization: the lead does those faster and with fewer errors.
4. **Two gates are non-negotiable**: the delivery visual gate (only GPT can see) and the
   independent review (producer never signs off). Everything else is negotiable.
5. **Re-read, never remember.** Percent is 1-pt coarse and invisible between calls.

## Read (gate)

```bash
python scripts/quota.py                        # verdict + tier + allocation
python scripts/quota.py --json                 # machine-readable
python scripts/quota.py --remaining 62 --minutes 210   # manual, no fresh sample
python scripts/quota.py --note "before draft"  # append a row to _work/quota_ledger.md
```

- Source: newest `rate_limits` record in `%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl`;
  `check_env.py` prints the same one-liner.
- Window already reset since the last sample → script assumes 100% and says so. If the user
  may have worked in the Codex app meanwhile, ask for `/status` and pass `--remaining`.
- Sample older than 30 min inside the same window → `STALE`; treat the number as a floor.
- `limit_id: codex` only. ChatGPT-app message quota is a different counter; never mix them.
- Re-read **after every GPT batch** and after every failure. Plan the next batch only on a
  reading you took since the last call.

## Tiers

| Tier | 5 h remaining | Lead | GPT | Reasoning effort | Calls |
|---|---|---|---|---|---|
| **S** surplus | ≥65% | integrate, scripted checks, arbitrate, deliver | production + all gates + optional second pass | `high` / `xhigh` | one per artifact |
| **A** normal | 50–65% | deterministic parts | quality-sensitive production + every gate | `medium` / `high` | one per artifact group |
| **B** conserve | 30–50% | all production | gates only, no exploratory passes | `low` / `medium` | batch artifacts and pages |
| **C** vision-only | 12–30% | all text / code / format | vision and layout judgement only | `low` | batch all images into one call |
| **R** reserve | <12% | everything | at most the single delivery visual gate | `low` | one call, then stop |

Effort is the throttle: `low→xhigh` moves cost roughly 2–4× for the same task. Always pass
`-m` and `model_reasoning_effort` explicitly (SKILL.md rule 4).

## Adjustments, applied in this order

1. **Burn window** — `hours_left ≤ 1` and `remaining ≥ 15` → tier **S**. The percent expires;
   spend it on the best queued item instead of losing it. Run the pending visual gate first,
   then the extra pass: never burn the reserve on text work while the gate is still owed.
2. **Pace** — with `hours_left ≥ 2`, `pace = remaining / hours_left` (pts/h):
   `pace ≥ 18` → promote one tier (too much slack per hour); `pace < 8` → demote one tier,
   never below **C** (demoting past vision-only would silently drop the gate).
3. **Weekly cap** — weekly remaining `< 25%` → at most **B**; `< 12%` → at most **C**.
   A fresh 5 h window never overrides a nearly-exhausted week.
4. **Floor** — `remaining ≤ 8%` → **R**, regardless of burn or pace.
5. **Hysteresis** — a tier change needs a fresh reading; a batch already in flight finishes.
   Do not oscillate around a boundary, and do not re-tier mid-call.

## Reserve

Keep **8–12%** for the end-of-delivery visual gate. Text review is deferrable until after
reset; the visual gate is not deferrable if the deliverable contains pages, figures, charts
or images. If visuals cannot be gated because quota ran out, say so in the delivery note —
do not ship as if it passed.

## What "more GPT work" means

Promote in this order when the tier allows; each item is worth more than the one below:

1. **Gates**: visual review, acceptance review. Always first.
2. **Production where the quality delta is largest**: argument structure, prose, requirement
   interpretation, spec/plan critique.
3. **Second, adversarial pass** on a different scope ("what would a reviewer attack"), not a
   repeat of pass 1.
4. **Cross-artifact consistency**: numbers re-derived, terminology, figure/table numbering,
   claims vs the facts pack.
5. **Figure-by-figure visual audit**, alternative drafts, options with trade-offs.
6. **Mechanical work** (rename, reformat, bulk edit, file moves): never. Zero marginal
   quality, higher error rate, and it consumes the pool the gates need.

Surplus rule: if tier is S or burn-window and the queue is empty, write "no valuable work
queued" in the ledger and leave the quota. Burning quota is not a goal; using it on the
backlog is.

## Cost model (calibrate, don't guess)

- Fixed per-call overhead is real: a trivial `exec "ok"` call observed ~1–3% of the 5 h pool
  (16 k tokens of system/repo context). At B/C, batch; at S, split for quality.
- Cached re-reads barely count; fresh input + output + reasoning do. Long sessions replaying
  a large context are cheaper than they look in raw token totals.
- Images are priced per image: batch every page into one call at C, and never send a page
  twice.
- Keep `_work/quota_ledger.md` (the `--note` rows). Three logged batches give per-job prices,
  after which plan in percent ("one review ≈ 6 pt") instead of vibes.

## Window plan

| Phase | Elapsed | Work |
|---|---|---|
| Open | 0–40% | GPT production while the pool is fresh; lead builds and integrates |
| Middle | 40–75% | reviews, iteration, fixes; re-read quota each batch |
| Close | last 60–90 min | visual gate, delivery checks, then surplus burn if `remaining > 20` |
| Report | reset | used% at start/end, calls, waste verdict |

## Report and KPI

- **Utilization**: `used%` at reset. Target **70–90%** when a backlog existed; above 92% is
  reckless (no room for the gate), below 50% with a backlog is wasted capacity.
- Final report line: `quota: 5h 20→78%, weekly 12→19%, tier S→B→C, 6 calls, 0 wasted`.
- Wasted windows and the reason (no backlog, user idle, proxy down) belong in the
  postmortem, not hidden.

## Worked examples

| Reading | Tier | What happens |
|---|---|---|
| 78% left, 4 h | S | GPT drafts the sections; lead builds; GPT visual gate; one extra consistency pass; end ≤25% |
| 62% left, 3.5 h | A | GPT produces the quality-sensitive parts + both gates; no speculative passes |
| 46% left, 2 h | B | Lead produces everything; one batched review; 8 pt held for the visual gate |
| 28% left, 90 min | C | Lead produces; one batched vision+layout pass at the end; text review deferred |
| 44% left, 40 min | S (burn) | Run the queued adversarial pass now; it expires with the window |
| 14% left, 3 h | C | Vision only; schedule text work after the reset at the printed time |
| 62% left but weekly 20% | B (weekly) | Weekly cap wins; batch, effort low |
