import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { watch } from 'node:fs'
import { readdir, realpath, stat } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'
import { BoardIndex, boardRootFor, deriveActivity } from './board-index.js'

const execFileAsync = promisify(execFile)

const OPS = new Set(['state', 'init', 'plan', 'assign', 'pin', 'start', 'result', 'review', 'fail', 'deliver'])
const MUTATING_OPS = new Set(['init', 'plan', 'assign', 'pin', 'start', 'result', 'review', 'fail', 'deliver'])
const REVIEW_ONLY = new Set(['review'])
const CACHE_MS = 30 * 1000

function failure(code, message) {
  return { ok: false, code, message: String(message || 'workflow unavailable') }
}

function isoNow() {
  return new Date().toISOString()
}

function normaliseTasks(tasks) {
  if (tasks === 'all' || tasks === true) return 'all'
  if (Array.isArray(tasks)) return tasks.map(String).filter(Boolean)
  if (typeof tasks === 'string') return tasks.split(',').map((item) => item.trim()).filter(Boolean)
  return []
}

export class WorkflowBridge {
  constructor({ ctx, engine, projectRoot, homeRoot, root, state }) {
    this.ctx = ctx
    this.engine = engine
    this.projectRoot = projectRoot
    this.homeRoot = homeRoot || path.join(os.tmpdir(), 'dsh-multimodel-coordination')
    this.boardRoot = boardRootFor(projectRoot)
    // The state machine ships with the package; the project only owns the board.
    this.pythonScript = path.join(root || projectRoot, 'scripts', 'orchestration.py')
    this.eligibilityScript = path.join(path.dirname(this.pythonScript), 'eligibility.py')
    this.receiptsDirPromise = this.receiptsDir()
    this.state = state || null
    this.index = new BoardIndex()
    this.projects = new Map()
    this.snapshotCache = new Map()
    this.modelsCache = { at: 0, value: null }
    this.eligibilityCache = new Map()
    this.clients = new Set()
    this.watcher = null
    this.timer = null
    this.rearmTimer = null
    this.refreshing = false
    this.lastFrame = undefined
    this.watchFailureLogged = false
    this.closed = false
  }

  async receiptsDir() {
    return this.receiptsDirFor(this.projectRoot)
  }

  async receiptsDirFor(projectRoot) {
    const root = await realpath(projectRoot)
    const projectKey = createHash('sha256').update(root).digest('hex').slice(0, 16)
    return path.join(this.homeRoot, 'orchestration', projectKey)
  }

  /** One target per resolved project root; the default stays the constructed root. */
  target(project) {
    const root = project && typeof project === 'string' && path.isAbsolute(project)
      ? path.resolve(project)
      : this.projectRoot
    let entry = this.projects.get(root)
    if (!entry) {
      entry = {
        projectRoot: root,
        boardRoot: boardRootFor(root),
        receiptsDirPromise: this.receiptsDirFor(root),
      }
      this.projects.set(root, entry)
    }
    return entry
  }

  /**
   * Each task keeps its own receipt stream, so mutating one board can never make
   * another board's chain look stale.
   */
  async receiptsFor(task, project) {
    const entry = this.target(project)
    const dir = await entry.receiptsDirPromise
    const safe = String(task || '').replace(/[^A-Za-z0-9._-]/g, '_')
    return path.join(dir, `${safe || 'default'}.jsonl`)
  }

