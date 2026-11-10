import { createHash } from 'node:crypto'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { isoLocal, localDay } from './paths.js'

const PRIORITIES = ['I0', 'I1', 'I2', 'I3']
const OPEN_STATUSES = ['open', 'investigating', 'planned', 'resolved', 'deferred', 'wont_fix']

function fingerprint(parts) {
  return createHash('sha256').update(parts.join('|'), 'utf8').digest('hex').slice(0, 16)
}

/** Remove secrets and private paths before anything reaches disk. */
export function redact(text) {
  let value = String(text ?? '')
  value = value.replace(/(?:sk|pk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}/g, '[redacted-key]')
  value = value.replace(/(Bearer)\s+[A-Za-z0-9._~+/=-]+/gi, '$1 [redacted]')
  value = value.replace(/((?:api[_-]?key|token|cookie|authorization|secret)\s*[:=]\s*)\S+/gi, '$1[redacted]')
  value = value.replace(/[A-Za-z]:\\Users\\[^\\\s]+/g, 'C:\\Users\\<user>')
  value = value.replace(/\/Users\/[^/\s]+/g, '/Users/<user>')
  value = value.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '<email>')
  return value.length > 800 ? `${value.slice(0, 797)}...` : value
}

function defaultPriority(eventType) {
  if (/corrupt|contract\.invalid|gate\.unavailable|install\.failed|verify\.failed|state\./i.test(eventType)) return 'I0'
  if (/fallback|retry|timeout|quota|denied|fallback/i.test(eventType)) return 'I1'
  if (/dedupe|slow|waste/i.test(eventType)) return 'I2'
  return 'I3'
}

/** Local-only JSONL store with daily files, dedupe index, rotation and retention. */
export class LogStore {
  constructor({ root, profile, projectId, version, enabled = false, maxBytes = 4 * 1024 * 1024, retentionDays = 30, log = null }) {
    this.root = root
    this.profile = profile
    this.projectId = projectId
    this.version = version
    this.collectionEnabled = enabled
    this.maxBytes = maxBytes
    this.retentionDays = retentionDays
    this.log = log
    this.indexFile = path.join(root, 'index', `${profile}.json`)
    this.dir = path.join(root, 'logs', profile)
    this.fallbackFile = path.join(root, 'fallback', `${profile}.jsonl`)
    this.index = { version: 1, issues: {} }
    this.queue = Promise.resolve()
    this.logWriteFailed = false
    this.fallbackWriteFailed = false
    this.indexWriteFailed = false
    this.initError = null
    this.ready = this.init().catch((error) => {
      this.logWriteFailed = true
      this.initError = String(error?.message || error).slice(0, 240)
    })
  }

  async init() {
    try {
      await fs.mkdir(this.dir, { recursive: true })
      await fs.mkdir(path.dirname(this.indexFile), { recursive: true })
      await fs.mkdir(path.dirname(this.fallbackFile), { recursive: true })
    } catch (error) {
      this.logWriteFailed = true
      this.initError = String(error?.message || error).slice(0, 240)
      return
    }
    try {
      const text = await fs.readFile(this.indexFile, 'utf8')
      const parsed = JSON.parse(text.replace(/^\uFEFF/, ''))
      if (parsed && parsed.issues) this.index = parsed
    } catch { /* first run or unreadable index: keep the empty one */ }
    await this.prune().catch(() => {})
  }

  setCollection(enabled) {
    this.collectionEnabled = Boolean(enabled)
  }

  async prune() {
    let names = []
    try { names = await fs.readdir(this.dir) } catch { return }
    const cutoff = Date.now() - this.retentionDays * 24 * 60 * 60 * 1000
    for (const name of names) {
      const match = name.match(/_(\d{4}-\d{2}-\d{2})\.jsonl(?:\.\d+)?$/)
      if (!match) continue
      const stamp = Date.parse(`${match[1]}T00:00:00`)
      if (Number.isFinite(stamp) && stamp < cutoff) {
        await fs.rm(path.join(this.dir, name), { force: true }).catch(() => {})
      }
    }
  }

