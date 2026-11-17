import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { WorkflowBridge } from '../lib/workflow.js'

const ROUTES = [
  { lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash', name: 'DeepSeek-V4.1-Flash' },
  { lane: 'dsh', provider: 'our-free-model', model: 'glm-5.3-flash-free', name: 'GLM 5.3 Flash' },
]

/** Board: n1 produces with deepseek-flash, r1 reviews n1. */
function statePayload(nodes) {
  return {
    ok: true,
    code: 0,
    message: 'ok',
    task: {
      task_id: 't1',
      title: 'demo',
      status: 'running',
      revision: 4,
      nodes: nodes || [
        { id: 'n1', kind: 'work', status: 'review', attempt: 1, depends_on: [], assignee: { lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash' } },
        { id: 'r1', kind: 'review', status: 'ready', attempt: 1, reviews: 'n1', depends_on: ['n1'] },
      ],
    },
  }
}

function verdicts() {
  return {
    ok: true,
    kind: 'review',
    producer: { id: 'deepseek-account/deepseek-flash', family: 'deepseek', known: true },
    options: [
      { ...ROUTES[0], family: 'deepseek', familyKnown: true, familySource: 'builtin', eligible: false, code: 'same_family', rule: '19', reason: '与产出方同族（deepseek）——规则 19：评审必须来自不同家族' },
      { ...ROUTES[1], family: 'glm', familyKnown: true, familySource: 'builtin', eligible: true, code: null, rule: null, reason: null },
    ],
  }
}

async function workspace() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'mmc-elig-'))
  await mkdir(path.join(root, 'scripts'), { recursive: true })
  await writeFile(path.join(root, 'scripts', 'orchestration.py'), '# stub\n', 'utf8')
  await writeFile(path.join(root, 'scripts', 'eligibility.py'), '# stub\n', 'utf8')
  await mkdir(path.join(root, '_work', 'orchestration'), { recursive: true })
  await writeFile(path.join(root, '_work', 'orchestration', 't1.json'), JSON.stringify({ task_id: 't1', revision: 4, nodes: [] }), 'utf8')
  return root
}

function engine(calls, { verdict = true } = {}) {
  return {
    spawn: async (argv) => {
      calls.push(argv)
      if (String(argv[0]).endsWith('eligibility.py')) {
        return { ok: true, code: '0', stdout: JSON.stringify(verdict ? verdicts() : { ok: false }), stderr: '' }
      }
      if (argv[1] === 'state') return { ok: true, code: '0', stdout: JSON.stringify(statePayload()), stderr: '' }
      return { ok: true, code: '0', stdout: JSON.stringify({ ok: true, code: 0, message: 'ok' }), stderr: '' }
    },
  }
}

function bridgeFor(root, calls, options = {}) {
  const llm = {
    listProviders: async () => [{ provider: 'deepseek-account' }, { provider: 'our-free-model' }],
    listModels: async (provider) => ROUTES.filter((route) => route.provider === provider).map((route) => ({ id: route.model, name: route.name })),
  }
  return new WorkflowBridge({
    ctx: { get: (name) => (name === 'llm' ? llm : undefined) },
    engine: engine(calls, options),
    projectRoot: root,
    homeRoot: root,
    root,
  })
}

test('rules: pinning the producer model to its own review node is refused with rule 19', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls)
  const value = await bridge.run('pin', { task: 't1', node: 'r1', lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash', expected_revision: 4 })
  assert.equal(value.ok, false)
  assert.equal(value.code, 'rule_violation')
  assert.equal(value.rule, '19')
  assert.match(value.reason, /同族/)
  assert.equal(calls.filter((argv) => argv[1] === 'pin').length, 0, 'python pin must not run')
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('rules: a different family is admitted and reaches the pin operation', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls)
  const value = await bridge.run('pin', { task: 't1', node: 'r1', lane: 'dsh', provider: 'our-free-model', model: 'glm-5.3-flash-free', expected_revision: 4 })
  assert.equal(value.ok, true)
  assert.equal(calls.filter((argv) => argv[1] === 'pin').length, 1)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('rules: a model outside the catalog is refused', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls)
  const value = await bridge.run('pin', { task: 't1', node: 'r1', lane: 'dsh', provider: 'nope', model: 'nope', expected_revision: 4 })
  assert.equal(value.ok, false)
  assert.equal(value.code, 'rule_violation')
  assert.match(value.reason, /目录/)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('rules: a work node is not constrained by independence', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls)
  const value = await bridge.run('pin', { task: 't1', node: 'n1', lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash', expected_revision: 4 })
  assert.equal(value.ok, true)
  assert.equal(calls.filter((argv) => argv[1] === 'pin').length, 1)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('rules: an unavailable gate degrades to "no verdict" instead of blocking every pin', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls, { verdict: false })
  const value = await bridge.run('pin', { task: 't1', node: 'r1', lane: 'dsh', provider: 'deepseek-account', model: 'deepseek-flash', expected_revision: 4 })
  assert.equal(value.ok, true)
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})

test('rules: listBoards/snapshotMany expose every board and only re-run python on change', async () => {
  const root = await workspace()
  const calls = []
  const bridge = bridgeFor(root, calls)
  const listed = await bridge.listBoards()
  assert.equal(listed.ok, true)
  assert.equal(listed.boards.length, 1)
  const first = await bridge.snapshotMany('all')
  assert.ok(first.t1)
  const stateCalls = () => calls.filter((argv) => argv[1] === 'state').length
  const before = stateCalls()
  await bridge.snapshotMany('all')
  assert.equal(stateCalls(), before, 'unchanged revision must not spawn python again')
  await writeFile(path.join(root, '_work', 'orchestration', 't1.json'), JSON.stringify({ task_id: 't1', revision: 5, nodes: [] }), 'utf8')
  await bridge.snapshotMany('all')
  assert.equal(stateCalls(), before + 1, 'a new revision must spawn once')
  await bridge.close()
  await rm(root, { recursive: true, force: true })
})
