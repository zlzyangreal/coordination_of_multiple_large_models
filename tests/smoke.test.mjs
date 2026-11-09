import assert from 'node:assert/strict'
import { mkdtemp, rm, stat } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { apply } from '../lib/index.js'

function request(handler, { method = 'GET', url = '/', body = null, headers = {} } = {}) {
  const req = { method, url, headers: { host: '127.0.0.1:4321', ...headers } }
  if (body !== null) {
    req[Symbol.asyncIterator] = async function* iterator() { yield Buffer.from(JSON.stringify(body)) }
  }
  return new Promise((resolve, reject) => {
    const res = {
      statusCode: 0,
      headers: {},
      setHeader(key, value) { this.headers[key] = value },
      end(payload) { resolve({ status: this.statusCode, body: payload ? JSON.parse(payload) : null }) },
    }
    Promise.resolve(handler(req, res)).catch(reject)
  })
}

test('apply registers one section and one route when OFF; verify then ON installs the guard', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-smoke-'))
  const sections = []
  const tools = []
  const guards = []
  const routes = []
  const effects = []
  const ctx = {
    get(name) {
      if (name === 'profileContext') return { name: 'test', home: tmp, dir: path.join(tmp, 'profiles', 'test') }
      if (name === 'webServer') return this.webServer
      return undefined
    },
    systemPrompt: { section(section) { sections.push(section); return () => {} } },
    tools: {
      register(definition) { tools.push(definition); return () => {} },
      guard(fn) { guards.push(fn); return () => {} },
    },
    webServer: { register(route) { routes.push(route); return () => {} } },
    inject(deps, callback) { if (deps.includes('webServer')) callback(ctx) },
    effect(fn) { effects.push(fn); return () => {} },
  }
  try {
    await apply(ctx, {})
    assert.equal(sections.length, 1)
    assert.equal(routes.length, 1)
    assert.equal(tools.length, 0, 'OFF registers no tool')
    assert.equal(guards.length, 0, 'OFF registers no guard')
    assert.equal(await stat(path.join(tmp, 'coordination', 'state', 'test.json')).then(() => true), true)

    const route = routes[0]
    const initial = await request(route.handler, { url: '/_multimodel/status' })
    assert.equal(initial.status, 200)
    assert.equal(initial.body.switch.pending, true, 'first install needs verification')
    const token = initial.body.csrfToken
    assert.ok(token)

    const verified = await request(route.handler, {
      method: 'POST', url: '/_multimodel/verify',
      headers: { 'content-type': 'application/json', 'x-coord-token': token },
      body: { activate: true },
    })
    assert.equal(verified.status, 200)
    assert.equal(verified.body.result.ok, true, JSON.stringify(verified.body.result))
    assert.equal(verified.body.result.activated, true)

    const toggled = await request(route.handler, {
      method: 'POST', url: '/_multimodel/toggle',
      headers: { 'content-type': 'application/json', 'x-coord-token': token },
      body: { enabled: true },
    })
    assert.equal(toggled.status, 200, JSON.stringify(toggled.body))
    assert.equal(toggled.body.result.enabled, true)
    assert.equal(tools.length, 1, 'ON registers the coordination tool')
    assert.equal(guards.length, 1, 'ON registers the guard')

    const tool = tools[0]
    const session = { agent: { session: { id: 'session-1' } } }
    const status = await tool.execute({ action: 'status' }, session)
    assert.equal(status.ok, true)
    const denied = guards[0]({ name: 'bash', arguments: {}, agent: session.agent })
    assert.match(String(denied), /contract/)
    const ack = await tool.execute({ action: 'contract' }, session)
    assert.equal(ack.ok, true, ack.result)
    assert.equal(guards[0]({ name: 'bash', arguments: {}, agent: session.agent }), undefined)
    const other = guards[0]({ name: 'bash', arguments: {}, agent: { session: { id: 'session-2' } } })
    assert.match(String(other), /contract/)
  } finally {
    for (const effect of effects) {
      const dispose = effect()
      if (typeof dispose === 'function') await dispose()
    }
    await rm(tmp, { recursive: true, force: true })
  }
})
