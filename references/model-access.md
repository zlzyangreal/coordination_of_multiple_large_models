# Model access: lanes, credentials, routing, failure

The skill started with two lanes and still runs on them by default. This page adds
optional HTTP providers beside them — free/keyless, API key, custom base URL, local
server, or a token minted by an account CLI — under one catalog, one router and one
call path.

## Lanes (and what is protected)

| id form | lane | called by | status |
|---|---|---|---|
| `codex:<slug>` | GPT through the codex CLI | `scripts/run_gpt.py` only | **protected** |
| `codex:auto` | whatever `~/.codex/config.toml` names | `run_gpt.py` | default for `review` / `vision` |
| `dsh:<slug>` | DeepSeek through the DSH host session | the lead session (never a script) | **protected** |
| `dsh:auto` | the lead itself | the host | default for `lead` / `mechanical` |
| `<provider>/<model>` | any HTTP endpoint | `scripts/models.py call` | additive, opt-in |
| `chain:<name>` | an ordered list of the above | `scripts/models.py call --chain` | virtual |

`auto` is deliberate: `codex:auto` means "do not pin a slug here; the runner resolves
model and effort from the codex config", and `dsh:auto` means "the session that is
reading this". Adding a provider never changes either default.

An HTTP provider is usable for ordinary calls as soon as it is configured, but it is
not eligible for the `review` role until a probe or a successful call has recorded
health (`models.py test`), because a review is the one gate that cannot be best
effort. Adds, role switches and removals append a row to
`_work/models/model_changes.log`.

`models.py call --model dsh:...` **refuses** (exit 2). A script that spawned its own
DeepSeek caller would be a second, unrecorded lead; the protected path stays the host
session. `models.py test codex:auto` delegates to `run_gpt.py --probe`, so there is
exactly one GPT call path.

**Protected families.** A GPT or DeepSeek row reached over HTTP is refused unless the
provider setting says `"allowProtectedFamily": true`. Without that flag the catalog
shows the row as unusable with the reason, which keeps the old paths from being
replaced by accident. With it, the user has explicitly chosen a second path and owns
the consequence (no rollout, no codex quota accounting).

## Configuration is per project

```
<project>/_work/models/
├── config.json        providers, lanes, roles, chains, budgets   (safe to read/share)
├── credentials.json   secrets                                    (0600, never printed)
└── state.json         health, cooldowns, usage, rotation         (disposable)
```

Resolution order (lowest to highest): built-in default → `~/.coord-models/config.json`
(only with `--scope user` writes) → `<project>/_work/models/config.json`. The project
file wins. `COORD_MODELS_HOME` moves the user-level directory, which is what the test
suite uses to stay hermetic.

Two projects never share state: a chain created for one deliverable cannot reroute
another. `--scope user` is the only way to share a provider roster or key, and even
then the project file overrides it.

## Credentials

Precedence for one provider (first hit wins):

1. `credential` spec in the provider setting
2. inline `apiKey` (**warned**: it is a readable file)
3. the store written by `models.py key set` (project, then user)
4. the provider's declared environment variable (registry `auth.env`)

```json
"credential": {"source": "env", "name": "COMPANY_LLM_KEY"}
"credential": {"source": "file", "path": "~/.qoder/token", "json": "access_token"}
"credential": {"source": "command", "argv": ["qoder", "auth", "token"], "timeoutSeconds": 30}
```

- A `command` spec runs **without a shell** (`argv` preferred; a plain `command`
  string is split with `shlex`, no metacharacter expansion), so a project config
  cannot smuggle a pipeline into a read command.
- `list`, `route`, `show`, `key status` and `doctor` **never execute** a credential
  command. They treat a declared command as configured ("minted on use"). Only an
  explicit `call`, `test` or `discover` mints it.
- A failing command's stderr is withheld: login helpers are exactly the programs
  that print a token on failure. Run the command yourself to see why it failed.
- Values never reach stdout, `state.json`, `model_calls.jsonl` or the review ledger:
  status output is `hasKey` + `…last4`, and provider error bodies are scrubbed
  against the resolved key before they are printed or stored.
- The store is written atomically, mode 0600, and on Windows its ACL is replaced with
  the current user (`icacls`). `models.py doctor` verifies the effective ACL; if the
  hardening failed, `key set` warns and doctor fails, rather than implying privacy
  that does not exist.
