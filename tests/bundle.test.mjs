import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'))

test('bundle manifest is complete and dependency-free', () => {
  assert.equal(pkg.name, 'dsh-multimodel-coordination')
  assert.equal(pkg.version, '3.0.2')
  assert.equal(pkg.engines.dsh, '0.2.1-rc.3')
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
  assert.equal(pkg.dsh.client.platform, 'web')
  assert.equal(pkg.dependencies, undefined)
  assert.equal(pkg.peerDependencies, undefined)
  for (const folder of ['lib', 'skills', 'scripts', 'assets', 'docs']) {
    assert.ok(existsSync(path.join(root, folder)), `missing ${folder}`)
  }
  assert.ok(!existsSync(path.join(root, 'dsh-our-free-model')), 'third-party source must not be bundled')
})

test('cordis patch inserts exactly one plugin row', () => {
  const patch = readFileSync(path.join(root, 'cordis.patch.yml'), 'utf8')
  assert.match(patch, /- insert:/)
  assert.match(patch, /id: multimodel-coordination/)
  assert.match(patch, /name: dsh-multimodel-coordination/)
})

test('client bundle is a ModuleLoader registration with matching id', () => {
  const source = readFileSync(path.join(root, 'lib', 'client.js'), 'utf8')
  assert.match(source, /window\.__ModuleLoader__\.load\(/)
  assert.match(source, /id: "dsh-multimodel-coordination"/)
  assert.match(source, /exports\.apply = apply/)
  assert.match(source, /settings\.section/)
})

test('40-rule manifest matches SKILL.md and the v2.4.0 baseline', () => {
  const result = spawnSync(process.env.PYTHON || 'python', ['scripts/check_rules.py', '--root', root], { encoding: 'utf8' })
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
  assert.match(result.stdout, /40 rules verified/)
})
