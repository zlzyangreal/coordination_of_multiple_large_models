# 可选扩展

## 声明驱动

可选扩展只来自 `package.json` 之外的插件配置 `extensions[]`（默认一项）；界面不维护第二份名单，也不按插件名写分支。默认声明：

```json
{
  "id": "dsh-our-free-model",
  "policy": "optional",
  "installByDefault": false,
  "source": "github:Ebony-Vinyl/dsh-our-free-model",
  "displayName": "dsh-our-free-model",
  "summary": "第三方模型接入与管理扩展",
  "permissions": ["plugin:install", "profile:write"],
  "probe": { "kind": "bundle" }
}
```

- `policy` 只有 `optional`/`required`；当前唯一声明是 `optional`，**默认不安装**。
- 未声明的插件不显示、不安装、不推断依赖等级。
- 新增扩展由维护者改声明；不需要改编排核心代码。

## 安装（禁用）

`POST /_multimodel/extensions/install { ids: [...], confirm: true }`：

- 只接受已声明且 `optional` 的 id；去重、按声明顺序执行、先做冲突检查；
- 调用宿主 `pluginManager.installBundle(spec, { enabled: false, requestId })`；
- 逐项返回下载/安装/注册/启用各阶段结果；一个失败不回滚其他项；
- 安装后状态是 `installed_disabled`，**不等于启用，也不等于通过适配验证**；
- 没有启动自动安装、日志触发安装、更新/卸载按钮。

## 启用（单独授权）

只有同时满足以下条件才允许本插件调用 `pluginManager.setBundleEnabled(id, true)`：

1. 宿主报告了具体版本/来源（`unknown` 不通过）；
2. 声明里存在公开能力探测（`probe.kind = "bundle"`，即 bundle 可用且可启用）；
3. 用户再次明确确认。

仅“存在/已安装”不构成启用资格；不符合时界面提示到 DSH 插件管理器处理，不伪造状态。安装授权不等于批准模型调用、外传数据或额度使用；实际参与编排时仍受 L1/L2/L3、模型接入审批与预算约束。

## 上游独立

第三方插件始终是 DSH Profile 的独立 Bundle：源码、配置、更新、卸载归上游/DSH 管理。本项目不复制、不内嵌、不 patch、不读私有配置、不写专属更新器；上游升级后按公开接口重新发现，不兼容只隔离并记录。
