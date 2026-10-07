import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { runInNewContext } from 'node:vm';
import { renderRows } from '../scripts/site-rows.mjs';

// Deploy-gating contract for pinghue.com (docs/, GitHub Pages, no build step).
// It pins security and integrity, and it pins product truth: the hero is the
// real TUI, so its columns, keys, colors, scale and state rules are read from
// src/pinghue rather than restated here.

const read = (path) => readFileSync(path, 'utf8');
const pngDimensions = (path) => {
  const png = readFileSync(path);
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
};
const stripCssComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, '');
const cssRuleBody = (source, selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp('(?:^|[\\s,}])' + escaped + '\\s*\\{([^}]*)\\}', 'm'));
  assert.ok(match, 'missing CSS rule for ' + selector);
  return match[1];
};
const attributeCount = (source, attribute) => (
  source.match(new RegExp('\\s' + attribute + '(?=[\\s=>])', 'g')) || []
).length;
const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

const TITLE = 'pinghue - concurrent ICMP and TCP ping monitor';
const DESCRIPTION = 'Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.';
const IMAGE_ALT = 'The pinghue terminal table monitoring several hosts during a maintenance window.';
const CSP = 'default-src \'none\'; script-src \'self\'; style-src \'self\'; img-src \'self\'; font-src \'self\'; media-src \'self\'; base-uri \'none\'; form-action \'none\'';
const CSP_TAG = `<meta http-equiv="Content-Security-Policy" content="${CSP}">`;
const INSTALL_COMMANDS = [
  'uv tool install pinghue',
  'pipx install pinghue',
  'brew install inxbit/tap/pinghue',
  'python -m pip install pinghue',
];

/* ------------------------------------------------ product sources */

