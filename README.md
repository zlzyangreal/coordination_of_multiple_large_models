# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制——代码、文档、数据、方案都适用；代码风格、写作、排版属于项目自己的事实包与 DoD，不写进这里。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：2.4.0（2026-11-03）
- 当前离线自测：331 项（mock providers，不联网）
- 主线：**人类定目标、硬约束、授权和验收；Agent 自主研究、规划、设计、执行；多模型独立评审；编排层在执行层拦截越权并留痕。**
- 上一代：`多模型协作Spec.md` v2.4.1，已冻结，仅作留档

## 核心能力（按版本）

| 版本 | 核心添加 |
|---|---|
| 2.4.0 | **人类治理的决策权**：委托书、思路评估、L1/L2/L3、请求与拒绝记忆、授权与原子额度、执行层守卫、控制模式、决策链 |
| 2.3.0 | **自举迭代**：稳定版驱动独立 worktree 候选版，commit 绑定测试证据，基线对比、用户验收、快照晋升与独立回退 |
| 2.2.0 | **可执行门禁**：方案评审门、评审预算、测试证据、事实包版本、工作区身份、GPT 生产核算、发版检查 |
| 2.1.0 | **规划与分发分离**：`plan` 角色独立理解需求、调研、设计并产出方案；planner 不负责分任务 |
| 2.0.0 | **多平台模型接入**：免费/API Key/自定义地址/本地服务/账号令牌统一管理；任务团队入队审批 |
| 1.x | 调用与契约基础：统一 `run_gpt.py`、零写盘评审、schema/信封、事实包、额度自适应 |

## 安装

把 skill 目录以 junction 挂进 DSH 的技能目录：

```powershell
New-Item -ItemType Junction `
  -Path  "D:\deepseek\resources\runtime\office-skills\coordination_of_multiple_large_models" `
  -Target "D:\deepseek_workspace\coordination_of_multiple_large_models"
```

- 挂完需要**重启 DSH** 重新扫描技能目录；不识别时可按普通文件夹使用，或挂到 `~/.codex/skills` 给 Codex 侧。
- 其他 Agent：把整个目录放进它的 skills 目录即可，`SKILL.md` 是入口。

## 同步

```powershell
cd D:\deepseek_workspace\coordination_of_multiple_large_models
git pull --rebase
git add -A
git commit -m "docs: ..."
git push
```

- 本机仓库已设 `http.proxy = http://127.0.0.1:7897`（Clash Verge 混合端口）；Clash 没开或端口变了，先 `git config --unset http.proxy` 或改成实际端口。
- 换机器：`git clone https://github.com/zlzyangreal/coordination_of_multiple_large_models.git`，再按安装步骤挂载。

## 目录

- `SKILL.md`：Agent 入口——何时用、38 条硬规则、工作流、脚本表。
- `references/`：`environment.md`、`model-access.md`、`model-registry.md`、`quota-routing.md`、`calling-gpt.md`、`execution-and-gates.md`、`handoff-protocol.md`、`iteration-bootstrap.md`、`governance.md`、`failure-and-artifacts.md`。
- `scripts/`：`scripts/check_env.py`、`scripts/session.py`、`scripts/run_gpt.py`、`scripts/authority.py`、`scripts/plan_gate.py`、`scripts/review_budget.py`、`scripts/facts_pack.py`、`scripts/test_evidence.py`、`scripts/check_docs.py`、`scripts/release.py`、`scripts/gate_common.py`、`scripts/models.py`、`scripts/model_registry.py`、`scripts/model_config.py`、`scripts/model_http.py`、`scripts/model_route.py`、`scripts/quota.py`、`scripts/validate_result.py`、`scripts/schema_for_codex.py`、`scripts/reap_turns.py`、`scripts/build_envelope.py`、`scripts/iteration.py`、`scripts/selftest_models.py`（完整说明见 `SKILL.md` 脚本表）。
- `assets/`：result/call schema、facts/envelope 模板、providers 注册表、模型配置示例、mandate/decision 示例。

## 快速开始

