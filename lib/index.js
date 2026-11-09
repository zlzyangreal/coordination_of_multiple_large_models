import { randomUUID } from 'node:crypto'
import { promises as fs, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadContract } from './contract.js'
import { PythonEngine } from './engine.js'
import { ExtensionRegistry } from './extensions.js'
import { registerRoutes } from './http.js'
import { LogStore } from './logs.js'
import { isoLocal, packageDigest, resolveProfile, resolveRoots } from './paths.js'
import { StateStore } from './state.js'
import { buildCoordinationTool, registerContractGuard } from './tools.js'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const PACKAGE = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'))
const RUNNING_VERSION = PACKAGE.version
const NULL_LOG = {
  record: async () => null,
  list: async () => [],
  markStatus: async () => { throw new Error('logging unavailable') },
  summary: () => ({ counts: { I0: 0, I1: 0, I2: 0, I3: 0 }, root: null, collectionEnabled: false, logWriteFailed: true, fallbackWriteFailed: true, top: [] }),
  flush: async () => {},
  setCollection: () => {},
}

export const name = 'dsh-multimodel-coordination'
export const inject = ['systemPrompt', 'tools']

/** Standard Schema accepted by Cordis (`Config['~standard'].validate`). */
export const Config = {
  '~standard': {
    version: 1,
    vendor: 'dsh-multimodel-coordination',
    validate(value) {
      const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
      const issues = []
      const out = {}
      if (input.extensions !== undefined) {
        if (!Array.isArray(input.extensions)) {
          issues.push({ message: 'extensions must be an array', path: ['extensions'] })
        } else {
          out.extensions = input.extensions.map((item, index) => {
            if (!item || typeof item !== 'object') {
              issues.push({ message: 'each extension must be an object', path: ['extensions', index] })
              return {}
            }
            if (!item.id || typeof item.id !== 'string') {
              issues.push({ message: 'extension id is required', path: ['extensions', index, 'id'] })
            }
            if (item.policy && !['required', 'optional'].includes(item.policy)) {
              issues.push({ message: 'policy must be required or optional', path: ['extensions', index, 'policy'] })
            }
            return { ...item }
          })
        }
      }
      if (input.logs !== undefined) {
        const logs = input.logs && typeof input.logs === 'object' ? input.logs : {}
        const retentionDays = logs.retentionDays === undefined ? 30 : Number(logs.retentionDays)
        const maxBytes = logs.maxBytes === undefined ? 4 * 1024 * 1024 : Number(logs.maxBytes)
        if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 3650) {
          issues.push({ message: 'logs.retentionDays must be an integer between 1 and 3650', path: ['logs', 'retentionDays'] })
        }
        if (!Number.isFinite(maxBytes) || maxBytes < 65536) {
          issues.push({ message: 'logs.maxBytes must be at least 65536', path: ['logs', 'maxBytes'] })
        }
        out.logs = { retentionDays, maxBytes }
      }
      if (input.pythonPath !== undefined) {
        if (typeof input.pythonPath !== 'string') {
          issues.push({ message: 'pythonPath must be a string', path: ['pythonPath'] })
        } else out.pythonPath = input.pythonPath
      }
      return issues.length > 0 ? { issues } : { value: out }
    },
  },
}

function nullLog() {
  return NULL_LOG
}

