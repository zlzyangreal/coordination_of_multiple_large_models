import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const source = await readFile(path.join(root, 'lib', 'client.js'), 'utf8')

function loadModule() {
  let entry = null
  const React = {
    createElement: () => ({}),
    useState: (value) => [value, () => {}],
    useEffect: () => {},
    useRef: (value) => ({ current: value }),
  }
  const window = { __ModuleLoader__: { load: (value) => { entry = value } } }
  new Function('window', 'require', source)(window, (name) => {
    if (name === 'react') return React
    throw new Error(`unexpected module ${name}`)
  })
  return entry.factory((name) => {
    if (name === 'react') return React
    throw new Error(`unexpected module ${name}`)
  })
}

const client = loadModule()
const layoutDag = client.layoutDag

test('client workflow: an empty graph has no size', () => {
  assert.deepEqual(layoutDag([], []), { width: 0, height: 0, nodes: [], edges: [] })
  assert.deepEqual(layoutDag(undefined, undefined), { width: 0, height: 0, nodes: [], edges: [] })
})

test('client workflow: a chain layers by dependency depth', () => {
  const nodes = [{ id: 'p' }, { id: 'r' }]
  const laid = layoutDag(nodes, [{ from: 'p', to: 'r', kind: 'depends' }])
  const byId = Object.fromEntries(laid.nodes.map((node) => [node.id, node]))
  assert.equal(byId.p.depth, 0)
  assert.equal(byId.r.depth, 1)
  assert.equal(byId.r.y - byId.p.y, 96)
  assert.equal(byId.p.w, 168)
  assert.equal(byId.p.h, 64)
})

test('client workflow: two producers feeding one consumer share a layer', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  const edges = [{ from: 'a', to: 'c' }, { from: 'b', to: 'c' }]
  const laid = layoutDag(nodes, edges)
  const byId = Object.fromEntries(laid.nodes.map((node) => [node.id, node]))
  assert.equal(byId.a.depth, 0)
  assert.equal(byId.b.depth, 0)
  assert.equal(byId.c.depth, 1)
  assert.notEqual(byId.a.x, byId.b.x)
  assert.equal(byId.c.x, byId.a.x)
})

test('client workflow: unknown or self edges are dropped', () => {
  const nodes = [{ id: 'a' }]
  const laid = layoutDag(nodes, [{ from: 'ghost', to: 'a' }, { from: 'a', to: 'a' }, { from: 'a', to: 'ghost' }])
  assert.equal(laid.edges.length, 0)
  assert.equal(laid.nodes.length, 1)
})

test('client workflow: rework edges do not change layering but keep their kind', () => {
  const nodes = [{ id: 'p' }, { id: 'r' }]
  const laid = layoutDag(nodes, [
    { from: 'p', to: 'r', kind: 'review' },
    { from: 'r', to: 'p', kind: 'rework' },
  ])
  const byId = Object.fromEntries(laid.nodes.map((node) => [node.id, node]))
  assert.equal(byId.p.depth, 0)
  assert.equal(byId.r.depth, 1)
  const rework = laid.edges.find((edge) => edge.kind === 'rework')
  assert.ok(rework)
  assert.match(rework.path, /^M /)
})

test('client workflow: layout is deterministic and de-duplicates ids', () => {
  const nodes = [{ id: 'a' }, { id: 'a' }, { id: 'b' }]
  const first = layoutDag(nodes, [{ from: 'a', to: 'b' }])
  const second = layoutDag(nodes, [{ from: 'a', to: 'b' }])
  assert.deepEqual(first, second)
  assert.equal(first.nodes.length, 2)
})

test('client workflow: the canvas bounds cover every node', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  const laid = layoutDag(nodes, [{ from: 'a', to: 'c' }, { from: 'b', to: 'c' }])
  for (const node of laid.nodes) {
    assert.ok(node.x >= 0 && node.x + node.w <= laid.width)
    assert.ok(node.y >= 0 && node.y + node.h <= laid.height)
  }
})

test('client workflow: every rendered edge carries an svg path', () => {
  const laid = layoutDag([{ id: 'a' }, { id: 'b' }], [{ from: 'a', to: 'b', kind: 'depends' }])
  assert.equal(laid.edges.length, 1)
  assert.match(laid.edges[0].path, /^M [\d.]+ [\d.]+ C /)
})

test('client workflow: the module still declares its slots and apply', () => {
  assert.deepEqual(client.inject, ['slots', 'sidebarRightTabs'])
  assert.equal(typeof client.apply, 'function')
  assert.equal(typeof client.layoutDag, 'function')
})