1. **开工**：`session.py pin` 锁契约与工作区身份；`check_env.py --short-call` 确认出口与额度；`models.py team scan` 把可用 Agent 列给用户，确认后才 `team add`。
2. **方案**：`models.py route --role plan` 选 planner → `models.py plan --requirements <需求>` 产出方案 → 独立 `plan-review` → `plan_gate.py check` 通过才分发。
3. **分发**：lead 建任务板、定 owner；`facts_pack.py freeze`；范围/预算变了就 `bump`；迭代中的写操作只落在候选工作区。
4. **生产与评审**：`run_gpt.py --role produce --work-package ...`；`build_envelope.py` + `run_gpt.py --role review`；`validate_result.py` 通过后逐条回源，采纳/拒绝写台账。
5. **交付**：`review_budget.py deliver` + `check_docs.py --run-tests`；发版用 `release.py`，本地可加 `--no-push`。
6. **迭代**：`iteration.py init/baseline` → 候选发版 → `evidence/compare` → 用户 `approve` → `promote`；失败 `rollback`。

## 常用命令

| 场景 | 命令 |
|---|---|
| 环境与身份 | `check_env.py --short-call`、`session.py pin\|check\|status` |
| 模型接入 | `models.py init/providers/add/key/account/list/discover/test/route/chains/call/plan/team` |
| 额度 | `quota.py`（5h/7d 剩余、档位 S/A/B/C/R/U） |
| 方案与评审 | `plan_gate.py check`、`build_envelope.py`、`run_gpt.py --role plan-review\|review`、`validate_result.py` |
| 预算与证据 | `review_budget.py`、`test_evidence.py`、`facts_pack.py` |
| 迭代 | `iteration.py init/baseline/evidence/compare/approve/promote/rollback` |
| 决策权 | `authority.py init/intake/classify/request/grant/guard/mode/report` |
| 发版 | `check_docs.py --run-tests`、`release.py --version X.Y.Z --note "..."` |

## 门禁（每个门都是退出码）

| 门 | 命令 | 拒绝条件 |
|---|---|---|
| 方案评审 | `plan_gate.py check` | 无评审（3）、方案改过（4）、high/medium 未闭环（5）、同族/家族不明（6） |
| 评审预算 | `review_budget.py check/raise` | 第 3 轮无 `--why-unclosed`（3）；超预算无 `--budget-change`/`raise`（4） |
| 测试证据 | `validate_result.py` | 缺 `test_refs`、日志哈希不符、observed 抄 summary（1） |
| 事实包 | `facts_pack.py check` | 未冻结/哈希过期（3）；重复头（1） |
| 工作区身份 | `session.py check` | 路径/盘符/卷不符或旧 pin（2） |
| 生产与交付 | `review_budget.py deliver` | S/A 无有效 GPT 生产单元或未采纳（5） |
| 发版一致性 | `check_docs.py --run-tests`、`release.py` | 版本/计数/规则/路径/脚本/资产不一致；脏树/已有 tag（1） |
| 迭代与晋升 | `iteration.py status/compare/promote` | 身份/隔离/执行端违规（3）；证据/对比/验收/tag/smoke 失败（4/5） |

## 治理边界（v2.4.0）

- 执行层守卫覆盖技能自己的受控脚本路径，以及候选根目录的 clean-tree/提交/diff 检查；**宿主工具写文件、任意 shell、不调用 `authority.py` 的第三方插件不在拦截范围**，依赖 DSH 沙箱/审批。
- `--by user` 是**声明**，不是身份认证；设置 `DSH_GOVERNANCE_SECRET` 后可校验 HMAC 审批令牌。状态缺失/损坏按失败关闭（exit 3）。
- 语义改写同一事项、暂停时已在飞行中的调用、成本/token 预算不强制；这些在 `references/governance.md` 的需求矩阵中标为 Partial。
- 删除全部本地标记（含 pin）超出本地保证；决策链是哈希绑定，不是密码学签名。

## 维护约定

