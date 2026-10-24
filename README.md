# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制，不管写作、排版、设计本身。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：1.3.0（2026-10-24）
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
├── SKILL.md             入口：何时用、11 条硬规则、6 步流程（英文，给 Agent 读）
├── references/
│   ├── environment.md           环境检查、工具链、网络三查/Clash、权限
│   ├── model-registry.md        模型登记与验活、路由、额度分档摘要
│   ├── quota-routing.md         5h/7d 额度读取、分档、节奏、保底、成本校准
│   ├── execution-and-gates.md   任务契约、任务板、事实包、8 步流程、DoD、回归
│   ├── handoff-protocol.md      请求信封、结果 schema、版本/mtime、复核门
│   └── failure-and-artifacts.md 失败矩阵、快照与恢复、清理、上报
├── scripts/
│   ├── check_env.py             一键环境自检（codex 路径/版本、进程、代理、可写、额度）
│   ├── quota.py                 读 5h/7d 剩余额度 → 档位 S/A/B/C/R → 分工与思考强度
│   └── build_envelope.py        生成请求信封（SHA256/mtime/page_map），校验三路径分离
└── assets/
    ├── envelope.example.yaml    信封示例
    └── result.schema.json        评审结果的结构约束
```

## 额度自适应（v1.3.0）

GPT 的额度是**按 5 小时窗口发、到期作废**的。旧版把 GPT 固定当评审员，窗口内经常大量额度空转——这才是"利用率低"的根因，不是评审不够多。

开工先读额度，分工跟着档位走：

```bash
python scripts/quota.py            # 读档位、分工、思考强度
python scripts/quota.py --json     # 给脚本/Agent 用
python scripts/quota.py --remaining 62 --minutes 210   # 读不到时手动给
python scripts/quota.py --note "交稿前"                  # 往 _work/quota_ledger.md 记一行
```

| 档 | 5h 剩余 | 谁生产 | GPT 做什么 | 思考强度 |
|---|---|---|---|---|
| **S** 富余 | ≥65% | lead 整合、跑脚本、交付 | 生产 + 全部评审门 + 额外二遍 | high / xhigh |
| **A** 正常 | 50–65% | lead 做确定性部分 | 质量敏感部分生产 + 全部评审门 | medium / high |
| **B** 收敛 | 30–50% | lead 全包生产 | 只做门，批量 | low / medium |
| **C** 仅多模态 | 12–30% | lead 全包文字/代码/格式 | 只看图、看版式 | low |
| **R** 保底 | <12% | lead 全包 | 最多一次交付视觉门 | low |

基准三档（≥50% 由 GPT 主生产、30–50% 减负、<30% 只做多模态）保留，另加三处修正：

1. **看"每小时可用点数"，不只看百分比。** 同样是 40%，离重置还有 30 分钟就该烧掉，还有 4 小时就该省着用。所以加了两个覆盖：`≤1h 且 ≥15%` → 直接按 S 花掉（过期作废）；`≥2h` 时按 `剩余/小时` 的节奏，≥18 上调一档、<8 下调一档（最多降到 C，不会跳过视觉门）。脚本每次都会把 pace 打出来。
2. **7 天周额度有否决权。** 5h 满格但周额度只剩 20% 时按 B 跑，<12% 时按 C 跑——只盯 5h 会在一周后突然断粮。
3. **保底线放在 12%，不是 30%。** "30% 以下只做多模态"在多数场景成立，但交付前那段刚好落在 30% 时会直接没有视觉门（只有 GPT 看得见版式）。B 档（生产归 lead、评审归 GPT、批量、低强度）比一刀砍更平滑；真正的保底是 8–12%，只留给交付前那一次视觉门。文审可以等重置，视觉门不能。

另外三条配套规则，解决"额度有了但没活干"：

- **S/A 档 GPT 是生产者，不是评审员。** 固定分工本身就是浪费；档位高时把"质量差最大"的活推给它（成稿、论证结构、需求解读、方案批评），机械活（改名、重排、批量替换、脚本）永远归 lead——那不是利用率，是浪费外加更多错。
- **空转也要有清单。** 队列里的增值项按优先级排：门 → 生产 → 对抗性二遍（换范围，不是重复第一遍）→ 跨文件一致性/数字复算 → 逐图视觉审计/备选方案 → 机械活（永不）。S 档或临重置时优先烧前三项；队列空了就写"无有价值任务"，不许为了烧而烧。
- **记单价。** 每次调用前后各 `--note` 一次，三次就有"一次评审≈6 点"这类单价，之后按百分比排期而不是凭感觉。目标：有 backlog 时每个窗口用到 **70–90%**；>92% 太冒险（门做不了），<50% 且有 backlog 算浪费，要写进复盘。

代价：`codex exec` 每次调用有固定开销（实测一次上下文很小的 `ok` 调用也要 1–3% 的窗口额度），所以 B/C 档必须批量（多页、多文件合并成一次调用），S 档才按件拆分换质量。

## 怎么用

1. 开工前：`python scripts/check_env.py --short-call`（确认出口真的能通，顺带打一行额度）。
2. 分工前：`python scripts/quota.py`，照它给的档位决定"谁生产、GPT 做什么、思考强度"。
3. 交评审前：`python scripts/build_envelope.py --artifact <文件> --pages 4,8 --out <yaml>`，填好每页标题再发。
4. 收结论后：逐条回源验证，采纳/拒绝写进 `review_ledger.md`，别把“评审说有问题”当“问题存在”。
5. 结束：回报额度用量（5h/周）、调用统计、偏差、未闭环项；临时文件清掉，快照每文件最多留 3 份。

## 维护约定

- **只改这个目录**，Spec 已冻结。
- Agent 读英文文件，人读这份 README；同一条规则不写两遍。
- 新增规则先判断是否真的改变决策；能从 SKILL.md 推断的不写。
- 改规则 → 升 `SKILL.md` 的 `metadata.version` → 在本文件更新日志加一行 → push。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.3.0 | 2026-10-24 | 额度自适应：新增 `scripts/quota.py`（读 Codex 5h/7d 剩余额度）与 `references/quota-routing.md`（S/A/B/C/R 分档、节奏与周额度否决、12% 视觉门保底、增值清单、单价校准）；GPT 从"固定评审员"改为随额度生产；check_env 与失败矩阵同步 |
| 1.2.0 | 2026-10-23 | 全面英文化、入口压到约 1k tokens；references 合并为 5 个；经验教训固化为规则；删除 CHANGELOG，版本记录并入 README；加入 GitHub 仓库与同步说明 |
| 1.1.0 | 2026-10-23 | 由单文件改为标准 skill 结构（SKILL.md + references + scripts + assets） |
| 1.0.0 | 2026-10-23 | 由 `多模型协作Spec.md` v2.4.1 转为单文件 skill |

早期经验来自 2026-10-20、10-21（晚间 / 对接）、10-23（机制）四份协作复盘；只取协作部分，原始记录在 `F:\deepseek_workspace\parper_writing\`。
