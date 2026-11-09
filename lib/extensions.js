const DEFAULT_DECLARATION = {
  id: 'dsh-our-free-model',
  policy: 'optional',
  installByDefault: false,
  source: 'github:Ebony-Vinyl/dsh-our-free-model',
  displayName: 'dsh-our-free-model',
  summary: '第三方模型接入与管理扩展；能力以其公开接口、实际版本和兼容验证为准。',
  documentation: 'https://github.com/Ebony-Vinyl/dsh-our-free-model',
  permissions: ['plugin:install', 'profile:write'],
  probe: { kind: 'bundle' },
}

function normalizeList(value) {
  if (Array.isArray(value)) return value
  if (value && Array.isArray(value.bundles)) return value.bundles
  if (value && value.value && Array.isArray(value.value.bundles)) return value.value.bundles
  return []
}

function bundleName(bundle) {
  return String(bundle?.name || bundle?.package || bundle?.id || '')
}

/** Declaration-driven optional-extension registry. No branch keys on a plugin name. */
export class ExtensionRegistry {
  constructor({ ctx, declarations, log, state }) {
    this.ctx = ctx
    this.declarations = Array.isArray(declarations) && declarations.length > 0
      ? declarations.map((item) => ({ ...item }))
      : [{ ...DEFAULT_DECLARATION }]
    this.log = log
    this.state = state
  }

  manager() {
    return this.ctx?.get?.('pluginManager') || null
  }

  async bundles() {
    const manager = this.manager()
    if (!manager?.listBundles) return null
    try {
      return normalizeList(await manager.listBundles())
    } catch {
      return null
    }
  }

  async list() {
    const bundles = await this.bundles()
    return this.declarations.map((declaration) => this.describe(declaration, bundles))
  }

  describe(declaration, bundles) {
    const base = {
      id: declaration.id,
      displayName: declaration.displayName || declaration.id,
      summary: declaration.summary || '',
      policy: declaration.policy === 'required' ? 'required' : 'optional',
      installByDefault: declaration.installByDefault === true,
      source: declaration.source || '',
      documentation: declaration.documentation || '',
      permissions: Array.isArray(declaration.permissions) ? declaration.permissions : [],
      probe: declaration.probe || null,
      installable: Boolean(declaration.source) && declaration.policy !== 'required',
      availability: null,
    }
    if (!declaration.source) {
      return {
        ...base,
        status: 'unknown',
        version: null,
        enabled: false,
        reason: 'source_not_declared: the maintainer must declare a trusted install source',
      }
    }
    if (bundles === null) {
      return { ...base, status: 'unknown', reason: 'plugin_manager_unavailable', version: null, enabled: false }
    }
    const bundle = bundles.find((item) => bundleName(item) === declaration.id)
    if (!bundle) return { ...base, status: 'not_installed', version: null, enabled: false, reason: null }
    const version = bundle.version || bundle.meta?.version || bundle.manifest?.version || null
    const enabled = bundle.enabled === true
    const availability = bundle.availability ?? null
    const readable = ['available', 'profile', 'installation'].includes(availability)
    if (bundle.error) {
      return { ...base, status: 'incompatible', version, enabled, availability, reason: String(bundle.error).slice(0, 240) }
    }
    if (availability === 'missing') {
      return { ...base, status: 'failed', version, enabled, availability, reason: 'installed package is missing' }
    }
    return {
      ...base,
      status: enabled ? 'enabled' : 'installed_disabled',
      version,
      enabled,
      availability,
      reason: null,
      identityKnown: Boolean(version),
      eligible: Boolean(version) && readable && declaration.probe?.kind === 'bundle',
    }
  }

