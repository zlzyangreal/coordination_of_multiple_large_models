import assert from 'node:assert/strict'
import { cp, mkdtemp, rm, appendFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { PythonEngine } from '../lib/engine.js'
import { runtimeDigest } from '../lib/paths.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

test('a Python engine changed after hashing is refused before execution', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-engine-'))
  try {
    await cp(path.join(root, 'scripts'), path.join(tmp, 'scripts'), { recursive: true })
    await cp(path.join(root, 'assets'), path.join(tmp, 'assets'), { recursive: true })
    const hash = await runtimeDigest(tmp)
    await appendFile(path.join(tmp, 'scripts', 'plan_gate.py'), '\n# changed after the digest\n', 'utf8')
    const engine = new PythonEngine({
      ctx: { get: () => undefined },
      root: tmp,
      projectRoot: tmp,
      runtimeHash: hash,
      log: null,
    })
    const result = await engine.runPlanGate(tmp)
    assert.equal(result.ok, false)
    assert.equal(result.code, 'package_digest_changed')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('abortAll cancels a gate before it can complete', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-engine-'))
  try {
    await cp(path.join(root, 'scripts'), path.join(tmp, 'scripts'), { recursive: true })
    await cp(path.join(root, 'assets'), path.join(tmp, 'assets'), { recursive: true })
    const engine = new PythonEngine({
      ctx: { get: () => undefined },
      root: tmp,
      projectRoot: tmp,
      runtimeHash: await runtimeDigest(tmp),
      log: null,
    })
    const running = engine.runPlanGate(tmp)
    engine.abortAll()
    const result = await running
    assert.equal(result.code, 'ABORT_ERR')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('abort during delayed resolution returns ABORT_ERR rather than ENOENT', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-engine-'))
  try {
    await cp(path.join(root, 'scripts'), path.join(tmp, 'scripts'), { recursive: true })
    await cp(path.join(root, 'assets'), path.join(tmp, 'assets'), { recursive: true })
    const engine = new PythonEngine({
      ctx: { get: () => undefined },
      root: tmp,
      projectRoot: tmp,
      runtimeHash: await runtimeDigest(tmp),
      log: null,
    })
    engine.resolve = async () => {
      await new Promise((resolve) => setTimeout(resolve, 100))
      return null
    }
    const running = engine.runPlanGate(tmp)
    setTimeout(() => engine.abortAll(), 10)
    const result = await running
    assert.equal(result.code, 'ABORT_ERR')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
