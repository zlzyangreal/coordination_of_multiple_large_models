import { atomicWriteJson, isoLocal, readJson } from './paths.js'

const SCHEMA = 1
const MAX_SESSIONS = 50
const STALE_OP_MS = 30 * 60 * 1000

function defaults(now = isoLocal()) {
  return {
    schema: SCHEMA,
    enabled: false,
    saved_enabled: false,
    verified_version: null,
    verified_digest: null,
    contract_epoch: 0,
    contract_sessions: {},
    ops: [],
    pending_disable: false,
    updated_at: now,
  }
}

/** Profile-scoped switch, verification, contract and controlled-operation state. */
export class StateStore {
  constructor({ file, runningVersion, runningDigest, log }) {
    this.file = file
    this.runningVersion = runningVersion
    this.runningDigest = runningDigest
    this.log = log
    this.state = defaults()
    this.chain = Promise.resolve()
    this.loaded = false
  }

  async load() {
    try {
      const parsed = await readJson(this.file)
      if (!parsed || parsed.schema !== SCHEMA) throw new Error(`unsupported state schema ${parsed?.schema}`)
      this.state = { ...defaults(), ...parsed }
      this.state.ops = Array.isArray(this.state.ops) ? this.state.ops : []
      this.state.contract_sessions = this.state.contract_sessions && typeof this.state.contract_sessions === 'object'
        ? this.state.contract_sessions : {}
    } catch (error) {
      if (error?.code === 'ENOENT') {
        this.state = defaults()
        await this.persist()
      } else {
        const quarantine = `${this.file}.corrupt-${Date.now()}`
        try { await import('node:fs/promises').then((fs) => fs.rename(this.file, quarantine)) } catch { /* ignore */ }
        this.state = defaults()
        await this.persist()
        await this.log?.record({
          kind: 'runtime', priority: 'I0', eventType: 'state.corrupt',
          summary: `state file was corrupt and has been quarantined: ${String(error?.message || error).slice(0, 160)}`,
          impact: 'switch, verification and contract state were reset to safe defaults',
          suggestedAction: 'inspect the quarantined file; re-enable and verify if the previous state is still wanted',
          status: 'open',
        })
      }
    }
    this.loaded = true
    await this.reconcileOps()
  }

  async persist() {
    await atomicWriteJson(this.file, this.state)
  }

  snapshot() {
    return structuredClone(this.state)
  }

  get() {
    return this.state
  }

  mode() {
    if (this.state.verified_version !== this.runningVersion) return 'pending_verification'
    if (this.state.verified_digest !== this.runningDigest) return 'pending_verification'
    return 'normal'
  }

  available() {
    return this.mode() === 'normal' && this.state.enabled === true
  }

  runningOps() {
    return this.state.ops.filter((op) => op.status === 'running')
  }

  async update(mutator) {
    const run = this.chain.then(async () => {
      const next = structuredClone(this.state)
      mutator(next)
      next.updated_at = isoLocal()
      await atomicWriteJson(this.file, next)
      this.state = next
      return structuredClone(next)
    })
    this.chain = run.then(() => undefined, () => undefined)
    return run
  }

  async setEnabled(enabled) {
    if (enabled && this.mode() !== 'normal') {
      const error = new Error('verification_pending: verify the running version and digest before enabling')
      error.code = 'verification_pending'
      throw error
    }
    if (!enabled) {
      const running = this.runningOps()
      if (running.length > 0) {
        const error = new Error(`controlled operations are still running: ${running.map((op) => op.kind).join(', ')}`)
        error.code = 'ops_running'
        error.ops = running
        throw error
      }
    }
    return this.update((next) => {
      next.enabled = Boolean(enabled)
      next.saved_enabled = Boolean(enabled)
      next.pending_disable = false
      if (enabled) next.contract_epoch += 1
    })
  }

