#!/usr/bin/env node
/**
 * Regenerates SPLASH_ART and SPLASH_ART_NARROW in banner.py using oh-my-logo.
 *
 * Usage:
 *   npm run regen-splash
 *   node scripts/regen_splash.js
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const BANNER_PY = path.resolve(__dirname, "../app/cli/interactive_shell/banner.py");

function render(blockFont, extraArgs = []) {
  const extra = extraArgs.join(" ");
  const raw = execSync(
    `npx oh-my-logo OPENSRE matrix --filled --block-font ${blockFont} --no-color ${extra}`.trim(),
    { encoding: "utf8" }
  );
  // Strip ANSI escape codes, strip trailing whitespace per line, drop blank lines.
  const clean = raw
    .replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "")
    .replace(/\r/g, "")
    .split("\n")
    .filter((l) => l.trim().length > 0)
    .map((l) => l.trimEnd())
    .join("\n");
  const maxWidth = Math.max(...clean.split("\n").map((l) => l.length));
  return { art: clean, maxWidth };
}

const primary = render("block", ["--letter-spacing", "0"]);
const narrow = render("simpleBlock");

console.log(`grid:        ${primary.maxWidth} cols`);
console.log(`simpleBlock: ${narrow.maxWidth} cols`);

let src = fs.readFileSync(BANNER_PY, "utf8");

function replaceBetween(src, startMarker, endMarker, replacement) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker, start + startMarker.length);
  if (start === -1 || end === -1) {
    throw new Error(`Could not find markers:\n  start: ${startMarker}\n  end:   ${endMarker}`);
  }
  return src.slice(0, start) + replacement + src.slice(end + endMarker.length);
}

const artToTriple = (art) => `"""\\\n${art}"""`;

src = replaceBetween(
  src,
  'SPLASH_ART = """\\\n',
  '"""',
  `SPLASH_ART = ${artToTriple(primary.art)}\n`
);

src = replaceBetween(
  src,
  'SPLASH_ART_NARROW = """\\\n',
  '"""',
  `SPLASH_ART_NARROW = ${artToTriple(narrow.art)}\n`
);

fs.writeFileSync(BANNER_PY, src);
console.log("Updated", BANNER_PY);
