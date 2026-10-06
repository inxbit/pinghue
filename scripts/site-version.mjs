#!/usr/bin/env node
// Sets the pinghue version that pinghue.com shows to the one in pyproject.toml:
// the TUI title in the hero and on the 404 page, the "pinghue_version" of the
// JSON report, and the social card (its template, then the re-rendered PNG).
//
//   node scripts/site-version.mjs            update every spot, re-render the card
//   node scripts/site-version.mjs --no-card  update the text only; render the card
//                                           later with scripts/gen-site-social-card.sh
//   node scripts/site-version.mjs --check    change nothing, exit 1 if a spot is stale
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const at = (relative) => path.join(root, relative);
const args = new Set(process.argv.slice(2));

const version = readFileSync(at("pyproject.toml"), "utf8").match(/^version = "([^"]+)"$/m)?.[1];
if (!version) {
  console.error("no version = \"...\" line in pyproject.toml");
  process.exit(1);
}

// Each spot prints the version as the second group, between the first and third.
const SPOTS = [
  ["docs/index.html", /(<span class="term-title" aria-hidden="true">PingHUE v)([^<]+)(<\/span>)/g],
  ["docs/index.html", /(<span class="jk">"pinghue_version"<\/span>: <span class="js">")([^"]+)(")/g],
  ["docs/404.html", /(<span class="window-title">PingHUE v)([^<]+)(<\/span>)/g],
  ["scripts/site-social-card.html", /(<span>PingHUE v)([^<]+)(<\/span>)/g],
];

const files = new Map();
const stale = [];
for (const [file, pattern] of SPOTS) {
  if (!files.has(file)) files.set(file, readFileSync(at(file), "utf8"));
  const text = files.get(file);
  const found = [...text.matchAll(pattern)];
  if (found.length !== 1) {
    console.error(`${file}: expected exactly one version spot for ${pattern}, found ${found.length}`);
    process.exit(1);
  }
  if (found[0][2] !== version) {
    stale.push(`${file}: ${found[0][2]}`);
    files.set(file, text.replace(pattern, `$1${version}$3`));
  }
}

if (args.has("--check")) {
  if (stale.length) {
    console.error(`site shows an old version (pyproject.toml says ${version}):\n  ${stale.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`site version spots match ${version}`);
  process.exit(0);
}

if (!stale.length) {
  console.log(`site already shows ${version}`);
  process.exit(0);
}

for (const [file, text] of files) writeFileSync(at(file), text);
console.log(`site version set to ${version}:\n  ${stale.join("\n  ")}`);

const cardChanged = stale.some((spot) => spot.startsWith("scripts/site-social-card.html"));
if (cardChanged && !args.has("--no-card")) {
  const render = spawnSync("bash", [at("scripts/gen-site-social-card.sh")], { stdio: "inherit" });
  if (render.status !== 0) {
    console.error("the card template is updated but the PNG is not: run scripts/gen-site-social-card.sh (needs Chrome)");
    process.exit(1);
  }
}
