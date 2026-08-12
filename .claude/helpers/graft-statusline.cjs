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

function entry(name) {
  for (const d of candidates()) {
    const f = path.join(d, name);
    if (fs.existsSync(f)) return f;
  }
  return path.join(dir, 'dist', 'claude', name); // last-ditch; import will no-op if absent
}

import(pathToFileURL(entry("statusline.js")).href).then((m) => m.main()).catch(() => { /* graft unavailable — no-op */ });
