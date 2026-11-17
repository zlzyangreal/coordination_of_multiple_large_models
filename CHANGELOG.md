# Changelog

## 3.2.0 - 2026-11-17

Workflow panel: every board at once, a typed model picker, and rule-disabled options.

- **同页多任务**：新增 `GET /_multimodel/workflow/boards`（纯 JSON 索引，按活跃度排序）与
  `GET /_multimodel/workflow/events?tasks=all`（单条 SSE 连接，增量帧 `{boards, tasks}`，
  `revision` 未变不跑 Python）。面板改为卡片网格，多条任务同页并列、各自实时。
- **可见性**：快照新增 `activity`（`current` / `blocked` / `last_event` / `stale_seconds`），
  卡片显示“谁在做、卡在哪、多久没动”和最近事件时间线。
- **模型选择**：`GET /_multimodel/models` 增加 `groups` / `favorites` / `recent`；面板改为可直接
  打字的 combobox（模糊过滤、键盘、常用置顶），`pin` 成功写入 `recent`。
- **规则闸门**：新增 `scripts/families.py`（项目 `overrides.<model>.family` > 随包
  `assets/families.json` > unknown）与 `scripts/eligibility.py`；新增
  `GET /_multimodel/workflow/options?task=&node=`，逐条返回 `eligible/code/rule/reason`。
  违规项在 UI 中禁用并写明理由（如“与产出方同族（deepseek）——规则 19”），`pin`/`assign`
  在 Node 侧同判并返回 **422 `rule_violation`**，不依赖 UI。
- **项目根**：板根优先取宿主 `workspaceRegistry` 中的工作区，取不到退回进程 cwd（旧行为）。
  `workflow`/`boards`/`tasks`/`options`/`events`/`models` 均接受 `?project=`。
- **一键审核**：待验证卡片改为「✓ 通过并启用 / ✗ 不通过」。通过 = 跑完整检查并启用，状态写入
  `verification:{decision:"pass", by, note, version, digest, at}`；不通过 = 记录原因、保持未验证、
  关闭受限模式，并写一条 I1 `verify.rejected` 日志（通过写 I2 `verify.approved`）。`/status`
  返回 `verification`，面板显示上次审核结论与原因。

## 3.1.0 - 2026-11-17

Collaboration workflow: an executable board, a live graph, and per-node model switching.

- `scripts/orchestration.py` is the deterministic board: schema 1 in
  `_work/orchestration/<task>.json`, revision CAS under a per-board cross-process lock, transitions
  T1–T11 (including `fail` for T4), and invariants I1–I8. A producer never reaches `done` on its
  own result — it reaches `review`, and only an independent review with `verdict pass` **at the
  current attempt** completes it (I6). A failed review returns the producer to `rework`, clears the
  stale result and verdict, increments the attempt, and re-enters the review
  (T10 → T5 → T3 → T7 → T8 → T9).
- Manual model assignment outranks automation: `pin` sets `source:"manual"`, `assign` refuses to
  overwrite it (I4), assignment is locked once a node leaves `pending`/`ready`/`rework`, and a
  result must carry the child model that was actually assigned (I8).
- Delivery is a gate, not a formality: the task must be in `reviewing`, every node `done`, no node
  in `review`/`rework`/`failed`, no `high`/`medium` finding open, each producer covered by its own
  passing review at the current attempt, and the receipt hash chain must verify. Empty boards and
  boards without a review node are refused at `plan` time, so a vacuous delivery is impossible.
- Receipts are per task (`<project-key>/<task>.jsonl`); every mutation first verifies that the
  current tip matches the bytes it is about to change, so an out-of-band edit cannot be re-blessed
  by a later legitimate operation, and one board's mutation cannot stale another board's chain.
- `lib/workflow.js` bridges the board: fixed argv into the Python CLI, a per-task receipt stream in
  `$DSH_HOME/coordination/orchestration/<project-key>/`, SSE snapshots (`{ok, task, chain}`) with a
  1 s tick fallback (`fs.watch` creation is contained, re-armed on `rename`, `unref`'d and disposed
  with the lifecycle), the live `ctx.llm` model catalog, one result-admission policy shared by the
  tool and the HTTP route (`subagent.listChildren` must confirm the child **and** its model), and
  `headCommit()` for UI-driven delivery.
- `/_multimodel/workflow`, `/workflow/events`, `/models`, `/workflow/pin` and `/workflow/node` sit
  behind the existing origin + CSRF checks; the `coordination` tool gains `action:"workflow"` with
  nine ops. When the `llm` or `subagent` service is absent the bridge says so and degrades — it
  never invents a model table or claims unverified dispatch.
- The right sidebar gains a `工作流` / `控制台` segmented view: an SVG DAG (dependency solid, review
  dotted, rework red), status colours, attempt badges, a node detail card with a model selector and
  `指定模型`, and a `交付` button gated by the server's `can_deliver`. Live via EventSource, falling
  back to a 5 s poll and a `轮询` pill after two stream errors.
- `docs/workflow.md` documents the model, transitions, invariants, routes and the residual risk
  that a local administrator can rewrite the board and the receipts.
- 78 Node tests (47 existing + 22 bridge + 9 client layout) and 373 Python cases pass; `verify.mjs`,
  `check_docs.py` and `check_rules.py` pass.

