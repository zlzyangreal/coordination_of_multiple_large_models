import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { WorkflowBridge } from '../lib/workflow.js'

function board(taskId = 't1') {
  return {
    task_id: taskId,
    title: 'demo',
    status: 'running',
    revision: 3,
    chain: 'ok',
    can_deliver: false,
    progress: { done: 1, total: 2 },
    nodes: [{ id: 'n1', title: 'a', kind: 'work', status: 'done', attempt: 1, ready: false }],
  }
}

function engineReturning(payload, capture = []) {
  return {
    spawn: async (argv, options) => {
      capture.push({ argv, options })
      return { ok: true, code: '0', stdout: JSON.stringify(payload), stderr: '' }
    },
  }
}

async function workspace() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-workflow-'))
  await mkdir(path.join(root, 'scripts'), { recursive: true })
  await writeFile(path.join(root, 'scripts', 'orchestration.py'), '# stub\n', 'utf8')
  await mkdir(path.join(root, '_work', 'orchestration'), { recursive: true })
  return root
}

function fakeResponse() {
  const chunks = []
  const headers = {}
  return {
    chunks,
    headers,
    statusCode: 0,
    ended: false,
    setHeader(key, value) { headers[key] = value },
    flushHeaders() {},
    write(chunk) { chunks.push(String(chunk)) },
    on() {},
    end() { this.ended = true },
    req: { on() {}, off() {} },
    off() {},
  }
}

const tick = () => new Promise((resolve) => setImmediate(resolve))

async function waitFor(predicate, timeoutMs = 2000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return true
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  return predicate()
}

