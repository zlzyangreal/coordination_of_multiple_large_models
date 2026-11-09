# 38 条规则迁移矩阵（v2.4.0 → v3.0.0 DSH 插件）

规则 1–38 是 v2.4.0 的原文基线（`assets/rule-baseline.v24.sha256` 绑定）；39–40 是本次新增。
"机制"指插件实现方式：P=提示契约，N=原生守卫/路由/状态，E=保留的 Python 引擎，U=UI。

| # | 规则（简称） | v2.4.0 入口 | v3 机制 | 测试 | 状态 |
|---|---|---|---|---|---|
| 1 | One lead | `SKILL 规则 1` | P | tests/smoke.test.mjs (contract) | full |
| 2 | Production ≠ review. | `SKILL 规则 2` | P/E | selftest plan_gate | full |
| 3 | No envelope, no review. | `build_envelope.py` | E | selftest envelope | full |
| 4 | Reviewer writes nothing. | `run_gpt.py` | E | selftest run_gpt | full |
| 5 | Schema verdict before adoption. | `validate_result.py` | E | selftest validate_result | full |
| 6 | Model = slug + reasoning. | `run_gpt.py` | E | selftest runner | full |
| 7 | Three paths differ: | `build_envelope.py` | E | selftest envelope | full |
| 8 | Version is hash + mtime. | `build_envelope.py` | E | plan_gate check | full |
| 9 | Persist every call. | `run_gpt.py` | E | selftest runner | full |
| 10 | Pin the contract and the root. | `session.py` | E/N | tests/state.test.mjs | full |
| 11 | Quota before routing. | `quota.py` | E/P | selftest quota | full |
| 12 | Network before waiting. | `check_env.py` | E | selftest check_env | full |
| 13 | Resolve executables at runtime; | `check_env.py` | N | tests/smoke (optional core) | full |
| 14 | Stay in force. | `SKILL 状态行` | N/P | tests/smoke guard | full |
| 15 | Stop lines: | `review_budget.py` | E | selftest review_budget | full |
| 16 | Delete temp files | `references/failure-and-artifacts.md` | P | check_docs | full |
| 17 | Two protected call paths, one semantic role each. | `run_gpt.py` | E/P | selftest runner | full |
| 18 | Credentials never leave the store. | `models.py` | E | selftest credentials | full |
| 19 | Pin and independence are exact. | `plan_gate.py` | E | selftest plan_gate | full |
| 20 | Team changes need a yes. | `models.py team` | E/P | selftest team | full |
| 21 | Planning ≠ dispatching. | `references/model-access.md` | P/E | selftest plan | full |
| 22 | Plan first, and gate it. | `plan_gate.py` | E | selftest plan_gate | full |
| 23 | Review budget is a budget, not a habit. | `review_budget.py` | E | selftest review_budget | full |
| 24 | A reviewer must see behaviour. | `test_evidence.py` | E | selftest evidence | full |
| 25 | GPT produces at S/A. | `review_budget.py deliver` | E | selftest deliver | full |
| 26 | Facts are versioned. | `facts_pack.py` | E | selftest facts | full |
| 27 | Stable drives candidate. | `iteration.py` | E/N | selftest iteration | full（仓库侧稳定版驱动；插件侧见 39/30） |
| 28 | Two identities, two roots. | `session.py + iteration.py` | E | selftest iteration | full |
| 29 | Freeze the executor. | `iteration.py guard` | E | selftest iteration | full |
| 30 | Promotion is a gate, not a version edit. | `iteration.py promote` | E/N | selftest iteration | partial（插件侧是验证后激活门；最终晋升仍需用户 approve/promote） |
| 31 | The human owns the ends; the agent owns the means. | `authority.py mandate` | E/P | selftest governance | full |
| 32 | Evaluate; never defer and never flatter. | `authority.py intake` | E | selftest governance | full |
| 33 | Three levels, one default. | `authority.py classify` | E | selftest governance | full |
| 34 | Controlled operations pass `authority.py guard`. | `authority.py guard` | E/N | tests/smoke guard + selftest governance | full |
| 35 | Grants are scoped and inherited by intersection. | `authority.py grant` | E | selftest governance | full |
| 36 | Ask once, batch, and keep moving. | `authority.py request` | E | selftest governance | full |
| 37 | Failure stops; it never degrades. | `authority.py mode/check` | N/E | tests/state.test.mjs + selftest governance | full |
| 38 | State the enforcement boundary. | `references/governance.md + SKILL §Scope` | P/U | docs/architecture.md + tests/bundle | partial（仅受控路径；宿主/第三方在范围外） |
| 39 | Lock the version; activate only after the user verifies. | `新增（mandate HC-8）` | N | tests/state.test.mjs + tests/engine.test.mjs | partial（验证后激活、摘要变更与引擎快照拒绝已测；真实宿主改写包文件的竞态仍无宿主级测试） |
| 40 | Load the rules before any controlled action. | `新增（mandate HC-9）` | N | tests/tools.test.mjs + tests/smoke.test.mjs | full（宿主注册 + 无效契约/无会话/待验证/未确认的单元与 mock 流程已测；live model turn 未跑） |

## 说明

- 规则 1–38 的原文与 `SKILL.md` 逐条一致，`scripts/check_rules.py` 在构建/启动时校验；缺一条或改动基线即失败（I0）。
- 规则 38 的边界不变：宿主工具、直接 shell/网络、非合作第三方插件与模型推理不在强制范围。
- 规则 39/40 的“同 Profile 双版本并行”不在声明内：39 是独立验证 Profile + 验证后激活；40 是宿主注入 + 会话确认 + 拒绝守卫。
- 331 项 Python 离线自测保持通过；新增 36 项 Node 测试覆盖 state/logs/extensions/guard/bundle/规则一致性。