  async newestTask(boardRoot = this.boardRoot) {
    try {
      const entries = await readdir(boardRoot, { withFileTypes: true })
      const boards = await Promise.all(entries
        .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
        .map(async (entry) => {
          const info = await stat(path.join(boardRoot, entry.name))
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
      const entry = this.target(payload.project)
      const taskId = String(payload.task || '')
      const receipts = await this.receiptsFor(taskId, entry.projectRoot)
      const argv = [this.pythonScript, op, '--project', entry.projectRoot]
      argv.push('--task', taskId)
      if (MUTATING_OPS.has(op)) {
        argv.push('--expected-revision', String(payload.expected_revision ?? ''))
      }
      argv.push('--receipts', receipts)
      const fields = {
        init: [['title', 'title']],
        plan: [['nodes_file', 'nodes-file']],
        assign: [['node', 'node'], ['lane', 'lane'], ['provider', 'provider'], ['model', 'model']],
        pin: [['node', 'node'], ['lane', 'lane'], ['provider', 'provider'], ['model', 'model'], ['by', 'by'], ['note', 'note']],
        start: [['node', 'node']],
        result: [['node', 'node'], ['summary', 'summary'], ['artifact', 'artifact'], ['commit', 'commit'], ['child_session', 'child-session'], ['child_model', 'child-model']],
        review: [['node', 'node'], ['verdict', 'verdict'], ['findings_file', 'findings-file'], ['by', 'by']],
        fail: [['node', 'node'], ['reason', 'reason']],
        deliver: [['commit', 'commit']],
      }
      for (const [field, flag] of fields[op] || []) {
        if (payload[field] !== undefined && payload[field] !== null) {
          argv.push(`--${flag}`, String(payload[field]))
        }
      }
      if (op === 'result' && payload.no_subagent === true) argv.push('--no-subagent')
      argv.push('--json')
      return this.parseResult(await this.engine.spawn(argv, { cwd: entry.projectRoot }))
    } catch (error) {
      if (error?.code === 'ENOENT') {
        return { ok: false, code: 'core_unavailable', message: 'python core unavailable' }
      }
      return failure(error?.code || 1, error?.message || error)
    }
  }

  async snapshot(task, project) {
    try {
      const entry = this.target(project)
      const selected = task || await this.newestTask(entry.boardRoot)
      if (!selected) return null
      const value = await this.invoke('state', { task: selected, project: entry.projectRoot })
      if (!value.ok) {
        if (/board not found/i.test(value.message)) return null
        return value
      }
      const snapshot = value.task
      if (snapshot && typeof snapshot === 'object') {
        delete snapshot.models
        snapshot.activity = deriveActivity(snapshot)
      }
      return snapshot
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  /** All boards of one project (cheap JSON read, no python). */
  async listBoards(project) {
    try {
      const entry = this.target(project)
      const listed = await this.index.list(entry.projectRoot)
      return { ok: true, project: entry.projectRoot, root: listed.root, boards: listed.boards }
    } catch (error) {
      return { ok: false, project: this.target(project).projectRoot, boards: [], reason: String(error?.message || error) }
    }
  }

  /**
   * Snapshots for several boards with a revision gate: python runs only when a
   * board's revision changed (or on first read), never once per tick per board.
   */
  async snapshotMany(tasks, project) {
    const entry = this.target(project)
    let wanted = normaliseTasks(tasks)
    if (wanted === 'all') {
      const listed = await this.index.list(entry.projectRoot)
      wanted = listed.boards.map((board) => board.task_id)
    }
    const out = {}
    for (const id of wanted.slice(0, 8)) {
      const key = `${entry.projectRoot}|${id}`
      const raw = await this.index.read(id, entry.projectRoot)
      if (!raw) {
        this.snapshotCache.delete(key)
        out[id] = null
        continue
      }
      const revision = Number(raw.revision) || 0
      const cached = this.snapshotCache.get(key)
      if (cached && cached.revision === revision && Date.now() - cached.at < CACHE_MS) {
        out[id] = cached.value
        continue
      }
      const value = await this.snapshot(id, entry.projectRoot)
      this.snapshotCache.set(key, { revision, at: Date.now(), value })
      out[id] = value
    }
    return out
  }

  async run(op, payload = {}) {
    try {
      if (op === 'state') {
        const task = await this.snapshot(payload.task || null, payload.project)
        if (task === null) return { ok: true, code: 0, message: 'ok', task: null }
        if (task?.ok === false) return task
        return { ok: true, code: 0, message: 'ok', task }
      }
      if (op === 'pin' || op === 'assign') {
        const refusal = await this.assertAssignmentAllowed(payload)
        if (refusal) return refusal
      }
      if (op === 'result') {
        const refused = await this.admitResult(payload)
        if (refused) return refused
      }
      return await this.invoke(op, payload)
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  /**
   * A result is admitted only through one policy, for every caller (tool or HTTP):
   * a catalog-confirmed child whose model equals the assignment, or an artifact
   * receipt allowed only when this host really has no subagent service.
   */
  async admitResult(payload = {}) {
    const child = payload.child_session
    const service = this.ctx?.get?.('subagent')
    const available = Boolean(service && typeof service.listChildren === 'function')
    if (child) {
      if (!payload.parent_session) {
        return failure(4, 'dispatch_unverified: no parent session was supplied')
      }
      const check = await this.verifyChild(payload.parent_session, child, payload.child_model)
      if (!check.ok || !check.verified) {
        return failure(4, `dispatch_unverified: ${check.reason}`)
      }
      return null
    }
    if (payload.no_subagent === true) {
      if (available) {
        return failure(4, 'artifact results are allowed only when the subagent service is unavailable')
      }
      return null
    }
    return failure(4, 'a result needs a verified child or an artifact receipt')
  }

  /**
   * Verify that a claimed child really belongs to this session and that the catalog
   * reports the exact model the board assigned (rule I8).
   */
  async verifyChild(parentSessionId, childId, declaredModel) {
    const service = this.ctx?.get?.('subagent')
    if (!service || typeof service.listChildren !== 'function' || !parentSessionId) {
      return { ok: false, verified: false, reason: 'subagent service unavailable' }
    }
    try {
      const catalog = await service.listChildren(parentSessionId)
      const entries = Array.isArray(catalog)
        ? catalog
        : catalog?.children || catalog?.entries || catalog?.items || []
      if (!Array.isArray(entries) || entries.length === 0) {
        return { ok: false, verified: false, reason: 'child catalog unavailable' }
      }
      const found = entries.find((entry) => {
        const id = entry?.childId || entry?.sessionId || entry?.id || entry?.target
        return id !== undefined && id !== null && String(id) === String(childId)
      })
      if (!found) {
        return { ok: false, verified: true, reason: `child ${childId} is not in this session's catalog` }
      }
      const model = found.model || found.agentModel || found.modelSelection?.model || null
      if (!model) {
        return { ok: false, verified: false, reason: `child ${childId} reports no model` }
      }
      if (declaredModel && String(model) !== String(declaredModel)) {
        return { ok: false, verified: true, model, reason: `child ran ${model}, not ${declaredModel}` }
      }
      return { ok: true, verified: true, model }
    } catch (error) {
      return { ok: false, verified: false, reason: String(error?.message || error) }
    }
  }

  /** Resolve the project commit the delivery should bind to. */
  async headCommit(project) {
    try {
      const { stdout } = await execFileAsync(
        'git',
        ['-C', this.target(project).projectRoot, 'rev-parse', '--short', 'HEAD'],
        { timeout: 5000, windowsHide: true },
      )
      const value = String(stdout || '').trim()
      return value || null
    } catch {
      return null
    }
  }

  async models(project) {
    if (this.modelsCache.value && Date.now() - this.modelsCache.at < CACHE_MS) {
      return this.modelsCache.value
    }
    let value
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
      const judged = await this.judgeRoutes(routes, 'work', null, this.target(project).projectRoot)
      const enriched = routes.map((route, index) => ({
        ...route,
        family: judged[index]?.family ?? null,
        familyKnown: judged[index]?.familyKnown === true,
        familySource: judged[index]?.familySource ?? 'unknown',
      }))
      const groups = []
      const seen = new Map()
      for (const route of enriched) {
        if (!seen.has(route.provider)) {
          seen.set(route.provider, { provider: route.provider, models: [] })
          groups.push(seen.get(route.provider))
        }
        seen.get(route.provider).models.push(route)
      }
      const recent = this.recentModels()
      const favorites = []
      const keys = new Set()
      for (const route of [...recent, ...enriched]) {
        const key = `${route.lane}|${route.provider}|${route.model}`
        if (keys.has(key)) continue
        keys.add(key)
        favorites.push(route)
        if (favorites.length >= 8) break
      }
      value = { ok: true, routes: enriched, groups, favorites, recent, reason: null }
    } catch (error) {
      value = { ok: false, routes: [], groups: [], favorites: [], recent: [], reason: `model catalog unavailable: ${String(error?.message || error)}` }
    }
    this.modelsCache = { at: Date.now(), value }
    return value
  }

  recentModels() {
    try {
      const rows = this.state?.get?.()?.recent_models
      return Array.isArray(rows) ? rows.filter((row) => row && row.provider && row.model).slice(0, 8) : []
    } catch {
      return []
    }
  }

  /** Record the model the user or lead just chose, so it shows up under 常用. */
  async rememberModel(route) {
    if (!route?.provider || !route?.model) return
    try {
      if (!this.state?.update) return
      await this.state.update((next) => {
        const rows = Array.isArray(next.recent_models) ? next.recent_models : []
        const key = `${route.lane || 'dsh'}|${route.provider}|${route.model}`
        const rest = rows.filter((row) => `${row.lane || 'dsh'}|${row.provider}|${row.model}` !== key)
        next.recent_models = [{ lane: route.lane || 'dsh', provider: route.provider, model: route.model, name: route.name || route.model, at: isoNow() }, ...rest].slice(0, 20)
      })
      this.modelsCache = { at: 0, value: null }
    } catch { /* remembering is best effort */ }
  }

  /**
   * Ask the python gate for one verdict per route. A failure degrades to "unknown
   * family, no verdict" rather than blocking the whole panel.
   */
  async judgeRoutes(routes, kind, producer, project) {
    const key = createHash('sha256')
      .update(JSON.stringify({ kind, producer: producer || null, project, routes }))
      .digest('hex')
    const cached = this.eligibilityCache.get(key)
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.options
    let options = null
    try {
      const argv = [this.eligibilityScript, '--kind', String(kind || 'work'), '--routes-json', JSON.stringify(routes), '--project', project]
      if (producer) argv.push('--producer', String(producer))
      const result = await this.engine.spawn(argv, { cwd: project })
      const parsed = JSON.parse(String(result?.stdout || '').trim())
      if (parsed?.ok === true && Array.isArray(parsed.options)) options = parsed.options
    } catch { /* degraded below */ }
    if (!options) {
      options = routes.map((route) => ({
        ...route,
        family: null,
        familyKnown: false,
        familySource: 'unknown',
        eligible: true,
        code: null,
        rule: null,
        reason: null,
        verdict: 'unavailable',
      }))
    }
    this.eligibilityCache.set(key, { at: Date.now(), options })
    return options
  }

  /** Per-node assignability: the server-side truth behind the disabled rows. */
  async options(task, node, project) {
    try {
      const entry = this.target(project)
      const board = await this.snapshot(task, entry.projectRoot)
      if (!board || board.ok === false) return failure(2, 'board not found')
      const nodes = Array.isArray(board.nodes) ? board.nodes : []
      const targetNode = nodes.find((item) => String(item.id) === String(node))
      if (!targetNode) return failure(4, `node ${node} not found`)
      const producerNode = targetNode.kind === 'review'
        ? nodes.find((item) => String(item.id) === String(targetNode.reviews))
        : null
      const producer = producerNode
        ? (producerNode.assignee?.provider && producerNode.assignee?.model
          ? `${producerNode.assignee.provider}/${producerNode.assignee.model}`
          : '')
        : null
      const catalog = await this.models(entry.projectRoot)
      if (catalog.ok === false) return { ...catalog, task, node }
      const judgeKind = targetNode.kind === 'review' ? 'review' : (targetNode.kind === 'integrate' ? 'integrate' : 'work')
      const options = await this.judgeRoutes(catalog.routes, judgeKind, producer, entry.projectRoot)
      const boardClosed = board.status === 'delivered' || targetNode.status === 'done'
      for (const option of options) {
        if (boardClosed) {
          option.eligible = false
          option.code = 'node_closed'
          option.rule = '2'
          option.reason = '节点已结束，指派不会再生效'
          continue
        }
        if (targetNode.assignee && option.eligible) {
          const same = String(option.provider) === String(targetNode.assignee.provider) &&
            String(option.model) === String(targetNode.assignee.model)
          if (same) option.current = true
        }
      }
      return {
        ok: true,
        task: String(task),
        node: String(node),
        kind: targetNode.kind || 'work',
        revision: Number(board.revision) || 0,
        producer: producerNode
          ? { node: String(producerNode.id), model: producer || null, family: options[0]?.familySource === 'unavailable' ? null : (producer ? producer.split('/').pop() : null) }
          : null,
        options,
      }
    } catch (error) {
      return failure(error?.code || 1, error?.message || error)
    }
  }

  /**
   * Hard gate used by pin and assign (never by the UI alone). Returns null when the
   * assignment is allowed, or a rule_violation result.
   */
  async assertAssignmentAllowed(payload = {}) {
    try {
      const entry = this.target(payload.project)
      const board = await this.snapshot(payload.task, entry.projectRoot)
      if (!board || board.ok === false) return null
      const nodes = Array.isArray(board.nodes) ? board.nodes : []
      const targetNode = nodes.find((item) => String(item.id) === String(payload.node))
      if (!targetNode || !REVIEW_ONLY.has(targetNode.kind)) return null
      const verdict = await this.options(payload.task, payload.node, entry.projectRoot)
      if (!verdict.ok) return null
      const hit = verdict.options.find((option) =>
        String(option.provider) === String(payload.provider) &&
        String(option.model) === String(payload.model))
      if (!hit) {
        return {
          ok: false,
          code: 'rule_violation',
          rule: '2',
          reason: '该模型不在宿主可用目录中',
          message: '该模型不在宿主可用目录中',
        }
      }
      if (hit.eligible === false) {
        return {
          ok: false,
          code: 'rule_violation',
          rule: hit.rule || null,
          detail: hit.code || null,
          reason: hit.reason || '该模型不能指派给这个节点',
          message: hit.reason || '该模型不能指派给这个节点',
        }
      }
      return null
    } catch {
      return null
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
      const watcher = watch(this.boardRoot, () => { void this.refresh(true) })
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
      const frame = value && value.ok === false
        ? value
        : { ok: true, task: value, chain: value?.chain ?? null }
      client.res.write(`data: ${JSON.stringify(frame)}\n\n`)
    } catch {
      this.removeClient(client)
    }
  }

  writeMulti(client, frame) {
    try {
      client.res.write(`data: ${JSON.stringify(frame)}\n\n`)
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

  async frameFor(client) {
    const entry = this.target(client.project)
    const listed = await this.index.list(entry.projectRoot)
    const tasks = await this.snapshotMany(client.tasks, entry.projectRoot)
    const changed = {}
    for (const [id, value] of Object.entries(tasks)) {
      const hash = JSON.stringify(value)
      if (client.lastTasks.get(id) === hash) continue
      client.lastTasks.set(id, hash)
      changed[id] = value
    }
    const boardsHash = JSON.stringify(listed.boards)
    const boards = boardsHash === client.lastBoards ? undefined : listed.boards
    client.lastBoards = boardsHash
    if (!boards && Object.keys(changed).length === 0) return null
    return { ok: true, at: isoNow(), project: entry.projectRoot, ...(boards ? { boards } : {}), ...(Object.keys(changed).length ? { tasks: changed } : {}) }
  }

  async refresh(force = false) {
    if (this.closed || this.refreshing || this.clients.size === 0) return
    this.refreshing = true
    try {
      for (const client of this.clients) {
        if (client.tasks === null) {
          const value = await this.snapshot(null, client.project)
          const frame = JSON.stringify(value)
          if (force || frame !== this.lastFrame) {
            this.lastFrame = frame
            this.writeFrame(client, value)
          }
        } else {
          const frame = await this.frameFor(client)
          if (frame) this.writeMulti(client, frame)
        }
        const now = Date.now()
        if (now - client.lastHeartbeat >= client.heartbeatMs) {
          try {
            client.res.write(': ping\n\n')
            client.lastHeartbeat = now
          } catch {
            this.removeClient(client)
          }
        }
      }
      if (!this.watcher) this.armWatcher()
    } catch (error) {
      const value = failure(error?.code || 1, error?.message || error)
      for (const client of this.clients) {
        if (client.tasks === null) this.writeFrame(client, value)
        else this.writeMulti(client, value)
      }
    } finally {
      this.refreshing = false
    }
  }

  subscribe(res, { heartbeatMs = 15000, tasks, project } = {}) {
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
        tasks: tasks === undefined || tasks === null || tasks === '' ? null : normaliseTasks(tasks),
        project: project || null,
        lastBoards: null,
        lastTasks: new Map(),
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
      if (client.tasks === null) {
        void this.snapshot(null, client.project).then((value) => {
          if (!this.clients.has(client)) return
          this.lastFrame = JSON.stringify(value)
          this.writeFrame(client, value)
        }).catch((error) => this.writeFrame(client, failure(error?.code || 1, error?.message || error)))
      } else {
        void this.frameFor(client).then((frame) => {
          if (!this.clients.has(client) || !frame) return
          this.writeMulti(client, frame)
        }).catch((error) => this.writeMulti(client, failure(error?.code || 1, error?.message || error)))
      }
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
