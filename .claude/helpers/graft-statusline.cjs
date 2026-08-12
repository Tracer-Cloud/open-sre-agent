#!/usr/bin/env node
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const { execFileSync } = require('child_process');
const dir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
// Optional per-developer override: absolute path to @nanonets/graft's dist/claude.
const BAKED = process.env.GRAFT_CLAUDE_DIR || "";

// The dist/claude dir of @nanonets/graft resolved from a base whose node_modules is searched.
function fromPkg(base) {
  try {
    const pkg = require.resolve('@nanonets/graft/package.json', { paths: [base] });
    return path.join(path.dirname(pkg), 'dist', 'claude');
  } catch { return null; }
}

// The global node_modules dir per npm (handles Homebrew/Windows/volta). Queried on demand.
function globalRoot() {
  try {
    const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], shell: process.platform === 'win32' }).trim();
    return root || null;
  } catch { return null; /* npm unavailable */ }
}

function candidates() {
  const out = [];
  if (BAKED) out.push(BAKED);
  const local = fromPkg(dir); if (local) out.push(local);
  const legacy = fromPkg(path.join(path.dirname(process.execPath), '..', 'lib')); if (legacy) out.push(legacy);
  const gr = globalRoot(); if (gr) out.push(path.join(gr, '@nanonets', 'graft', 'dist', 'claude'));
  return out;
}

// Every trusted candidate that actually holds the entrypoint, best first.
//
// There is deliberately no fallback to `<project>/dist/claude/<name>`: on a
// machine without graft installed that imported and executed the file straight
// out of whatever repository happened to be open, so a clone carrying its own
// dist/claude/hooks.js would run on session-start, post-edit and stop with no
// install step and no prompt. Absent graft, these helpers do nothing.
function entries(name) {
  const out = [];
  for (const d of candidates()) {
    const f = path.join(d, name);
    if (fs.existsSync(f)) out.push(f);
  }
  return out;
}

// Try each candidate in turn: a stale or half-installed first hit must not
// mask a working installation further down the list. Only a *load* failure
// falls through -- once main() has been reached it owns the outcome, and
// retrying the next candidate would run its side effects twice.
(async () => {
  for (const f of entries("statusline.js")) {
    let mod;
    try {
      mod = await import(pathToFileURL(f).href);
    } catch {
      continue;
    }
    if (typeof mod.main !== 'function') continue;
    try { await mod.main(); } catch { /* status line failed — render nothing */ }
    return;
  }
  /* graft unavailable — no-op */
})();
