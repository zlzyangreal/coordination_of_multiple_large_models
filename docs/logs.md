# 本地日志（I0–I3）

日志只在本机、默认在 Git 工作区之外，供用户日后**自行决定**是否调查/优化；日志系统不会修复、改配置、改路由、装插件、发布或上传。

## 路径

```text
$DSH_HOME/coordination/
  logs/<profile>/runtime_error_YYYY-MM-DD.jsonl
  logs/<profile>/optimization_YYYY-MM-DD.jsonl
  index/<profile>.json          # 跨日期问题索引（去重、优先级、状态）
  state/<profile>.json          # 开关/验证/契约/受控操作
  fallback/<profile>.jsonl      # 主日志写入失败时的降级落点
```

- 每个 Profile 独立；不跨项目混写。若 `$DSH_HOME/coordination` 位于 Git 工作区内，改用 `%LOCALAPPDATA%\dsh-coordination` 并记 I1。
- 每日新建文件；单文件超过 `logs.maxBytes`（默认 4 MiB）轮转为 `.1`；`logs.retentionDays`（默认 30 天）在启动时清理过期文件。

## 优先级

| 级别 | 含义 | 例子 |
|---|---|---|
| I0 | 致命/阻断 | 契约无效/注入失败、state 损坏、核心门禁不可用、安装/激活失败、插件无法启动 |
| I1 | 高价值/高影响 | 反复调用失败、回退、额度/超时、显著浪费 |
| I2 | 一般/可改进 | 局部低效、非关键重试、评审冗余 |
| I3 | 低价值/观察 | 罕见、证据不足、收益小 |

I0 永远置顶、不采样、不被静默去重；I1–I3 按 `issue_id` 合并，保留首次/最后时间与次数。用户可标记 `open / investigating / planned / resolved / deferred / wont_fix`，标记不会删除记录。

## 脱敏与字段

记录 `timestamp`（含本地时区偏移）、`priority`、`event_type`、`plugin_version`、`profile`、`project_id`、`task_id`、`phase`、`model/provider`、`duration_ms`、`error_code`、`summary`、`impact`、`suggested_action`、`status`、`issue_id`、`priority_reason`、`frequency`、`confidence`、`last_seen`、`event_id`；不可获得的指标为 `null`。API Key/Token/Cookie/Bearer、用户主目录、邮箱会脱敏；不记录提示词、对话、文件正文或完整请求/响应。

## 写入失败

主日志失败 → 写 `fallback/<profile>.jsonl` 并记 I2；fallback 也失败 → 内存环形缓冲 + 状态里 `logWriteFailed:true`，不向任务抛异常。OFF 时只记录最小化的生命周期故障（启动/停止/state 失败），不采集优化遥测。

## 不外传

不上传 GitHub/Issue/CI/远程日志平台，不作为模型输入自动外发；`.gitignore` 覆盖 `coordination/`、`*.jsonl`、`.multimodel/`，发布包排除这些路径。需要分享时由用户手动脱敏导出。
