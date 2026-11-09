# 安装、验证、升级与卸载

支持范围：**DSH 0.2.1-rc.3 Web Profile，Windows 11 x64**。桌面（Electron）Profile 预期可用，但本版本**未实测**，见 [desktop.md](desktop.md)。

## 1. 安装到独立验证 Profile（推荐）

当前正在用的 Profile 不动，先把候选版装到单独的验证 Profile：

```powershell
# 在仓库根目录；dsh 与 pnpm 在 PATH 上
dsh plugin --profile coordination-verify add .
```

`dsh plugin` 会把包操作转发给 pnpm；pnpm 不在 PATH 时请先按 pnpm 官方方式安装。不要通过
`app.asar` 内部路径或手工改 Profile 清单安装；桌面 Profile 由 Electron 应用管理，请在应用内
插件管理器安装/更新/卸载（桌面本版未实测，见 [desktop.md](desktop.md)）。

安装后确认 bundle 层：

```powershell
dsh --profile coordination-verify --dump-config | Select-String "dsh-multimodel-coordination" -Context 1,2
```

## 2. 启动并验证

```powershell
dsh --profile coordination-verify --port 0 --no-open
```

打开打印出的地址（带一次性 token），进入 **设置 → 多模型协作**：

1. 首次安装状态是“待验证”；点 **验证此版本**，逐项检查 state/契约/摘要/路由/工具/日志（Python 核心可选）。
2. 全部必需项通过后，再点 **确认启用**；这一步才会写入 `verified_version` 与 `verified_digest`。
3. 打开总开关（默认 OFF）。第一次工具调用前模型必须调用
   `coordination({action:"contract"})`，否则宿主会拒绝其他工具。

## 3. 升级（版本锁定）

1. 新版本先装到另一个验证 Profile（或同一验证 Profile 重新安装）；当前 Profile 不动。
2. 新版本在目标 Profile 启动时会进入 `pending_verification`：不注入契约、不注册工具、开关不可开启。
3. 用户完成上面的验证 + 确认启用后才切换。插件不会自动升级或热切换。
4. 回退：用 DSH 插件管理器重装上一版本，或 `python scripts/iteration.py rollback` 恢复 v2.4.0 快照。

## 4. 卸载

```powershell
dsh plugin --profile coordination-verify remove dsh-multimodel-coordination
```

卸载不会删除 `_work`、项目配置或本地日志。需要清理本地数据时，手动删除 `$DSH_HOME\coordination\`（日志/索引/状态）——插件不会自动上传或删除历史证据。

桌面 Profile 由 Electron 应用管理：请在应用内插件管理器安装/更新/卸载，不要用 CLI 直接改 `desktop` Profile。

## 5. 常见问题

- **`profile_unknown`**：宿主没有提供 `profileContext`，也没有 `DSH_PROFILE`+`DSH_HOME`；插件只显示只读状态，拒绝开关/验证/安装。
- **`core_unavailable`**：没有可用的 Python 3；门禁动作 fail-closed（I0），插件本体仍启动。
- **桌面未实测**：不声称桌面支持；按 [desktop.md](desktop.md) 的清单自行验证后再用于生产。
