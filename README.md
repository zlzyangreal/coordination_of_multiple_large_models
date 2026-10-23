# 多模型协作 SKILL（中文说明）

用于 **2 个及以上大模型协作** 的调度与对接：谁负责、怎么交接、评审结论怎么判断可信、出错怎么退。只管协作机制，不管写作、排版、设计本身。

- 仓库：<https://github.com/zlzyangreal/coordination_of_multiple_large_models>
- 版本：1.2.0（2026-10-23）
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
├── SKILL.md             入口：何时用、10 条硬规则、6 步流程（英文，给 Agent 读）
├── references/
│   ├── environment.md           环境检查、工具链、网络三查/Clash、权限
│   ├── model-registry.md        模型登记与验活、路由、预算与停止线
│   ├── execution-and-gates.md   任务契约、任务板、事实包、8 步流程、DoD、回归
│   ├── handoff-protocol.md      请求信封、结果 schema、版本/mtime、复核门
│   └── failure-and-artifacts.md 失败矩阵、快照与恢复、清理、上报
├── scripts/
│   ├── check_env.py             一键环境自检（codex 路径/版本、进程、代理、可写）
│   └── build_envelope.py        生成请求信封（SHA256/mtime/page_map），校验三路径分离
└── assets/
    ├── envelope.example.yaml    信封示例
    └── result.schema.json        评审结果的结构约束
```

## 怎么用

1. 开工前：`python scripts/check_env.py --short-call`（确认出口真的能通）。
2. 交评审前：`python scripts/build_envelope.py --artifact <文件> --pages 4,8 --out <yaml>`，填好每页标题再发。
3. 收结论后：逐条回源验证，采纳/拒绝写进 `review_ledger.md`，别把“评审说有问题”当“问题存在”。
4. 结束：回调用统计、偏差、未闭环项；临时文件清掉，快照每文件最多留 3 份。

## 维护约定

- **只改这个目录**，Spec 已冻结。
- Agent 读英文文件，人读这份 README；同一条规则不写两遍。
- 新增规则先判断是否真的改变决策；能从 SKILL.md 推断的不写。
- 改规则 → 升 `SKILL.md` 的 `metadata.version` → 在本文件更新日志加一行 → push。

## 更新日志

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.2.0 | 2026-10-23 | 全面英文化、入口压到约 1k tokens；references 合并为 5 个；经验教训固化为规则；删除 CHANGELOG，版本记录并入 README；加入 GitHub 仓库与同步说明 |
| 1.1.0 | 2026-10-23 | 由单文件改为标准 skill 结构（SKILL.md + references + scripts + assets） |
| 1.0.0 | 2026-10-23 | 由 `多模型协作Spec.md` v2.4.1 转为单文件 skill |

早期经验来自 2026-10-20、10-21（晚间 / 对接）、10-23（机制）四份协作复盘；只取协作部分，原始记录在 `F:\deepseek_workspace\parper_writing\`。
