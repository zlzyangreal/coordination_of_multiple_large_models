# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制，不管写作、排版、设计本身。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：1.4.0（2026-10-24）
- 上一代：`多模型协作Spec.md` v2.4.1，已冻结，仅作留档

## 安装

本机已装：把 skill 目录以 junction 挂进 DSH 的技能目录。

```powershell
New-Item -ItemType Junction `
  -Path  "D:\deepseek\resources\runtime\office-skills\coordination_of_multiple_large_models" `
  -Target "F:\deepseek_workspace\coordination_of_multiple_large_models"
```

- 挂完需要**重启 DSH** 才会重新扫描技能目录。
- 若重启后仍不识别，说明技能清单是打包在 `app.asar` 里的，目录挂载不会生效；此时按普通文件夹使用（需要时直接读 SKILL.md），或挂到 `~/.codex/skills` 给 Codex 侧用。
- 其他 Agent：把整个目录放进它的 skills 目录即可，`SKILL.md` 是入口。

## 同步

```powershell
cd F:\deepseek_workspace\coordination_of_multiple_large_models
git pull --rebase
# 改完
git add -A
git commit -m "docs: ..."
git push
```

- 本机仓库已设 `http.proxy = http://127.0.0.1:7897`（Clash Verge 混合端口）。
- Clash 没开或端口变了，先 `git config --unset http.proxy` 或改成实际端口，否则 push 会卡住。
- 换机器：`git clone https://github.com/zlzyangreal/coordination_of_multiple_large_models.git`，再按上面的安装步骤挂载。

## 目录

```
coordination_of_multiple_large_models/
├── README.md            本文件（给人看）
├── SKILL.md             入口：何时用、12 条硬规则、6 步流程（英文，给 Agent 读）
├── references/
│   ├── environment.md           环境检查、工具链、网络三查/Clash、权限
│   ├── model-registry.md        模型登记与验活、路由、额度分档摘要
│   ├── quota-routing.md         5h/7d 额度读取、分档、节奏、保底、成本校准
│   ├── calling-gpt.md           调用入口、持久化、可观测性、.ps1 编码、中断收尾
│   ├── execution-and-gates.md   任务契约、任务板、事实包、8 步流程、DoD、回归
│   ├── handoff-protocol.md      请求信封、结果 schema、版本/mtime、复核门
│   └── failure-and-artifacts.md 失败矩阵、快照与恢复、清理、上报
├── scripts/
│   ├── check_env.py             一键环境自检（codex 路径/版本、进程、代理、可写、实时额度）
│   ├── run_gpt.py               唯一调用入口：默认持久化、留 thread_id/rollout、记前后额度
│   ├── quota.py                 实时额度（app-server）→ 档位 S/A/B/C/R/U → 分工与调用方式
│   ├── reap_turns.py            收尾卡在 inProgress 的回合（先备份、只改该状态）
│   └── build_envelope.py        生成请求信封（SHA256/mtime/page_map），校验三路径分离
└── assets/
    ├── envelope.example.yaml    信封示例
    ├── result.schema.json        评审结果的结构约束
    └── call.schema.json          调用记录的结构约束
```

## 额度自适应（v1.3.0 起）

GPT 的额度是**按 5 小时窗口发、到期作废**的。旧版把 GPT 固定当评审员，窗口内经常大量额度空转——这才是"利用率低"的根因，不是评审不够多。

开工先读额度，分工跟着档位走：

```bash
python scripts/quota.py                # 实时值（app-server），失败自动回退会话日志
python scripts/quota.py --source log   # 只看历史样本（不启动 app-server）
python scripts/quota.py --json         # 给脚本/Agent 用
python scripts/quota.py --remaining 62 --minutes 210   # 读不到时手动给
python scripts/quota.py --note "交稿前"                  # 往 _work/quota_ledger.md 记一行
```

实时值走 `codex app-server` 的 `account/rateLimits/read`，约 2.5 秒，能看到**所有客户端**的消耗；历史样本只在"仍属于当前窗口"时有效，窗口一旦过期就报 `U`（unknown）而不是 100%。

