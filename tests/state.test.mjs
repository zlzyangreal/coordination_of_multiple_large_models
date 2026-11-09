import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { StateStore } from '../lib/state.js'

const VERSION = '3.0.0'
const DIGEST = 'a'.repeat(64)

async function makeStore(tmp) {
  const store = new StateStore({
    file: path.join(tmp, 'state.json'),
    runningVersion: VERSION,
    runningDigest: DIGEST,
    log: null,
  })
  await store.load()
  return store
}

test('first install is pending; verification enables; upgrade forces pending again', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-state-'))
  try {
    const store = await makeStore(tmp)
    assert.equal(store.mode(), 'pending_verification')
    await assert.rejects(() => store.setEnabled(true), /verification_pending/)
    await store.completeVerification()
    assert.equal(store.mode(), 'normal')
    await store.setEnabled(true)
    assert.equal(store.available(), true)

    const upgraded = new StateStore({ file: store.file, runningVersion: '3.0.1', runningDigest: DIGEST, log: null })
    await upgraded.load()
    assert.equal(upgraded.mode(), 'pending_verification')
    assert.equal(upgraded.available(), false)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('contract ack is session, hash and epoch bound', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-state-'))
  try {
    const store = await makeStore(tmp)
    await store.completeVerification()
    await store.setEnabled(true)
    await store.ackContract('session-1', 'hash-1')
    assert.equal(store.isAcked('session-1', 'hash-1'), true)
    assert.equal(store.isAcked('session-2', 'hash-1'), false)
    assert.equal(store.isAcked('session-1', 'hash-2'), false)
    await store.setEnabled(false)
    await store.setEnabled(true)
    assert.equal(store.isAcked('session-1', 'hash-1'), false, 're-enable bumps the epoch')
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('controlled operations block OFF; finish disables after the last op', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-state-'))
  try {
    const store = await makeStore(tmp)
    await store.completeVerification()
    await store.setEnabled(true)
    const op = await store.beginOp({ kind: 'extension-install', requestId: 'x' })
    await assert.rejects(() => store.setEnabled(false), /controlled operations/)
    const answer = await store.requestDisable('finish')
    assert.equal(answer.pendingDisable, true)
    assert.equal(store.available(), true, 'guard stays active while the op runs')
    await store.endOp(op, 'done')
    assert.equal(store.available(), false)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})

test('corrupt state is quarantined and fails safe', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'mmc-state-'))
  try {
    const file = path.join(tmp, 'state.json')
    await writeFile(file, '{not json', 'utf8')
    const store = new StateStore({ file, runningVersion: VERSION, runningDigest: DIGEST, log: null })
    await store.load()
    assert.equal(store.mode(), 'pending_verification')
    assert.equal(store.available(), false)
  } finally {
    await rm(tmp, { recursive: true, force: true })
  }
})