  async requestDisable(safeStop) {
    const running = this.runningOps()
    if (running.length === 0) return this.setEnabled(false)
    if (!safeStop || !['finish', 'abort'].includes(safeStop)) {
      const error = new Error('controlled operations are running; pass safeStop "finish" or "abort"')
      error.code = 'ops_running'
      error.ops = running
      throw error
    }
    if (safeStop === 'finish') {
      await this.update((next) => { next.pending_disable = true })
      return { pendingDisable: true, ops: running }
    }
    await this.update((next) => { next.pending_disable = true })
    return { pendingDisable: true, ops: running, abort: true }
  }

  async beginOp({ kind, requestId = null, detail = null }) {
    const id = `OP-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    await this.update((next) => {
      next.ops = next.ops.filter((op) => op.status === 'running' || op.status === 'done' || op.status === 'aborted').slice(-20)
      next.ops.push({ id, kind, requestId, detail, status: 'running', startedAt: isoLocal(), finishedAt: null })
    })
    return id
  }

  async endOp(id, status = 'done', detail = null) {
    return this.update((next) => {
      const op = next.ops.find((item) => item.id === id)
      if (op) {
        op.status = status
        op.finishedAt = isoLocal()
        if (detail) op.detail = detail
      }
      if (next.pending_disable && !next.ops.some((item) => item.status === 'running')) {
        next.enabled = false
        next.saved_enabled = false
        next.pending_disable = false
      }
    })
  }

  async reconcileOps() {
    const now = Date.now()
    let changed = false
    for (const op of this.state.ops) {
      if (op.status !== 'running') continue
      const started = Date.parse(op.startedAt)
      if (!Number.isFinite(started) || now - started > STALE_OP_MS) {
        op.status = 'failed'
        op.finishedAt = isoLocal()
        op.detail = 'reconciled at startup: the operation did not reach a terminal state'
        changed = true
      }
    }
    if (this.state.pending_disable && !this.state.ops.some((op) => op.status === 'running')) {
      this.state.enabled = false
      this.state.saved_enabled = false
      this.state.pending_disable = false
      changed = true
    }
    if (changed) await this.persist()
  }

  async ackContract(sessionId, hash) {
    if (!sessionId) {
      const error = new Error('session_identity_unavailable')
      error.code = 'session_identity_unavailable'
      throw error
    }
    const at = isoLocal()
    await this.update((next) => {
      next.contract_sessions[sessionId] = { hash, epoch: next.contract_epoch, at }
      const entries = Object.entries(next.contract_sessions)
      if (entries.length > MAX_SESSIONS) {
        entries.sort((a, b) => String(a[1].at).localeCompare(String(b[1].at)))
        for (const [key] of entries.slice(0, entries.length - MAX_SESSIONS)) delete next.contract_sessions[key]
      }
    })
    return { sessionId, hash, epoch: this.state.contract_epoch, at }
  }

  isAcked(sessionId, hash) {
    if (!sessionId) return false
    const record = this.state.contract_sessions[sessionId]
    return Boolean(record && record.hash === hash && record.epoch === this.state.contract_epoch)
  }

  async completeVerification({ trigger = 'manual' } = {}) {
    return this.update((next) => {
      next.verified_version = this.runningVersion
      next.verified_digest = this.runningDigest
      next.enabled = next.saved_enabled === true
      next.pending_disable = false
      next.contract_epoch += 1
      next.last_verified_at = isoLocal()
      next.last_verified_trigger = trigger
    })
  }

  summary() {
    return {
      mode: this.mode(),
      enabled: this.state.enabled === true,
      savedEnabled: this.state.saved_enabled === true,
      verifiedVersion: this.state.verified_version,
      verifiedDigest: this.state.verified_digest,
      contractEpoch: this.state.contract_epoch,
      acknowledgedSessions: Object.keys(this.state.contract_sessions).length,
      runningOps: this.runningOps(),
      pendingDisable: this.state.pending_disable === true,
      updatedAt: this.state.updated_at,
    }
  }
}
