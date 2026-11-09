# 架构

## 组成

| 文件 | 职责 |
|---|---|
| `lib/index.js` | Bundle 入口：解析 Profile、加载 state/契约/日志/引擎/扩展、注册提示段与工具、生命周期清理 |
| `lib/paths.js` | `profileContext`/env 解析、Profile 隔离目录、原子 JSON、包摘要、ISO 本地时间 |
| `lib/state.js` | 开关、版本+摘要验证、契约 epoch/会话确认、受控操作、损坏隔离与启动对账 |
| `lib/contract.js` | 40 条 manifest/rules.md 校验、哈希、首次动作说明 |
| `lib/tools.js` | 单一 `coordination` 工具（contract/status/gate/logs/extensions）与单调守卫 |
| `lib/logs.js` | 每日 JSONL、I0–I3、去重索引、脱敏、轮转/留存、fallback |
| `lib/extensions.js` | 声明目录、状态映射、批量安装（禁用）、身份+探测门槛的启用 |
| `lib/engine.js` | Python 核心适配：固定 argv、`ctx.subprocess`/execFile、超时、退出码映射、I0 降级 |
| `lib/http.js` | 人类专用 `/_multimodel/*` 路由：同源 + CSRF + 显式 confirm |
| `lib/client.js` | 设置页：开关、验证、契约、扩展、日志、诊断 |

## 启动顺序

1. `profileContext`（缺失则 env；都缺失 → `profile_unknown`，拒绝变更）；
2. 计算运行版本与包摘要（`package.json`、patch、`lib/**`、`skills/coordination/**`、`scripts/**/*.py`、`assets/**`）；
3. 载入 state；首次/版本或摘要不一致 → `pending_verification`；
4. 校验 40 条契约；失败 → I0 + 拒绝守卫；
5. 注册提示段（每次组装动态取文本）；ON 时注册工具与守卫；
6. 注册 HTTP 路由（OFF 也可用于开启）；启动日志；`ctx.effect` 统一清理。

## 强制边界

- 强制：系统提示注入、`coordination` 工具/守卫、人类安装/验证路由、Python 门禁调用、日志写入。
- 不强制：插件之外直接调用的宿主工具、直接 shell/网络、其他插件私有路径、模型自身推理。
- 不宣称宿主级全局拦截、OS 级沙箱或官方认证；`HC-8` 是验证后激活门，不是同 Profile 双版本并行。
