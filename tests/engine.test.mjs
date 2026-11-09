import assert from 'node:assert/strict'
import { cp, mkdtemp, rm, appendFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { PythonEngine } from '../lib/engine.js'
import { scriptsDigest } from '../lib/paths.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

test('a Python engine changed after hashing is refused before execution', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-engine-'))
  try {
    await cp(path.join(root, 'scripts'), path.join(tmp, 'scripts'), { recursive: true })
    const hash = await scriptsDigest(tmp)
    await appendFile(path.join(tmp, 'scripts', 'plan_gate.py'), '\n# changed after the digest\n', 'utf8')
    const engine = new PythonEngine({
      ctx: { get: () => undefined },
      root: tmp,
      projectRoot: tmp,
      scriptsHash: hash,
      log: null,
    })
    const result = await engine.runPlanGate(tmp)
    assert.equal(result.ok, false)
    assert.equal(result.code, 'package_digest_changed')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