  async install(ids, { confirm = false, requestId = null, onOp = null } = {}) {
    if (confirm !== true) throw new Error('installation requires confirm:true')
    const wanted = [...new Set((Array.isArray(ids) ? ids : [ids]).map(String))]
    if (wanted.length === 0) throw new Error('no extension id supplied')
    const manager = this.manager()
    if (!manager?.installBundle) throw new Error('plugin_manager_unavailable')
    const selected = wanted.map((id) => {
      const declaration = this.declarations.find((item) => item.id === id)
      if (!declaration) throw new Error(`undeclared extension: ${id}`)
      if (declaration.policy !== 'optional') throw new Error(`extension ${id} is not declared optional`)
      if (!declaration.source) throw new Error(`extension ${id} has no declared source`)
      return declaration
    })
    const results = []
    for (const declaration of selected) {
      const opRequestId = requestId
        ? `${requestId}-${declaration.id}`
        : `EXT-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const opId = onOp ? await onOp('begin', declaration, null, opRequestId) : null
      try {
        const answer = await manager.installBundle(declaration.source, {
          enabled: false,
          requestId: opRequestId,
        })
        const ok = answer?.application !== 'failed' && answer?.application !== 'cancelled'
        results.push({
          id: declaration.id,
          ok,
          requestId: opRequestId,
          stage: answer?.application || answer?.stage || 'unknown',
          version: answer?.version || answer?.packageResult?.version || null,
          failedAt: answer?.failedAt || null,
          nextStep: answer?.nextStep || null,
        })
        await this.log?.record({
          kind: 'runtime',
          priority: ok ? 'I2' : 'I0',
          eventType: ok ? 'extension.installed' : 'extension.install_failed',
          summary: `${declaration.id}: ${ok ? 'installed (disabled)' : 'install failed'}${answer?.failedAt ? ` at ${answer.failedAt}` : ''}`,
          impact: ok ? 'extension is installed but not enabled or authorized for use' : 'extension was not installed; the host restored the profile files',
          suggestedAction: ok ? 'verify identity/capability, then activate only if eligible' : 'read the host diagnostic and retry only after fixing it',
          status: 'open',
        })
      } catch (error) {
        results.push({ id: declaration.id, ok: false, stage: 'failed', error: String(error?.message || error).slice(0, 240) })
        await this.log?.record({
          kind: 'runtime', priority: 'I0', eventType: 'extension.install_failed',
          summary: `${declaration.id}: ${String(error?.message || error).slice(0, 200)}`,
          impact: 'extension was not installed', suggestedAction: 'read the host diagnostic and retry after fixing it',
        })
      } finally {
        if (opId && onOp) await onOp('end', declaration, opId, opRequestId)
      }
    }
    return results
  }

  async activate(id, { confirm = false } = {}) {
    if (confirm !== true) throw new Error('activation requires confirm:true')
    const declaration = this.declarations.find((item) => item.id === id)
    if (!declaration) throw new Error(`undeclared extension: ${id}`)
    const current = (await this.list()).find((item) => item.id === id)
    if (!current) throw new Error(`unknown extension: ${id}`)
    if (current.status === 'enabled') return { ok: true, status: 'enabled', already: true }
    if (current.status !== 'installed_disabled') throw new Error(`extension is ${current.status}, not installed_disabled`)
    if (!current.identityKnown) throw new Error('identity_unknown: the host did not report a version/source; activation is refused')
    const readable = ['available', 'profile', 'installation'].includes(current.availability)
    if (!readable) throw new Error('probe_unavailable: the host did not report the bundle as readable/available; activation is refused')
    if (declaration.probe?.kind !== 'bundle') throw new Error('no declared public probe; presence-only is not eligible for activation')
    const manager = this.manager()
    if (!manager?.setBundleEnabled) throw new Error('plugin_manager_unavailable')
    const answer = await manager.setBundleEnabled(id, true)
    const after = (await this.list()).find((item) => item.id === id)
    const enabled = after?.status === 'enabled'
    await this.log?.record({
      kind: 'runtime',
      priority: enabled ? 'I2' : 'I0',
      eventType: enabled ? 'extension.activated' : 'extension.activate_failed',
      summary: `${id}: ${enabled ? 'enabled' : 'enable did not take effect'}`,
      impact: enabled ? 'the extension is enabled; its public capability still governs actual use' : 'the extension stays disabled',
      suggestedAction: enabled ? 'use only through its public contract under the existing L1/L2/L3 rules' : 'inspect the host plugin manager diagnostic',
    })
    return { ok: enabled, status: after?.status || 'unknown', application: answer?.application || null }
  }
}
