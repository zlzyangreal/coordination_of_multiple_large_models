# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制——代码、文档、数据、方案都适用；代码风格、写作、排版这些领域规则属于项目自己的事实包与 DoD，不写进这里。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：2.0.0（2026-10-27）
- 本版为**大版本**：接入 [dsh-our-free-model](https://github.com/Ebony-Vinyl/dsh-our-free-model) 的多渠道模型能力（76 家注册表、三线协议、账号/API Key/自定义地址/本地模型），并新增"任务开始扫清单、用户同意才入队"的团队审批流程；原有 dsh + codex 两条保护通道不变。
- 上一代：`多模型协作Spec.md` v2.4.1，已冻结，仅作留档

## 安装

本机已装：把 skill 目录以 junction 挂进 DSH 的技能目录。

```powershell
New-Item -ItemType Junction `
  -Path  "D:\deepseek\resources\runtime\office-skills\coordination_of_multiple_large_models" `
  -Target "D:\deepseek_workspace\coordination_of_multiple_large_models"
```

- 挂完需要**重启 DSH** 才会重新扫描技能目录。
- 若重启后仍不识别，说明技能清单是打包在 `app.asar` 里的，目录挂载不会生效；此时按普通文件夹使用（需要时直接读 SKILL.md），或挂到 `~/.codex/skills` 给 Codex 侧用。
- 其他 Agent：把整个目录放进它的 skills 目录即可，`SKILL.md` 是入口。

## 同步

```powershell
cd D:\deepseek_workspace\coordination_of_multiple_large_models
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
├── SKILL.md             入口：何时用、19 条硬规则、6 步流程（英文，给 Agent 读）
├── references/
│   ├── environment.md           环境检查、工具链、网络三查/Clash、权限
│   ├── model-access.md          模型接入：三条车道、凭据、发现/验活、路由、故障与回退、边界
│   ├── model-registry.md        模型登记与验活、路由、额度分档摘要
│   ├── quota-routing.md         5h/7d 额度读取、分档、节奏、保底、成本校准
│   ├── calling-gpt.md           调用入口、持久化、评审调用、可观测性、.ps1 编码、中断收尾
│   ├── execution-and-gates.md   任务契约、任务板、事实包（约束/预算）、8 步流程、DoD、回归
│   ├── handoff-protocol.md      零写盘评审、信封、结果 schema、来源标注、复核门
│   └── failure-and-artifacts.md 失败矩阵、快照与恢复、清理、上报
├── scripts/
│   ├── check_env.py             一键环境自检（codex 路径/版本、进程、代理、可写、实时额度）
│   ├── session.py               契约锚点：pin / check（漂移）/ status（状态行）/ note
│   ├── run_gpt.py               唯一 GPT 调用入口：持久化、评审 schema 校验、前后额度、自动台账
│   ├── models.py                多平台模型接入 CLI：init/providers/add/key/account/list/discover/test/
│   │                            use/route/chains/call/health/budget/doctor
│   ├── model_registry.py        内置 76 家提供商注册表 + provider/model 语法、端点、请求头
│   ├── model_config.py          项目级配置/凭据存储（0600）/运行状态/掩码与脱敏/跨进程锁
│   ├── model_http.py            三种线协议（chat/responses/messages）、列表探测、失败分类
│   ├── model_route.py           模型清单、能力/额度/健康路由、家族独立性、降级链
│   ├── quota.py                 实时额度（app-server）→ 档位 S/A/B/C/R/U → 分工与调用方式
│   ├── validate_result.py       评审结果 JSON Schema + 回显 + 单元覆盖校验（拦截不合规结果）
│   ├── schema_for_codex.py      把契约派生成 codex strict 模式的 schema（--output-schema 必须）
│   ├── reap_turns.py            收尾卡在 inProgress 的回合（先备份、只改该状态）
│   ├── build_envelope.py        生成请求信封（hash/mtime/units/prior_findings/schema 路径）
│   └── selftest_models.py       模型层离线自测（本地 mock 提供商，不花额度、不联网）
└── assets/
    ├── envelope.example.yaml    信封示例（零写盘 + prior_findings）
    ├── result.schema.json        评审结果的结构约束
    ├── call.schema.json          调用记录的结构约束
    ├── facts-pack.example.md     事实包模板（constraints 与 budgets 分栏）
    ├── providers.registry.json   内置提供商注册表（76 家，来自 dsh-our-free-model，MIT）
    ├── providers.registry.NOTICE.md  数据来源与许可说明
    └── models.config.example.json    模型配置示例（API Key / 本地 / 自定义网关 / 账号令牌）
```

## 模型接入与统一管理（v2.0.0 起）

参考 `dsh-our-free-model` 的多渠道做法，把"免费模型、自有平台账号登录、API Key、自定义 API
地址、本地部署"统一成**同一个可选清单**。原来的两条调用通道原样保留，且是默认值：

| 车道 | 写法 | 谁来调 | 说明 |
|---|---|---|---|
| GPT / codex | `codex:auto`、`codex:<slug>` | 只走 `scripts/run_gpt.py` | **受保护**：持久化、schema、额度台账都不变 |
| DeepSeek / dsh | `dsh:auto`、`dsh:<slug>` | 宿主会话（lead 本人） | **受保护**：脚本不会另起一个 DeepSeek 调用者 |
| 任意 HTTP 提供商 | `<provider>/<model>` | `scripts/models.py call` | 可选：免密/免费、API Key、自定义地址、本地服务、账号令牌 |
| 降级链 | `chain:<name>` | `models.py call --chain` | 命名有序链，`fallback`/`round-robin`/`smart`/`pace` |

`models.py call --model dsh:...` 会直接拒绝（exit 2）并说明原因；`models.py test codex:auto`
委托给 `run_gpt.py --probe`。也就是说 **GPT 仍然只有 run_gpt.py 一条路，DeepSeek 仍然只走 dsh**。
若确实要用 HTTP 直连 GPT/DeepSeek 家族的模型，必须在提供商配置里写
`"allowProtectedFamily": true`——这是显式的第二条路，不会被静默替换。

```bash
python scripts/models.py init                 # 建 <项目>/_work/models/config.json
python scripts/models.py providers --search deep
python scripts/models.py add deepseek         # 内置 76 家之一
python scripts/models.py key set deepseek     # 从 stdin 读，只回显 …末四位
python scripts/models.py discover deepseek    # 拉实时模型清单（不花钱）
python scripts/models.py add ollama           # 本机模型，无需凭据
python scripts/models.py account login qoder_local   # 账号登录交给厂商 CLI，skill 不接触密码
python scripts/models.py add-custom my_gw --base-url https://llm.internal/v1 \
    --models internal-chat-1 --format openai-chat --auth apiKey
python scripts/models.py list                 # 清单 + 可用性（不可用会写原因）
python scripts/models.py test deepseek        # 验活（列表探测；--generate 才花一次生成）
python scripts/models.py use review codex:auto        # 按角色切换（项目级）
python scripts/models.py route --role review --producer codex:auto   # 选主 + 回退链
python scripts/models.py chains set review-safe \
    --models codex:auto,deepseek/deepseek-v4-pro,ollama/qwen3:8b
python scripts/models.py call --chain review-safe --prompt-file brief.md --output out.md
python scripts/models.py team scan                    # 任务开始先扫可用 Agent，不自动加入
python scripts/models.py team add <候选...>           # 用户同意后才入队
python scripts/models.py team decline <候选...>       # 不同意的记下来，本任务不再使用
python scripts/models.py account login <provider>   # 账号登录交给厂商 CLI
python scripts/models.py health | budget | doctor
```

几条关键规则（完整版见 `references/model-access.md`）：

- **配置按项目隔离**：配置/凭据/状态都在 `<项目>/_work/models/` 下，两个项目互不影响；只有
  显式 `--scope user` 才写用户级（`~/.coord-models`）。`COORD_MODELS_HOME` 可改用户级目录。
- **凭据优先级**：`credential`（env/file/command）> 内联 `apiKey`（会警告）> 0600 凭据库 >
  注册表声明的环境变量。`list/route/doctor` 这类只读命令**不会执行**配置里的取令牌命令；
  只有真正的 call/test/discover 才会执行，且用 `argv` 直接调用、不经 shell。
- **不泄露**：终端/状态/调用日志只出现 `hasKey` 和 `…末四位`；提供商回显的报错会先按密钥脱敏，
  失败命令的 stderr 一律不显示（登录命令失败时最可能打印令牌）。Windows 上用 icacls 收 ACL，
  `doctor` 校验实际权限，收不紧会直接报错。
- **可用性与回退**：按状态码判定失败类型。鉴权/欠费/权限封整家提供商；429、5xx、超时、模型不存在
  只封该模型（同一家的其它模型仍可回退）；URL 级 404 / 400 属于请求缺陷，**不回退**，直接 exit 4。
  重试次数有上限（`retryTransient`，0 表示只试一次），每次尝试都落 `model_calls.jsonl`。
- **路由**：`--pin` 是精确的，用不了就 exit 3，绝不换成另一个模型；`use` 是持久化的角色切换；
  `produce` 按额度档位决定谁生产（S/A 交给 GPT，B/C/R 由 lead 生产）；`review` 会排除生产者的
  家族，家族不明的聚合行默认不能当独立评审（`--allow-unknown-family` 可显式承担风险）。
- **额度与预算**：HTTP 提供商可在配置里写 `limits.callsPerDay/tokensPerDay`，`models.py budget`
  对照本地计数；没配额度就按"未知"处理，不会当成无限。GPT 仍看 `quota.py` 的 5h/周额度。
- **入队要问**：任务开始先 `team scan`，把可用候选（配置内、本机服务、已设环境变量 Key 的平台）列给用户；同意才 `team add`，不同意 `team decline`。没有 `team.json` 时保持旧行为（配置了就能用）；建队后未入队的 HTTP 渠道默认不参与，dsh/codex 两条保护通道不受影响。
- **账号登录**：`account login/status/logout/list` 把登录委托给厂商 CLI（`loginArgv`），skill 只记录登录态、登出时清掉已存令牌；调用仍用 `credential` 取令牌，不保存密码。
- **边界**：浏览器 OAuth 登录流、以及需要动态会话 id + 专属请求头的匿名免费车道（参考项目的
  opencode/Kilo 车道）不在本 skill 内复刻——OAuth 类在清单里标成"不可驱动"，账号登录请用
  厂商 CLI，拿到 token 后用 `credential: {source: command|file}` 导入。本 skill 是客户端，
  不做转发网关。

### 免费成员清单（v2.0.0）

下表是**可接入的免费候选**，不是"已入队"：是否加入由你确认（`team scan` → 询问 →
`team add`）。"免费层"通常仍要注册账号/Key，只有标注"无需"的才是真正免密。

| 成员 | 注册表分类 | 凭据 | 接入要点 | 备注 |
|---|---|---|---|---|
| Pollinations | 公网免费（非注册表） | **无需** | `add-custom` + `--chat-url https://text.pollinations.ai/openai`，模型以 `/models` 返回为准（当前 `openai-fast`） | 公网服务，稳定性和隐私策略由对方决定，勿发敏感内容 |
| Ollama | local | **无需** | 本机 `http://127.0.0.1:11434/v1` | 启动后 `team scan` 自动发现已拉取模型 |
| LM Studio | local | **无需** | 本机 `http://127.0.0.1:1234/v1` | 需在 LM Studio 里先加载模型 |
| LLaMA.cpp | local | **无需** | 本机 OpenAI 兼容端口（默认探测 8080） | `llama-server` 启动后可用 |
| BazaarLink | free-tier | 需 Key | 注册后 `models.py add bazaarlink` + `key set` | 免费额度以平台为准 |
| BytePlus Ark | free-tier | 需 Key | `add byteplus` + `key set` | 字节系，区域/额度以平台为准 |
| Cloudflare | free-tier | 需 Key | `add cloudflare` + `key set` | Workers AI / AI Gateway |
| Gemini | free-tier | 需 Key | `add gemini` + `key set` | OAuth 登录需另导入令牌 |
| Kilo.ai | free-tier | 需 Key | `add kilo` + `key set` | 免费池，上游会声明 prompt 可能被记录 |
| Kimchi | free-tier | 需 Key | `add kimchi` + `key set` | 免费额度以平台为准 |
| NVIDIA NIM | free-tier | 需 Key | `add nvidia_nim` + `key set` | 开发者计划免费额度 |
| Ollama Cloud | free-tier | 需 Key | `add ollama_cloud` + `key set` | 云端服务，非本机 |
| OpenRouter | free-tier | 需 Key | `add open_router` + `key set` | 选择带 `:free` 的模型 |
| OpenCode Zen | free-tier | 需 Key，但**默认接不通** | — | 需要动态会话 id + 专属请求头，按普通 API Key 接入会失败；请用参考项目的插件 |

> 另有 60+ 家自带 Key 的平台（DeepSeek 官方 API、Kimi、Qwen、GLM、MiniMax、Mistral、
> Groq、Cerebras、SambaNova、SiliconFlow、Together、Fireworks、Nebius、xAI 等）在
> `assets/providers.registry.json`，用 `python scripts/models.py providers --search <名字>`
> 查询。任何一家都需要先配置 Key、再 `team scan` 让你确认后才会参与调用。

### Claude / Anthropic（付费，非免费）

Claude 走 Anthropic 官方 API，注册表内置 `anthropic` 提供商（`anthropic-messages` 协议）；
它不是免费成员，按量计费。模型名以 Anthropic 当前可用 id 为准，注册表内置的示例：

| 模型名称（id） | 协议 | 说明 |
|---|---|---|
| `claude-sonnet-4-20250514` | `anthropic-messages` | Sonnet，日常生产/评审首选 |
| `claude-opus-4-20250514` | `anthropic-messages` | Opus，更贵、推理更强 |
| `claude-3-5-sonnet-20241022` | `anthropic-messages` | 旧版 Sonnet，兼容保留 |

```powershell
python scripts/models.py add anthropic --models claude-sonnet-4-20250514
python scripts/models.py key set anthropic          # 或设 ANTHROPIC_API_KEY
python scripts/models.py test anthropic --generate  # 可选：花一次很小的生成验活
python scripts/models.py team scan
python scripts/models.py team add anthropic/claude-sonnet-4-20250514 --allow-unverified
```

- **必须带 `--allow-unverified`**：Anthropic 没有模型列表接口，`team add` 无法用列表验证；
  入队后第一次成功调用会把健康状态标成 `ok`。
- Claude 属于 anthropic 家族，与 GPT/DeepSeek 不同族，可当独立评审：`python scripts/models.py use review anthropic/claude-sonnet-4-20250514`。
- 只有 OpenRouter Key 时：`add open_router --models anthropic/claude-sonnet-4`，再 `team add open_router/anthropic/claude-sonnet-4 --allow-unverified`。
- Claude Pro/Max 订阅（Claude Code 登录）没有内置 OAuth；需要手工把令牌通过 `credential` +
  自定义 `headers` 导入并自行维护刷新。Bedrock/Vertex 上的 Claude 默认不在注册表内，
  Vertex 的 ADC 认证不可驱动。

离线自测（不联网、不花钱）：`python scripts/selftest_models.py`，覆盖三线协议、429 回退、
400 不回退、凭据脱敏、模型级冷却、精确 pin、家族独立性、跨进程状态锁、受保护家族直连拦截、
凭据命令延迟执行、codex 委托的 cwd 与空响应等 136 项。

补充两条容易踩的边界：HTTP 提供商配置好即可用于普通调用，但**未验证（没跑过 `test` 或成功
调用）时不会进入 `review` 角色**——评审门不做"尽力而为"；`add`/`use`/`remove` 会写
`_work/models/model_changes.log`，换模型有据可查。

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

## 契约与门（v1.5.0 起）

第二份反馈单（对 v1.4.0，9 条）指出：脚本能调用了，但"谁负责版本、结果落哪、证据怎么绑、谁不能审谁"四个断点没闭合。这版把每个断点落到一个执行点：

| 断点 | 规则 |
|---|---|
| 只读评审却要求写 `result_path`（必然失败） | 评审**零写盘**：结果作为最终消息由 `run_gpt.py --output` 捕获，lead 校验后复制到 `result_path`。信封拆成 `artifact_access: read-only` + `result_capture: runner`；`read-only + direct` 在构建期直接报错 |
| 契约会话中途被替换，无人察觉 | 开工 `session.py pin`，每轮只比对不重读；变了记一行偏差、**继续按旧版执行**；交付写明"pinned vX / 仓库 vY" |
| `--schema` 是死的，11/11 条 finding 缺字段全放行 | 评审调用强制 `--schema assets/result.schema.json`；runner 收完即校验（回显 mtime、单元覆盖、必填字段），不合规 exit 3。另有 `validate_result.py` 兜住非 runner 来源。删掉"缺 `severity_basis` 按 medium"的旧兜底。**注意**：codex 的 `--output-schema` 走 strict 模式（每个对象要 `additionalProperties: false`、所有字段 must-required），手写契约直接喂会 400，runner 会自动派生 `<request_id>.codex-schema.json` 再交给 codex |
| 被拒条目跨轮重复提 | 信封必填 `prior_findings`，由 `build_envelope.py --ledger` 从台账自动生成；缺 `--ledger` 构建失败 |
| 事实包把"规模上限"当硬约束审（页数、行数、PR 大小都是预算） | 事实包拆 `constraints`（违反即返工）与 `budgets`（超限只报 `notice`，不得作否决理由）；模板 `assets/facts-pack.example.md` |
| 同一模型的草稿由它自己放行 | advisory draft 计入生产：对应 finding 标 `origin: advisory-derived`，不计入独立评审；只有单模型可用时，交付必须写"本轮评审非独立（同源）" |
| 额度读取与台账没有执行点 | "一次 runner 调用 = 一批"；runner 每次自动写一行 `_work/quota_ledger.md`；每条用户消息读一次 live 额度 |
| SKILL 可在会话内静默失效 | 每轮首行状态行：`SKILL v2.0.0 (pinned 19:36) \| quota 5h 78% / week 96% \| 阶段：…`；中断当场声明并补门；复盘固定问"本轮 skill 是否中断" |
| 证据没绑定到实际被读的东西 | 评审实际读的派生件（diff / dump / 导出）在调用前立即生成，`build_envelope.py --review-input` 把 sha256(8)+mtime 记进 `_work/rounds.md`；未记录前交付不得声称"版本已绑定"（信封结构按决议暂不动） |

一句话：v1.4.0 解决"怎么调用"，v1.5.0 解决"调用了算不算数"，v1.6.0 解决"换到代码/数据/方案也成立"。

## 怎么用

1. 开工：`python scripts/session.py pin`（锁契约版本）+ `python scripts/check_env.py --short-call`（确认出口，顺带打一行实时额度）。
2. 每轮：首行 `python scripts/session.py status --stage <阶段>`；变了就跑 `session.py check`，记偏差但不切版本。
3. 分工前：`python scripts/quota.py`，照它给的档位决定"谁生产、GPT 做什么、怎么拆调用"。
4. 评审前：`python scripts/build_envelope.py --artifact <文件或目录> --out <yaml> --ledger <台账> --unit "api::src/api.py:40-90::handle_request"`，第一轮加 `--init-ledger`。
5. 调用：`python scripts/run_gpt.py --prompt-file <brief> --request-id <编号> --sandbox read-only --schema assets/result.schema.json --envelope <yaml> --output <payload> --result-path <结果>`，不合 schema 会 exit 3。
6. 收结论：`python scripts/validate_result.py --result <结果> --envelope <yaml>` 通过后再逐条回源验证，采纳/拒绝写进台账（被拒条目下轮自动进 `prior_findings`）。
7. 结束：回报额度用量、调用统计、偏差、未闭环项，并回答"本轮 skill 是否中断"；临时文件清掉，快照每文件最多留 3 份。

## 维护约定

- **只改这个目录**，Spec 已冻结。
- Agent 读英文文件，人读这份 README；同一条规则不写两遍。
- 新增规则先判断是否真的改变决策；能从 SKILL.md 推断的不写。
- 改规则 → 升 `SKILL.md` 的 `metadata.version` → 在本文件更新日志加一行 → push。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
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
