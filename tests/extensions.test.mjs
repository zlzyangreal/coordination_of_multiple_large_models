import assert from 'node:assert/strict'
import test from 'node:test'
import { ExtensionRegistry } from '../lib/extensions.js'

function makeCtx(bundles, { install, enable } = {}) {
  const manager = {
    listBundles: async () => bundles,
    installBundle: install || (async () => ({ application: 'installed', version: '1.2.3' })),
    setBundleEnabled: enable || (async () => ({ application: 'applied' })),
  }
  return { get: (name) => (name === 'pluginManager' ? manager : undefined) }
}

test('declaration-driven status and disabled install', async () => {
  const ctx = makeCtx([{ name: 'dsh-our-free-model', enabled: false, version: '1.2.3', availability: 'available' }])
  let installedWith = null
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: false, version: '1.2.3', availability: 'available' }], { install: async (spec, options) => { installedWith = { spec, options }; return { application: 'installed', version: '1.2.3' } } }),
    declarations: undefined,
    log: null,
    state: null,
  })
  const listed = await registry.list()
  assert.equal(listed.length, 1)
  assert.equal(listed[0].id, 'dsh-our-free-model')
  assert.equal(listed[0].status, 'installed_disabled')
  assert.equal(listed[0].eligible, true)

  const results = await registry.install(['dsh-our-free-model'], { confirm: true })
  assert.equal(results[0].ok, true)
  assert.equal(installedWith.options.enabled, false, 'install must stay disabled')
  assert.equal(installedWith.spec, 'github:Ebony-Vinyl/dsh-our-free-model')
  await assert.rejects(() => registry.install(['dsh-our-free-model'], { confirm: false }), /confirm/)
  await assert.rejects(() => registry.install(['undeclared'], { confirm: true }), /undeclared/)
})

test('activation requires known identity and a declared probe', async () => {
  let enabled = null
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: false, availability: 'available' }], {
      enable: async (name, value) => { enabled = { name, value }; return { application: 'applied' } },
    }),
    declarations: undefined,
    log: null,
    state: null,
  })
  const listed = await registry.list()
  assert.equal(listed[0].identityKnown, false)
  await assert.rejects(() => registry.activate('dsh-our-free-model', { confirm: true }), /identity_unknown/)
  assert.equal(enabled, null)
})

test('a declaration without its own source never inherits the default source', async () => {
  const registry = new ExtensionRegistry({
    ctx: makeCtx([]),
    declarations: [{ id: 'future-extension', policy: 'optional', displayName: 'Future', probe: { kind: 'bundle' } }],
    log: null,
    state: null,
  })
  const listed = await registry.list()
  assert.equal(listed[0].installable, false)
  assert.equal(listed[0].status, 'unknown')
  assert.match(listed[0].reason, /source_not_declared/)
  await assert.rejects(() => registry.install(['future-extension'], { confirm: true }), /no declared source/)
})

test('presence without an available bundle is not eligible for activation', async () => {
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: false, version: '1.2.3' }]),
    declarations: undefined,
    log: null,
    state: null,
  })
  const listed = await registry.list()
  assert.equal(listed[0].eligible, false)
  await assert.rejects(() => registry.activate('dsh-our-free-model', { confirm: true }), /probe_unavailable/)
})

test('install passes the same request id to the host and the operation', async () => {
  let seen = null
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: false, version: '1.2.3', availability: 'available' }], {
      install: async (spec, options) => { seen = options; return { application: 'installed', version: '1.2.3' } },
    }),
    declarations: undefined,
    log: null,
    state: null,
  })
  const results = await registry.install(['dsh-our-free-model'], { confirm: true })
  assert.ok(seen.requestId)
  assert.equal(seen.enabled, false)
  assert.equal(results[0].requestId, seen.requestId)
})

test('activation that does not take effect returns ok:false', async () => {
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: false, version: '1.2.3', availability: 'available' }], {
      enable: async () => ({ application: 'applied' }),
    }),
    declarations: undefined,
    log: null,
    state: null,
  })
  const result = await registry.activate('dsh-our-free-model', { confirm: true })
  assert.equal(result.ok, false)
  assert.equal(result.status, 'installed_disabled')
})

test('an enabled but unverified bundle is not reported as a verified activation', async () => {
  const registry = new ExtensionRegistry({
    ctx: makeCtx([{ name: 'dsh-our-free-model', enabled: true }]),
    declarations: undefined,
    log: null,
    state: null,
  })
  const result = await registry.activate('dsh-our-free-model', { confirm: true })
  assert.equal(result.ok, false)
  assert.equal(result.status, 'enabled_outside_gate')
})
