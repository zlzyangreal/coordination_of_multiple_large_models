import { readdir, readFile, stat } from 'node:fs/promises'
import path from 'node:path'

const ACTIVE = new Set(['running', 'review'])

export function boardRootFor(projectRoot) {
  return path.join(projectRoot, '_work', 'orchestration')
}

function stamp(node) {
  return node?.updated_at || null
}

/** Cheap, python-free view of one board file: enough for a task card. */
export function summariseBoard(value, mtimeMs = 0) {
  const nodes = Array.isArray(value?.nodes) ? value.nodes : []
  const done = nodes.filter((node) => node.status === 'done').length
  const active = nodes.filter((node) => ACTIVE.has(node.status)).map((node) => String(node.id))
  const history = Array.isArray(value?.history) ? value.history : []
  const last = history.length ? history[history.length - 1] : null
  const stamps = nodes.map(stamp).filter(Boolean)
  if (last?.at) stamps.push(last.at)
  stamps.sort()
  const progress = value?.progress && Number.isFinite(Number(value.progress.total))
    ? { done: Number(value.progress.done) || done, total: Number(value.progress.total) || nodes.length }
    : { done, total: nodes.length }
  return {
    task_id: String(value?.task_id || ''),
    title: String(value?.title || value?.task_id || ''),
    status: String(value?.status || 'planning'),
    revision: Number(value?.revision) || 1,
    progress,
    active,
    updated_at: stamps.length ? stamps[stamps.length - 1] : (value?.created_at || null),
    can_deliver: value?.can_deliver === true,
    legacy: false,
    mtime: Number(mtimeMs) || 0,
  }
}

/**
 * Board index over one project root. Reads JSON only (no python) and caches by
 * mtime+size, so a 1s tick over N boards stays cheap.
 */
export class BoardIndex {
  constructor() {
    this.cache = new Map()
  }

  async files(projectRoot) {
    const root = boardRootFor(projectRoot)
    try {
      const entries = await readdir(root, { withFileTypes: true })
      return entries
        .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
        .map((entry) => path.join(root, entry.name))
    } catch (error) {
      if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') return []
      throw error
    }
  }

  async readFile(file) {
    let info
    try {
      info = await stat(file)
    } catch (error) {
      if (error?.code === 'ENOENT') return null
      throw error
    }
    const cached = this.cache.get(file)
    if (cached && cached.mtimeMs === info.mtimeMs && cached.size === info.size) return cached.value
    let value = null
    try {
      const parsed = JSON.parse(await readFile(file, 'utf8'))
      value = summariseBoard(parsed, info.mtimeMs)
    } catch {
      value = null
    }
    this.cache.set(file, { mtimeMs: info.mtimeMs, size: info.size, value })
    return value
  }

  /** Summaries, active boards first, then most recently updated. */
  async list(projectRoot) {
    const files = await this.files(projectRoot)
    const rows = (await Promise.all(files.map((file) => this.readFile(file)))).filter(Boolean)
    const rank = { failed: 0, running: 1, reviewing: 1, planning: 2, delivered: 3 }
    rows.sort((left, right) => {
      const byStatus = (rank[left.status] ?? 4) - (rank[right.status] ?? 4)
      if (byStatus !== 0) return byStatus
      return String(right.updated_at || '').localeCompare(String(left.updated_at || ''))
    })
    return { root: boardRootFor(projectRoot), boards: rows }
  }

  /** Raw board JSON for one task id (used by the revision gate). */
  async read(task, projectRoot) {
    if (!task) return null
    const file = path.join(boardRootFor(projectRoot), `${String(task).replace(/[^A-Za-z0-9._-]/g, '_')}.json`)
    try {
      return JSON.parse(await readFile(file, 'utf8'))
    } catch {
      return null
    }
  }
}

function secondsSince(value, now) {
  const at = Date.parse(String(value || ''))
  if (!Number.isFinite(at)) return null
  return Math.max(0, Math.round((now - at) / 1000))
}

/**
 * What the board is doing right now, derived from the state snapshot only:
 * no new board field, no python change.
 */
export function deriveActivity(board, now = Date.now()) {
  const nodes = Array.isArray(board?.nodes) ? board.nodes : []
  const byId = new Map(nodes.map((node) => [String(node.id), node]))
  const current = nodes
    .filter((node) => ACTIVE.has(node.status))
    .map((node) => ({
      id: String(node.id),
      title: node.title || node.id,
      status: node.status,
      assignee: node.assignee || null,
      child_session: node.result?.child_session || null,
      updated_at: stamp(node),
      stale_seconds: secondsSince(stamp(node), now),
    }))
  const blocked = nodes
    .filter((node) => node.status === 'pending' && node.ready !== true)
    .map((node) => {
      const deps = Array.isArray(node.depends_on) ? node.depends_on : []
      const pending = deps
        .map((id) => byId.get(String(id)))
        .filter((dep) => dep && dep.status !== 'done')
        .map((dep) => `${dep.id}(${dep.status})`)
      return {
        id: String(node.id),
        title: node.title || node.id,
        reason: pending.length ? `等待 ${pending.join('、')}` : '等待上游节点',
      }
    })
  const history = Array.isArray(board?.history) ? board.history : []
  const last = history.length ? history[history.length - 1] : null
  return {
    current,
    blocked,
    last_event: last ? { at: last.at, op: last.op, detail: last.detail, revision: last.revision } : null,
    stale_seconds: secondsSince(last?.at || board?.created_at, now),
  }
}