test('workflow: an unknown op is refused without spawning python', async () => {
  const root = await workspace()
  const calls = []
  const bridge = new WorkflowBridge({ ctx: {}, engine: engineReturning({ ok: true }, calls), projectRoot: root, homeRoot: root, root })
  const value = await bridge.run('explode', {})
  assert.equal(value.ok, false)
  assert.equal(value.code, 2)
  assert.equal(calls.length, 0)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: result maps child fields and the no-subagent flag to fixed argv', async () => {
  const root = await workspace()
  const calls = []
  const bridge = new WorkflowBridge({ ctx: {}, engine: engineReturning({ ok: true, code: 0, message: 'ok' }, calls), projectRoot: root, homeRoot: root, root })
  await bridge.invoke('result', {
    task: 't1', node: 'n1', summary: 'done', child_session: 'c1', child_model: 'm1',
    no_subagent: true, expected_revision: 2,
  })
  const argv = calls[0].argv
  assert.equal(argv[0], path.join(root, 'scripts', 'orchestration.py'))
  assert.equal(argv[1], 'result')
  assert.deepEqual(argv.slice(2, 4), ['--project', root])
  assert.ok(argv.includes('--child-session'))
  assert.ok(argv.includes('--child-model'))
  assert.ok(argv.includes('--no-subagent'))
  assert.ok(argv.includes('--receipts'))
  assert.ok(argv.includes('--json'))
  assert.ok(argv.includes('--expected-revision'))
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: a missing python interpreter degrades to core_unavailable', async () => {
  const root = await workspace()
  const engine = { spawn: async () => ({ ok: false, code: 'ENOENT', stdout: '', stderr: 'no python' }) }
  const bridge = new WorkflowBridge({ ctx: {}, engine, projectRoot: root, homeRoot: root, root })
  const value = await bridge.run('state', { task: 't1' })
  assert.equal(value.ok, false)
  assert.equal(value.code, 'core_unavailable')
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: invalid stdout is reported, not thrown', async () => {
  const root = await workspace()
  const engine = { spawn: async () => ({ ok: true, code: '0', stdout: 'not json', stderr: '' }) }
  const bridge = new WorkflowBridge({ ctx: {}, engine, projectRoot: root, homeRoot: root, root })
  const value = await bridge.run('start', { task: 't1', node: 'n1', expected_revision: 1 })
  assert.equal(value.ok, false)
  assert.match(value.message, /invalid workflow response/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: snapshot returns null when no board exists', async () => {
  const root = await workspace()
  const bridge = new WorkflowBridge({
    ctx: {},
    engine: engineReturning({ ok: false, code: 2, message: 'board not found: t1' }),
    projectRoot: root, homeRoot: root, root,
  })
  assert.equal(await bridge.snapshot('t1'), null)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: snapshot returns the board and drops any models field', async () => {
  const root = await workspace()
  const payload = { ok: true, code: 0, message: 'ok', task: { ...board(), models: [{ model: 'x' }] } }
  const bridge = new WorkflowBridge({ ctx: {}, engine: engineReturning(payload), projectRoot: root, homeRoot: root, root })
  const value = await bridge.snapshot('t1')
  assert.equal(value.task_id, 't1')
  assert.equal(value.models, undefined)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: run("state") returns a null task when nothing is planned', async () => {
  const root = await workspace()
  const bridge = new WorkflowBridge({
    ctx: {},
    engine: engineReturning({ ok: false, code: 2, message: 'board not found: t1' }),
    projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.run('state', { task: 't1' })
  assert.deepEqual(value, { ok: true, code: 0, message: 'ok', task: null })
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: models() lists live llm routes', async () => {
  const root = await workspace()
  const llm = {
    listProviders: async () => [{ provider: 'deepseek-official' }, { provider: 'openai-gateway' }],
    listModels: async (provider) => [{ id: `${provider}-m1`, name: 'Model 1' }],
  }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'llm' ? llm : undefined) },
    engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.models()
  assert.equal(value.ok, true)
  assert.deepEqual(value.routes.map((item) => `${item.provider}/${item.model}`),
    ['deepseek-official/deepseek-official-m1', 'openai-gateway/openai-gateway-m1'])
  assert.equal(value.routes[0].lane, 'dsh')
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: a missing llm service explains itself instead of faking a table', async () => {
  const root = await workspace()
  const bridge = new WorkflowBridge({ ctx: { get: () => undefined }, engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root })
  const value = await bridge.models()
  assert.equal(value.ok, false)
  assert.equal(value.routes.length, 0)
  assert.match(value.reason, /model catalog unavailable/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: a throwing adapter degrades the catalog', async () => {
  const root = await workspace()
  const llm = {
    listProviders: async () => [{ provider: 'p' }],
    listModels: async () => { throw new Error('boom') },
  }
  const bridge = new WorkflowBridge({ ctx: { get: () => llm }, engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root })
  const value = await bridge.models()
  assert.equal(value.ok, false)
  assert.match(value.reason, /boom/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: subscribe emits SSE headers and an initial frame', async () => {
  const root = await workspace()
  await writeFile(path.join(root, '_work', 'orchestration', 't1.json'), '{"task_id":"t1"}', 'utf8')
  const payload = { ok: true, code: 0, message: 'ok', task: board() }
  const bridge = new WorkflowBridge({ ctx: {}, engine: engineReturning(payload), projectRoot: root, homeRoot: root, root })
  const res = fakeResponse()
  const value = bridge.subscribe(res, {})
  assert.equal(value.ok, true)
  assert.equal(res.headers['content-type'], 'text/event-stream')
  assert.equal(res.headers['cache-control'], 'no-cache')
  assert.ok(await waitFor(() => res.chunks.some((chunk) => chunk.startsWith('data: '))),
    `no initial frame within 2s: ${JSON.stringify(res.chunks)}`)
  const frame = JSON.parse(res.chunks.find((chunk) => chunk.startsWith('data: ')).slice(6))
  assert.equal(frame.ok, true)
  assert.equal(frame.task.task_id, 't1')
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: close ends subscribers and is idempotent', async () => {
  const root = await workspace()
  await writeFile(path.join(root, '_work', 'orchestration', 't1.json'), '{"task_id":"t1"}', 'utf8')
  const bridge = new WorkflowBridge({ ctx: {}, engine: engineReturning({ ok: true, task: board() }), projectRoot: root, homeRoot: root, root })
  const res = fakeResponse()
  bridge.subscribe(res, {})
  await bridge.close()
  assert.equal(res.ended, true)
  assert.equal((await bridge.close()).ok, true)
  await rm(root, { recursive: true, force: true })
})

test('workflow: verifyChild reports an unavailable service as unverified', async () => {
  const root = await workspace()
  const bridge = new WorkflowBridge({ ctx: { get: () => undefined }, engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root })
  const value = await bridge.verifyChild('s1', 'c1', 'm1')
  assert.equal(value.ok, false)
  assert.equal(value.verified, false)
  assert.match(value.reason, /unavailable/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: verifyChild refuses a child outside the session catalog', async () => {
  const root = await workspace()
  const subagent = { listChildren: async () => [{ childId: 'other', model: 'm1' }] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.verifyChild('s1', 'c1', 'm1')
  assert.equal(value.ok, false)
  assert.equal(value.verified, true)
  assert.match(value.reason, /not in this session/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: verifyChild refuses a child that ran a different model', async () => {
  const root = await workspace()
  const subagent = { listChildren: async () => [{ childId: 'c1', model: 'other-model' }] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.verifyChild('s1', 'c1', 'm1')
  assert.equal(value.ok, false)
  assert.equal(value.verified, true)
  assert.match(value.reason, /other-model/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: verifyChild accepts the assigned model', async () => {
  const root = await workspace()
  const subagent = { listChildren: async () => [{ childId: 'c1', model: 'm1' }] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.verifyChild('s1', 'c1', 'm1')
  assert.equal(value.ok, true)
  assert.equal(value.verified, true)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: verifyChild treats a child without a reported model as unverified', async () => {
  const root = await workspace()
  const subagent = { listChildren: async () => [{ childId: 'c1' }] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.verifyChild('s1', 'c1', 'm1')
  assert.equal(value.ok, false)
  assert.equal(value.verified, false)
  assert.match(value.reason, /no model/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: a result without a parent session is refused before python runs', async () => {
  const root = await workspace()
  const calls = []
  const subagent = { listChildren: async () => [{ childId: 'c1', model: 'm1' }] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }, calls), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.run('result', { task: 't1', node: 'n1', child_session: 'c1', child_model: 'm1' })
  assert.equal(value.ok, false)
  assert.match(value.message, /dispatch_unverified/)
  assert.equal(calls.length, 0)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: the artifact path is refused while a subagent service exists', async () => {
  const root = await workspace()
  const calls = []
  const subagent = { listChildren: async () => [] }
  const bridge = new WorkflowBridge({
    ctx: { get: (name) => (name === 'subagent' ? subagent : undefined) },
    engine: engineReturning({ ok: true }, calls), projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.run('result', { task: 't1', node: 'n1', artifact: 'a.md', no_subagent: true })
  assert.equal(value.ok, false)
  assert.match(value.message, /artifact results are allowed only/)
  assert.equal(calls.length, 0)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: the artifact path is admitted when no subagent service exists', async () => {
  const root = await workspace()
  const calls = []
  const bridge = new WorkflowBridge({
    ctx: { get: () => undefined },
    engine: engineReturning({ ok: true, code: 0, message: 'ok' }, calls),
    projectRoot: root, homeRoot: root, root,
  })
  const value = await bridge.run('result', { task: 't1', node: 'n1', artifact: 'a.md', no_subagent: true })
  assert.equal(value.ok, true)
  assert.equal(calls.length, 1)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: a result with neither child nor artifact is refused', async () => {
  const root = await workspace()
  const bridge = new WorkflowBridge({ ctx: { get: () => undefined }, engine: engineReturning({ ok: true }), projectRoot: root, homeRoot: root, root })
  const value = await bridge.run('result', { task: 't1', node: 'n1' })
  assert.equal(value.ok, false)
  assert.match(value.message, /verified child or an artifact receipt/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('workflow: an unwatchable board directory never throws into the host', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-workflow-bad-'))
  const bridge = new WorkflowBridge({
    ctx: { get: () => undefined },
    engine: engineReturning({ ok: true, code: 0, message: 'ok', task: board() }),
    projectRoot: root, homeRoot: root, root,
  })
  const res = fakeResponse()
  assert.equal(bridge.subscribe(res, {}).ok, true)
  await tick()
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})
