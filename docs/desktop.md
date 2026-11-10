# 桌面版（Electron）验证清单 — 本版本未实测

v3.0.0 的发布声明只覆盖 **DSH 0.2.1-rc.3 Web Profile / Windows 11 x64**。桌面版没有在本次交付中执行真实安装与 UI 验证，因此**不作支持声明**。下面是给用户的验证清单；全部通过后可按同样方式启用。

## 为什么不能由 CLI 安装桌面 Profile

`dsh plugin --profile desktop ...` 会被拒绝：`profile "desktop" is managed exclusively by the Electron application`。桌面 Profile 的安装/更新/卸载归 Electron 应用（插件管理器/更新流程）所有，本项目不绕过。

## 清单

1. 通过桌面应用的插件管理器安装/更新 `dsh-multimodel-coordination`（或安装到桌面应用可管理的验证 Profile）。
2. 重启/重载后，设置里出现 **多模型协作**；首次状态为“待验证”。
3. 点 **验证此版本**：state/契约/摘要/路由/工具/日志检查全部通过；Python 核心可选。
4. 点 **确认启用**，再打开总开关；状态显示“已启用”，覆盖范围文本可见。
5. 在一个新会话里先让模型调用 `coordination({action:"contract"})`；未确认前调用其他工具应被拒绝并给出契约提示。
6. 可选扩展页只显示声明项；`dsh-our-free-model` 未安装时不自动安装；点安装需确认，装完仍为禁用，单独验证后才可启用。
7. `$DSH_HOME\coordination\logs\<profile>\` 出现按日 JSONL；`git status` 看不到日志；关闭开关后新任务不再采集优化日志。
8. 升级到新版本后重启：应先显示 `pending_verification`，确认前不注入契约；验证并确认后才恢复。
9. 卸载后 `_work`、项目配置与本地日志保留；没有悬挂监听/路由/进程。

任一项失败：不要在生产 Profile 启用，保留日志并回报；桌面版在修复并实测前仍不在支持范围内。