- `providers.registry.json` includes `oauth` / `adc` rows for completeness. They are
  shown as **not drivable**: their credentials are minted by an interactive sign-in
  or a cloud identity flow this skill cannot run. Use the vendor client, then import
  the resulting token as a `token`/`command` credential.

## Accounts: login is the vendor's job

A platform that authenticates a *person* (browser OAuth, device code, QR scan) is not
something a coordination CLI should reimplement: the password prompt, the redirect and
the token exchange belong to the vendor client. The skill therefore supports a
vendor-mediated account lifecycle:

```json
"account": {
  "loginArgv":  ["qoder", "login"],
  "statusArgv": ["qoder", "whoami"],
  "logoutArgv": ["qoder", "logout"],
  "timeoutSeconds": 300
}
```

```bash
python scripts/models.py account login  qoder_local   # launches the vendor flow
python scripts/models.py account status qoder_local   # vendor whoami, redacted
python scripts/models.py account logout qoder_local   # vendor logout + token cleared
python scripts/models.py account list                 # configured accounts
```

- Commands are argv lists, never a shell; login/logout inherit the terminal so an
  interactive prompt works, status output is captured and redacted.
- The skill stores **no password**: it records `loggedIn`, the time and the vendor's own
  status line in `state.json`, and clears the project/user stored token on logout.
- A call still obtains its token through the provider's `credential` spec (typically
  `{"source": "command", "argv": ["qoder", "auth", "token"]}`), so "login" and "call"
  share one credential source and re-login is just `account login` again.
- The reference project's ten browser-OAuth channels remain out of scope as *built-in*
  channels; this is the supported way to bring one of them in without reimplementing its
  sign-in.

## Providers and model rows

`models.py providers` lists the vendored registry (76 providers: 61 API-key, 10
free-tier, 3 local, 2 OAuth). Add one for this project:

```bash
python scripts/models.py add deepseek --models deepseek-v4-pro
python scripts/models.py key set deepseek            # reads stdin, prints only …last4
python scripts/models.py discover deepseek           # live listing, no generation spent
python scripts/models.py add ollama                  # local, no credential
python scripts/models.py add-custom my_gateway --base-url https://llm.internal/v1 \
    --models internal-chat-1 --format openai-chat --auth apiKey
```

- `format` is one of `openai-chat`, `openai-responses`, `anthropic-messages`; the
  vendored row normally declares it and a custom row defaults to `openai-chat`.
- `models` is the contract. When it is absent, a discovered listing is used, then the
  registry's published ids. A discovered list is a cache, never an override.
- A custom endpoint gets `<base>/models` derived for `discover` only when it is
  OpenAI-compatible and declares no `modelsUrl`; vendored providers without a declared
  listing are not guessed at.
- `overrides.<model>` can correct capability data for models the registry does not
  describe (local models, private deployments): `vision`, `reasoning`,
  `contextWindow`, `maxOutput`, and `family` (see independence below).
- `limits` (`callsPerDay`, `tokensPerDay`) are counted from `state.json` usage; the
  router demotes a provider that has spent its declared budget and the call path
  skips it instead of spending more.

## Availability and failure

`test <provider|model>` uses the model listing (a key check that spends no
generation). `--generate` spends one tiny completion, which is the only way to verify
a provider that publishes no listing.

Failures are classified by status first, text second, into scopes:

| kind | scope | fallback? | cooldown |
|---|---|---|---|
| `auth`, `permission`, `billing` | provider (whole row) | later calls skip it | 6–12 h |
| `rate_limit` (429) | model | yes, next link | provider `Retry-After`, else 15 min |
| `overloaded` / `upstream` (5xx) | model | yes, after one retry | 2 min |
| `timeout`, `transport` | model | yes, after one retry | 30–60 s |
| `model_missing` (404 naming a model) | model | yes | 1 h on that model |
| `endpoint_missing` (404 on the URL) | request | **no** | none |
| `invalid_request` (400/413/422) | request | **no** | none |
| `empty_response` | model | yes | 30 s |

- A request-scoped defect is our bug or a wrong base URL; walking to another provider
  would hide it. The CLI exits 4 and prints the provider's own message.
- `rate_limit`/`5xx` cool only the failing model, so a chain inside one provider still
  works. Credential-level refusals cool the provider, because every model behind that
  key will fail the same way.
- Retries are bounded by `defaults.retryTransient` (default 1, `0` = one attempt) and
  only for transient kinds; a 429 or an auth refusal moves to the next link at once.
