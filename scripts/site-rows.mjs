#!/usr/bin/env node
// Writes the no-JavaScript rows of the hero table in docs/index.html: the
// simulated run in docs/site.js at START, in exactly the markup site.js paints,
// so the page does not jump when the script takes over. Run it after changing
// the run; tests/site_pages.test.mjs fails while the two disagree.
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const sim = require(path.join(here, "..", "docs", "site.js"));

const escape = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const cell = (text, tone) => (tone ? `<td class="t-${tone}">${escape(text)}</td>` : `<td>${escape(text)}</td>`);

export function renderRows() {
  const run = sim.buildRun();
  return sim.HOSTS.map((_, i) => {
    const r = run.rowAt(i, sim.START);
    const bars = r.history.map((ms) => (ms === null ? '<i class="fail"></i>' : `<i class="${sim.toneFor(ms)} h${sim.levelOf(ms)}"></i>`)).join("");
    return [
      "            <tr>",
      `<th scope="row">${escape(r.host)}</th>`,
      `<td>${escape(r.address)}</td>`,
      `<td class="s-${r.state}">${r.state}</td>`,
      cell(sim.fmt(r.last), r.tones.last),
      cell(sim.fmt(r.min), ""),
      cell(sim.fmt(r.avg), r.tones.avg),
      cell(sim.fmt(r.max), r.tones.max),
      cell(sim.fmt(r.jitter), r.tones.jitter),
      cell(sim.fmtLoss(r.loss), r.tones.loss),
      `<td>${r.mode}</td>`,
      `<td><span class="bars" role="img" aria-label="${escape(sim.historyLabel(r.history))}">${bars}</span></td>`,
      "</tr>",
    ].join("");
  }).join("\n");
}

export const ROWS_PATTERN = /(<tbody data-rows>\n)[\s\S]*?(\n\s*<\/tbody>)/;

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const file = path.join(here, "..", "docs", "index.html");
  const html = readFileSync(file, "utf8");
  if (!ROWS_PATTERN.test(html)) throw new Error("docs/index.html has no <tbody data-rows> block");
  writeFileSync(file, html.replace(ROWS_PATTERN, `$1${renderRows()}$2`));
  console.log("wrote", sim.HOSTS.length, "rows at tick", sim.START, sim.clockAt(sim.START));
}
