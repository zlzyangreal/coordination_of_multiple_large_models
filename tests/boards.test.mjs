import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm, utimes } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { BoardIndex, boardRootFor, deriveActivity, summariseBoard } from '../lib/board-index.js'

function board(overrides = {}) {
  return {
    task_id: 'demo',
    title: '季度报告',
    status: 'running',
    revision: 7,
    created_at: '2026-11-17T20:00:00+08:00',
    nodes: [
      { id: 'n1', title: '拆解', kind: 'work', status: 'done', attempt: 1, updated_at: '2026-11-17T20:00:10+08:00', depends_on: [] },
      { id: 'n2', title: '写作', kind: 'work', status: 'running', attempt: 1, updated_at: '2026-11-17T20:00:20+08:00', depends_on: ['n1'], assignee: { lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash' } },
      { id: 'n3', title: '校对', kind: 'work', status: 'pending', attempt: 1, updated_at: '2026-11-17T20:00:20+08:00', depends_on: ['n2'] },
    ],
    history: [
      { at: '2026-11-17T20:00:05+08:00', op: 'init', detail: '季度报告', revision: 1 },
      { at: '2026-11-17T20:00:20+08:00', op: 'start', detail: 'node=n2 attempt=1', revision: 7 },
    ],
    ...overrides,
  }
}

test('boards: a summary carries progress, active nodes and the newest stamp', () => {
  const summary = summariseBoard(board(), 1234)
  assert.equal(summary.task_id, 'demo')
  assert.equal(summary.title, '季度报告')
  assert.equal(summary.status, 'running')
  assert.equal(summary.revision, 7)
  assert.deepEqual(summary.progress, { done: 1, total: 3 })
  assert.deepEqual(summary.active, ['n2'])
  assert.equal(summary.updated_at, '2026-11-17T20:00:20+08:00')
  assert.equal(summary.mtime, 1234)
})

test('boards: a board without a title falls back to its id', () => {
  const summary = summariseBoard({ task_id: 't9', nodes: [] })
  assert.equal(summary.title, 't9')
  assert.deepEqual(summary.progress, { done: 0, total: 0 })
  assert.equal(summary.updated_at, null)
})

test('boards: activity names the running node, the blocked node and the last event', () => {
  const now = Date.parse('2026-11-17T20:01:20+08:00')
  const activity = deriveActivity(board(), now)
  assert.equal(activity.current.length, 1)
  assert.equal(activity.current[0].id, 'n2')
  assert.equal(activity.current[0].assignee.model, 'deepseek-flash')
  assert.equal(activity.current[0].stale_seconds, 60)
  assert.equal(activity.blocked.length, 1)
  assert.equal(activity.blocked[0].id, 'n3')
  assert.match(activity.blocked[0].reason, /n2\(running\)/)
  assert.equal(activity.last_event.op, 'start')
  assert.equal(activity.stale_seconds, 60)
})

test('boards: the index reads json only, sorts active boards first and skips broken files', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-boards-'))
  const dir = boardRootFor(root)
  await mkdir(dir, { recursive: true })
  await writeFile(path.join(dir, 'done.json'), JSON.stringify(board({ task_id: 'done', status: 'delivered' })), 'utf8')
  await writeFile(path.join(dir, 'fail.json'), JSON.stringify(board({ task_id: 'fail', status: 'failed' })), 'utf8')
  await writeFile(path.join(dir, 'live.json'), JSON.stringify(board({ task_id: 'live', status: 'running' })), 'utf8')
  await writeFile(path.join(dir, 'broken.json'), 'not json', 'utf8')
  const index = new BoardIndex()
  const listed = await index.list(root)
  assert.equal(listed.root, dir)
  assert.deepEqual(listed.boards.map((item) => item.task_id), ['fail', 'live', 'done'])
  assert.equal(listed.boards.length, 3)
  await rm(root, { recursive: true, force: true })
})

test('boards: an unchanged file is served from cache, a touched one is re-read', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-boards-'))
  const dir = boardRootFor(root)
  await mkdir(dir, { recursive: true })
  const file = path.join(dir, 'demo.json')
  await writeFile(file, JSON.stringify(board({ revision: 7 })), 'utf8')
  const index = new BoardIndex()
  const first = await index.list(root)
  assert.equal(first.boards[0].revision, 7)
  const second = await index.list(root)
  assert.equal(second.boards[0].revision, 7)
  await writeFile(file, JSON.stringify(board({ revision: 9 })), 'utf8')
  const later = new Date(Date.now() + 5000)
  await utimes(file, later, later)
  const third = await index.list(root)
  assert.equal(third.boards[0].revision, 9)
  await rm(root, { recursive: true, force: true })
})

test('boards: a missing directory is an empty list, not an error', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-boards-'))
  const index = new BoardIndex()
  const listed = await index.list(root)
  assert.deepEqual(listed.boards, [])
  await rm(root, { recursive: true, force: true })
})
