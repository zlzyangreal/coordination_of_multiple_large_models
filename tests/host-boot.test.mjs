import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { apply } from '../lib/index.js'

function makeCtx(tmp, { webServer, late = false } = {}) {
  const injections = []
  const ctx = {
    get(name) {
      if (name === 'profileContext') return { name: 'boot', home: tmp, dir: path.join(tmp, 'profiles', 'boot') }
      if (name === 'webServer' && !late) return webServer
      return undefined
    },
    systemPrompt: { section() { return () => {} } },
    tools: { register() { return () => {} }, guard() { return () => {} } },
    effect() { return () => {} },
    inject(_deps, callback) { injections.push(callback); return () => {} },
  }
  return { ctx, injections }
}

async function readLogs(tmp) {
  const files = []
  async function walk(dir) {
    let entries = []
    try { entries = await readdir(dir, { withFileTypes: true }) } catch { return }
    for (const entry of entries) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) await walk(full)
      else if (entry.name.endsWith('.jsonl')) files.push(full)
    }
  }
  await walk(path.join(tmp, 'coordination'))
  let text = ''
  for (const file of files) text += await readFile(file, 'utf8')
  return text
}

test('a direct route registration failure is contained and logged while OFF', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-boot-'))
  try {
    const server = { register() { throw new Error('duplicate _multimodel route') } }
    const { ctx } = makeCtx(tmp, { webServer: server })
    await assert.doesNotReject(() => apply(ctx, {}))
    const logs = await readLogs(tmp)
    assert.match(logs, /plugin\.routes_failed/)
    assert.match(logs, /duplicate _multimodel route/)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('a late webServer callback throw is contained after apply settles', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-boot-'))
  try {
    const server = { register() { throw new Error('late duplicate route') } }
    const { ctx, injections } = makeCtx(tmp, { late: true })
    await assert.doesNotReject(() => apply(ctx, {}))
    assert.equal(injections.length, 1)
    const inner = { get: (name) => (name === 'webServer' ? server : undefined) }
    assert.doesNotThrow(() => injections[0](inner))
    await new Promise((resolve) => setTimeout(resolve, 30))
    const logs = await readLogs(tmp)
    assert.match(logs, /plugin\.routes_failed/)
    assert.match(logs, /late duplicate route/)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
