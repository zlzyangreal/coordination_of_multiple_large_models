import { createHash } from 'node:crypto'
import { watch } from 'node:fs'
import { readdir, realpath, stat } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const OPS = new Set(['state', 'init', 'plan', 'assign', 'pin', 'start', 'result', 'review', 'deliver'])
const MUTATING_OPS = new Set(['init', 'plan', 'assign', 'pin', 'start', 'result', 'review', 'deliver'])

function failure(code, message) {
  return { ok: false, code, message: String(message || 'workflow unavailable') }
}

export class WorkflowBridge {
  constructor({ ctx, engine, projectRoot, homeRoot, root }) {
    this.ctx = ctx
    this.engine = engine
    this.projectRoot = projectRoot
    this.homeRoot = homeRoot || path.join(os.tmpdir(), 'dsh-multimodel-coordination')
    this.boardRoot = path.join(projectRoot, '_work', 'orchestration')
    // The state machine ships with the package; the project only owns the board.
    this.pythonScript = path.join(root || projectRoot, 'scripts', 'orchestration.py')
    this.receiptsPromise = this.receiptsPath()
    this.clients = new Set()
    this.watcher = null
    this.timer = null
    this.rearmTimer = null
    this.refreshing = false
    this.lastFrame = undefined
    this.watchFailureLogged = false
    this.closed = false
  }

  async receiptsPath() {
    const root = await realpath(this.projectRoot)
    const projectKey = createHash('sha256').update(root).digest('hex').slice(0, 16)
    return path.join(this.homeRoot, 'orchestration', projectKey, 'receipts.jsonl')
  }

  async newestTask() {
    try {
      const entries = await readdir(this.boardRoot, { withFileTypes: true })
      const boards = await Promise.all(entries
        .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
        .map(async (entry) => {
          const info = await stat(path.join(this.boardRoot, entry.name))
          return { name: entry.name.slice(0, -5), mtimeMs: info.mtimeMs }
        }))
      boards.sort((a, b) => b.mtimeMs - a.mtimeMs || a.name.localeCompare(b.name))
      return boards[0]?.name || null
    } catch (error) {
      if (error?.code === 'ENOENT') return null
      throw error
    }
  }

  parseResult(result) {
    if (result?.code === 'ENOENT') {
      return { ok: false, code: 'core_unavailable', message: 'python core unavailable' }
    }
    try {
      const value = JSON.parse(String(result?.stdout || '').trim())
      const response = {
        ok: value.ok === true,
        code: value.code,
        message: String(value.message || ''),
      }
      if (Object.hasOwn(value, 'task')) response.task = value.task
      return response
    } catch (error) {
      return failure(result?.code || 'invalid_response', `invalid workflow response: ${error?.message || error}`)
    }
  }

  async invoke(op, payload = {}) {
    if (!OPS.has(op)) return failure(2, `unsupported workflow operation: ${op}`)
    try {
      const receipts = await this.receiptsPromise
      const argv = [this.pythonScript, op, '--project', this.projectRoot]
      argv.push('--task', String(payload.task || ''))
      if (MUTATING_OPS.has(op)) {
        argv.push('--expected-revision', String(payload.expected_revision ?? ''))
        argv.push('--receipts', receipts)
      } else {
        argv.push('--receipts', receipts)
      }
      const fields = {
        init: [['title', 'title']],
        plan: [['nodes_file', 'nodes-file']],
        assign: [['node', 'node'], ['lane', 'lane'], ['provider', 'provider'], ['model', 'model']],
        pin: [['node', 'node'], ['lane', 'lane'], ['provider', 'provider'], ['model', 'model'], ['by', 'by'], ['note', 'note']],
        start: [['node', 'node']],
        result: [['node', 'node'], ['summary', 'summary'], ['artifact', 'artifact'], ['commit', 'commit'], ['child_session', 'child-session'], ['child_model', 'child-model']],
        review: [['node', 'node'], ['verdict', 'verdict'], ['findings_file', 'findings-file'], ['by', 'by']],
        deliver: [['commit', 'commit']],
      }
      for (const [field, flag] of fields[op] || []) {
        if (payload[field] !== undefined && payload[field] !== null) {
          argv.push(`--${flag}`, String(payload[field]))
        }
      }
      if (op === 'result' && payload.no_subagent === true) argv.push('--no-subagent')
      argv.push('--json')
      return this.parseResult(await this.engine.spawn(argv, { cwd: this.projectRoot }))
    } catch (error) {
      if (error?.code === 'ENOENT') {
        return { ok: false, code: 'core_unavailable', message: 'python core unavailable' }
      }
      return failure(error?.code || 1, error?.message || error)
    }
  }

