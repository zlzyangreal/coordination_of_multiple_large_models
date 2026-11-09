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