- Every attempt lands in `_work/models/model_calls.jsonl` (redacted) and updates
  `state.json`; the call record is the evidence, not the claim.

## Routing

```bash
python scripts/models.py route --role review --producer codex:auto
python scripts/models.py route --role vision --needs vision
python scripts/models.py route --role produce            # quota tier decides
python scripts/models.py route --role lead --pin dsh:auto
python scripts/models.py use review mock_a/mock-ok       # switch, project-scoped
```

Selection order: an explicit `--pin` (exact, else exit 3 — never a substitute) → the
role's configured model (`use`) → the quota tier for `produce` (S/A: codex produces;
B/C/R/U: the lead produces) → the protected lane for the role → capability, health,
budget and the `preference` list.

For `review`, the producer's **family** is excluded (`--producer <id>`), and a row
whose family cannot be established is excluded as well: an aggregator selling
`openai/gpt-*` next to a codex-produced artifact would otherwise look independent.
Declare `overrides.<model>.family` for a private model, or pass
`--allow-unknown-family` when you accept that the independence claim is unverified.
When no candidate survives, routing fails (exit 3) instead of shipping a same-family
review.

`auto` for `produce` returns a plan, not a call: the lead performs the work in its
own lane. `models.py call --role produce` therefore refuses the `dsh:*` link (the
session is the producer) and uses the codex link when the tier gave it one.

## Chains

```bash
python scripts/models.py chains set review-safe \
    --models codex:auto,deepseek/deepseek-v4-pro,ollama/qwen3:8b --strategy fallback
python scripts/models.py chains show review-safe
python scripts/models.py call --chain review-safe --prompt-file brief.md --output out.md
```

`fallback` is a fixed ladder; `round-robin` rotates the head and sticks for
`stickyLimit` turns; `smart` demotes cooling/over-budget links; `pace` additionally
uses the codex quota reading. Links that are missing, unusable, cooling or protected
are dropped **with the reason** (`chains show`), never silently replaced.

## Calling

```bash
python scripts/models.py call --model mock_a/mock-ok --prompt "hello" --output out.md
python scripts/models.py call --chain review-safe --prompt-file _work/brief.md --json
python scripts/models.py call --model codex:auto --prompt-file brief.md --dry-run
```

- Output: the answer on stdout (or `--output`), plus `--json` for a record with
  `requestId`, `model`, `usage`, `latencySeconds`, `attempts`, `dropped`.
- The codex lane builds a `run_gpt.py` invocation (`--calls-dir` and `--ledger` inside
  the selected project, `cwd=project`) and returns the runner's exit code; an empty
  payload is a failed attempt, not a success.
- `call --role lead` / `--role mechanical` refuses: that role is the session, not a model
  a script can call. `call --role produce` also refuses when the quota tier assigned
  production to the lead, instead of silently calling codex as a substitute lead. A
  `dsh` row is never offered as a fallback link, because a fallback that cannot be
  called is not a fallback.
- The HTTP lane is non-streaming by design: a coordination call is a bounded unit of
  work whose result the lead reads in full, and every OpenAI-compatible local server
  implements the non-streaming shape.

Exit codes: `0` ok, `1` failure, `2` misuse/unavailable (including a `dsh:*` call),
`3` no usable candidate / exact pin unusable, `4` request-scoped defect (no fallback),
`5` all links failed.

## Boundaries

- Browser OAuth sign-in flows, and gateways that require a minted session id plus
  vendor-specific headers (the reference project's anonymous free lane and Kilo
  channel), are **not** reproduced here. The registry lists the former as not
  drivable; for the latter use the reference plugin. Importing a token those clients
  already minted is the supported path.
- This layer is a client, not a broker: it does not proxy, cache or reshape requests
  for other tools. The reference project's local gateway is out of scope.
- Configuration is code-equivalent for `command` credentials: only run projects you
  trust, and keep `credentials.json` out of version control (`_work/` already is).

## Compatibility

With no `_work/models/config.json`, the behaviour of the skill is the v1.6.0 behaviour:
`review`/`vision` use `codex:auto`, `lead`/`mechanical` use `dsh:auto`, and the
envelope/schema/zero-write review gate is unchanged. The new layer adds commands,
files and docs; it does not edit `run_gpt.py`, `quota.py`, `session.py`,
`check_env.py`, `validate_result.py`, `build_envelope.py`, `schema_for_codex.py` or
`reap_turns.py`.