| 档 | 5h 剩余 | 谁生产 | GPT 做什么 | 调用 |
|---|---|---|---|---|
| **S** 富余 | ≥65% | lead 整合、跑脚本、交付 | 生产 + 全部评审门 + 额外二遍 | 按件拆 |
| **A** 正常 | 50–65% | lead 做确定性部分 | 质量敏感部分生产 + 全部评审门 | 按组一次 |
| **B** 收敛 | 30–50% | lead 全包生产 | 只做门，批量 | 页面/文件合并成一次 |
| **C** 仅多模态 | 12–30% | lead 全包文字/代码/格式 | 只看图、看版式 | 所有图合并一次 |
| **R** 保底 | <12% | lead 全包 | 最多一次交付视觉门 | 一次即停 |

**思考强度固定不动。** 按 `~/.codex/config.toml` 的配置跑（现在 `medium`），档位只改"谁生产、几遍、几次调用、看多少页"，不改 effort。降强度会让判断力在最终稿最关键的时候变差（收敛档恰恰最不能出错）；反过来用超高强度烧富余额度也不划算——两个不同范围的普通遍比一遍 `xhigh` 更有用，而且前后强度不同，评审结论不可比。唯一例外：R 档且门必须跑，这一次降一档并记进 ledger。

基准三档（≥50% 由 GPT 主生产、30–50% 减负、<30% 只做多模态）保留，另加三处修正：

1. **看"每小时可用点数"，不只看百分比。** 同样是 40%，离重置还有 30 分钟就该烧掉，还有 4 小时就该省着用。所以加了两个覆盖：`≤1h 且 ≥15%` → 直接按 S 花掉（过期作废）；`≥2h` 时按 `剩余/小时` 的节奏，≥18 上调一档、<8 下调一档（最多降到 C，不会跳过视觉门）。脚本每次都会把 pace 打出来。
2. **7 天周额度有否决权。** 5h 满格但周额度只剩 20% 时按 B 跑，<12% 时按 C 跑——只盯 5h 会在一周后突然断粮。
3. **保底线放在 12%，不是 30%。** "30% 以下只做多模态"在多数场景成立，但交付前那段刚好落在 30% 时会直接没有视觉门（只有 GPT 看得见版式）。B 档（生产归 lead、评审归 GPT、批量、只做门）比一刀砍更平滑；真正的保底是 8–12%，只留给交付前那一次视觉门。文审可以等重置，视觉门不能。

另外三条配套规则，解决"额度有了但没活干"：

- **S/A 档 GPT 是生产者，不是评审员。** 固定分工本身就是浪费；档位高时把"质量差最大"的活推给它（成稿、论证结构、需求解读、方案批评），机械活（改名、重排、批量替换、脚本）永远归 lead——那不是利用率，是浪费外加更多错。
- **空转也要有清单。** 队列里的增值项按优先级排：门 → 生产 → 对抗性二遍（换范围，不是重复第一遍）→ 跨文件一致性/数字复算 → 逐图视觉审计/备选方案 → 机械活（永不）。S 档或临重置时优先烧前三项；队列空了就写"无有价值任务"，不许为了烧而烧。
- **记单价。** 每次调用前后各 `--note` 一次，三次就有"一次评审≈6 点"这类单价，之后按百分比排期而不是凭感觉。目标：有 backlog 时每个窗口用到 **70–90%**；>92% 太冒险（门做不了），<50% 且有 backlog 算浪费，要写进复盘。

代价：`codex exec` 每次调用有固定开销（实测一次上下文很小的 `ok` 调用也要 1–3% 的窗口额度），所以 B/C 档必须批量（多页、多文件合并成一次调用），S 档才按件拆分换质量。

## 调用入口与可观测性（v1.4.0）

2026-10-24 的缺陷反馈单暴露了一条链路矛盾：**探针的写法被抄成了生产写法**。`codex exec --ephemeral` 不写 rollout、不留会话，于是额度脚本永远只读得到旧样本；旧样本窗口过期后又按 5 小时外推成 100%，上层据此判 S 档放量——而同一分钟实时值已经是已用 11%。下游 4 个调用脚本全部沿用 `--ephemeral`，其中 2 个写死了已失效的 `bin/<hash>/codex.exe`，1 个 `.ps1` 无 BOM 直接语法错。

修法三件事：

