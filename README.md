# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制——代码、文档、数据、方案都适用；代码风格、写作、排版属于项目自己的事实包与 DoD，不写进这里。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：3.0.2（2026-11-10）
- 当前离线自测：331 项（mock providers，不联网）
- 上一代：`多模型协作Spec.md` v2.4.1，已冻结，仅作留档

## 版本核心

### 3.0.0 DSH 原生多模型协作插件

同一个仓库现在也是可安装的 DSH Bundle（`package.json` → `dsh.bundle`、`cordis.patch.yml`、`lib/`）。新增：默认 **OFF** 的全局总开关；开关 ON 时宿主注入 **40 条规则契约**，会话确认前拒绝其他工具（规则先行，不依赖 agent 主动读 SKILL.md）；**版本锁定 + 用户验证后激活**（运行版本与包摘要都匹配上次验证值才可启用，新版本在独立验证 Profile 暂存）；设置页按维护者声明列出可选扩展，安装需确认且先装为禁用、再单独验证启用；本地 I0–I3 JSONL 日志（按日轮转、去重、脱敏、留存，不自动修复/上传/进 Git）。支持范围为 **DSH 0.2.1-rc.3 Web Profile / Windows 11 x64**；桌面版预期可用但本版未实测，见 `docs/desktop.md`。

### 2.4.0 人类治理的决策权

人类定目标、硬约束、授权和验收；Agent 自主研究、规划、设计、执行；多模型独立评审；编排层在执行层拦截越权、记录决策、说明无法保证的范围。新增 `authority.py`：委托书、思路评估、L1/L2/L3、请求与拒绝记忆、授权与原子额度、控制模式、决策链。

### 2.3.0 自举迭代

已发布的稳定版 V(N) 保持为执行端，V(N+1) 在独立 Git worktree 中开发；commit 绑定测试证据，基线对比、用户验收后才晋升，失败自动回退。新增 `iteration.py`。

### 2.2.0 可执行门禁

方案评审门、评审预算、测试证据、事实包版本、工作区身份、GPT 生产核算、发版一致性检查；每个门都是退出码，不是提醒。

### 2.1.0 规划与分发分离

新增 `plan` 角色和 `models.py plan`：planner 独立理解需求、调研、设计并产出可执行方案；planner 不负责分任务，方案先过独立评审，再由 lead 分发。

### 2.0.0 多平台模型接入

**接入价值**：借鉴 `dsh-our-free-model` 的多渠道做法，把“免费/免密、API Key、自定义 API 地址、本地服务、账号令牌”统一成一份可选清单。此前只有 codex 与 dsh 两条通道；接入后，免费模型、本地模型、自建网关和各家的付费 API 都能进入同一套路由、验活、额度与团队审批，而 GPT/DeepSeek 两条受保护通道保持不变。

**接入什么模型可用**：

- 免费/免密：Pollinations（无需 Key）、Ollama、LM Studio、LLaMA.cpp（本机服务）。
- 免费额度：Gemini、Cloudflare、OpenRouter 的 `:free` 模型、NVIDIA NIM、BytePlus、Kilo 等。
- 付费/自有 Key：DeepSeek、Kimi、Qwen、GLM、MiniMax、Mistral、Groq、Cerebras、xAI、Anthropic 等。
- 任意 OpenAI/Anthropic/Responses 兼容端点：`add-custom` 填 base URL 即可接入。
内置 76 家提供商，用 `models.py providers --search` 查询、`discover` 拉实时模型清单、`list` 看可用性。

**受保护家族**：GPT/codex 仍只通过 `run_gpt.py`，DeepSeek/dsh 仍只通过宿主会话；HTTP 直连这两个家族必须在提供商配置里显式 `allowProtectedFamily: true`，不会被静默替换或降级。

**团队**：任务开始先 `team scan`，把可用候选（配置内、本机服务、已设 Key 的平台）列给用户；同意才 `team add`，不同意 `team decline`；未入队的 HTTP 渠道默认不参与，两条受保护通道不受影响。`team.json` 存在即启用入队审批。

### 1.3.1 思考强度固定

思考强度不再随额度档位升降，固定按配置值运行；额度杠杆只改“谁生产、几遍、几次调用、范围”，避免最终稿因降强度而判断力变差。

### 1.3.0 额度自适应

新增 `quota.py` 读取 Codex 5h/7d 剩余额度，按 S/A/B/C/R/U 分档决定谁生产、GPT 做什么、如何拆调用；过期样本报 `U`，不伪造成 100%；保留 12% 视觉门保底，GPT 从“固定评审员”改为随额度生产。

## 安装与同步

```powershell
New-Item -ItemType Junction `
  -Path  "D:\deepseek\resources\runtime\office-skills\coordination_of_multiple_large_models" `
  -Target "D:\deepseek_workspace\coordination_of_multiple_large_models"
```

- 挂完重启 DSH；其他 Agent 把整个目录放进 skills 目录即可，`SKILL.md` 是入口。
- 同步：`git pull --rebase` → 提交 → `git push`；本机代理 `http://127.0.0.1:7897`，Clash 未开时先取消 `http.proxy`。
- 换机器：`git clone https://github.com/zlzyangreal/coordination_of_multiple_large_models.git`。

## 脚本

- `scripts/check_env.py`
- `scripts/session.py`
- `scripts/run_gpt.py`
- `scripts/authority.py`
- `scripts/plan_gate.py`
- `scripts/review_budget.py`
- `scripts/facts_pack.py`
- `scripts/test_evidence.py`
- `scripts/check_docs.py`
- `scripts/check_rules.py`
- `scripts/release.py`
- `scripts/gate_common.py`
- `scripts/models.py`
- `scripts/model_registry.py`
- `scripts/model_config.py`
- `scripts/model_http.py`
- `scripts/model_route.py`
- `scripts/quota.py`
- `scripts/validate_result.py`
- `scripts/schema_for_codex.py`
- `scripts/reap_turns.py`
- `scripts/build_envelope.py`
- `scripts/iteration.py`
- `scripts/selftest_models.py`

完整说明见 `SKILL.md` 脚本表。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 3.0.2 | 2026-11-10 | 修复客户端未声明 `slots` 服务依赖；入口改为右侧栏“多模型协作”，不再占用设置页或左侧栏；可选扩展安装增加超时/取消与失败原因提示；45 项 Node 测试、331 项离线自测 |
| 3.0.1 | 2026-11-10 | v3.0.1 桌面启动热修：客户端设置区改为字符串 label/包名 id 并包裹 slot 注册异常；host 直接/晚注册路由失败写 I0 plugin.routes_failed 并保持加载；42 项 Node 测试 + 331 项离线自测；桌面冷启动待用户重启验证 |
| 3.0.0 | 2026-11-10 | DSH 原生插件：标准 Bundle/生命周期、默认 OFF 总开关、40 条规则先行契约门禁、版本锁定与用户验证后激活、声明式可选扩展（安装禁用 + 单独启用）、本地 I0–I3 日志；保留 v2.4.0 的 38 条规则、331 项离线自测与全部门禁；新增 37 项 Node 测试；支持 DSH 0.2.1-rc.3 Web Profile（桌面未实测） |
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
