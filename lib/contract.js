import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { sha256Text } from './paths.js'

export const CONTRACT_VERSION = '3.0.0'

/**
 * Load the canonical contract. The manifest is the machine-readable source; rules.md is its
 * rendered form. Any gap makes `ok` false, which the plugin turns into a deny guard plus I0.
 */
export async function loadContract(root) {
  try {
    const manifest = JSON.parse(await readFile(path.join(root, 'lib', 'rule-manifest.json'), 'utf8'))
    const text = await readFile(path.join(root, 'lib', 'rules.md'), 'utf8')
    const errors = []
    const rules = Array.isArray(manifest.rules) ? manifest.rules : []
    if (rules.length !== 40) errors.push(`manifest has ${rules.length} rules, expected 40`)
    const ids = new Set(rules.map((rule) => Number(rule.id)))
    for (let id = 1; id <= 40; id += 1) {
      if (!ids.has(id)) errors.push(`manifest is missing rule ${id}`)
      if (!text.includes(`\n${id}. `)) errors.push(`rules.md is missing rule ${id}`)
    }
    if (!text.includes('coordination({action:"contract"})')) errors.push('rules.md has no first-action instruction')
    for (const rule of rules) {
      if (!rule.mechanism || !Array.isArray(rule.mechanism) || rule.mechanism.length === 0) {
        errors.push(`rule ${rule.id} has no mechanism`)
      }
      if (!String(rule.text || '').trim()) errors.push(`rule ${rule.id} is empty`)
    }
    return {
      ok: errors.length === 0,
      text,
      hash: sha256Text(text),
      version: manifest.version || CONTRACT_VERSION,
      errors,
      ruleCount: rules.length,
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
