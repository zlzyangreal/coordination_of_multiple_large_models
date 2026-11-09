import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { sha256Text } from './paths.js'

export const CONTRACT_VERSION = '3.0.0'

function normalize(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim()
}

function renderDigest(rules) {
  const body = []
  for (let id = 1; id <= 38; id += 1) body.push(normalize(rules.get(id)?.text || ''))
  return sha256Text(body.join('\n'))
}

/**
 * Load and validate the canonical contract. The manifest is the source; `rules.md` must be its
 * exact rendered form, rules 1-38 must match the pinned v2.4.0 baseline hash, and every clause must
 * be present. Any gap makes `ok` false, which turns into a deny guard plus I0.
 */
export async function loadContract(root) {
  try {
    const manifest = JSON.parse(await readFile(path.join(root, 'lib', 'rule-manifest.json'), 'utf8'))
    const text = await readFile(path.join(root, 'lib', 'rules.md'), 'utf8')
    const baselineFile = await readFile(path.join(root, 'assets', 'rule-baseline.v24.sha256'), 'utf8')
      .catch(() => '')
    const errors = []
    const rules = new Map()
    for (const rule of Array.isArray(manifest.rules) ? manifest.rules : []) {
      const id = Number(rule.id)
      if (!Number.isInteger(id) || id < 1 || id > 40) {
        errors.push(`manifest has an invalid rule id: ${rule.id}`)
        continue
      }
      if (rules.has(id)) errors.push(`manifest has duplicate rule ${id}`)
      rules.set(id, rule)
    }
    for (let id = 1; id <= 40; id += 1) {
      if (!rules.has(id)) errors.push(`manifest is missing rule ${id}`)
    }
    const rendered = new Map()
    for (const line of text.split(/\r?\n/)) {
      const match = line.match(/^(\d+)\. \*\*(.+?)\*\* (.*)$/)
      if (!match) continue
      const id = Number(match[1])
      if (rendered.has(id)) errors.push(`rules.md has duplicate rule ${id}`)
      if (id > 40) errors.push(`rules.md has an unexpected rule ${id}`)
      rendered.set(id, match[3])
    }
    for (let id = 1; id <= 40; id += 1) {
      const rule = rules.get(id)
      if (!rule) continue
      if (!rendered.has(id)) {
        errors.push(`rules.md is missing rule ${id}`)
        continue
      }
      if (normalize(rendered.get(id)) !== normalize(rule.text)) {
        errors.push(`rules.md clause ${id} differs from the manifest`)
      }
      if (!rule.mechanism || !Array.isArray(rule.mechanism) || rule.mechanism.length === 0) {
        errors.push(`rule ${id} has no mechanism`)
      }
      if (!normalize(rule.text)) errors.push(`rule ${id} is empty`)
    }
    if (!text.includes('coordination({action:"contract"})')) errors.push('rules.md has no first-action instruction')
    const baseline = renderDigest(rules)
    if (manifest.baseline_sha256 !== baseline) {
      errors.push('manifest baseline_sha256 does not match rules 1-38')
    }
    if (baselineFile && baselineFile.trim() !== baseline) {
      errors.push('rules 1-38 no longer match assets/rule-baseline.v24.sha256')
    }
    if (!baselineFile) errors.push('assets/rule-baseline.v24.sha256 is missing')
    return {
      ok: errors.length === 0,
      text,
      hash: sha256Text(text),
      version: manifest.version || CONTRACT_VERSION,
      errors,
      ruleCount: rules.size,
    }
  } catch (error) {
    return {
      ok: false,
      text: '',
      hash: '',
      version: CONTRACT_VERSION,
      errors: [`contract load failed: ${String(error?.message || error).slice(0, 200)}`],
      ruleCount: 0,
    }
  }
}
