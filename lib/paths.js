import { createHash } from 'node:crypto'
import { existsSync, statSync, promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** Walk up from a path looking for a .git entry (directory or file). */
export function findGitAncestor(start) {
  let dir = path.resolve(start)
  for (;;) {
    if (existsSync(path.join(dir, '.git'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
}

/**
 * Resolve the active DSH profile from the launcher-provided service first, then the documented
 * environment pair. Returns null when neither source identifies a profile.
 */
export function resolveProfile(ctx) {
  try {
    const profile = ctx?.get?.('profileContext')
    if (profile && typeof profile === 'object' && profile.name && profile.home) {
      return {
        name: String(profile.name),
        home: String(profile.home),
        dir: profile.dir || path.join(String(profile.home), 'profiles', String(profile.name)),
        source: 'profileContext',
      }
    }
  } catch { /* the service may not exist yet */ }
  const name = process.env.DSH_PROFILE
  const home = process.env.DSH_HOME
  if (name && home) {
    return { name, home, dir: path.join(home, 'profiles', name), source: 'env' }
  }
  return null
}

/**
 * Profile-isolated roots. Logs/state never live inside a git worktree: if the harness home is in
 * one, the plugin falls back to the platform application-data directory.
 */
export function resolveRoots(profile) {
  if (!profile?.home) return null
  let root = path.join(profile.home, 'coordination')
  let gitFallback = false
  if (findGitAncestor(root)) {
    const base = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
    root = path.join(base, 'dsh-coordination')
    gitFallback = true
  }
  return {
    home: profile.home,
    profile: profile.name,
    profileDir: profile.dir,
    root,
    stateDir: path.join(root, 'state'),
    logDir: path.join(root, 'logs', profile.name),
    indexDir: path.join(root, 'index'),
    fallbackDir: path.join(root, 'fallback'),
    stateFile: path.join(root, 'state', `${profile.name}.json`),
    projectId: projectId(process.cwd()),
    gitFallback,
  }
}

export function projectId(dir) {
  return createHash('sha256').update(path.resolve(dir)).digest('hex').slice(0, 12)
}

export function sha256Text(text) {
  return createHash('sha256').update(String(text), 'utf8').digest('hex')
}

/**
 * Digest of every shipped runtime/governance file. Documentation and tests are excluded on
 * purpose: this value gates activation, so it must cover the contract, engine, assets and code.
 */
export async function packageDigest(root) {
  const files = []
  const includeRoots = ['lib', 'skills', 'scripts', 'assets']
  const direct = ['package.json', 'cordis.patch.yml']
  for (const name of direct) addFile(files, root, path.join(root, name))
  for (const folder of includeRoots) await walk(files, path.join(root, folder), root)
  files.sort((a, b) => a.rel.localeCompare(b.rel))
  const hash = createHash('sha256')
  for (const file of files) {
    hash.update(file.rel)
    hash.update('\0')
    hash.update(String(file.size))
    hash.update('\0')
    hash.update(await fs.readFile(file.abs))
    hash.update('\0')
  }
  return hash.digest('hex')
}

function addFile(out, root, abs) {
  let stat
  try {
    stat = statSync(abs)
  } catch {
    return
  }
  if (!stat.isFile()) return
  out.push({ abs, rel: path.relative(root, abs).split(path.sep).join('/'), size: stat.size })
}

async function walk(out, dir, root) {
  let entries
  try {
    entries = await fs.readdir(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries) {
    if (entry.name === '__pycache__' || entry.name.endsWith('.pyc') || entry.name === '.DS_Store') continue
    const abs = path.join(dir, entry.name)
    if (entry.isDirectory()) await walk(out, abs, root)
    else if (entry.isFile()) addFile(out, root, abs)
  }
}

export async function atomicWriteJson(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true })
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`
  const body = `${JSON.stringify(value, null, 2)}\n`
  await fs.writeFile(tmp, body, 'utf8')
  await fs.rename(tmp, file)
}

export async function readJson(file) {
  const text = await fs.readFile(file, 'utf8')
  return JSON.parse(text.replace(/^\uFEFF/, ''))
}

/** ISO 8601 with the local UTC offset (e.g. 2026-11-09T21:04:00+08:00). */
export function isoLocal(date = new Date()) {
  const pad = (value, width = 2) => String(value).padStart(width, '0')
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const absolute = Math.abs(offset)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(Math.floor(absolute / 60))}:${pad(absolute % 60)}`
}

export function localDay(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function insideWorktree(target) {
  return Boolean(findGitAncestor(target))
}
