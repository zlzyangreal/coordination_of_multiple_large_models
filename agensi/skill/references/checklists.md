# Checklists

Short, copyable gates. Each item is a yes/no; a "not sure" is a no.

## Pre-flight (step 0)

- [ ] Working directory and repository root printed, and they are the ones I expect.
- [ ] Branch and HEAD recorded; no uncommitted surprise in the tree.
- [ ] Remaining quota and reset time read from a live source (expired = unknown).
- [ ] Models available now: at least one producer and one reviewer from different families.
- [ ] Call budget for this milestone written down (default: 2 review calls, 30 minutes).
- [ ] Artifact paths exist and are writable; temp output directory known.

## Plan gate (step 2)

- [ ] Requirement restated in one paragraph, in the requester's terms.
- [ ] At least two approaches, with the trade-off that decides between them.
- [ ] Work-package table: id, output path, owner role, acceptance test.
- [ ] Risks listed with mitigations; unknowns named.
- [ ] Reviewed by a different family than the planner; findings resolved or explicitly deferred.
- [ ] Call budget and stop lines stated.

## Dispatch (step 3)

- [ ] Each unit has one owner and one output path.
- [ ] Envelope includes criteria, not adjectives.
- [ ] Facts frozen and versioned; the version number is in the envelope.
- [ ] No unit asks a model to both produce and approve.

## Review gate (step 5)

- [ ] Result is schema-valid (verdict, severity_basis, units_confirmed present).
- [ ] Version echo matches the artifact hash and mtime sent.
- [ ] Test evidence attached, or the review is marked static-only.
- [ ] Every finding verified at source; nothing adopted on trust.
- [ ] Accepted fixes applied; rejected findings have a one-line reason; deferred have an owner.
- [ ] Decision logged with timestamp; review round counted against the budget.

## Delivery gate

- [ ] Acceptance tests run, with the command and exit code recorded.
- [ ] Docs and version strings agree (release notes, manifest, skill metadata).
- [ ] No capability silently removed; anything removed is declared.
- [ ] The artifact path delivered is the one that was reviewed and tested.
- [ ] Postmortem written if any gate was overridden, with the reason and the cost.

## Failure triage

- [ ] Transport (proxy/DNS/endpoint) ruled out before changing the prompt.
- [ ] 401/403/429 handled by switching or waiting, not by a third identical retry.
- [ ] Stale context rebuilt from disk, not from conversation history.
- [ ] After two silent retries: stop, report, show evidence, ask.
