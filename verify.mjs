#!/usr/bin/env node
// Offline verification for the shipped package: version, digest, 40-rule contract.
// Does not start DSH or touch any profile.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadContract } from './lib/contract.js'
import { packageDigest } from './lib/paths.js'

const root = path.dirname(fileURLToPath(import.meta.url))
const pkg = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'))
const contract = await loadContract(root)
const digest = await packageDigest(root)
const checks = [
  { id: 'version', ok: pkg.version === '3.0.0', detail: pkg.version },
  { id: 'engines.dsh', ok: pkg.engines?.dsh === '0.2.1-rc.3', detail: pkg.engines?.dsh },
  { id: 'bundle', ok: pkg.dsh?.bundle?.patch === './cordis.patch.yml', detail: pkg.dsh?.bundle?.patch },
  { id: 'client', ok: pkg.dsh?.client?.platform === 'web', detail: pkg.dsh?.client?.platform },
  { id: 'contract', ok: contract.ok, detail: contract.ok ? `${contract.ruleCount} rules` : contract.errors.join('; ') },
  { id: 'digest', ok: /^[0-9a-f]{64}$/.test(digest), detail: digest.slice(0, 16) },
  { id: 'no_dependencies', ok: !pkg.dependencies && !pkg.peerDependencies, detail: 'none' },
]
const ok = checks.every((check) => check.ok)
console.log(JSON.stringify({ ok, version: pkg.version, digest, contract: { ruleCount: contract.ruleCount, hash: contract.hash }, checks }, null, 2))
process.exit(ok ? 0 : 1)