export async function apply(ctx, rawConfig = {}) {
  const config = rawConfig && typeof rawConfig === 'object' ? rawConfig : {}
  const profile = resolveProfile(ctx)
  const roots = profile ? resolveRoots(profile) : null
  const version = RUNNING_VERSION
  const digest = await packageDigest(ROOT)
  const logStore = roots
    ? new LogStore({
      root: roots.root,
      profile: roots.profile,
      projectId: roots.projectId,
      version,
      enabled: false,
      maxBytes: config.logs?.maxBytes || 4 * 1024 * 1024,
      retentionDays: config.logs?.retentionDays || 30,
      log: null,
    })
    : nullLog()
  const store = new StateStore({
    file: roots ? roots.stateFile : path.join(os.tmpdir(), 'dsh-multimodel-coordination', 'state.json'),
    runningVersion: version,
    runningDigest: digest,
    log: logStore,
  })
  await store.load()
  logStore.setCollection(store.available())
  const contract = await loadContract(ROOT)
  if (!contract.ok) {
    await logStore.record({
      kind: 'runtime', priority: 'I0', eventType: 'contract.invalid',
      summary: `contract validation failed: ${contract.errors.slice(0, 3).join('; ')}`,
      impact: 'all controlled tools are fail-closed while the switch is ON',
      suggestedAction: 'run scripts/check_rules.py and rebuild lib/rules.md before enabling',
      status: 'open',
    })
  }
  const engine = new PythonEngine({ ctx, root: ROOT, projectRoot: process.cwd(), pythonPath: config.pythonPath || null, log: logStore })
  const registry = new ExtensionRegistry({ ctx, declarations: config.extensions, log: logStore, state: store })
  const token = randomUUID()
  let toolDisposers = []
  let routeDisposer = null
  let sectionDisposer = null
  let injectDisposer = null
  let disposed = false

  const coverage = {
    covered: [
      'system prompt contract injection',
      'coordination tool and monotonic guard',
      'human-only install/verify HTTP routes',
      'Python core gate calls',
    ],
    notCovered: [
      'host tools called outside this plugin',
      'direct shell or network access',
      "other plugins' private paths",
      'model reasoning itself',
    ],
  }

  async function coreStatus() {
    const probe = await engine.probe()
    return {
      available: probe.available,
      python: probe.python,
      degraded: false,
    }
  }

  async function status() {
    const summary = store.summary()
    return {
      ok: true,
      profile: profile?.name || null,
      profileSource: profile?.source || null,
      profileKnown: Boolean(profile),
      workingDirectory: process.cwd(),
      platform: process.platform,
      version: {
        running: version,
        verified: summary.verifiedVersion,
        digest: digest.slice(0, 16),
        verifiedDigest: summary.verifiedDigest ? String(summary.verifiedDigest).slice(0, 16) : null,
      },
      switch: {
        enabled: summary.enabled,
        savedEnabled: summary.savedEnabled,
        mode: summary.mode,
        pending: summary.mode !== 'normal',
        pendingDisable: summary.pendingDisable,
        ops: summary.runningOps,
      },
      contract: {
        ok: contract.ok,
        ruleCount: contract.ruleCount,
        hash: contract.hash,
        version: contract.version,
        epoch: summary.contractEpoch,
        acknowledgedSessions: summary.acknowledgedSessions,
        errors: contract.errors,
      },
      coverage,
      core: await coreStatus(),
      extensions: await registry.list(),
      logs: logStore.summary(),
      logRoot: roots?.logDir || null,
      lastError: summary.lastError || null,
      updatedAt: summary.updatedAt,
    }
  }

  function registerTooling() {
    if (toolDisposers.length > 0) return
    const deps = {
      state: store,
      contract,
      logs: logStore,
      status,
      extensions: registry,
      engine: {
        probe: () => engine.probe(),
        runPlanGate: async (project) => {
          await ensurePackageIntact()
          const opId = await store.beginOp({ kind: 'engine-gate', detail: project || null })
          let outcome
          try {
            outcome = await engine.runPlanGate(project)
            return outcome
          } finally {
            const aborted = outcome?.code === 'ABORT_ERR' || outcome?.code === 'ABORTED'
            await store.endOp(opId, aborted ? 'aborted' : 'done')
            refresh()
          }
        },
      },
    }
    toolDisposers = [
      ctx.tools.register(buildCoordinationTool({ deps })),
      registerContractGuard(ctx, { state: store, contract }),
    ]
  }

  function refresh() {
    if (disposed) return
    const active = store.available()
    logStore.setCollection(active)
    if (active) registerTooling()
    else {
      for (const dispose of toolDisposers) {
        try { dispose() } catch { /* already gone */ }
      }
      toolDisposers = []
    }
  }

  async function ensureProfile() {
    if (!profile) {
      const error = new Error('profile_unknown: the host did not identify the active profile; mutating actions are refused')
      error.code = 'profile_unknown'
      throw error
    }
  }

  /** Re-hash the shipped runtime before a controlled action; a mismatch forces pending. */
  async function ensurePackageIntact() {
    const current = await packageDigest(ROOT)
    if (current === digest) return
    await store.invalidate(`package digest changed: ${digest.slice(0, 12)} -> ${current.slice(0, 12)}`)
    refresh()
    await logStore.record({
      kind: 'runtime', priority: 'I0', eventType: 'package.digest_changed',
      summary: `package digest changed while running (${digest.slice(0, 12)} -> ${current.slice(0, 12)})`,
      impact: 'controlled actions are stopped; the plugin re-enters pending_verification',
      suggestedAction: 'restart, verify the running package and explicitly activate it again',
    })
    const error = new Error('package_digest_changed: controlled action refused until the package is verified again')
    error.code = 'package_digest_changed'
    throw error
  }

  async function toggle(body = {}) {
    await ensureProfile()
    if (body.enabled === true) {
      await ensurePackageIntact()
      await store.setEnabled(true)
      refresh()
      return store.summary()
    }
    const result = await store.requestDisable(body.safeStop)
    if (result.abort) {
      engine.abortAll()
      for (const op of result.ops) {
        if (op.kind === 'extension-install' && op.requestId) {
          try { await ctx.get('pluginManager')?.cancelInstall?.(op.requestId) } catch { /* best effort */ }
        }
      }
    }
    refresh()
    return { ...store.summary(), safeStop: body.safeStop || null }
  }

  async function verify(body = {}) {
    await ensureProfile()
    const checks = []
    const check = (id, ok, detail) => checks.push({ id, ok: Boolean(ok), detail: String(detail ?? '') })
    check('profile', true, `${profile.name} (${profile.source})`)
    check('contract', contract.ok, contract.ok ? `${contract.ruleCount} rules, ${contract.hash.slice(0, 12)}` : contract.errors.join('; '))
    const currentDigest = await packageDigest(ROOT)
    check('package_digest', currentDigest === digest, currentDigest.slice(0, 16))
    if (currentDigest !== digest) {
      await store.invalidate(`verify saw a changed package digest: ${digest.slice(0, 12)} -> ${currentDigest.slice(0, 12)}`)
      refresh()
    }
    try {
      const probe = path.join(roots.stateDir, `.verify-${process.pid}`)
      await fs.mkdir(roots.stateDir, { recursive: true })
      await fs.writeFile(probe, 'ok', 'utf8')
      await fs.rm(probe, { force: true })
      check('state_write', true, roots.stateDir)
    } catch (error) {
      check('state_write', false, String(error?.message || error))
    }
    check('routes', Boolean(routeDisposer), routeDisposer ? 'registered' : 'webServer unavailable')
    let toolOk = false
    try {
      const definition = buildCoordinationTool({ deps: { state: store, contract, logs: logStore, status, extensions: registry, engine } })
      toolOk = definition?.name === 'coordination' && typeof definition.execute === 'function'
    } catch { toolOk = false }
    check('tool', toolOk, toolOk ? 'coordination definition valid' : 'definition invalid')
    try {
      await fs.mkdir(roots.logDir, { recursive: true })
      const probe = path.join(roots.logDir, `.verify-${process.pid}`)
      await fs.writeFile(probe, 'ok', 'utf8')
      await fs.rm(probe, { force: true })
      check('log_write', true, roots.logDir)
    } catch (error) {
      check('log_write', false, String(error?.message || error))
    }
    const probe = await engine.probe()
    checks.push({ id: 'python_core', ok: probe.available, detail: probe.python || 'unavailable (optional; gate actions fail closed)' , optional: true })
    const required = checks.filter((item) => !item.optional)
    const ok = required.every((item) => item.ok)
    if (!ok) {
      await logStore.record({
        kind: 'runtime', priority: 'I0', eventType: 'verify.failed',
        summary: `verification failed: ${checks.filter((item) => !item.ok).map((item) => item.id).join(', ')}`,
        impact: 'the plugin stays pending; no contract or tool is active',
        suggestedAction: 'fix the failing check and verify again',
      })
    }
    if (ok && body.activate === true) {
      await store.completeVerification({ trigger: 'panel', version, digest: currentDigest })
      refresh()
    }
    return { ok, checks, activated: ok && body.activate === true, mode: store.mode() }
  }

  async function onExtensionOp(action, declaration, opId, requestId) {
    if (action === 'begin') {
      return store.beginOp({ kind: 'extension-install', requestId: requestId || null, detail: declaration.id })
    }
    if (action === 'end') {
      await store.endOp(opId, 'done')
      refresh()
    }
    return null
  }

  const extensionFacade = {
    list: () => registry.list(),
    install: async (ids, options) => {
      await ensurePackageIntact()
      return registry.install(ids, options)
    },
    activate: async (id, options) => {
      await ensurePackageIntact()
      return registry.activate(id, options)
    },
  }

  const deps = {
    status,
    toggle,
    verify,
    extensions: extensionFacade,
    logs: logStore,
    contract,
    token,
    onExtensionOp,
  }

  // Prompt section: registered once, resolved on every assembly.
  sectionDisposer = ctx.systemPrompt.section({
    name: 'coordination:contract',
    order: 9000,
    text: () => {
      if (!store.available()) return ''
      if (!contract.ok) {
        return 'CONTRACT INVALID — controlled operations are blocked (I0). Call coordination({action:"status"}) for details.'
      }
      return contract.text
    },
  })

  if (ctx.get('webServer')) routeDisposer = registerRoutes(ctx, deps)
  else {
    try {
      injectDisposer = ctx.inject(['webServer'], (inner) => {
        routeDisposer = registerRoutes(inner, deps)
      })
    } catch { /* a headless profile may have no web server */ }
  }

  refresh()

  ctx.effect(() => async () => {
    disposed = true
    engine.abortAll()
    for (const dispose of toolDisposers) {
      try { dispose() } catch { /* already gone */ }
    }
    toolDisposers = []
    try { routeDisposer?.() } catch { /* already gone */ }
    routeDisposer = null
    try { sectionDisposer?.() } catch { /* already gone */ }
    sectionDisposer = null
    try { injectDisposer?.() } catch { /* already gone */ }
    injectDisposer = null
    await logStore.flush().catch(() => {})
  }, 'multimodel-coordination: lifecycle')
}
