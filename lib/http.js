const PREFIX = '/_multimodel'
const MAX_BODY = 64 * 1024

function send(res, code, body) {
  const payload = JSON.stringify(body)
  res.statusCode = code
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(payload)
}

async function readJson(req) {
  let size = 0
  const chunks = []
  for await (const chunk of req) {
    size += chunk.length
    if (size > MAX_BODY) throw Object.assign(new Error('request body too large'), { status: 413 })
    chunks.push(chunk)
  }
  if (chunks.length === 0) return {}
  const text = Buffer.concat(chunks).toString('utf8')
  if (!text.trim()) return {}
  try {
    return JSON.parse(text)
  } catch {
    throw Object.assign(new Error('invalid JSON body'), { status: 400 })
  }
}

function originAllowed(req) {
  const origin = req.headers.origin
  if (!origin || origin === 'null' || origin === 'file://') return true
  try {
    const parsed = new URL(origin)
    return parsed.host === req.headers.host
  } catch {
    return false
  }
}

/** Human-only HTTP control plane. Never registered as a model tool. */
export function registerRoutes(ctx, deps, webServer = ctx.get?.('webServer')) {
  if (!webServer?.register) throw new Error('webServer service is unavailable')
  const handler = async (req, res) => {
    try {
      if (!originAllowed(req)) return send(res, 403, { ok: false, error: 'origin_not_allowed' })
      const url = new URL(req.url, 'http://local')
      if (!url.pathname.startsWith(PREFIX)) return send(res, 404, { ok: false, error: 'not_found' })
      const route = url.pathname.slice(PREFIX.length) || '/'
      const method = String(req.method || 'GET').toUpperCase()
      const mutation = method !== 'GET'
      if (mutation && req.headers['x-coord-token'] !== deps.token) {
        return send(res, 403, { ok: false, error: 'invalid_csrf_token' })
      }
      if (method === 'GET') {
        if (route === '/status') return send(res, 200, { ...(await deps.status()), csrfToken: deps.token })
        if (route === '/extensions') return send(res, 200, { ok: true, extensions: await deps.extensions.list() })
        if (route === '/contract') {
          return send(res, 200, { ok: deps.contract.ok, text: deps.contract.text, errors: deps.contract.errors })
        }
        if (route === '/logs') {
          const rows = await deps.logs.list({
            limit: url.searchParams.get('limit') || 50,
            priority: url.searchParams.get('priority'),
            date: url.searchParams.get('date'),
            status: url.searchParams.get('status'),
          })
          return send(res, 200, { ok: true, rows, summary: deps.logs.summary() })
        }
        if (route === '/summary') {
          return send(res, 200, { ok: true, status: await deps.status(), logs: deps.logs.summary() })
        }
        return send(res, 404, { ok: false, error: 'not_found' })
      }
      if (method !== 'POST') return send(res, 405, { ok: false, error: 'method_not_allowed' })
      const body = await readJson(req)
      if (route === '/toggle') return send(res, 200, { ok: true, result: await deps.toggle(body) })
      if (route === '/verify') return send(res, 200, { ok: true, result: await deps.verify(body) })
      if (route === '/extensions/install') {
        const results = await deps.extensions.install(body.ids || body.id, { confirm: body.confirm === true, requestId: body.requestId || null, onOp: deps.onExtensionOp })
        return send(res, 200, { ok: results.every((item) => item.ok), results })
      }
      if (route === '/extensions/activate') {
        const result = await deps.extensions.activate(String(body.id || ''), { confirm: body.confirm === true })
        return send(res, 200, { ok: result.ok, result })
      }
      if (route === '/logs/status') {
        const issue = await deps.logs.markStatus({ issueId: String(body.issueId || ''), status: String(body.status || ''), note: body.note || null })
        return send(res, 200, { ok: true, issue })
      }
      return send(res, 404, { ok: false, error: 'not_found' })
    } catch (error) {
      const status = Number(error?.status) || (error?.code === 'ops_running' ? 409 : error?.code === 'verification_pending' ? 409 : 400)
      return send(res, status, {
        ok: false,
        error: error?.code || 'request_failed',
        message: String(error?.message || error).slice(0, 500),
        ...(error?.ops ? { ops: error.ops } : {}),
      })
    }
  }
  return webServer.register({ kind: 'prefix', path: PREFIX, handler })
}
