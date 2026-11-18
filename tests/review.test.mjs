import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { StateStore } from '../lib/state.js'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const VERSION = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8')).version
const DIGEST = 'b'.repeat(64)

async function makeStore(tmp, overrides = {}) {
  const store = new StateStore({
    file: path.join(tmp, 'state.json'),
    runningVersion: VERSION,
    runningDigest: DIGEST,
    log: null,
    ...overrides,
  })
  await store.load()
  return store
}

test('review: "pass" records who approved, the digest and the note', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-review-'))
  try {
    const store = await makeStore(tmp)
    await store.completeVerification({ by: 'user', note: 'looks right' })
    const record = store.summary().verification
    assert.equal(record.decision, 'pass')
    assert.equal(record.by, 'user')
    assert.equal(record.note, 'looks right')
    assert.equal(record.version, VERSION)
    assert.equal(record.digest, DIGEST)
    assert.equal(store.mode(), 'normal')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('review: "reject" leaves the package pending, switches the mode off and keeps the reason', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-review-'))
  try {
    const store = await makeStore(tmp)
    await store.completeVerification()
    await store.setEnabled(true)
    assert.equal(store.available(), true)

    const upgraded = new StateStore({ file: store.file, runningVersion: '3.2.1', runningDigest: DIGEST, log: null })
    await upgraded.load()
    assert.equal(upgraded.mode(), 'pending_verification')

    await upgraded.rejectVerification({ by: 'user', note: 'UI regression' })
    const summary = upgraded.summary()
    assert.equal(summary.verification.decision, 'reject')
    assert.equal(summary.verification.note, 'UI regression')
    assert.equal(summary.enabled, false)
    assert.equal(summary.verifiedVersion, null)
    assert.equal(upgraded.mode(), 'pending_verification')
    assert.equal(upgraded.available(), false)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('review: a rejection is cleared once the same package is approved again', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-review-'))
  try {
    const store = await makeStore(tmp)
    await store.rejectVerification({ note: 'first look' })
    assert.equal(store.mode(), 'pending_verification')
    await store.completeVerification({ note: 'fixed' })
    const record = store.summary().verification
    assert.equal(record.decision, 'pass')
    assert.equal(record.note, 'fixed')
    assert.equal(store.mode(), 'normal')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('review: invalidation forgets a stale decision', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-review-'))
  try {
    const store = await makeStore(tmp)
    await store.completeVerification()
    await store.invalidate('package digest changed')
    assert.equal(store.summary().verification, null)
    assert.equal(store.mode(), 'pending_verification')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