const pythonBuckets = () => {
  const source = read('src/pinghue/history.py');
  const block = source.match(/BAR_BUCKETS[^=]*=\s*\(([\s\S]*?)\n\)/);
  assert.ok(block, 'BAR_BUCKETS not found in history.py');
  const buckets = [...block[1].matchAll(/\(\s*([\d.]+)\s*,\s*"(.)"\s*\)/g)]
    .map(([, limit, glyph]) => [Number(limit), glyph]);
  assert.equal(buckets.length, 7);
  return buckets;
};
const pythonNumber = (path, pattern) => {
  const match = read(path).match(pattern);
  assert.ok(match, pattern + ' not found in ' + path);
  return Number(match[1]);
};
const pythonString = (path, pattern) => {
  const match = read(path).match(pattern);
  assert.ok(match, pattern + ' not found in ' + path);
  return match[1];
};
const cliDefault = (flag) => pythonNumber(
  'src/pinghue/cli.py',
  new RegExp('"' + flag + '",[^)]*?default=([\\d.]+)'),
);
const projectVersion = () => pythonString('pyproject.toml', /^version = "([^"]+)"$/m);
const tuiColumns = () => {
  const block = read('src/pinghue/ui.py').match(/COLUMN_KEYS = \(([\s\S]*?)\)/);
  assert.ok(block, 'COLUMN_KEYS not found in ui.py');
  return [...block[1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
};
const tuiBindings = () => [...read('src/pinghue/app.py').matchAll(/Binding\("(\w)", "\w+", "([^"]+)"\)/g)]
  .map(([, key, label]) => [key, label]);
const readmeNotList = () => {
  const section = read('README.md').match(/## What This Is Not\n\n([\s\S]*?)\n\n## /);
  assert.ok(section, 'README "What This Is Not" section missing');
  return [...section[1].matchAll(/^- (.+)$/gm)].map((m) => m[1]);
};

/* ------------------------------------------------ site.js as a module */

// site.js exports its pure core when a "module" global exists, then returns
// before it touches the DOM.
const loadSiteModule = () => {
  const module = { exports: {} };
  runInNewContext(read('docs/site.js'), {
    module,
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    window: {},
    navigator: {},
    setTimeout,
    clearTimeout,
  });
  for (const name of ['buildRun', 'levelOf', 'glyphFor', 'toneFor', 'historyLabel', 'fmt', 'fmtLoss', 'clockAt', 'START', 'HISTORY', 'RUN_SECONDS', 'HOSTS']) {
    assert.ok(name in module.exports, 'site.js must export ' + name);
  }
  return module.exports;
};

/* ------------------------------------------------ static contract */

test('GitHub Pages site ships the expected files', () => {
  for (const path of [
    'docs/index.html',
    'docs/site.css',
    'docs/site.js',
    'docs/404.html',
    'docs/.nojekyll',
    'docs/robots.txt',
    'docs/sitemap.xml',
    'docs/assets/pinghue-favicon.svg',
    'docs/assets/pinghue-wordmark.svg',
    'docs/assets/pinghue-social-card.png',
    'docs/assets/hero-poster.webp',
    'docs/fonts/zalando-sans-latin.woff2',
    'docs/fonts/ubuntu-sans-mono-latin.woff2',
    'docs/media/hero.mp4',
  ]) {
    assert.equal(existsSync(path), true, path + ' must exist');
  }
  assert.equal(read('docs/CNAME').trim(), 'pinghue.com');
  assert.deepEqual(pngDimensions('docs/assets/pinghue-social-card.png'), { width: 1200, height: 630 });

  // The previous builds are gone, not shadowing this one.
  for (const path of [
    'docs/styles.css',
    'docs/script.js',
    'docs/assets/slate-texture.jpg',
    'docs/fonts/archivo-var-latin.woff2',
    'docs/fonts/jetbrains-mono-var-latin.woff2',
  ]) {
    assert.equal(existsSync(path), false, path + ' must not be published');
  }

  // Binary assets are what their names say, and the footage stays small.
  for (const name of ['zalando-sans-latin', 'ubuntu-sans-mono-latin']) {
    assert.equal(readFileSync('docs/fonts/' + name + '.woff2').subarray(0, 4).toString('latin1'), 'wOF2');
  }
  const clip = readFileSync('docs/media/hero.mp4');
  assert.equal(clip.subarray(4, 8).toString('latin1'), 'ftyp');
  assert.ok(statSync('docs/media/hero.mp4').size < 1_000_000, 'hero.mp4 must stay under 1 MB');
  const poster = readFileSync('docs/assets/hero-poster.webp');
  assert.equal(poster.subarray(0, 4).toString('latin1'), 'RIFF');
  assert.equal(poster.subarray(8, 12).toString('latin1'), 'WEBP');
  assert.match(read('docs/assets/pinghue-wordmark.svg'), /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"[^>]*role="img" aria-label="pinghue">/);

  assert.equal(
    read('docs/robots.txt'),
    'User-agent: *\nAllow: /\nSitemap: https://pinghue.com/sitemap.xml\n',
  );
  assert.equal(
    read('docs/sitemap.xml'),
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
      '  <url><loc>https://pinghue.com/</loc></url>\n' +
      '</urlset>\n',
  );

  assert.match(read('README.md'), /https:\/\/pinghue\.com/);
});

test('index.html carries the exact title, description, social and canonical meta', () => {
  const html = read('docs/index.html');
  for (const tag of [
    '<title>' + TITLE + '</title>',
    '<meta name="description" content="' + DESCRIPTION + '">',
    '<meta name="theme-color" content="#08090a">',
    '<link rel="canonical" href="https://pinghue.com/">',
    '<link rel="icon" type="image/svg+xml" href="assets/pinghue-favicon.svg">',
    '<meta property="og:type" content="website">',
    '<meta property="og:url" content="https://pinghue.com/">',
    '<meta property="og:title" content="' + TITLE + '">',
    '<meta property="og:description" content="' + DESCRIPTION + '">',
    '<meta property="og:image" content="https://pinghue.com/assets/pinghue-social-card.png">',
    '<meta property="og:image:width" content="1200">',
    '<meta property="og:image:height" content="630">',
    '<meta property="og:image:alt" content="' + IMAGE_ALT + '">',
    '<meta name="twitter:card" content="summary_large_image">',
    '<meta name="twitter:title" content="' + TITLE + '">',
    '<meta name="twitter:description" content="' + DESCRIPTION + '">',
    '<meta name="twitter:image" content="https://pinghue.com/assets/pinghue-social-card.png">',
    '<meta name="twitter:image:alt" content="' + IMAGE_ALT + '">',
  ]) {
    assert.equal(html.includes(tag), true, 'missing ' + tag);
  }
  assert.equal((html.match(/<title>/g) || []).length, 1);
  assert.equal((html.match(/<meta name="description"/g) || []).length, 1);
});

test('both pages ship the identical strict CSP and nothing inline', () => {
  const pages = { 'docs/index.html': read('docs/index.html'), 'docs/404.html': read('docs/404.html') };
  for (const [path, html] of Object.entries(pages)) {
    assert.equal(html.includes(CSP_TAG), true, path + ' must carry the exact CSP');
    assert.equal((html.match(/http-equiv="Content-Security-Policy"/g) || []).length, 1, path);
    // No inline style or script, so 'unsafe-inline' is never needed.
    assert.doesNotMatch(html, /<style/i, path);
    assert.doesNotMatch(html, /\sstyle=/i, path);
    assert.doesNotMatch(html, /\son[a-z]+=/i, path + ' must not use inline event handlers');
    assert.doesNotMatch(html, /<script(?![^>]*\ssrc=)[^>]*>/i, path + ' must not carry inline script');
    assert.doesNotMatch(html, /fonts\.googleapis\.com|fonts\.gstatic\.com/, path);
    // Visible copy carries no em or en dashes (site copy rule).
    assert.doesNotMatch(html, /[\u2014\u2013]|&mdash;|&ndash;/, path);
  }

  const html = pages['docs/index.html'];
  const scriptSrcs = [...html.matchAll(/<script[^>]*\bsrc="([^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(scriptSrcs, ['site.js']);
  const stylesheets = [...html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="([^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(stylesheets, ['site.css']);
});

test('install commands are pinned exactly', () => {
  const html = read('docs/index.html');
  // Copyable commands are pinned exactly; a poisoned command fails the deploy.
  const copyCommands = [...html.matchAll(/data-copy="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(new Set(copyCommands), new Set(INSTALL_COMMANDS));
  // Each copy button copies the command printed next to it.
  for (const [, command, copied] of html.matchAll(/<code>([^<]+)<\/code>\s*<button class="copy-btn" type="button" data-copy="([^"]+)"/g)) {
    assert.equal(command, copied);
  }
  for (const command of INSTALL_COMMANDS) {
    assert.match(html, new RegExp('<code>' + escapeRegExp(command) + '</code>'));
  }
  assert.equal(attributeCount(html, 'data-copy-status'), 1);
  assert.match(html, /role="status"[^>]*aria-live="polite"[^>]*data-copy-status/);
});

test('page links, sections and local references resolve', () => {
  const html = read('docs/index.html');
  assert.match(html, /href="https:\/\/github\.com\/inxbit\/pinghue"/);
  assert.match(html, /href="https:\/\/pypi\.org\/project\/pinghue\/"/);
  assert.match(html, /<a class="skip-link" href="#main">Skip to content<\/a>/);
  assert.match(html, /<main id="main">/);

  for (const id of ['modes', 'scale', 'evidence', 'not', 'install']) {
    assert.match(html, new RegExp('<section[^>]*\\sid="' + id + '"'), 'section #' + id);
  }
  const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  for (const [, anchor] of html.matchAll(/href="#([^"]+)"/g)) {
    assert.equal(ids.has(anchor), true, 'dangling in-page link #' + anchor);
  }

  // Every local file the page or stylesheet asks for is published.
  const css = stripCssComments(read('docs/site.css'));
  const localRefs = [
    ...[...html.matchAll(/\s(?:src|href|srcset|data-src)="([^"#:]+)"/g)].map((m) => m[1]),
    ...[...css.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]),
  ].filter((ref) => !ref.startsWith('/') || ref.length > 1);
  for (const expected of ['media/hero.mp4', 'assets/hero-poster.webp', 'assets/pinghue-wordmark.svg', 'fonts/zalando-sans-latin.woff2', 'fonts/ubuntu-sans-mono-latin.woff2']) {
    assert.ok(localRefs.includes(expected), 'page must reference ' + expected);
  }
  for (const ref of localRefs) {
    assert.equal(existsSync('docs/' + ref.replace(/^\//, '')), true, 'missing local file ' + ref);
  }
  assert.doesNotMatch(css, /@import|url\(\s*["']?(?:https?:)?\/\//, 'site.css loads nothing remote');
});

test('the hero is the real TUI, labeled as a simulated run', () => {
  const html = read('docs/index.html');
  assert.match(html, /<h1 id="hero-title">Every host\. One live table\.<\/h1>/);
  const term = html.match(/<figure class="term"[^>]*>[\s\S]*?<\/figure>/);
  assert.ok(term, 'hero terminal missing');
  assert.equal(attributeCount(html, 'data-term'), 1);

  // The columns are COLUMN_KEYS, in order, as the TUI prints them.
  const columns = [...term[0].match(/<thead>[\s\S]*?<\/thead>/)[0].matchAll(/<th scope="col">([^<]*)<\/th>/g)].map((m) => m[1]);
  assert.deepEqual(columns, tuiColumns());

  // The window title is the Textual app title with the released version.
  assert.match(read('src/pinghue/app.py'), /TITLE = f"PingHUE v\{__version__\}"/);
  assert.match(term[0], new RegExp('<span class="term-title" aria-hidden="true">PingHUE v' + escapeRegExp(projectVersion()) + '</span>'));

  // Every key on the page is a real binding with its real label, in order.
  // They ship disabled, so without the script they are the TUI footer and nothing more.
  const keys = [...term[0].matchAll(/<button class="key" type="button" data-key="(\w)" disabled><kbd>(\w)<\/kbd>([^<]+)<\/button>/g)]
    .map(([, dataKey, key, label]) => {
      assert.equal(dataKey, key);
      return [key, label];
    });
  assert.deepEqual(keys, tuiBindings());

  // The table repaints every second; it must not flood a screen reader, and
  // it says plainly that the run is simulated.
  assert.doesNotMatch(term[0].match(/<table[\s\S]*?<\/table>/)[0], /aria-live/);
  assert.match(term[0], /<figcaption id="term-cap">A simulated run in the real pinghue TUI layout: same columns, states, latency scale and keys<span class="wide-only">, with addresses shown as if you had pressed <kbd>a<\/kbd><\/span><span class="narrow-only">; a narrow screen shows five of the eleven columns<\/span>\. The hosts are made up\.<span class="js-only"> The keys work here too\.<\/span><\/figcaption>/);
  assert.match(read('docs/site.js'), /btn\.disabled = false;/);
  // Everything that moves can be paused (WCAG 2.2.2); the control is a JS enhancement.
  assert.match(term[0], /<button class="term-pause" type="button" aria-label="Pause the live demo" data-pause hidden>/);
  // The footage is decoration and loads only on demand.
  assert.match(html, /<div class="backdrop" aria-hidden="true">/);
  assert.match(html, /<video muted playsinline loop preload="none" data-src="media\/hero\.mp4"><\/video>/);
  assert.match(html, /<img class="still" src="assets\/hero-poster\.webp" width="2508" height="1412" alt=""/);
});

test('every version the site shows is the released one', () => {
  // scripts/site-version.mjs rewrites these from pyproject.toml at release time.
  const check = spawnSync(process.execPath, ['scripts/site-version.mjs', '--check'], { encoding: 'utf8' });
  assert.equal(check.status, 0, check.stderr || check.stdout);
  const version = projectVersion();
  assert.match(read('docs/404.html'), new RegExp('<span class="window-title">PingHUE v' + escapeRegExp(version) + '</span>'));
  assert.match(read('scripts/site-social-card.html'), new RegExp('<span>PingHUE v' + escapeRegExp(version) + '</span>'));
  assert.match(read('.github/release-checklist.md'), /node scripts\/site-version\.mjs/);
});

test('the evidence, modes and scope sections say what pinghue really does', () => {
  const html = read('docs/index.html');
  const site = loadSiteModule();

  // The report is the simulated run after 180 one-second probes (--duration 180).
  const stats = site.buildRun().fold(2, 0, 179);
  const number = (key) => html.match(new RegExp('"' + key + '"</span>: <span class="jn">([\\d.]+)</span>'))[1];
  assert.equal(number('sent'), String(stats.sent));
  assert.equal(number('received'), String(stats.received));
  assert.equal(number('loss_pct'), (((stats.sent - stats.received) / stats.sent) * 100).toFixed(2));
  assert.equal(number('min_ms'), stats.min.toFixed(2));
  assert.equal(number('avg_ms'), stats.mean.toFixed(2));
  assert.equal(number('max_ms'), stats.max.toFixed(2));
  assert.equal(number('jitter_ms'), stats.jitter.toFixed(2));
  assert.match(html, /"schema_version"<\/span>: <span class="jn">1<\/span>/);
  assert.match(html, /"pinghue_version"<\/span>: <span class="js">"([^"]+)"<\/span>/);
  assert.equal(html.match(/"pinghue_version"<\/span>: <span class="js">"([^"]+)"<\/span>/)[1], projectVersion());
  assert.match(html, /"samples_window"<\/span>: <span class="jn">(\d+)<\/span>/);
  const maxSamples = Number(pythonString('src/pinghue/models.py', /^MAX_TARGET_SAMPLES = ([\d_]+)$/m).replaceAll('_', ''));
  assert.equal(Number(number('samples_window')), maxSamples);
  assert.match(html, new RegExp('at most ' + maxSamples + ' per host'));

  // Every JSON key on the page exists in the v1 schema.
  const schema = JSON.parse(read('schemas/output-v1.schema.json'));
  const known = new Set([
    ...Object.keys(schema.properties),
    ...Object.values(schema.$defs).flatMap((def) => Object.keys(def.properties || {})),
  ]);
  for (const [, key] of html.matchAll(/<span class="jk">"([a-z_]+)"<\/span>/g)) {
    assert.equal(known.has(key), true, 'JSON key not in schema: ' + key);
  }
  assert.ok(schema.$defs.run.properties.exit_reason.enum.includes('deadline'));
  assert.match(html, /"exit_reason"<\/span>: <span class="js">"deadline"<\/span>/);
  assert.match(html, /"status"<\/span>: <span class="js c-amber">"intermittent"<\/span>/);
  assert.equal(pythonString('src/pinghue/cli.py', /"--host-label",[^)]*?default="([^"]+)"/), 'local');

  assert.equal(pythonNumber('src/pinghue/export.py', /^SCHEMA_VERSION = (\d+)$/m), 1);
  assert.match(read('src/pinghue/export.py'), /return 0o600/);
  assert.match(html, /<code>0600<\/code>/);
  assert.equal(pythonNumber('src/pinghue/runner.py', /^EXIT_TARGETS_DOWN = (\d+)$/m), 3);
  assert.match(html, /<code>3<\/code> a fail-on-down condition triggered/);

  // The --no-tui lines use print_sample's format.
  assert.match(read('src/pinghue/runner.py'), /f"\{sample\.status\.value\} latency=\{latency\}\{error\}"/);
  for (const [, line] of html.matchAll(/\n(\d{4}-\d\d-\d\dT[^\n<]+)/g)) {
    assert.match(line, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00 \S+ ok latency=\d+\.\d\dms$/);
  }

  const maxConcurrency = pythonNumber('src/pinghue/cli.py', /^CONCURRENCY_MAXIMUM = (\d+)$/m);
  assert.match(html, new RegExp('up to ' + maxConcurrency + ' probes at a time with <code>--concurrency</code>'));

  const scope = html.match(/<section(?=[^>]*\sid="not")[^>]*>[\s\S]*?<\/section>/);
  assert.ok(scope);
  assert.deepEqual([...scope[0].matchAll(/<li>([^<]+)<\/li>/g)].map((m) => m[1]), readmeNotList());

  const classifiers = [...read('pyproject.toml').matchAll(/Programming Language :: Python :: (3\.\d+)"/g)]
    .map((m) => m[1]);
  assert.match(html, new RegExp('Python ' + escapeRegExp(classifiers[0]) + ' to ' + escapeRegExp(classifiers.at(-1))));
});

test('the stylesheet self-hosts its faces and carries the TUI palette from ui.py', () => {
  const css = stripCssComments(read('docs/site.css'));
  const html = read('docs/index.html');
  assert.match(css, /@font-face\s*\{[^}]*font-family:\s*"Zalando Sans"[^}]*src:\s*url\("fonts\/zalando-sans-latin\.woff2"\)\s*format\("woff2"\)/);
  assert.match(css, /@font-face\s*\{[^}]*font-family:\s*"Ubuntu Sans Mono"[^}]*src:\s*url\("fonts\/ubuntu-sans-mono-latin\.woff2"\)\s*format\("woff2"\)/);
  assert.match(html, /<link rel="preload" href="fonts\/zalando-sans-latin\.woff2" as="font" type="font\/woff2" crossorigin>/);
  assert.match(html, /<link rel="preload" href="fonts\/ubuntu-sans-mono-latin\.woff2" as="font" type="font\/woff2" crossorigin>/);
  for (const retired of [/Archivo/i, /JetBrains/i, /Inconsolata/i]) {
    assert.doesNotMatch(css, retired);
    assert.doesNotMatch(html, retired);
  }

  // Inside the terminal every color is the product's own.
  const root = cssRuleBody(css, ':root');
  const ui = read('src/pinghue/ui.py');
  const uiColor = (name) => ui.match(new RegExp('^' + name + ' = "(#[0-9a-f]{6})"$', 'm'))[1];
  assert.match(root, new RegExp('--green:\\s*' + uiColor('GREEN')));
  assert.match(root, new RegExp('--amber:\\s*' + uiColor('AMBER')));
  assert.match(root, new RegExp('--red:\\s*' + uiColor('RED')));
  assert.match(root, new RegExp('--tui-text:\\s*' + uiColor('TEXT')));
  assert.match(root, new RegExp('--tui-muted:\\s*' + uiColor('MUTED')));
  assert.match(read('src/pinghue/app.py'), /background: #58a6ff 22%;/);
  assert.match(root, /--blue:\s*#58a6ff/);
  assert.match(css, /\.tui tbody tr\.is-cursor > \*\s*\{\s*background: rgb\(88 166 255 \/ 0\.22\);/);
  assert.match(root, /--bg:\s*#08090a/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);

  // The 404 page has no stylesheet of its own; these rules carry it.
  for (const selector of ['.lost', '.lost-lede', '.lost-window', '.lost-link', '.skip-link', '.visually-hidden']) {
    cssRuleBody(css, selector);
  }
  assert.match(cssRuleBody(css, '.lost-link'), /min-height:\s*48px/);
  assert.match(cssRuleBody(css, '.lost h1'), /overflow-wrap:\s*anywhere/);
});

test('site.js stays first-party and DOM-safe', () => {
  const js = read('docs/site.js');
  assert.doesNotMatch(js, /https?:\/\//, 'site.js must not reach any network origin');
  assert.doesNotMatch(js, /\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon/);
  assert.doesNotMatch(js, /\beval\(|new Function\(|innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|document\.cookie/);
  assert.match(js, /documentElement\.classList\.add\("js"\)/);
  assert.match(js, /prefers-reduced-motion: reduce/);
});

test('404 page is a down host, styled by site.css', () => {
  const notFound = read('docs/404.html');
  assert.match(notFound, /^<!DOCTYPE html>\n<html lang="en">/);
  assert.match(notFound, /<meta name="robots" content="noindex">/);
  // GitHub Pages serves this file at any missing path, so every reference is root-absolute.
  assert.match(notFound, /<link rel="stylesheet" href="\/site\.css">/);
  assert.match(notFound, /<link rel="icon" type="image\/svg\+xml" href="\/assets\/pinghue-favicon\.svg">/);
  const refs = [...notFound.matchAll(/\s(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
  for (const ref of refs) {
    assert.ok(ref === '#main' || ref.startsWith('/'), '404 reference must be root-absolute: ' + ref);
    if (ref.length > 1 && ref.startsWith('/')) assert.equal(existsSync('docs' + ref), true, 'missing ' + ref);
  }
  assert.doesNotMatch(notFound, /styles\.css|script\.js|<script/);

  assert.match(notFound, /<a class="skip-link" href="#main">Skip to content<\/a>/);
  assert.match(notFound, /<main class="lost" id="main">/);
  assert.match(notFound, /<h1>404: no reply\.<\/h1>/);
  assert.match(notFound, /class="btn lost-link" href="\/"/);
  // Three misses in a row is the CLI's default fail threshold: the page is down.
  assert.equal(cliDefault('--fail-threshold'), 3);
  assert.match(notFound, /missed three probes in a row/);
  assert.match(notFound, /<td class="s-down">down<\/td>/);
});

test('published docs tree excludes local workflow artifacts', () => {
  const publishedPaths = readdirSync('docs', { recursive: true }).map(String);
  assert.deepEqual(
    publishedPaths.filter((path) => (
      /(^|\/)\.impeccable(\/|$)|(^|\/)\.superpowers(\/|$)|\.webp\.png$|\.tmp\./i.test(path)
    )),
    [],
  );
});

test('social card generator validates a same-directory temporary file before atomic replacement', () => {
  const generator = read('scripts/gen-site-social-card.sh');
  const mktempIndex = generator.indexOf('mktemp "$output_dir/');
  const validateIndex = generator.indexOf('python3 - "$temp_output"');
  const moveIndex = generator.indexOf('mv -f -- "$temp_output" "$output"');
  assert.notEqual(mktempIndex, -1);
  assert.match(generator, /temp_stub="\$\(mktemp "\$output_dir\/\.pinghue-social-card\.tmp\.XXXXXX"\)"/);
  assert.match(generator, /temp_output="\$\{temp_stub\}\.png"/);
  assert.equal(validateIndex > mktempIndex, true);
  assert.equal(moveIndex > validateIndex, true);
  assert.match(generator, /trap cleanup EXIT HUP INT TERM/);
  assert.doesNotMatch(generator, /--screenshot="\$output"/);
});

/* ------------------------------------------------ product truth */

test('site.js scale and thresholds equal the pinghue source', () => {
  const js = read('docs/site.js');
  const buckets = pythonBuckets();

  const jsBlock = js.match(/const BUCKETS = \[(.*?)\];/);
  assert.ok(jsBlock, 'BUCKETS not found in site.js');
  const jsBuckets = [...jsBlock[1].matchAll(/\[\s*([\d.]+)\s*,\s*"(.)"\s*\]/g)]
    .map(([, limit, glyph]) => [Number(limit), glyph]);
  assert.deepEqual(jsBuckets, buckets);

  const slow = pythonNumber('src/pinghue/app.py', /^SLOW_LATENCY_MS = ([\d.]+)$/m);
  assert.equal(Number(js.match(/const SLOW_MS = ([\d.]+);/)[1]), slow);
  assert.equal(Number(js.match(/const JITTER_THRESHOLD = ([\d.]+);/)[1]), cliDefault('--jitter-threshold'));
  assert.equal(Number(js.match(/const FAIL_THRESHOLD = ([\d.]+);/)[1]), cliDefault('--fail-threshold'));

  // Behaviour, not just text: every bucket edge maps the way history_symbol does.
  const history = read('src/pinghue/history.py');
  assert.match(history, /return "█"\n/);
  assert.match(history, /return "·"\n/);
  const site = loadSiteModule();
  buckets.forEach(([limit, glyph], i) => {
    assert.equal(site.glyphFor(limit), glyph, 'glyph at ' + limit + 'ms');
    assert.equal(site.levelOf(limit), i + 1);
    const above = i + 1 < buckets.length ? buckets[i + 1][1] : '█';
    assert.equal(site.glyphFor(limit + 0.01), above, 'glyph just above ' + limit + 'ms');
  });
  assert.equal(site.levelOf(buckets.at(-1)[0] + 0.01), 8);
  assert.equal(site.glyphFor(null), '·');
  assert.equal(site.toneFor(null), 'fail');
  assert.equal(site.toneFor(slow), 'ok');
  assert.equal(site.toneFor(slow + 0.01), 'slow');
});

test('the printed scale on the page is the real scale', () => {
  const html = read('docs/index.html');
  const buckets = pythonBuckets();
  const slow = pythonNumber('src/pinghue/app.py', /^SLOW_LATENCY_MS = ([\d.]+)$/m);
  const ruler = html.match(/<ol class="ruler"[^>]*>([\s\S]*?)<\/ol>/);
  assert.ok(ruler, 'scale ruler missing');
  const steps = [...ruler[1].matchAll(/<li><span class="bars"><i class="(ok|slow) h(\d)"><\/i><\/span><span class="band">([^<]+)<\/span><\/li>/g)]
    .map(([, tone, level, band]) => [tone, Number(level), band]);
  assert.deepEqual(steps, [
    ...buckets.map(([limit], i) => [limit <= slow ? 'ok' : 'slow', i + 1, 'up to ' + limit + '&nbsp;ms']),
    ['slow', 8, 'over ' + buckets.at(-1)[0] + '&nbsp;ms'],
  ]);
  assert.match(ruler[1], /<li class="ruler-mark"><span class="bars"><i class="fail"><\/i><\/span><span class="band">lost, down<\/span><\/li>/);
  assert.match(ruler[1], /<li class="ruler-mark"><span class="bars"><i class="refused"><\/i><\/span><span class="band">TCP refused<\/span><\/li>/);
  assert.match(read('src/pinghue/history.py'), /REFUSED:\n\s*return "!"/);
  assert.match(html, new RegExp('Green until ' + slow + '&nbsp;ms, amber past it\\.'));
});

test('the no-JS hero rows are the simulated frame at START', () => {
  const html = read('docs/index.html');
  const body = html.match(/<tbody data-rows>\n([\s\S]*?)\n\s*<\/tbody>/);
  assert.ok(body, 'static table body missing');
  assert.equal(body[1], renderRows(), 'run node scripts/site-rows.mjs after changing the run');
  const site = loadSiteModule();
  assert.equal(site.clockAt(site.START), '02:31:04');
  assert.match(html, /<span class="term-clock" aria-hidden="true" data-clock>02:31:04<\/span>/);
});

test('the simulated night follows classify_samples and the ui.py cell tones', () => {
  const site = loadSiteModule();
  const run = site.buildRun();
  assert.deepEqual(site.buildRun().samples, run.samples, 'every visitor sees the same night');
  assert.deepEqual(JSON.parse(JSON.stringify(site.HOSTS.map(([host]) => host))),
    ['edge-router-1', 'core-sw-1', 'db-primary', 'api-gw', 'backup-nas', 'dns-resolver']);

  const failThreshold = cliDefault('--fail-threshold');
  const jitterThreshold = cliDefault('--jitter-threshold');
  const slow = pythonNumber('src/pinghue/app.py', /^SLOW_LATENCY_MS = ([\d.]+)$/m);
  const classify = (series) => {
    // models.py classify_samples, restated independently over raw samples.
    let fails = 0;
    let received = 0;
    let prev = null;
    let jitter = 0;
    let jitterMax = 0;
    for (const ms of series) {
      if (ms === null) {
        fails += 1;
        continue;
      }
      fails = 0;
      received += 1;
      if (prev !== null) {
        jitter += (Math.abs(ms - prev) - jitter) / 16;
        jitterMax = Math.max(jitterMax, jitter);
      }
      prev = ms;
    }
    if (!series.length || fails >= failThreshold || received === 0) return 'down';
    if (received < series.length) return 'intermittent';
    if (received >= 2 && jitterMax > jitterThreshold) return 'intermittent';
    return 'healthy';
  };

  for (let i = 0; i < site.HOSTS.length; i++) {
    const samples = run.samples[i];
    for (let t = 0; t < site.RUN_SECONDS; t += 7) {
      const series = samples.slice(0, t + 1);
      const row = run.rowAt(i, t);
      assert.equal(row.state, classify(series), site.HOSTS[i][0] + ' at tick ' + t);
      const newest = series.at(-1);
      const lastGood = series.filter((ms) => ms !== null).at(-1) ?? null;
      assert.equal(row.last, lastGood, 'last is the newest probe with a latency');
      assert.equal(row.tones.last, newest === null ? 'red' : newest > slow ? 'amber' : '');
      assert.equal(row.tones.loss, series.includes(null) ? 'red' : '');
      assert.equal(row.tones.max, row.max !== null && row.max > slow ? 'amber' : '');
      assert.equal(row.history.length, Math.min(site.HISTORY, t + 1));
    }
  }

  // The opening frame tells the story the page tells.
  const at = Object.fromEntries(site.HOSTS.map(([host], i) => [host, run.rowAt(i, site.START)]));
  assert.equal(at['db-primary'].state, 'intermittent');
  assert.equal(at['backup-nas'].state, 'down');
  for (const host of ['edge-router-1', 'core-sw-1', 'api-gw', 'dns-resolver']) {
    assert.equal(at[host].state, 'healthy', host);
  }
});

/* ------------------------------------------------ copy buttons */

const createStubElement = (initialAttributes = {}) => {
  const attributes = new Map(Object.entries(initialAttributes));
  const classes = new Set();
  const listeners = new Map();
  return {
    attributes,
    classes,
    listeners,
    hidden: false,
    textContent: '',
    getAttribute: (name) => attributes.get(name),
    setAttribute: (name, value) => attributes.set(name, value),
    addEventListener: (event, handler) => listeners.set(event, handler),
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle: (name, enabled) => (enabled ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    },
  };
};

const createCopyHarness = (writeText, commands = ['uv tool install pinghue']) => {
  const status = { textContent: '' };
  const buttons = commands.map((command) => createStubElement({
    'aria-label': 'Copy install command',
    'data-copy': command,
  }));
  const canceledTimerIds = [];
  const scheduledTimerIds = [];
  const timers = new Map();
  let nextTimerId = 1;

  // No "module" global here: site.js must run its DOM half.
  runInNewContext(read('docs/site.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: (selector) => (selector === '.copy-btn' ? buttons : []),
      querySelector: (selector) => (selector === '[data-copy-status]' ? status : null),
      addEventListener: () => {},
      hidden: false,
    },
    window: { matchMedia: () => ({ matches: false }), addEventListener: () => {} },
    navigator: { clipboard: { writeText } },
    clearTimeout: (id) => {
      if (id !== null && id !== undefined && timers.delete(id)) canceledTimerIds.push(id);
    },
    setTimeout: (handler) => {
      const id = nextTimerId;
      nextTimerId += 1;
      scheduledTimerIds.push(id);
      timers.set(id, handler);
      return id;
    },
    setInterval: () => 1,
    clearInterval: () => {},
  });

  const runTimer = (id) => {
    const handler = timers.get(id);
    if (!handler) return false;
    timers.delete(id);
    handler();
    return true;
  };

  return {
    canceledTimerIds,
    button: buttons[0],
    buttons,
    getActiveTimerIds: () => [...timers.keys()],
    reset: (index = 0) => runTimer(scheduledTimerIds[index]),
    runTimer,
    scheduledTimerIds,
    status,
  };
};

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('copy buttons report a rejected clipboard write as a failure', async () => {
  const harness = createCopyHarness(() => Promise.reject(new Error('clipboard denied')));
  harness.button.listeners.get('click')();
  await settle();
  assert.equal(harness.button.classes.has('failed'), true);
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy failed');
});

test('copy buttons announce success and reset their visible and accessible state', async () => {
  const harness = createCopyHarness(() => Promise.resolve());
  harness.button.listeners.get('click')();
  await settle();
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.attributes.get('aria-label'), 'Command copied');
  assert.equal(harness.status.textContent, 'Install command copied.');

  harness.reset();
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy install command');
  assert.equal(harness.status.textContent, '');
});

test('copy buttons copy exactly their data-copy command', async () => {
  const copied = [];
  const harness = createCopyHarness((text) => {
    copied.push(text);
    return Promise.resolve();
  }, INSTALL_COMMANDS);
  harness.buttons.forEach((button) => button.listeners.get('click')());
  await settle();
  assert.deepEqual(copied, INSTALL_COMMANDS);
});

test('an older copy reset cannot clear a newer shared announcement', async () => {
  const firstCommand = 'uv tool install pinghue';
  const secondCommand = 'brew install inxbit/tap/pinghue';
  const harness = createCopyHarness(
    (command) => (command === firstCommand ? Promise.resolve() : Promise.reject(new Error('clipboard denied'))),
    [firstCommand, secondCommand],
  );

  harness.buttons[0].listeners.get('click')();
  await settle();
  assert.equal(harness.status.textContent, 'Install command copied.');

  harness.buttons[1].listeners.get('click')();
  await settle();
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');

  harness.reset(0);
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');
  harness.reset(1);
  assert.equal(harness.status.textContent, '');
});

test('a new click cancels the current button reset and neutralizes stale feedback', async () => {
  const harness = createCopyHarness(() => Promise.resolve());

  harness.button.listeners.get('click')();
  await settle();
  const staleReset = harness.scheduledTimerIds[0];
  assert.deepEqual(harness.getActiveTimerIds(), [staleReset]);
  assert.equal(harness.button.classes.has('copied'), true);

  harness.button.listeners.get('click')();
  assert.deepEqual(harness.canceledTimerIds, [staleReset]);
  assert.deepEqual(harness.getActiveTimerIds(), []);
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy install command');
  assert.equal(harness.status.textContent, '');
  assert.equal(harness.runTimer(staleReset), false);

  await settle();
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.status.textContent, 'Install command copied.');
});

test('an older same-button completion cannot mutate or cancel the newer result', async () => {
  const operations = [];
  const harness = createCopyHarness(() => new Promise((resolve, reject) => {
    operations.push({ reject, resolve });
  }));

  harness.button.listeners.get('click')();
  harness.button.listeners.get('click')();
  assert.equal(operations.length, 2);

  operations[1].resolve();
  await settle();
  const currentReset = harness.scheduledTimerIds[0];
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.attributes.get('aria-label'), 'Command copied');
  assert.equal(harness.status.textContent, 'Install command copied.');
  assert.deepEqual(harness.getActiveTimerIds(), [currentReset]);

  operations[0].reject(new Error('older clipboard failure'));
  await settle();
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.classes.has('failed'), false);
  assert.equal(harness.status.textContent, 'Install command copied.');
  assert.deepEqual(harness.canceledTimerIds, []);
  assert.deepEqual(harness.scheduledTimerIds, [currentReset]);

  assert.equal(harness.runTimer(currentReset), true);
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy install command');
  assert.equal(harness.status.textContent, '');
  assert.equal(harness.runTimer(currentReset), false);
});

/* ------------------------------------------------ motion */

const createMotionHarness = ({ reduced }) => {
  const video = createStubElement({ 'data-src': 'media/hero.mp4' });
  let plays = 0;
  video.paused = true;
  video.play = () => {
    plays += 1;
    return Promise.reject(new Error('autoplay blocked'));
  };
  video.pause = () => {};
  const hero = createStubElement();
  hero.querySelector = (selector) => (selector === 'video' ? video : null);
  const pause = createStubElement({ 'aria-label': 'Pause the live demo' });
  pause.hidden = true;

  const intervals = [];
  runInNewContext(read('docs/site.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: () => [],
      querySelector: (selector) => ({ '[data-hero]': hero, '[data-pause]': pause })[selector] || null,
      addEventListener: () => {},
      hidden: false,
    },
    window: { matchMedia: (query) => ({ matches: reduced && query === '(prefers-reduced-motion: reduce)' }) },
    navigator: {},
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: (handler, delay) => {
      intervals.push(delay);
      return intervals.length;
    },
    clearInterval: () => {},
  });
  return { intervals, pause, plays: () => plays, video };
};

test('reduced motion keeps the table still and never loads the footage', async () => {
  const harness = createMotionHarness({ reduced: true });
  await settle();
  assert.deepEqual(harness.intervals, []);
  assert.equal(harness.plays(), 0);
  assert.equal(harness.video.src, undefined);
  // The pause control appears, offering to play.
  assert.equal(harness.pause.hidden, false);
  assert.equal(harness.pause.attributes.get('aria-label'), 'Play the live demo');
});

test('without reduced motion the table ticks once a second and the footage is attempted', async () => {
  const harness = createMotionHarness({ reduced: false });
  await settle();
  assert.deepEqual(harness.intervals, [1000]);
  assert.equal(harness.plays(), 1);
  assert.equal(harness.pause.attributes.get('aria-label'), 'Pause the live demo');

  harness.pause.listeners.get('click')();
  assert.equal(harness.pause.attributes.get('aria-label'), 'Play the live demo');
  assert.equal(harness.pause.classes.has('is-paused'), true);
});