  async record(input = {}) {
    await this.ready
    const kind = input.kind === 'runtime' ? 'runtime' : 'optimization'
    const eventType = String(input.eventType || 'event')
    const offAllowed = /^(plugin\.|state\.|contract\.|verify\.)/.test(eventType)
    if (!this.collectionEnabled && !offAllowed) return null
    const priority = PRIORITIES.includes(input.priority) ? input.priority : defaultPriority(eventType)
    const summary = redact(input.summary ?? '')
    const issueId = input.issueId || fingerprint([kind, eventType, input.code || '', summary, this.projectId])
    const now = isoLocal()
    const fingerprintParts = [kind, eventType, input.code || '', summary, this.projectId]
    const record = {
      timestamp: now,
      level: priority,
      event_type: eventType,
      plugin_version: this.version,
      profile: this.profile,
      project_id: this.projectId,
      task_id: input.taskId ? redact(input.taskId) : null,
      phase: input.phase ? redact(input.phase) : null,
      model: input.model ? redact(input.model) : null,
      provider: input.provider ? redact(input.provider) : null,
      duration_ms: Number.isFinite(input.durationMs) ? input.durationMs : null,
      error_code: input.code ? redact(input.code) : null,
      summary,
      impact: input.impact ? redact(input.impact) : null,
      suggested_action: input.suggestedAction ? redact(input.suggestedAction) : null,
      status: OPEN_STATUSES.includes(input.status) ? input.status : 'open',
      issue_id: issueId,
      priority,
      priority_reason: input.priorityReason ? redact(input.priorityReason) : `classified from ${eventType}`,
      frequency: 1,
      confidence: input.confidence || 'medium',
      last_seen: now,
      event_id: `E-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      fingerprint: fingerprintParts.join('|'),
      missing: input.missing || [],
    }
    const previous = this.index.issues[issueId]
    const repeat = Boolean(previous)
    if (previous) {
      previous.frequency = (previous.frequency || 1) + 1
      previous.last_seen = now
      previous.priority = priority === 'I0' ? 'I0' : (previous.priority || priority)
      if (priority === 'I0') previous.priority = 'I0'
      if (input.status && OPEN_STATUSES.includes(input.status)) previous.status = input.status
      record.frequency = previous.frequency
    } else {
      this.index.issues[issueId] = {
        issue_id: issueId,
        kind,
        priority,
        event_type: eventType,
        summary,
        status: record.status,
        first_seen: now,
        last_seen: now,
        frequency: 1,
        priority_reason: record.priority_reason,
        impact: record.impact,
        suggested_action: record.suggested_action,
      }
    }
    const shouldAppend = priority === 'I0' || !repeat
    if (shouldAppend) {
      const line = `${JSON.stringify(record)}\n`
      try {
        await this.enqueue(() => this.append(kind, line))
      } catch (error) {
        this.logWriteFailed = true
        try {
          await this.fallback(line, error)
        } catch {
          this.fallbackWriteFailed = true
        }
      }
    }
    this.scheduleIndexWrite()
    return record
  }

  async append(kind, line) {
    const file = path.join(this.dir, `${kind === 'runtime' ? 'runtime_error' : 'optimization'}_${localDay()}.jsonl`)
    try {
      let size = 0
      try { size = (await fs.stat(file)).size } catch { /* new file */ }
      if (size + Buffer.byteLength(line) > this.maxBytes) {
        await fs.rm(`${file}.1`, { force: true })
        await fs.rename(file, `${file}.1`).catch(() => {})
      }
      await fs.appendFile(file, line, 'utf8')
    } catch (error) {
      this.logWriteFailed = true
      await this.fallback(line, error)
    }
  }

  async fallback(line, error) {
    try {
      await fs.mkdir(path.dirname(this.fallbackFile), { recursive: true })
      await fs.appendFile(this.fallbackFile, line, 'utf8')
    } catch {
      this.fallbackWriteFailed = true
      this.pending = this.pending || []
      this.pending.push(line)
      if (this.pending.length > 200) this.pending.shift()
      // The caller never sees a throw: logging must not break a task.
      void error
    }
  }

  enqueue(task) {
    const run = this.queue.then(task)
    this.queue = run.then(() => undefined, () => undefined)
    return run
  }

  scheduleIndexWrite() {
    if (this.indexTimer) return
    this.indexTimer = setTimeout(() => {
      this.indexTimer = null
      this.writeIndex().catch(() => { this.indexWriteFailed = true })
    }, 400)
    this.indexTimer.unref?.()
  }

  async writeIndex() {
    try {
      const tmp = `${this.indexFile}.${process.pid}.tmp`
      await fs.writeFile(tmp, `${JSON.stringify(this.index, null, 2)}\n`, 'utf8')
      await fs.rename(tmp, this.indexFile)
      this.indexWriteFailed = false
    } catch (error) {
      this.indexWriteFailed = true
      throw error
    }
  }

  async list({ limit = 50, priority = null, date = null, status = null } = {}) {
    await this.ready
    await this.queue
    const day = date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : localDay()
    const wanted = priority ? new Set(String(priority).split(',').map((item) => item.trim()).filter(Boolean)) : null
    const rows = []
    for (const kind of ['runtime_error', 'optimization']) {
      const file = path.join(this.dir, `${kind}_${day}.jsonl`)
      let text = ''
      try { text = await fs.readFile(file, 'utf8') } catch { continue }
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim()) continue
        try {
          const row = JSON.parse(line)
          if (wanted && !wanted.has(row.priority)) continue
          if (status && row.status !== status) continue
          rows.push(row)
        } catch { /* skip a torn line */ }
      }
    }
    rows.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
    return rows.slice(0, Math.max(1, Math.min(500, Number(limit) || 50)))
  }

  async markStatus({ issueId, status, note = null }) {
    await this.ready
    if (!OPEN_STATUSES.includes(status)) throw new Error(`invalid status: ${status}`)
    const issue = this.index.issues[issueId]
    if (!issue) throw new Error(`unknown issue_id: ${issueId}`)
    issue.status = status
    issue.note = note ? redact(note) : issue.note || null
    issue.updated_at = isoLocal()
    let persisted = true
    try {
      await this.writeIndex()
    } catch {
      persisted = false
      this.indexWriteFailed = true
    }
    await this.record({
      kind: issue.kind, priority: 'I3', eventType: 'log.status', summary: `issue ${issueId} marked ${status}`,
      status, issueId: `${issueId}-status-${Date.now()}`, suggestedAction: null,
    }).catch(() => {})
    return { ...issue, persisted }
  }

  summary() {
    const counts = { I0: 0, I1: 0, I2: 0, I3: 0 }
    for (const issue of Object.values(this.index.issues)) {
      if (counts[issue.priority] !== undefined) counts[issue.priority] += 1
    }
    return {
      counts,
      root: this.dir,
      fallbackFile: this.fallbackFile,
      collectionEnabled: this.collectionEnabled,
      logWriteFailed: this.logWriteFailed,
      fallbackWriteFailed: this.fallbackWriteFailed,
      indexWriteFailed: this.indexWriteFailed,
      initError: this.initError,
      top: Object.values(this.index.issues)
        .sort((a, b) => (PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority)) || String(b.last_seen).localeCompare(String(a.last_seen)))
        .slice(0, 10),
    }
  }

  async flush() {
    await this.ready
    await this.queue
    if (this.indexTimer) {
      clearTimeout(this.indexTimer)
      this.indexTimer = null
    }
    await this.writeIndex().catch(() => { this.indexWriteFailed = true })
  }
}
