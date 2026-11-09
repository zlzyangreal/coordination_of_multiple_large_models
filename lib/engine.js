import { execFile } from 'node:child_process'
import { cp, mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'
import { runtimeDigest } from './paths.js'

const execFileAsync = promisify(execFile)

const EXIT_MEANING = {
  0: 'ok',
  3: 'no_review_or_state',
  4: 'stale_or_mixed_evidence',
  5: 'open_high_or_medium_finding',
  6: 'not_independent',
}

function aborted() {
  return { ok: false, code: 'ABORT_ERR', report: null, stdout: '', stderr: 'aborted' }
}

/** Fixed-argv Python engine adapter. It never accepts a command or arbitrary arguments. */
export class PythonEngine {
  constructor({ ctx, root, projectRoot, pythonPath = null, log = null, runtimeHash = null }) {
    this.ctx = ctx
    this.root = root
    this.projectRoot = projectRoot
    this.pythonPath = pythonPath
    this.log = log
    this.resolved = undefined
    this.controllers = new Set()
    this.runtimeHash = runtimeHash
  }

  abortAll() {
    for (const controller of this.controllers) {
      try { controller.abort() } catch { /* already aborted */ }
    }
  }

  async resolve() {
    if (this.resolved !== undefined) return this.resolved
    const candidates = [this.pythonPath, process.env.DSH_PYTHON, 'python', 'python3', 'py']
      .filter(Boolean)
    const subprocess = this.ctx?.get?.('subprocess')
    if (subprocess?.resolveExecutable) {
      for (const candidate of candidates) {
        try {
          const executable = await subprocess.resolveExecutable(candidate)
          if (executable) {
            this.resolved = executable
            return executable
          }
        } catch { /* try the next candidate */ }
      }
    }
    for (const candidate of candidates) {
      try {
        await execFileAsync(candidate, ['--version'], { timeout: 5000, windowsHide: true })
        this.resolved = candidate
        return candidate
      } catch { /* try the next candidate */ }
    }
    this.resolved = null
    return null
  }

  async spawn(argv, { timeoutMs = 60000, cwd = this.projectRoot, controller = null } = {}) {
    const python = await this.resolve()
    if (!python) return { ok: false, code: 'ENOENT', stdout: '', stderr: 'python unavailable' }
    const own = controller === null ? new AbortController() : null
    const active = controller || own
    if (own) this.controllers.add(own)
    const timer = setTimeout(() => active.abort(), timeoutMs)
    timer.unref?.()
    try {
      const subprocess = this.ctx?.get?.('subprocess')
      if (subprocess?.spawn) {
        const handle = subprocess.spawn({
          argv: [python, ...argv],
          cwd,
          stdio: { stdin: 'ignore', stdout: { maxBytes: 65536 }, stderr: { maxBytes: 8192 } },
          graceMs: 5000,
          signal: active.signal,
        })
        const outcome = await handle.done
        const stdout = handle.collected?.stdout?.readFrom(0)?.text || ''
        const stderr = handle.collected?.stderr?.readFrom(0)?.text || ''
        return { ok: outcome.exitCode === 0, code: String(outcome.exitCode), stdout, stderr }
      }
      const { stdout, stderr } = await execFileAsync(python, argv, {
        cwd, timeout: timeoutMs, maxBuffer: 256 * 1024, windowsHide: true, signal: active.signal,
      })
      return { ok: true, code: '0', stdout, stderr }
    } catch (error) {
      return {
        ok: false,
        code: String(error?.code ?? 'ERR'),
        stdout: String(error?.stdout ?? ''),
        stderr: String(error?.stderr ?? error?.message ?? error),
      }
    } finally {
      clearTimeout(timer)
      if (own) this.controllers.delete(own)
    }
  }

  async probe() {
    const python = await this.resolve()
    return { available: Boolean(python), python: python || null }
  }

  async runPlanGate(project) {
    const target = project || this.projectRoot
    const controller = new AbortController()
    this.controllers.add(controller)
    let snapshot = null
    try {
      if (controller.signal.aborted) return aborted()
      snapshot = await mkdtemp(path.join(os.tmpdir(), 'mmc-scripts-'))
      await cp(path.join(this.root, 'scripts'), path.join(snapshot, 'scripts'), {
        recursive: true,
        filter: (source) => !source.includes('__pycache__') && !source.endsWith('.pyc'),
      })
      await cp(path.join(this.root, 'assets'), path.join(snapshot, 'assets'), { recursive: true })
      if (controller.signal.aborted) return aborted()
      if (this.runtimeHash) {
        const digest = await runtimeDigest(snapshot)
        if (controller.signal.aborted) return aborted()
        if (digest !== this.runtimeHash) {
          await this.log?.record({
            kind: 'runtime', priority: 'I0', eventType: 'package.digest_changed',
            summary: 'Python engine/assets changed before execution; the gate was refused',
            impact: 'the gate is fail-closed until the package is verified again',
            suggestedAction: 'restart, verify and explicitly activate the package again',
          })
          return { ok: false, code: 'package_digest_changed', report: null, stderr: 'runtime digest mismatch' }
        }
      }
      const script = path.join(snapshot, 'scripts', 'plan_gate.py')
      const result = await this.spawn([script, 'check', '--project', target, '--json'], { controller })
      if (result.code === 'ENOENT') {
        await this.log?.record({
          kind: 'runtime', priority: 'I0', eventType: 'gate.unavailable',
          summary: 'Python core is unavailable; the plan gate cannot run',
          impact: 'gate actions are fail-closed until a Python interpreter is configured',
          suggestedAction: 'install Python 3 or set config.pythonPath / DSH_PYTHON',
          status: 'open',
        })
        return { ok: false, code: 'core_unavailable', report: null, stderr: result.stderr }
      }
      if (result.code === 'ABORT_ERR' || result.code === 'ABORTED') return aborted()
      let report = null
      try {
        report = JSON.parse(result.stdout.slice(result.stdout.indexOf('{')))
      } catch {
        report = null
      }
      return {
        ok: result.ok,
        code: result.ok ? 'ok' : (EXIT_MEANING[Number(result.code)] || `exit_${result.code}`),
        report,
        stdout: result.stdout,
        stderr: result.stderr,
      }
    } finally {
      this.controllers.delete(controller)
      if (snapshot) await rm(snapshot, { recursive: true, force: true }).catch(() => {})
    }
  }
}
