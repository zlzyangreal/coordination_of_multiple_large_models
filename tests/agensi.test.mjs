import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..')
const SCRIPT = path.join(REPO, 'scripts', 'agensi_publish.py')
const PY = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

const hasPython = spawnSync(PY, ['-V'], { encoding: 'utf8' }).status === 0
const skip = hasPython ? false : `python not available (${PY})`

const SKILL = `---
name: Multi-Model Coordination
description: A test skill.
summary: A test skill summary.
version: 3.2.0
tags:
  - agents-orchestration
---

# Test skill

Body.
`

const LISTING = {
  schema: 1,
  listing: {
    name: 'Multi-Model Coordination',
    summary: 'A test skill summary.',
    description: 'A test skill.',
    pricing_type: 'free',
    tags: ['agents-orchestration'],
    keywords: [],
    use_cases: [],
  },
}

async function fixture({ version = '3.3.0', published = null } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'agensi-test-'))
  await mkdir(path.join(root, 'agensi', 'skill', 'references'), { recursive: true })
  await writeFile(path.join(root, 'package.json'), JSON.stringify({ name: 'pkg', version }))
  await writeFile(path.join(root, 'agensi', 'skill', 'SKILL.md'), SKILL)
  await writeFile(path.join(root, 'agensi', 'skill', 'references', 'notes.md'), '# Notes\n')
  await writeFile(path.join(root, 'agensi', 'listing.json'), JSON.stringify(LISTING))
  await writeFile(path.join(root, 'CHANGELOG.md'), '# Changelog\n\n## 3.3.0 - 2026-11-18\n\n- did things\n')
  if (published) {
    await writeFile(path.join(root, 'agensi', 'publish-state.json'),
      JSON.stringify({ schema: 1, slug: 'multi-model-coordination', version: published }))
  }
  return root
}

function run(root, args) {
  const result = spawnSync(PY, [SCRIPT, '--root', root, ...args], { encoding: 'utf8' })
  return { code: result.status, stdout: result.stdout, stderr: result.stderr }
}

test('first listing when nothing has been published', { skip }, async () => {
  const root = await fixture()
  try {
    const out = JSON.parse(run(root, ['check', '--json']).stdout)
    assert.equal(out.action, 'submit')
    assert.equal(out.published_version, null)
    assert.equal(out.pricing_type, 'free')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('same version is a no-op', { skip }, async () => {
  const root = await fixture({ version: '3.3.0', published: '3.3.0' })
  try {
    assert.equal(JSON.parse(run(root, ['check', '--json']).stdout).action, 'none')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('minor bump publishes', { skip }, async () => {
  const root = await fixture({ version: '3.3.0', published: '3.2.0' })
  try {
    assert.equal(JSON.parse(run(root, ['check', '--json']).stdout).action, 'update')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('patch bump is skipped by policy', { skip }, async () => {
  const root = await fixture({ version: '3.3.1', published: '3.3.0' })
  try {
    const out = JSON.parse(run(root, ['check', '--json']).stdout)
    assert.equal(out.action, 'skip')
    assert.match(out.reason, /patch-only/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('--force turns a patch bump into an update', { skip }, async () => {
  const root = await fixture({ version: '3.3.1', published: '3.3.0' })
  try {
    assert.equal(JSON.parse(run(root, ['check', '--json', '--force']).stdout).action, 'update')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('a lower version is refused with exit 5', { skip }, async () => {
  const root = await fixture({ version: '3.1.0', published: '3.3.0' })
  try {
    const result = run(root, ['check', '--json'])
    assert.equal(result.code, 5)
    assert.equal(JSON.parse(result.stdout).action, 'refuse')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('the packaged SKILL.md carries the release version, not the source version', { skip }, async () => {
  const root = await fixture({ version: '3.9.0' })
  try {
    const out = JSON.parse(run(root, ['check', '--json']).stdout)
    assert.equal(out.front_matter.version, '3.9.0')
    assert.equal(out.files, 2)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('--version is accepted after the subcommand and drives the policy', { skip }, async () => {
  const root = await fixture({ version: '3.3.0', published: '3.3.0' })
  try {
    const patch = JSON.parse(run(root, ['check', '--version', '3.3.1', '--json']).stdout)
    assert.equal(patch.action, 'skip')
    const minor = JSON.parse(run(root, ['check', '--version', '3.4.0', '--json']).stdout)
    assert.equal(minor.action, 'update')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('sync --yes refuses to publish when the session pin is stale', { skip }, async () => {
  const root = await fixture({ version: '3.4.0', published: '3.3.0' })
  try {
    await mkdir(path.join(root, 'scripts'), { recursive: true })
    await writeFile(path.join(root, 'scripts', 'session.py'),
      'import sys\nprint("DEVIATION: contract updated")\nsys.exit(2)\n')
    const result = run(root, ['sync', '--yes'])
    assert.equal(result.code, 4)
    assert.match(result.stderr, /workflow gate/)
    assert.match(result.stderr, /pin/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('sync --yes refuses a version the pin does not name', { skip }, async () => {
  const root = await fixture({ version: '9.0.0', published: '3.3.0' })
  try {
    await mkdir(path.join(root, 'scripts'), { recursive: true })
    await writeFile(path.join(root, 'scripts', 'session.py'), 'import sys\nsys.exit(0)\n')
    await mkdir(path.join(root, '_work'), { recursive: true })
    await writeFile(path.join(root, '_work', 'skill_pin.json'), JSON.stringify({ version: '3.3.0' }))
    const result = run(root, ['sync', '--yes'])
    assert.equal(result.code, 4)
    assert.match(result.stderr, /pin names 3\.3\.0/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('a non-object publish state is reported, not a traceback', { skip }, async () => {
  const root = await fixture({ version: '3.3.0', published: '3.3.0' })
  try {
    await writeFile(path.join(root, 'agensi', 'publish-state.json'), '[]')
    const result = run(root, ['check'])
    assert.equal(result.code, 2)
    assert.match(result.stderr, /must contain a JSON object/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('the approval zip is deterministic', { skip }, async () => {
  const root = await fixture({ version: '3.3.0' })
  try {
    const first = run(root, ['package']).stdout.match(/sha256 ([0-9a-f]+)/)?.[1]
    const second = run(root, ['package']).stdout.match(/sha256 ([0-9a-f]+)/)?.[1]
    assert.ok(first, 'package printed a hash')
    assert.equal(first, second)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('a non-HTTPS endpoint is refused before the token is used', { skip }, async () => {
  const root = await fixture({ version: '3.3.0' })
  try {
    const result = run(root, ['status', '--endpoint', 'http://evil.example/mcp'])
    assert.equal(result.code, 2)
    assert.match(result.stderr, /HTTPS is required/)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
