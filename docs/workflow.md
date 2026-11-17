# 协作工作流（v3.1.0）

给「多模型协作」加的一层**可执行编排**：任务怎么拆、每个节点由谁（哪个模型）做、谁独立评审、
返工几次、什么时候允许交付，全部写在一个确定性的看板文件里；右侧栏把它画成实时流程图，并且
可以直接在图上换某个节点的模型。

它不运行模型，也不替代 DSH 的 agent：拆分与执行仍由会话里的 lead 完成，看板负责**记录、约束与
验证**——没有独立评审的产出不能算完成，模型被手动指定后自动分配不能覆盖，交付前必须过校验链。

## 组成

| 文件 | 职责 |
|---|---|
| `scripts/orchestration.py` | 看板状态机：T1–T11 转移、I1–I8 不变量、回执哈希链、`--json` 状态输出 |
| `lib/workflow.js` | 宿主桥：固定 argv 调 Python、回执链写入、SSE 广播、模型目录、失败降级 |
| `lib/http.js` | `/_multimodel/workflow*`、`/_multimodel/models` 路由（同源 + CSRF） |
| `lib/tools.js` | `coordination` 工具新增 `action:"workflow"` 与九个 `op` |
| `lib/client.js` | 右侧栏「工作流」视图：DAG、状态色、节点详情、模型下拉、交付按钮 |

复用而非重写：模型清单来自宿主 `ctx.llm.listProviders()/listModels()`；实时推送用 `webServer`
的 SSE；派发结果用 `subagent` 的 `listChildren()` 校验；界面挂在已有 `sidebarRightTabs` 上。

## 数据模型

看板文件：`<项目>/_work/orchestration/<task_id>.json`（项目内、不进 Git、不在插件包内）。`revision` 单调递增，所有变更带 `expected_revision`（CAS）。

节点字段：`id, title, kind (work|review|integrate), depends_on[], reviews, attempt,
assignee {lane, provider, model, source auto|manual}, status, result, review, updated_at`。

任务字段：`task_id, title, status, revision, nodes[], review_policy, delivery, history[]`。

## 转移 T1–T11

| id | 种类 | from → to | 条件 |
|---|---|---|---|
| T1 | work/integrate | pending → ready | 所有 `depends_on` 均 `done` |
| T2 | work/integrate | ready → running | 已解析 `lane` + `model` |
| T3 | work/integrate | running → review | 有 `summary`，且子会话+模型，或 `artifact`+`commit` |
| T4 | work/integrate | running → failed | 显式失败原因（`fail --reason`） |
| T5 | work/integrate | rework → running | `attempt`+1、清空结果、依赖仍 `done` |
| T6 | work/integrate | failed → running | 同 T5（显式重试） |
| T7 | review | pending → ready | 被评节点处于 `review`，且该 id 在评审节点自己的 `depends_on` 里 |
| T8 | review | ready → running | 记录 `review.attempt := producer.attempt` |
| T9 | review | running → done | `pass` → 被执行节点同时 `done` |
| T10 | review | running → pending | `fail` → 被执行节点 `rework`、结果清空、`review.attempt` 清空 |
| T11 | task | reviewing → delivered | `deliver` 且 I7 成立、回执链通过；来源状态必须是 `reviewing` |

其它任何转移返回 3（状态机拒绝），参数错误返回 2，策略违规返回 4。

## 不变量 I1–I8

- **I1** `depends_on` + `reviews` 构图无环。
- **I2** 评审节点恰好评审一个 work/integrate 节点，且该节点 id 在评审节点自己的 `depends_on` 中；
  被执行节点不得反向依赖评审（否则成环）。
- **I3** 评审家族必须与被执行家族不同：`lane` 为 `codex`/`dsh`，或 `http:<provider>`。
- **I4** `source:"manual"` 的指派不会被 `assign` 覆盖，只能由 `pin` 改，并记录谁、何时；
  节点一旦进入 `running`/`review`/`done`/`failed`，指派即锁定（避免"结果之后换模型"的假承诺）。
- **I5** `expected_revision` 过期 → 3，且不写盘（字节级不变）。
- **I6** 被执行节点只有在**当前 attempt** 的独立评审 `pass` 后才能 `done`。
- **I7** 交付要求所有节点 `done`、无 `review/rework/failed`、无 open 的 high/medium 发现；
  空看板、无评审节点的看板一律不可交付。
- **I8** `result` 必须带子会话 id 与模型，且模型等于被指派模型；仅在显式 `--no-subagent`
  时接受 `artifact`+`commit`，并记为 `verified:"artifact"`。

## CLI

每个变更命令都带 `--expected-revision`（CAS）与同一个任务自己的 `--receipts` 文件；`init` 的
`--expected-revision` 为 `0`。下面是一段可执行的完整序列（每次成功后 revision +1）：

