import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { LogStore, redact } from '../lib/logs.js'

test('redaction removes secrets, tokens and private home paths', () => {
  const value = redact('key sk-abcdefgh12345678 Bearer abc.def C:\\Users\\alice\\secret a@b.com')
  assert.ok(!value.includes('sk-abcdefgh12345678'))
  assert.ok(!value.includes('abc.def'))
  assert.ok(!value.includes('alice'))
  assert.ok(!value.includes('a@b.com'))
})

test('I0 is never deduped; I1 repeats merge in the index', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-logs-'))
  try {
    const store = new LogStore({ root: tmp, profile: 'test', projectId: 'p', version: '3.0.0', enabled: true })
    await store.record({ kind: 'runtime', priority: 'I0', eventType: 'gate.unavailable', summary: 'no python' })
    await store.record({ kind: 'runtime', priority: 'I0', eventType: 'gate.unavailable', summary: 'no python' })
    const i0 = await store.list({ limit: 50 })
    assert.equal(i0.filter((row) => row.priority === 'I0').length, 2)
    for (let i = 0; i < 3; i += 1) {
      await store.record({ kind: 'optimization', priority: 'I1', eventType: 'retry', summary: 'same retry' })
    }
    const all = await store.list({ limit: 50 })
    assert.equal(all.filter((row) => row.event_type === 'retry').length, 1)
    const summary = store.summary()
    assert.equal(summary.counts.I1, 1)
    const issue = summary.top.find((item) => item.event_type === 'retry')
    assert.equal(issue.frequency, 3)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('OFF collects no optimization events but keeps lifecycle faults', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-logs-'))
  try {
    const store = new LogStore({ root: tmp, profile: 'test', projectId: 'p', version: '3.0.0', enabled: false })
    assert.equal(await store.record({ kind: 'optimization', eventType: 'slow', summary: 'x' }), null)
    assert.ok(await store.record({ kind: 'runtime', eventType: 'plugin.lifecycle', summary: 'loaded' }))
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('fallback file receives a line when the primary log path fails', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-logs-'))
  try {
    const store = new LogStore({ root: tmp, profile: 'test', projectId: 'p', version: '3.0.0', enabled: true })
    await store.ready
    const file = path.join(tmp, 'logs', 'test', 'blocked.jsonl')
    await writeFile(file, '', 'utf8')
    const original = store.append.bind(store)
    store.append = async () => { throw new Error('primary write failed') }
    await store.record({ kind: 'runtime', priority: 'I0', eventType: 'state.corrupt', summary: 'boom' })
    await store.flush()
    const fallback = path.join(tmp, 'fallback', 'test.jsonl')
    assert.equal(existsSync(fallback), true)
    assert.match(await readFile(fallback, 'utf8'), /boom/)
    store.append = original
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
