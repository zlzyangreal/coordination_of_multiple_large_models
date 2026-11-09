export function sessionId(exec) {
  const value = exec?.agent?.session?.id
  return typeof value === 'string' && value.length > 0 ? value : null
}

const TOOL_PARAMETERS = {
  type: 'object',
  additionalProperties: false,
  required: ['action'],
  properties: {
    action: { type: 'string', enum: ['contract', 'status', 'gate', 'logs', 'extensions'] },
    kind: { type: 'string', enum: ['plan'] },
    project: { type: 'string' },
    limit: { type: 'integer', minimum: 1, maximum: 200 },
    priority: { type: 'string' },
    issueId: { type: 'string' },
    status: { type: 'string', enum: ['open', 'investigating', 'planned', 'resolved', 'deferred', 'wont_fix'] },
    note: { type: 'string' },
  },
}

const TOOL_OUTPUT = {
  type: 'object',
  additionalProperties: false,
  required: ['ok', 'action', 'result'],
  properties: {
    ok: { type: 'boolean' },
    action: { type: 'string' },
    result: { type: 'string' },
  },
}

function text(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

/** The single model-facing entry point while the switch is ON. */
export function buildCoordinationTool({ deps }) {
  return {
    name: 'coordination',
    description: 'Multi-model coordination control: acknowledge the rule contract, read status, run the plan gate, inspect logs, list declared optional extensions. Call action "contract" first; the host denies every other tool until the contract is acknowledged.',
    parameters: TOOL_PARAMETERS,
    output: {
      schema: TOOL_OUTPUT,
      render: (_args, value) => [{ type: 'text', text: String(value?.result ?? '') }],
    },
    async execute(args, exec) {
      const action = String(args.action)
      const sid = sessionId(exec)
      try {
        if (action === 'status') {
          return { ok: true, action, result: text(await deps.status()) }
        }
        if (!deps.state.available()) {
          return { ok: false, action, result: 'pending_verification: the running package must be verified and explicitly activated first' }
        }
        if (action === 'contract') {
          if (!deps.contract.ok) {
            return { ok: false, action, result: `contract_invalid: ${deps.contract.errors.join('; ')}` }
          }
          if (!sid) {
            await deps.logs.record({
              kind: 'runtime', priority: 'I0', eventType: 'contract.session_missing',
              summary: 'session identity was unavailable; contract acknowledgement refused',
              impact: 'controlled tools stay denied', suggestedAction: 'report the host bug; do not retry blindly',
            })
            return { ok: false, action, result: 'session_identity_unavailable' }
          }
          const receipt = await deps.state.ackContract(sid, deps.contract.hash)
          return {
            ok: true,
            action,
            result: `contract acknowledged for session ${sid} (${deps.contract.ruleCount} rules, hash ${deps.contract.hash.slice(0, 12)}, epoch ${receipt.epoch}). Proceed under the contract.`,
          }
        }
        if (!deps.contract.ok) {
          return { ok: false, action, result: `contract_invalid: ${deps.contract.errors.join('; ')}` }
        }
        if (!sid || !deps.state.isAcked(sid, deps.contract.hash)) {
          return { ok: false, action, result: 'contract_not_acknowledged: call coordination({action:"contract"}) first' }
        }
        if (action === 'gate') {
          if (args.kind !== 'plan') return { ok: false, action, result: 'unsupported gate kind; v1 supports only "plan"' }
          const outcome = await deps.engine.runPlanGate(args.project || undefined)
          return { ok: outcome.ok, action, result: text({ code: outcome.code, report: outcome.report, stderr: outcome.stderr || null }) }
        }
        if (action === 'logs') {
          if (args.issueId) {
            if (!args.status) return { ok: false, action, result: 'status is required with issueId' }
            const issue = await deps.logs.markStatus({ issueId: args.issueId, status: args.status, note: args.note || null })
            return { ok: true, action, result: text(issue) }
          }
          if (args.priority || args.limit) {
            const rows = await deps.logs.list({ limit: args.limit || 50, priority: args.priority || null })
            return { ok: true, action, result: text(rows) }
          }
          return { ok: true, action, result: text(deps.logs.summary()) }
        }
        if (action === 'extensions') {
          return { ok: true, action, result: text(await deps.extensions.list()) }
        }
        return { ok: false, action, result: `unknown action: ${action}` }
      } catch (error) {
        return { ok: false, action, result: `error: ${String(error?.message || error).slice(0, 300)}` }
      }
    },
  }
}

/**
 * Monotonic guard: while ON, nothing but `coordination status|contract` runs until the contract is
 * acknowledged. An invalid contract denies every tool except status. A missing session id is a
 * hard denial, not a shared fallback bucket.
 */
export function registerContractGuard(ctx, { state, contract }) {
  return ctx.tools.guard((exec) => {
    const logicallyEnabled = typeof state.logicallyEnabled === 'function'
      ? state.logicallyEnabled()
      : state.available()
    if (!logicallyEnabled) return undefined
    const name = String(exec?.name || '')
    const action = name === 'coordination' ? String(exec?.arguments?.action || '') : ''
    if (name === 'coordination' && action === 'status') return undefined
    if (!contract.ok) {
      return 'CONTRACT INVALID: the 40-rule contract failed validation. Call coordination({action:"status"}) for details; every other tool is blocked (I0).'
    }
    if (!state.available()) {
      return 'PENDING VERIFICATION: the running package has not been verified and explicitly activated. Call coordination({action:"status"}) for details; other tools are blocked.'
    }
    const sid = sessionId(exec)
    if (!sid) return 'session_identity_unavailable: cannot bind the rule contract to this session; controlled tool calls are blocked (I0).'
    if (name === 'coordination' && action === 'contract') return undefined
    if (!state.isAcked(sid, contract.hash)) {
      return 'Rule contract not loaded for this session. Call coordination({action:"contract"}) before any other tool.'
    }
    return undefined
  })
}