## 3.0.4 - 2026-11-16

Right-sidebar panel scrolling and busy feedback.

- The panel root now uses `height: 100%`, `box-sizing: border-box` and `overflow: auto`; the
  right-sidebar tab scrolls vertically and horizontally instead of clipping the bottom content
  and the action column.
- Live CDP measurement: clientHeight 390 / scrollHeight 1081, overflow auto, no horizontal
  overflow at the normal pane width.
- A global busy banner now shows a native indeterminate `<progress>` plus the current action
  label; “验证并启用” changes to “处理中…” and stays visible for at least 600 ms, then the
  verification result card and eight checks render. The same feedback covers toggle, install
  and activate.
- The optional-extension confirmation dialog now shows its own progress bar and “正在安装扩展…”
  label while the dialog stays open with controls disabled; completion closes the dialog and
  shows a green “已安装（仍为禁用）” notice, failure shows a red per-extension reason.
- Extension selection is derived only from `optional` + `not_installed` + installable rows; an
  `installed_disabled`/`enabled` row cannot be checked, its stale selection is dropped, and the
  install count cannot include it. The duplicate “诊断” verify/enable buttons were removed; that
  card is now a read-only “运行状态” summary.
- An enabled extension now has a “停用” action with confirmation. Deactivation calls
  `setBundleEnabled(id, false)`, logs `extension.deactivated` at I2, keeps the files and
  Profile entry, returns to `installed_disabled`, and is idempotent.
- On a desktop process without HMR the host returns `restart-required` after writing the
  profile; activation/deactivation now treat a changed profile as success and tell the user to
  restart, instead of reporting a false failure. A full app restart is required for the running
  process to drop the extension.
- The HTTP deactivation route goes through the extension facade's `deactivate` method; the
  smoke test asserts the route exists and does not return “deactivate is not a function”.
- 47 Node tests and 331 Python cases pass.

## 3.0.3 - 2026-11-11

Right-sidebar guide entry.

- The right-sidebar tab definition now carries a `guide` array. `sidebarRightTabs` uses it to
  render the "开始" card; the earlier custom `sidebar.right.tab.guide.entry` was not dispatched
  by the owner and therefore never appeared.
- The card opens the `multimodel-coordination` tab (switch, contract, optional extensions,
  logs). 45 Node tests and 331 Python cases pass.

## 3.0.2 - 2026-11-10

Client settings section dependency fix.

- The client module now declares `exports.inject = ["slots"]`, so Cordis waits for the slot
  service before `apply`. Without it, v3.0.0 failed the client entry and v3.0.1's containment
  catch hid the failure, leaving the app bootable but without a "多模型协作" entry.
- The entry moved from Settings to the right sidebar: a `sidebar.right.tab.guide.entry` card
  and a registered right-sidebar tab open the panel with the switch, contract, optional
  extensions and logs.
- Optional extension installation now times out (default 120 s), cancels the host install when
  possible, and reports the reason in the UI instead of hanging forever on an unreachable source.
- The client test asserts the `slots` dependency and both slot registrations; 45 Node tests and
  331 Python cases pass.

## 3.0.1 - 2026-11-10

Desktop boot hotfix.

- The client settings section now uses a string label and the package id, and both the
  slot injection and registration are contained; a client slot failure can no longer fail
  the renderer entry or prevent DSH from starting.
- The host guards direct and late `webServer` route registration; a route failure is logged
  as I0 `plugin.routes_failed` while the plugin stays loaded, instead of failing the host
  entry. The OFF allowlist already admits `plugin.*`.
- New client and host boot tests cover throwing `inject`/`register` and post-settlement
  late callbacks; 42 Node tests and 331 Python cases pass.

## 3.0.0 — 2026-11-09

DSH-native bundle on top of the v2.4.0 engine.

- Standard bundle: `package.json` (`dsh.bundle`, `dsh.client`), `cordis.patch.yml`, host and client
  entries; `engines.dsh` is exactly `0.2.1-rc.3`.
- One global switch, default **OFF**; ON injects a 40-rule contract and registers one
  `coordination` tool. The monotonic guard denies every other tool until the session calls
  `coordination({action:"contract"})`; an invalid contract keeps a deny guard and logs I0.
- Version lock: running version **and** package digest must match the user-verified values;
  otherwise the plugin starts `pending_verification` (no contract, no tool, switch disabled).
  First install is pending too. A new version is staged in a separate verification profile.
- Optional extensions are declaration-driven; install is confirmed and **disabled**, activation
  needs a reported version/source plus a declared probe. `dsh-our-free-model` is optional and is
  neither bundled nor installed by default.
- Local-only logs: daily `runtime_error_*.jsonl` / `optimization_*.jsonl`, I0–I3, dedupe/index,
  redaction, rotation/retention, fallback; OFF collects only minimal lifecycle faults. No
  automatic repair, config write, upload or git inclusion.
- Python core retained through `ctx.subprocess`; `core_unavailable` is I0 and fail-closed.
- 331 Python offline cases still pass; 37 new Node tests cover state, logs, extensions, guard,
  bundle metadata and 40-rule parity.
- Supported platform: **DSH 0.2.1-rc.3 Web profile, Windows 11 x64**. Desktop is unverified.