```powershell
$r = "_work/receipts/t1.jsonl"
python scripts/orchestration.py init    --task t1 --title "季度报告" --project . --expected-revision 0 --receipts $r
python scripts/orchestration.py plan    --task t1 --nodes-file nodes.json --project . --expected-revision 1 --receipts $r
python scripts/orchestration.py pin     --task t1 --node n1 --lane codex --provider openai --model gpt-5.6-sol --by user --project . --expected-revision 2 --receipts $r
python scripts/orchestration.py start   --task t1 --node n1 --project . --expected-revision 3 --receipts $r
python scripts/orchestration.py result  --task t1 --node n1 --summary "..." --child-session <id> --child-model <model> --project . --expected-revision 4 --receipts $r
python scripts/orchestration.py start   --task t1 --node r1 --project . --expected-revision 5 --receipts $r
python scripts/orchestration.py review  --task t1 --node r1 --verdict pass --by reviewer --project . --expected-revision 6 --receipts $r
python scripts/orchestration.py deliver --task t1 --commit <sha> --project . --expected-revision 7 --receipts $r
python scripts/orchestration.py state   --task t1 --project . --receipts $r
```

跑挂一个节点用 `fail --node <id> --reason "<文本>"`（T4，`running → failed`），之后可用 `start`
从 `failed` 重试（T6）。每个命令输出一行 JSON：`{ok, code, message, task|error}`，退出码等于
`code`。通过宿主插件使用时，`lib/workflow.js` 会替你拼这些参数并做子会话校验。

## 回执链

每个任务一条独立回执流：`$DSH_HOME/coordination/orchestration/<project-key>/<task>.jsonl`，每次
变更追加 `{at, op, revision, prev_digest, board_digest}`。任何**变更前**都要求当前回执末端与看板
字节一致，否则拒绝（`chain: missing|stale|broken`）——被外部改过的看板无法靠一次普通操作"洗白"。
`deliver` 再次校验末端摘要与链接完整性；任务之间互不影响（A 的变更不会让 B 的链变旧）。

**残余风险**：本机管理员可以直接改看板再改回执。回执链保护的是插件/工具/HTTP 路径，不是文件系统；
`docs/security.md` 的边界描述同样适用。

## 派发验证

`result` 的准入在宿主桥里只有一条策略（工具与 HTTP 共用）：声明的子会话必须出现在
`ctx.get('subagent').listChildren(sessionId)` 的目录里，且目录报告的模型必须等于当前指派模型；
目录不可用、子会话不在目录里、或子会话没有报告模型，一律拒绝（`dispatch_unverified: ...`）。
只有当宿主**确实没有** `subagent` 服务时，才接受 `artifact`+`commit` 并记为
`verified:"artifact"`——调用方不能用一个 `no_subagent` 标志绕过校验。

DSH 没有让第三方插件代替用户发起模型回合的公开接口，所以看板**不自己执行**：它给出必须调用的
lane/provider/model 与提示词，并在结果回传时验证。

## 模型目录与切换

`GET /_multimodel/models` 从 `ctx.llm` 读取实时目录。右侧栏节点详情里的下拉框写回 `pin`；手动
指定后节点显示「手动」徽标，后续自动分配不会覆盖它。`ctx.llm` 不可用时下拉框显示原因而非伪造列表。

## 实时更新

`GET /_multimodel/workflow/events` 为 SSE：连接即推送一帧快照，随后在看板变化时推送。`fs.watch`
创建在独立 `try/catch` 内并带 `error` 监听，`rename`（Windows 原子替换）后 200ms 重新挂载；1 秒
tick 始终重读文件作为兜底。浏览器连续两次出错后降级为 5 秒轮询并显示「轮询」标记。

## 与需求文档的对应

| 需求 | 落地 |
|---|---|
| 自动协作（拆分/分配/汇总/审核） | lead 用 `plan/assign/start/result/review` 驱动看板；评审不通过自动回到 `rework` 并增加 attempt |
| 实时可视化（分工/关系/状态/回传/审核） | 右侧栏 DAG：依赖实线、评审点线、返工红色虚线；节点色表示状态；SSE 推送 |
| 模型可切换（手动优先） | 节点详情下拉 + `pin`；I4 保证手动不被覆盖，I8 保证结果确实来自该模型 |
| 优先复用开源 | 复用 DSH 原生 `llm`、`webServer` SSE、client slot、`subagent`；不内嵌 Flowise/LangGraph/AutoGen/MAF |

## 已知限制

- 只支持 DSH 0.2.1-rc.3 Web Profile（与插件整体一致）；桌面未实测。
- 一次只展示最近修改的一个看板；多任务并行需要按 `task` 切换。
- 图布局是固定分层（依赖深度），不是可拖拽的自由画布。
- 回执链不防本机管理员；不提供跨机器签名。
