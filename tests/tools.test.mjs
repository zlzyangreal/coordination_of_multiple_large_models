import assert from 'node:assert/strict'
import test from 'node:test'
import { buildCoordinationTool, registerContractGuard } from '../lib/tools.js'

function makeState() {
  const state = {
    acked: false,
    available: () => true,
    isAcked: () => state.acked,
    ackContract: async () => { state.acked = true; return { epoch: 1 } },
  }
  return state
}

function makeTool(overrides = {}) {
  const deps = {
    state: makeState(),
    contract: { ok: true, hash: 'hash', ruleCount: 40, text: 'rules', errors: [], version: '3.0.0' },
    logs: { record: async () => null, list: async () => [], summary: () => ({ counts: {} }), markStatus: async () => ({}) },
    status: async () => ({ ok: true }),
    extensions: { list: async () => [] },
    engine: { probe: async () => ({ available: true }), runPlanGate: async () => ({ ok: true }) },
    ...overrides,
  }
  return { tool: buildCoordinationTool({ deps }), deps }
}

test('guard allows status and contract before ack, denies other tools', () => {
  const state = makeState()
  const contract = { ok: true, hash: 'hash', ruleCount: 40, text: 'rules', errors: [] }
  const guards = []
  registerContractGuard({ tools: { guard: (fn) => { guards.push(fn); return () => {} } } }, { state, contract })
  const guard = guards[0]
  const exec = (name, agent = { session: { id: 's1' } }, args = {}) => ({ name, agent, arguments: args })
  assert.equal(guard(exec('coordination', { session: { id: 's1' } }, { action: 'status' })), undefined)
  assert.equal(guard(exec('coordination', { session: { id: 's1' } }, { action: 'contract' })), undefined)
  assert.match(String(guard(exec('bash'))), /contract/)
  state.acked = true
  assert.equal(guard(exec('bash')), undefined)
})

test('invalid contract denies everything except status', () => {
  const state = makeState()
  state.acked = true
  const contract = { ok: false, hash: '', ruleCount: 0, text: '', errors: ['bad'] }
  const guards = []
  registerContractGuard({ tools: { guard: (fn) => { guards.push(fn); return () => {} } } }, { state, contract })
  const guard = guards[0]
  assert.match(String(guard({ name: 'coordination', arguments: { action: 'contract' }, agent: { session: { id: 's' } } })), /CONTRACT INVALID/)
  assert.match(String(guard({ name: 'bash', arguments: {}, agent: { session: { id: 's' } } })), /CONTRACT INVALID/)
  assert.equal(guard({ name: 'coordination', arguments: { action: 'status' }, agent: { session: { id: 's' } } }), undefined)
})

test('a session without session.id cannot acknowledge or inherit', async () => {
  const { tool } = makeTool()
  const shared = { agent: { id: 'shared-agent' } }
  const answer = await tool.execute({ action: 'contract' }, shared)
  assert.equal(answer.ok, false)
  assert.match(answer.result, /session_identity_unavailable/)
  const state = makeState()
  const contract = { ok: true, hash: 'hash', ruleCount: 40, text: 'rules', errors: [] }
  const guards = []
  registerContractGuard({ tools: { guard: (fn) => { guards.push(fn); return () => {} } } }, { state, contract })
  assert.match(String(guards[0]({ name: 'bash', arguments: {}, agent: { id: 'shared-agent' } })), /session_identity_unavailable/)
})

test('tool contract acknowledges only a real session and then allows another tool', async () => {
  const { tool, deps } = makeTool()
  const session = { agent: { session: { id: 'session-1' } } }
  const ack = await tool.execute({ action: 'contract' }, session)
  assert.equal(ack.ok, true, ack.result)
  assert.equal(deps.state.acked, true)
  const status = await tool.execute({ action: 'status' }, session)
  assert.equal(status.ok, true)
})