  async snapshot(task) {
    try {
      const selected = task || await this.newestTask()
      if (!selected) return null
      const value = await this.invoke('state', { task: selected })
      if (!value.ok) {
        if (/board not found/i.test(value.message)) return null
        return value
      }
      const snapshot = value.task
      if (snapshot && typeof snapshot === 'object') delete snapshot.models
      return snapshot
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  async run(op, payload = {}) {
    try {
      if (op === 'state') {
        const task = await this.snapshot(payload.task || null)
        if (task === null) return { ok: true, code: 0, message: 'ok', task: null }
        if (task?.ok === false) return task
        return { ok: true, code: 0, message: 'ok', task }
      }
      return await this.invoke(op, payload)
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  async models() {
    try {
      const llm = this.ctx?.get?.('llm')
      if (!llm) throw new Error('llm service unavailable')
      if (typeof llm.listProviders !== 'function' || typeof llm.listModels !== 'function') {
        throw new Error('llm catalog adapter unavailable')
      }
      const listedProviders = await llm.listProviders()
      const providers = Array.isArray(listedProviders) ? listedProviders : listedProviders?.providers
      if (!Array.isArray(providers)) throw new Error('provider catalog is invalid')
      const routes = []
      for (const item of providers) {
        const provider = typeof item === 'string' ? item : item?.id || item?.provider || item?.name
        if (!provider) continue
        const listedModels = await llm.listModels(provider)
        const models = Array.isArray(listedModels) ? listedModels : listedModels?.models
        if (!Array.isArray(models)) throw new Error(`model catalog is invalid for ${provider}`)
        for (const entry of models) {
          const model = typeof entry === 'string' ? entry : entry?.id || entry?.model
          if (!model) continue
          const name = typeof entry === 'string' ? entry : entry?.name || model
          routes.push({ lane: 'dsh', provider, model, name })
        }
      }
      return { ok: true, routes, reason: null }
    } catch (error) {
      return { ok: false, routes: [], reason: `model catalog unavailable: ${String(error?.message || error)}` }
    }
  }

  logWatchFailure(error) {
    if (this.watchFailureLogged) return
    this.watchFailureLogged = true
    const message = `workflow board watcher unavailable: ${String(error?.message || error)}`
    try {
      const logger = typeof this.ctx?.logger === 'function'
        ? this.ctx.logger('dsh-multimodel-coordination')
        : this.ctx?.logger
      if (logger?.warn) logger.warn(message)
      else console.warn(message)
    } catch {
      try { console.warn(message) } catch { /* logging is best effort */ }
    }
  }

  scheduleRearm() {
    if (this.closed || this.rearmTimer) return
    this.rearmTimer = setTimeout(() => {
      this.rearmTimer = null
      this.armWatcher()
    }, 200)
    this.rearmTimer.unref?.()
  }

  armWatcher() {
    if (this.closed || this.watcher || this.rearmTimer || this.clients.size === 0) return
    try {
      const watcher = watch(this.boardRoot, (_event, filename) => {
        void this.refresh()
        if (_event === 'rename') {
          try { watcher.close() } catch { /* already closed */ }
          if (this.watcher === watcher) this.watcher = null
          this.scheduleRearm()
        }
      })
      watcher.on('error', (error) => {
        this.logWatchFailure(error)
        try { watcher.close() } catch { /* already closed */ }
        if (this.watcher === watcher) this.watcher = null
        this.scheduleRearm()
      })
      watcher.unref?.()
      this.watcher = watcher
    } catch (error) {
      this.logWatchFailure(error)
    }
  }

  writeFrame(client, value) {
    try {
      client.res.write(`data: ${JSON.stringify(value)}\n\n`)
    } catch {
      this.removeClient(client)
    }
  }

  removeClient(client) {
    if (!this.clients.delete(client)) return
    try { client.req?.off?.('close', client.onClose) } catch { /* already gone */ }
    try { client.res.off?.('close', client.onClose) } catch { /* already gone */ }
    if (this.clients.size === 0) {
      try { this.watcher?.close() } catch { /* already closed */ }
      this.watcher = null
      if (this.timer) clearInterval(this.timer)
      this.timer = null
    }
  }

  async refresh(force = false) {
    if (this.closed || this.refreshing || this.clients.size === 0) return
    this.refreshing = true
    try {
      const value = await this.snapshot(null)
      const frame = JSON.stringify(value)
      if (force || frame !== this.lastFrame) {
        this.lastFrame = frame
        for (const client of this.clients) this.writeFrame(client, value)
      }
      const now = Date.now()
      for (const client of this.clients) {
        if (now - client.lastHeartbeat < client.heartbeatMs) continue
        try {
          client.res.write(': ping\n\n')
          client.lastHeartbeat = now
        } catch {
          this.removeClient(client)
        }
      }
      if (!this.watcher) this.armWatcher()
    } catch (error) {
      const value = failure(error?.code || 1, error?.message || error)
      for (const client of this.clients) this.writeFrame(client, value)
    } finally {
      this.refreshing = false
    }
  }

  subscribe(res, { heartbeatMs = 15000 } = {}) {
    try {
      if (this.closed) return failure('closed', 'workflow bridge is closed')
      res.statusCode = 200
      res.setHeader('content-type', 'text/event-stream')
      res.setHeader('cache-control', 'no-cache')
      res.setHeader('connection', 'keep-alive')
      res.flushHeaders?.()
      const client = {
        res,
        req: res.req || null,
        heartbeatMs: Number(heartbeatMs) > 0 ? Number(heartbeatMs) : 15000,
        lastHeartbeat: Date.now(),
        onClose: null,
      }
      client.onClose = () => this.removeClient(client)
      client.req?.on?.('close', client.onClose)
      res.on?.('close', client.onClose)
      this.clients.add(client)
      if (!this.timer) {
        this.timer = setInterval(() => { void this.refresh() }, 1000)
        this.timer.unref?.()
      }
      this.armWatcher()
      void this.snapshot(null).then((value) => {
        if (!this.clients.has(client)) return
        this.lastFrame = JSON.stringify(value)
        this.writeFrame(client, value)
      }).catch((error) => this.writeFrame(client, failure(error?.code || 1, error?.message || error)))
      return { ok: true }
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  async close() {
    try {
      this.closed = true
      try { this.watcher?.close() } catch { /* already closed */ }
      this.watcher = null
      if (this.timer) clearInterval(this.timer)
      this.timer = null
      if (this.rearmTimer) clearTimeout(this.rearmTimer)
      this.rearmTimer = null
      for (const client of [...this.clients]) {
        this.removeClient(client)
        try { client.res.end() } catch { /* already closed */ }
      }
      return { ok: true }
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }
}