- **只改这个目录**，Spec 已冻结。
- Agent 读英文文件，人读这份 README；同一条规则不写两遍。
- 新增规则先判断是否真的改变决策；能从 `SKILL.md` 推断的不写。
- 改规则 → 升 `SKILL.md` 的 `metadata.version` → 在更新日志加一行 → push。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 2.4.0 | 2026-11-03 | 人类治理决策权：委托书/思路评估/L1-L2-L3/请求与拒绝记忆/授权与原子额度/执行层守卫/控制模式/决策链；331 项离线自测 |
| 2.3.0 | 2026-11-02 | 自举迭代：稳定版驱动独立 worktree 候选版，执行端哈希冻结与双身份 pin，commit 绑定测试证据，基线对比、用户验收、快照晋升与独立回退；新增 iteration.py、迭代文档与 28 项迭代专项测试；288 项离线自测 |
| 2.2.0 | 2026-10-29 | 可执行门禁：方案评审门、评审预算、测试证据、事实包版本、工作区身份、GPT 生产核算与发版检查；设计/实现各 4 轮 GPT 评审后通过；260 项离线自测 |
| 2.1.0 | 2026-10-27 | 方案设计与任务分发分离：新增 `plan` 角色与 `models.py plan`，按 Reasoning/Design/Agentic/Coding 评分选 planner（可在 `overrides.<model>.scores` 覆盖），先独立评审方案再由 lead 分发；`call --role plan` 拒绝；dsh planner 由会话自己写；155 项离线自测 |
| 2.0.0 | 2026-10-27 | 大版本：接入 dsh-our-free-model 的多渠道模型能力，并加入任务团队入队审批。多平台模型接入与统一管理：新增 `scripts/models.py`（init/providers/add/key/account/list/discover/test/use/route/chains/call/health/budget/doctor）、`model_registry.py`（内置 76 家提供商注册表 + id 语法）、`model_config.py`（项目级配置、0600 凭据库、状态与跨进程锁、脱敏）、`model_http.py`（chat/responses/messages 三线协议、失败分类）、`model_route.py`（清单、能力/额度/健康路由、家族独立性、降级链）与 `selftest_models.py`（136 项离线自测）；GPT 仍只走 `run_gpt.py`、DeepSeek 仍只走 dsh 会话，HTTP 直连这两族需显式 `allowProtectedFamily`；凭据按项目隔离、只读命令不执行取令牌命令；新增 `references/model-access.md`、`assets/providers.registry.json`（MIT，附 NOTICE）与 `assets/models.config.example.json`；任务开始先 `team scan` 经用户同意才入队（`team.json` 存在即开关）；SKILL 增加第 17~20 条硬规则 |
| 1.6.0 | 2026-10-24 | 去特化：评审单元由"页"改为通用 `units`（文件 / 片段 / diff / 接口 / 单元格 / 页……），`page_map`/`pages_seen`/`title_confirmed` 改为 `units`/`units_seen`/`units_confirmed`；目录工件改用树哈希；DoD、事实包、环境、清理里的文档/排版专用表述移除；schema 强制与零写盘不变 |
| 1.5.0 | 2026-10-24 | 修第二份反馈单 BUG-1~9：评审零写盘（`artifact_access`/`result_capture`）、契约 pin 与漂移（`session.py`）、评审强制 schema 并在 runner 内校验（exit 3，另加 `validate_result.py`）、信封必填 `prior_findings` 由台账生成、事实包拆 constraints/budgets、advisory draft 计入生产（`origin`）、每消息读额度 + runner 自动台账、每轮状态行与中断声明；新增 `schema_for_codex.py`（codex strict 模式派生）与 `assets/facts-pack.example.md` |
| 1.4.0 | 2026-10-24 | 修反馈单 BUG-1~6：额度改实时源（app-server `account/rateLimits/read`），过期样本报 `U` 不再伪造成 100%；新增 `scripts/run_gpt.py` 统一调用入口（默认持久化、运行时解析 codex、前后额度、调用记录）与 `scripts/reap_turns.py`（收尾 `inProgress`）；新增 `references/calling-gpt.md` 与 `assets/call.schema.json`；补 `.ps1` BOM 与 `bin/<hash>` 约束 |
| 1.3.1 | 2026-10-24 | 思考强度不再随档位升降：固定按配置值（`medium`）跑，只有 R 档单次门可降一档并记录；额度杠杆改为"谁生产 / 几遍 / 几次调用 / 范围"，`quota.py` 打印配置强度 |
| 1.3.0 | 2026-10-24 | 额度自适应：新增 `scripts/quota.py`（读 Codex 5h/7d 剩余额度）与 `references/quota-routing.md`（S/A/B/C/R 分档、节奏与周额度否决、12% 视觉门保底、增值清单、单价校准）；GPT 从"固定评审员"改为随额度生产；check_env 与失败矩阵同步 |
| 1.2.0 | 2026-10-23 | 全面英文化、入口压到约 1k tokens；references 合并为 5 个；经验教训固化为规则；删除 CHANGELOG，版本记录并入 README；加入 GitHub 仓库与同步说明 |
| 1.1.0 | 2026-10-23 | 由单文件改为标准 skill 结构（SKILL.md + references + scripts + assets） |
| 1.0.0 | 2026-10-23 | 由 `多模型协作Spec.md` v2.4.1 转为单文件 skill |

早期经验来自 2026-10-20 至 10-24 的多次协作复盘（写作、评审、脚本化交付）；只取协作机制部分。
