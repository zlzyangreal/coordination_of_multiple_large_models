# 上架 Agensi

[Agensi](https://www.agensi.io) 是一个 SKILL.md 市场：买家通过网站或 MCP 连接器把 skill 加载进
自己的 Agent（Claude Code、Cursor、Codex、DSH……）。本仓库以**免费、开源（MIT）**方式上架
可移植协议层，并让每次发版只在**次版本 / 大版本**变化时同步过去。

## 上架内容

| 目录 | 作用 |
|---|---|
| `agensi/skill/SKILL.md` | 上架用的可移植 skill：12 条契约、角色表、工作流、信封、版本迭代、中文速览 |
| `agensi/skill/references/envelopes.md` | 请求/结果信封字段、采纳流程、常见失败模式 |
| `agensi/skill/references/checklists.md` | 预检、方案、分发、评审、交付、故障排查清单 |
| `agensi/listing.json` | 上架文案与元数据（名称、摘要、描述、标签、用例、权限、示例） |
| `agensi/publish-state.json` | 已发布的 slug 与版本（随仓库提交，保证多机一致） |
| `scripts/agensi_publish.py` | 登录、检查、打包、提交/更新 |

`agensi/skill/` 刻意**不引用**本仓库的 `scripts/*.py`：Agensi 把脚本写到临时文件单独执行，
跨文件 import 会失败。所以平台上的是协议层，完整工具链留在 GitHub 仓库里（listing 描述中已注明）。

## 一次性授权

```powershell
python scripts/agensi_publish.py login
```

1. 脚本做 OAuth 2.1 发现 + 动态客户端注册（PKCE S256，公共客户端，无 API Key）。
2. 打印授权链接并打开默认浏览器；用 **zlzyangreal@gmail.com** 的 Google 账号登录并同意。
3. 回调落到 `http://127.0.0.1:<随机端口>/callback`，脚本换到 token，写入
   `_work/agensi/token.json`（0600，git 忽略），有效期 1 小时，用 refresh token 续期 30 天。

之后每天运行只需要有效的 refresh token；过期就再 `login` 一次。

> Agensi 规定**一个账号只能有一个 Agent 连接**：新客户端拿到 token 后，旧客户端会失效。
> 如果同一账号还接着别的 Agent，请只用其中一个。

## 命令

```powershell
python scripts/agensi_publish.py check            # 离线：打印本次发版会不会同步
python scripts/agensi_publish.py check --json     # 给 CI / release.py 用
python scripts/agensi_publish.py package          # 生成手工上传用 zip，打印 sha256
python scripts/agensi_publish.py status           # creator_status + my_skills
python scripts/agensi_publish.py sync             # 只打印计划（不发送）
python scripts/agensi_publish.py sync --yes       # 真正提交 / 更新（先过发布门）
python scripts/agensi_publish.py sync --yes --preview   # 首次提交只出草稿预览，不确认
```

- `--preview` 只在**首次提交**上有意义，而且必须同时带 `--yes`（否则命令在联网前就返回了计划）；
  对 `update` 使用 `--preview` 会直接报错，因为平台没有非写入的更新预览。
- 发布过程中会写 `_work/agensi/pending-<version>.json` 并持有 `_work/agensi/.sync.lock`；
  如果上一次发布在服务端成功但本地没收到结果，下一次会拒绝并要求先 `status` 核对，
  核对后再加 `--resolve-pending` 重跑。并发运行会被锁直接拒绝（远端没有幂等键）。

退出码：`0` 正常/跳过/预览，`2` 包或状态错误，`3` 需要登录，`4` 发布门或平台错误，`5` 版本策略拒绝。

## 版本策略

| 已发布 | 当前 | 动作 |
|---|---|---|
| —— | 3.3.0 | `submit`：首次上架（先出草稿预览，再确认） |
| 3.2.0 | 3.3.0 | `update`：同步 |
| 3.3.0 | 3.3.1 | `skip`：**不同步**（patch 不发版） |
| 3.3.0 | 3.3.1 + `--force` | `update`：单次强制同步 |
| 3.3.0 | 3.1.0 | `refuse`：低于线上版本，拒绝 |
| 3.3.0 | 3.3.0 | `none`：无需动作 |

- 判定用 `package.json` 的 `version` 与 `agensi/publish-state.json` 的 `version` 比较；
  次版本/大版本任一变化才同步。
- 打包时会用当前 release 版本覆盖 `agensi/skill/SKILL.md` front matter 里的 `version`，
  避免源文件版本漂移。
- 更新时 changelog 取 `CHANGELOG.md` 中 `## <version>` 段落（最多 2000 字符）。

## 发布门（2026-11-18 加严）

`sync --yes` 在联网之前先过两道门，任何一道不过都直接退出（退出码 4），不会发出任何写请求：

1. **pin 一致**：`session.py check --strict` 必须返回 0。版本一变就要先
   `python scripts/session.py pin --force`，否则发布被拒。这一条正是 3.3.0 那次漏掉的：
   仓库已经是 3.3.0，pin 还停在 3.0.5。
2. **L1 决策**：`authority.guard(root, "publish", subject=<version>)` 必须允许。没有决策时的提示会直接给出
   下一步命令：

   ```powershell
   python scripts/agensi_publish.py package          # 产出 zip，并打印它的 sha256
   python scripts/authority.py decide --kind publish --subject 3.3.0 `
     --topic "Agensi 上架 3.3.0" --proposal "发布可移植协议层" `
     --decision accept --by user --reason "用户要求上架" `
     --approval-ref _work/approvals/publish-3.3.0.md `
     --artifact _work/agensi/multi-model-coordination-3.3.0.zip
   ```

   两个参数都不能少：`--approval-ref` 指向写有用户原话的批准文件（没有
   `DSH_GOVERNANCE_SECRET` 时是"未认证的用户凭据"，命令行会打印 NOTICE；有 secret 时必须带
   `--approval-token`），`--artifact` 把批准绑定到那个 zip 的字节上。zip 是确定性构建
   （固定时间戳），同样的内容两次打包 sha256 相同；发布时会重新打包并比对，
   包在批准之后被改过就会拒绝。

**每一次版本更新都需要新的批准**（`--subject` 换成新版本）。这是有意的：发布是 L1 操作，
流程的意义就在于每个外发动作都有人签字。预览（`sync --yes --preview`）同样受门约束。

门的判定是 fail-closed 的，几个细节容易踩：

- **治理必须已初始化**。仓库里没有 `_work/governance/` 时，`guard` 会返回
  `governance disabled` 并标 `allowed`，脚本不把它当作许可：没有决策记录就不发。
- **pin 的版本必须等于发布版本**。`session.py` 只比对仓库 `SKILL.md`，所以脚本自己读
  `_work/skill_pin.json` 再比一次；`sync --version 9.0.0` 在 3.3.0 的 pin 下会被拒。
- **包内符号链接直接拒绝**，避免把包外内容以包内路径上传；token 以 0600 原子写入。
- **锁与待定标记**：状态在拿到 `.sync.lock` 之后重新读取并重算；远端调用前写
  `pending-<version>.json`。若上一次结果不明，`--resolve-pending` 会先查 `my_skills` 对账，
  已落地就只补本地状态，没落地还要再加 `--reissue` 才会重发。
- **预览可续**：`--preview` 会把 `draft_id` 存到 `_work/agensi/draft-<version>.json`，
  下一次 `sync --yes` 确认的是同一个草稿，而不是重新建一个。

`check` 不联网、不过门，可以随时跑；`sync` 不带 `--yes` 只打印计划。

## 首次上架流程

1. 账号：用 Google 账号登录 <https://www.agensi.io>，在 Creator Studio 打开 **Creator Mode**。
   免费 skill 不需要绑定收款；付费/订阅才需要 Stripe 或 USDC。
2. 授权：`python scripts/agensi_publish.py login`。
3. 预览：`python scripts/agensi_publish.py sync --yes --preview`，检查草稿（文件、名称、摘要、价格）。
4. 提交：`python scripts/agensi_publish.py sync --yes`（会带上 `draft_id` 确认上架）。
5. 审核：8 项自动安全检查 + AI 审阅 + 人工，通常 24–48 小时；通过后 `status` 里能看到 `live`。
6. 失败：`status` 会给出 rejected 原因与未通过的安全检查项；修完重新 `sync --yes`。

## 手工兜底

如果授权不可用（例如只想人工上传）：

```powershell
python scripts/agensi_publish.py package
```

把生成的 `_work/agensi/<slug>-<version>.zip` 上传到 <https://www.agensi.io/dashboard/submit>，
价格选 **Free**。zip 与 `sync` 发送的文件完全一致（同一套校验：≤100 文件、≤5 MB、纯文本、UTF-8）。

## 安全与状态

- 令牌：`_work/agensi/token.json`，0600，`_work/` 在 `.gitignore` 中，永不提交。
- 发布状态：`agensi/publish-state.json`，记录 slug / 版本 / 包哈希 / 时间，随仓库走。
- 平台侧只有文本文件；脚本不会上传 `_work/`、`.git/`、构建产物。
- 发布是 L1 动作：`sync` 不带 `--yes` 不发送任何写请求；`check` 完全不联网。

## 排错

| 现象 | 处理 |
|---|---|
| `[FAIL] not signed in` / HTTP 401 | 重新 `login`；refresh token 过期或已被新客户端顶掉 |
| `token refresh failed` | 同上；Agensi 的 refresh token 用后即换，旧 token 复用会导致整条连接失效 |
| 名称已被占用 / 包重复 | 改 `agensi/listing.json` 的 `name`；名称全站唯一，重复包会被拦 |
| 安全扫描不通过 | `status` 看未通过项；常见是隐藏文件、二进制、可疑网络调用 |
| 审核被拒 | 按 `status` 的 rejection reason 修改；线上旧版本继续服务，不受影响 |
| `--yes` 后 24h 内没有 live | 正常审核窗口；用 `status` 查 `pending` |

## 与发版的关系

推荐顺序：`release.py` 完成版本、测试、tag、push 之后，再执行

```powershell
python scripts/agensi_publish.py check
python scripts/agensi_publish.py sync --yes
```

`check` 在 patch 版本会直接给 `skip`，所以可以安全地放进每次发版后的固定步骤：
只有次版本/大版本才会真的调用平台。
