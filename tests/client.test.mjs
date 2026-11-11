import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const source = await readFile(path.join(root, 'lib', 'client.js'), 'utf8')

function loadClient() {
  const registered = []
  const errors = []
  const originalError = console.error
  console.error = (...args) => { errors.push(args.map(String).join(' ')) }
  let entry = null
  const React = {
    createElement: () => ({}),
    useState: (value) => [value, () => {}],
    useEffect: () => {},
    useRef: (value) => ({ current: value }),
  }
  const window = {
    __ModuleLoader__: {
      load: (value) => { entry = value },
    },
  }
  new Function('window', 'require', source)(window, (name) => {
    if (name === 'react') return React
    throw new Error(`unexpected module ${name}`)
  })
  const module = entry.factory((name) => {
    if (name === 'react') return React
    throw new Error(`unexpected module ${name}`)
  })
  return {
    registered,
    errors,
    inject: module.inject,
    apply(ctx) { module.apply(ctx) },
    restore() { console.error = originalError },
  }
}

function ctxWith(slots) {
  return { slots }
}

test('client registers a right-sidebar tab, title and guide entry', () => {
  const loaded = loadClient()
  const types = []
  try {
    loaded.apply({
      slots: {
        inject(_name, callback) { return callback() },
        register(options, component) {
          assert.equal(typeof component, 'function')
          assert.equal(options.key, 'multimodel-coordination')
          loaded.registered.push(options)
          return () => {}
        },
      },
      sidebarRightTabs: { register(definition) { types.push(definition); return () => {} } },
      effect(fn) { return fn() },
    })
    assert.equal(types.length, 1)
    assert.equal(types[0].kind, 'multimodel-coordination')
    assert.equal(types[0].guide.length, 1)
    assert.equal(types[0].guide[0].id, 'open')
    assert.equal(loaded.registered.length, 2)
    assert.deepEqual(loaded.registered.map((item) => item.name).sort(), [
      'sidebar.right.pane.tab',
      'sidebar.right.pane.tab.title',
    ])
  } finally {
    loaded.restore()
  }
})

test('client apply does not throw when inject or register throws', () => {
  const loaded = loadClient()
  try {
    assert.doesNotThrow(() => loaded.apply(ctxWith({
      inject() { throw new Error('slot missing') },
    })))
    assert.doesNotThrow(() => loaded.apply(ctxWith({
      inject(_name, callback) { return callback() },
      register() { throw new Error('invalid options') },
    })))
    assert.ok(loaded.errors.some((line) => line.includes('multimodel-coordination')))
  } finally {
    loaded.restore()
  }
})

test('client delayed injection callback is safe after apply settles', () => {
  const loaded = loadClient()
  try {
    let callback = null
    loaded.apply(ctxWith({
      inject(_name, value) { callback = value; return () => {} },
      register() { throw new Error('late invalid options') },
    }))
    assert.equal(typeof callback, 'function')
    assert.doesNotThrow(() => callback())
  } finally {
    loaded.restore()
  }
})

test('client declares the slots service dependency', () => {
  const loaded = loadClient()
  try {
    assert.deepEqual(loaded.inject, ['slots', 'sidebarRightTabs'])
  } finally {
    loaded.restore()
  }
})