1. **额度读实时源。** `codex app-server` 的 `account/rateLimits/read`（约 2.5 秒）优先，会话日志只作兜底；**过期样本一律 `U`（unknown）**，脚本再也不会把旧样本外推成 100%。`U` 档按 R 档规划，先拿到读数再批量。
2. **所有生产/评审调用走 `scripts/run_gpt.py`。** 默认持久化（不传 `--ephemeral`），运行时解析 codex 路径，显式传 model + effort，超时自动收尾，并把每次调用写成 `_work/calls/<request_id>.json`：`thread_id`、`rollout_path`、模型/强度/沙箱、起止时间、token 用量、调用前后额度与差值。`--probe` 才是 ephemeral，只用于连通性。
3. **卡住的回合要收尾。** 被强杀/超时的回合会在 `thread_history` 里永远 `inProgress`，桌面端一直显示"正在处理"。runner 超时后会把该回合标成 `interrupted`；历史遗留用 `python scripts/reap_turns.py --older-than 30`（先 dry-run，`--apply` 前自动备份，只改 `inProgress` 行）。

两条硬约束：

- 生成的 `.ps1` 必须是 **UTF-8 with BOM**（或纯 ASCII），否则 PowerShell 5.1 把中文按 ANSI 解析，报的却是无关的 `{` 语法错；能用 `.py` 就别生成 `.ps1`。
- 不经过 `codex exec` 的调用（直连 API、别的编排层）本来就不产生 rollout，不在额度读取范围内——报告里要写明这条边界，别当成"监控坏了"。

## 怎么用

1. 开工前：`python scripts/check_env.py --short-call`（确认出口真的能通，顺带打一行实时额度）。
2. 分工前：`python scripts/quota.py`，照它给的档位决定"谁生产、GPT 做什么、怎么拆调用"。
3. 调用时：`python scripts/run_gpt.py --prompt-file <提示词> --cd <工作目录> --request-id <编号>`，产物落 `_work/calls/<编号>.json`。
4. 交评审前：`python scripts/build_envelope.py --artifact <文件> --pages 4,8 --out <yaml>`，填好每页标题再发。
5. 收结论后：逐条回源验证，采纳/拒绝写进 `review_ledger.md`，别把“评审说有问题”当“问题存在”。
6. 结束：回报额度用量（5h/周）、调用统计、偏差、未闭环项；临时文件清掉，快照每文件最多留 3 份。

## 维护约定

- **只改这个目录**，Spec 已冻结。
- Agent 读英文文件，人读这份 README；同一条规则不写两遍。
- 新增规则先判断是否真的改变决策；能从 SKILL.md 推断的不写。
- 改规则 → 升 `SKILL.md` 的 `metadata.version` → 在本文件更新日志加一行 → push。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.4.0 | 2026-10-24 | 修反馈单 BUG-1~6：额度改实时源（app-server `account/rateLimits/read`），过期样本报 `U` 不再伪造成 100%；新增 `scripts/run_gpt.py` 统一调用入口（默认持久化、运行时解析 codex、前后额度、调用记录）与 `scripts/reap_turns.py`（收尾 `inProgress`）；新增 `references/calling-gpt.md` 与 `assets/call.schema.json`；补 `.ps1` BOM 与 `bin/<hash>` 约束 |
| 1.3.1 | 2026-10-24 | 思考强度不再随档位升降：固定按配置值（`medium`）跑，只有 R 档单次门可降一档并记录；额度杠杆改为"谁生产 / 几遍 / 几次调用 / 范围"，`quota.py` 打印配置强度 |
| 1.3.0 | 2026-10-24 | 额度自适应：新增 `scripts/quota.py`（读 Codex 5h/7d 剩余额度）与 `references/quota-routing.md`（S/A/B/C/R 分档、节奏与周额度否决、12% 视觉门保底、增值清单、单价校准）；GPT 从"固定评审员"改为随额度生产；check_env 与失败矩阵同步 |
| 1.2.0 | 2026-10-23 | 全面英文化、入口压到约 1k tokens；references 合并为 5 个；经验教训固化为规则；删除 CHANGELOG，版本记录并入 README；加入 GitHub 仓库与同步说明 |
| 1.1.0 | 2026-10-23 | 由单文件改为标准 skill 结构（SKILL.md + references + scripts + assets） |
| 1.0.0 | 2026-10-23 | 由 `多模型协作Spec.md` v2.4.1 转为单文件 skill |

早期经验来自 2026-10-20、10-21（晚间 / 对接）、10-23（机制）四份协作复盘；只取协作部分，原始记录在 `F:\deepseek_workspace\parper_writing\`。
