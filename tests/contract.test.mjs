import assert from 'node:assert/strict'
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { loadContract } from '../lib/contract.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

async function fixture() {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-contract-'))
  await cp(path.join(root, 'lib', 'rule-manifest.json'), path.join(tmp, 'lib', 'rule-manifest.json'))
  await cp(path.join(root, 'lib', 'rules.md'), path.join(tmp, 'lib', 'rules.md'))
  await cp(path.join(root, 'assets', 'rule-baseline.v24.sha256'), path.join(tmp, 'assets', 'rule-baseline.v24.sha256'))
  return tmp
}

test('a valid package contract loads and hashes', async () => {
  const tmp = await fixture()
  try {
    const contract = await loadContract(tmp)
    assert.equal(contract.ok, true, contract.errors.join('; '))
    assert.equal(contract.ruleCount, 40)
    assert.match(contract.hash, /^[0-9a-f]{64}$/)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('an altered clause is rejected at runtime', async () => {
  const tmp = await fixture()
  try {
    const file = path.join(tmp, 'lib', 'rules.md')
    const text = await readFile(file, 'utf8')
    await writeFile(file, text.replace(/^(1\. \*\*.+?\*\* ).*$/m, '$1ALTERED CLAUSE'), 'utf8')
    const contract = await loadContract(tmp)
    assert.equal(contract.ok, false)
    assert.ok(contract.errors.some((item) => item.includes('clause')), contract.errors.join('; '))
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('a changed v2.4.0 baseline is rejected', async () => {
  const tmp = await fixture()
  try {
    await writeFile(path.join(tmp, 'assets', 'rule-baseline.v24.sha256'), `${'0'.repeat(64)}\n`, 'utf8')
    const contract = await loadContract(tmp)
    assert.equal(contract.ok, false)
    assert.ok(contract.errors.some((item) => item.includes('baseline')), contract.errors.join('; '))
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
