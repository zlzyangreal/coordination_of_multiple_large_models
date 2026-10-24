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
python scripts/quota.py                         # live app-server, log fallback
python scripts/quota.py --source log            # history only, no app-server
python scripts/quota.py --json                  # machine-readable
python scripts/quota.py --remaining 62 --minutes 210   # manual, no reading
python scripts/quota.py --note "before draft"   # append a row to _work/quota_ledger.md
```

- **Live first**: `codex app-server` + JSON-RPC `account/rateLimits/read` (~2.5 s) is the
  authoritative value. It sees consumption from every client, including calls that wrote no
  rollout, so it stays correct even when work runs elsewhere.
- **Log fallback** (`--source log`, or live unavailable): the newest `rate_limits` record in
  `%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl`. It is valid **only while that record
  belongs to the current window**.
- **An expired sample is unknown, never 100%.** When the sample's `resets_at` is in the past,
  the script returns `remaining=null`, tier `U`, exit code `2`. Do not extrapolate a fresh
  window from an old sample: that is how an earlier version claimed 100% while the account
  was at 89% used.
- Sample older than 30 min inside the same window → `STALE`; the number is an upper bound on
  remaining, not a measurement.
- `limit_id: codex` only. ChatGPT-app message quota is a different counter; never mix them.
- **When to read**: on every user message (one live read at the top of the turn), before each
  new batch, and after any failure. A reading older than the turn it is used in is not a reading.
- **A batch = one `run_gpt.py` call.** The runner appends its own row to
  `_work/quota_ledger.md` (tier, 5 h left, weekly left, before→after, delta), so a manual
  `--note` is only for readings taken outside the runner.
- If live fails, fall back to the log, then to `U`. Never invent a number to keep the flow.
- `check_env.py` prints the same one-liner, so the environment gate already carries quota.

## Tiers

| Tier | 5 h remaining | Lead | GPT | Calls |
|---|---|---|---|---|
| **S** surplus | ≥65% | integrate, scripted checks, arbitrate, deliver | production + all gates + optional second pass | one per artifact |
| **A** normal | 50–65% | deterministic parts | quality-sensitive production + every gate | one per artifact group |
| **B** conserve | 30–50% | all production | gates only, no exploratory passes | batch artifacts and pages |
| **C** vision-only | 12–30% | all text / code / format | vision and layout judgement only | batch all images into one call |
| **R** reserve | <12% | everything | at most the single delivery visual gate | one call, then stop |

**U - unknown** is not a band: no usable reading exists (expired sample, app-server down,
never called). Plan as **R** and take a reading before any batch. A script that would
otherwise report "surplus" must report `U` instead; pretending is worse than not knowing.

## Effort stays put

`-m` and `model_reasoning_effort` are always passed explicitly (SKILL.md rule 4), but the level does
**not** move with the tier. It stays at the configured value (`~/.codex/config.toml`, currently
`medium`; the script prints it). Two reasons:

- Lowering effort degrades judgement exactly when the artifact matters most — the conserve tiers
  are where a bad review costs the most.
- Raising effort is a poor way to burn surplus: two normal passes over different scopes beat one
  `xhigh` pass, and a changed level makes reviews incomparable between rounds.

One exception, written in the ledger: tier **R** with a gate that cannot wait → drop **one** notch
for that call only, then restore. Any other change needs the user's explicit request.

The quota levers, in order of effect: **who produces** (lead vs GPT) → **number of passes** →
**number of calls** (batching) → **scope** (pages/artifacts). Effort is not on the list.

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
- Effort does multiply cost (`low`→`xhigh` is roughly 2–4×), which is exactly why it is left at
  one setting: it is a quality knob, not a budget knob.
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
- Per message: the status line (`session.py status`) carries the current 5 h and weekly
  readings, so the number is auditable at any point, not reconstructed at the end.
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
| 62% left but weekly 20% | B (weekly) | Weekly cap wins; batch, no extra passes |
