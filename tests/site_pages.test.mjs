import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

// Deploy-gating contract for pinghue.com (docs/, GitHub Pages, no build step).
// It pins security and integrity, and it pins product truth: the page draws in
// pinghue's own fixed glyph scale, so the numbers here are read from src/pinghue.

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

const TITLE = 'PingHue - concurrent ICMP and TCP ping monitor';
const DESCRIPTION = 'Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.';
const IMAGE_ALT = 'A latency terrain drawn in pinghue\'s fixed glyph scale above the table pinghue prints.';
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
const cliDefault = (flag) => pythonNumber(
  'src/pinghue/cli.py',
  new RegExp('"' + flag + '",[^)]*?default=([\\d.]+)'),
);

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
  for (const name of ['buildRun', 'glyphFor', 'toneFor', 'bandOf', 'fmt', 'fmtLoss', 'START', 'HISTORY', 'PERIOD']) {
    assert.ok(name in module.exports, 'site.js must export ' + name);
  }
  return module.exports;
};

const TABLE_TAIL = 14;

// The flat table's cells exactly as site.js renderTable() writes them.
// JSON round trip: arrays built inside the vm context carry that realm's prototype.
const expectedRows = (site, tick) => {
  const run = site.buildRun();
  return JSON.parse(JSON.stringify(run.frames[tick].map((f, i) => ({
    host: f.host,
    cells: [
      ['st-' + f.state, f.state],
      [f.last === null ? 't-fail' : f.last > 100 ? 't-slow' : '', site.fmt(f.last)],
      [f.avg !== null && f.avg > 100 ? 't-slow' : '', site.fmt(f.avg)],
      [f.jitter > 50 ? 't-slow' : '', site.fmt(f.jitter)],
      [f.loss > 0 ? 't-fail' : '', site.fmtLoss(f.loss)],
    ],
    history: run.historyAt(i, tick).slice(-TABLE_TAIL)
      .map((ms) => ['g-' + site.toneFor(ms), site.glyphFor(ms)]),
  }))));
};

const staticRows = (html) => {
  const body = html.match(/<tbody data-rows>([\s\S]*?)<\/tbody>/);
  assert.ok(body, 'static table body missing');
  return [...body[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(([, row]) => {
    const host = row.match(/<th scope="row">([^<]*)<\/th>/);
    assert.ok(host, 'row header missing in ' + row);
    const tds = [...row.matchAll(/<td(?: class="([^"]*)")?>([\s\S]*?)<\/td>/g)];
    assert.equal(tds.length, 6, 'each row has six data cells');
    return {
      host: host[1],
      cells: tds.slice(0, 5).map(([, cls = '', text]) => [cls, text]),
      history: [...tds[5][2].matchAll(/<span class="([^"]*)">([^<]*)<\/span>/g)]
        .map(([, cls, glyph]) => [cls, glyph]),
    };
  });
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
    'docs/assets/pinghue-social-card.png',
    'docs/assets/terrain-poster.webp',
    'docs/fonts/inconsolata-var-ascii.woff2',
    'docs/media/ridge.mp4',
    'docs/media/dawn.mp4',
    'docs/assets/dawn-poster.webp',
  ]) {
    assert.equal(existsSync(path), true, path + ' must exist');
  }
  assert.equal(read('docs/CNAME').trim(), 'pinghue.com');
  assert.deepEqual(pngDimensions('docs/assets/pinghue-social-card.png'), { width: 1200, height: 630 });

  // The old Signal Theatre build is gone, not shadowing the new one.
  for (const path of ['docs/styles.css', 'docs/script.js', 'docs/assets/slate-texture.jpg']) {
    assert.equal(existsSync(path), false, path + ' must not be published');
  }

  // Binary assets are what their names say, and the clip stays small.
  const font = readFileSync('docs/fonts/inconsolata-var-ascii.woff2');
  assert.equal(font.subarray(0, 4).toString('latin1'), 'wOF2');
  for (const name of ['ridge', 'dawn']) {
    const clip = readFileSync('docs/media/' + name + '.mp4');
    assert.equal(clip.subarray(4, 8).toString('latin1'), 'ftyp');
    assert.ok(statSync('docs/media/' + name + '.mp4').size < 1_000_000, name + '.mp4 must stay under 1 MB');
  }
  for (const name of ['terrain-poster', 'dawn-poster']) {
    const poster = readFileSync('docs/assets/' + name + '.webp');
    assert.equal(poster.subarray(0, 4).toString('latin1'), 'RIFF');
    assert.equal(poster.subarray(8, 12).toString('latin1'), 'WEBP');
  }

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
    assert.doesNotMatch(html, /[—–]|&mdash;|&ndash;/, path);
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
  for (const command of INSTALL_COMMANDS) {
    assert.match(html, new RegExp('<code>' + command.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&') + '</code>'));
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

  for (const id of ['terrain', 'modes', 'scale', 'evidence', 'not', 'install']) {
    assert.match(html, new RegExp('<section[^>]*\\sid="' + id + '"'), 'section #' + id);
  }
  const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  for (const [, anchor] of html.matchAll(/href="#([^"]+)"/g)) {
    assert.equal(ids.has(anchor), true, 'dangling in-page link #' + anchor);
  }

  // Every local file the page or stylesheet asks for is published.
  const css = stripCssComments(read('docs/site.css'));
  const localRefs = [
    ...[...html.matchAll(/\s(?:src|href|data-clip)="([^"#:]+)"/g)].map((m) => m[1]),
    ...[...css.matchAll(/url\("?([^")]+)"?\)/g)].map((m) => m[1]),
  ].filter((ref) => !ref.startsWith('/') || ref.length > 1);
  assert.ok(localRefs.includes('media/ridge.mp4'));
  assert.ok(localRefs.includes('media/dawn.mp4'));
  assert.ok(localRefs.includes('assets/terrain-poster.webp'));
  assert.ok(localRefs.includes('assets/dawn-poster.webp'));
  assert.ok(localRefs.includes('fonts/inconsolata-var-ascii.woff2'));
  for (const ref of localRefs) {
    assert.equal(existsSync('docs/' + ref.replace(/^\//, '')), true, 'missing local file ' + ref);
  }
  assert.doesNotMatch(css, /@import|url\(\s*["']?(?:https?:)?\/\//, 'site.css loads nothing remote');
});

test('the JSON evidence and the scope section say what pinghue really does', () => {
  const html = read('docs/index.html');
  assert.match(html, /"schema_version"<\/span>: <span class="jn">1<\/span>/);
  assert.match(html, /"exit_reason"<\/span>: <span class="json-string">"deadline"<\/span>/);
  assert.match(html, /"status"<\/span>: <span class="json-string c-amber">"intermittent"<\/span>/);
  assert.match(html, /"loss_pct"<\/span>: <span class="jn">1\.11<\/span>/);
  assert.doesNotMatch(html, /"exit_reason"<\/span>: <span class="json-string">"duration"<\/span>/);
  assert.doesNotMatch(html, /"state"<\/span>: <span class="json-string c-amber">"intermittent"<\/span>/);

  // The printed loss follows from the printed counters.
  const sent = Number(html.match(/"sent"<\/span>: <span class="jn">(\d+)<\/span>/)[1]);
  const received = Number(html.match(/"received"<\/span>: <span class="jn">(\d+)<\/span>/)[1]);
  assert.equal((((sent - received) / sent) * 100).toFixed(2), '1.11');

  assert.equal(pythonNumber('src/pinghue/export.py', /^SCHEMA_VERSION = (\d+)$/m), 1);
  assert.match(read('src/pinghue/export.py'), /return 0o600/);
  assert.match(html, /<code>0600<\/code>/);

  const scope = html.match(/<section(?=[^>]*\sid="not")[^>]*>[\s\S]*?<\/section>/);
  assert.ok(scope);
  assert.match(scope[0], /<h2 id="not-title">what pinghue is not<\/h2>/);
  assert.equal((scope[0].match(/<li>Not /g) || []).length, 5);
  assert.match(scope[0], /Not a privileged daemon\./);
  assert.match(scope[0], /Not a service that accepts remote network requests\./);

  const maxConcurrency = pythonNumber('src/pinghue/cli.py', /^CONCURRENCY_MAXIMUM = (\d+)$/m);
  assert.match(html, new RegExp('Up to ' + maxConcurrency + ' concurrent probes'));

  const classifiers = [...read('pyproject.toml').matchAll(/Programming Language :: Python :: (3\.\d+)"/g)]
    .map((m) => m[1]);
  assert.match(html, new RegExp('Python ' + classifiers[0].replace('.', '\\.') + ' to ' + classifiers.at(-1).replace('.', '\\.')));
});

test('the hero table is a real, quiet table', () => {
  const html = read('docs/index.html');
  const stage = html.match(/<figure(?=[^>]*\sdata-terrain(?:\s|>))[^>]*>[\s\S]*?<\/figure>/);
  assert.ok(stage);
  assert.equal(attributeCount(html, 'data-terrain'), 1);
  assert.match(stage[0], /data-clip="media\/ridge\.mp4"/);
  assert.match(stage[0], /<canvas class="stage-canvas" aria-hidden="true"><\/canvas>/);
  assert.match(stage[0], /<img class="stage-poster" src="assets\/terrain-poster\.webp"[^>]*\salt=""/);
  assert.match(stage[0], /<table class="tui" data-table>/);
  assert.match(stage[0], /<caption class="visually-hidden">[^<]+<\/caption>/);
  assert.deepEqual(
    [...stage[0].matchAll(/<th scope="col">([^<]*)<\/th>/g)].map((m) => m[1]),
    ['host', 'state', 'last', 'avg', 'jitter', 'loss', 'history'],
  );
  assert.doesNotMatch(stage[0], /role="img"/);
  assert.doesNotMatch(stage[0], /<table[^>]*aria-hidden/);
  // The table repaints every second; it must not flood a screen reader.
  assert.doesNotMatch(stage[0], /aria-live/);
  // The head-on toggle is a JS enhancement and starts hidden.
  assert.match(stage[0], /<button class="tilt-btn" type="button" data-tilt aria-pressed="false" hidden>/);
  // Everything that moves can be paused (WCAG 2.2.2); the control is a JS enhancement too.
  assert.match(stage[0], /<button class="tilt-btn" type="button" data-pause hidden>pause<\/button>/);
  // The headline art is decorative; the real heading text is there for everyone.
  assert.match(html, /<h1 class="hl" id="hero-title">\s*<span class="visually-hidden">The table is the terrain\.<\/span>/);
  assert.equal((html.match(/<span class="hl-art[^"]*" aria-hidden="true">/g) || []).length, 2);
});

test('the stylesheet is self-hosted Inconsolata on paper and serves the 404', () => {
  const css = stripCssComments(read('docs/site.css'));
  const html = read('docs/index.html');
  assert.match(css, /@font-face\s*\{[^}]*font-family:\s*"Inconsolata"[^}]*src:\s*url\("fonts\/inconsolata-var-ascii\.woff2"\)\s*format\("woff2"\)/);
  assert.match(html, /<link rel="preload" href="fonts\/inconsolata-var-ascii\.woff2" as="font" type="font\/woff2" crossorigin>/);
  for (const retired of [/Archivo/i, /JetBrains/i, /archivo-var-latin/, /jetbrains-mono-var-latin/]) {
    assert.doesNotMatch(css, retired);
    assert.doesNotMatch(html, retired);
  }

  const root = cssRuleBody(css, ':root');
  assert.match(root, /--paper:\s*#efede6/i);
  assert.match(root, /--ink:\s*#161513/i);
  assert.match(root, /--green:\s*#1d7a37/i);
  assert.match(root, /--amber:\s*#9c5a00/i);
  assert.match(root, /--red:\s*#b8322b/i);
  assert.match(html, /<meta name="theme-color" content="#efede6">/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);

  // The 404 page has no stylesheet of its own; these rules carry it.
  for (const selector of ['.lost', '.lost-shell', '.lost-link', '.lost-wordmark', '.skip-link', '.visually-hidden']) {
    cssRuleBody(css, selector);
  }
  assert.match(cssRuleBody(css, '.lost-link'), /min-height:\s*44px/);
  assert.match(cssRuleBody(css, '.lost-shell h1'), /overflow-wrap:\s*anywhere/);
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

test('404 page is the down state, styled by site.css', () => {
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
  assert.match(notFound, /<a class="wordmark lost-wordmark" href="\/" aria-label="pinghue home">/);
  assert.match(notFound, /<div class="lost-shell">/);
  assert.match(notFound, /<h1>[^<]*404/);
  assert.match(notFound, /class="lost-link" href="\/"/);
  // The message is a probe line in pinghue's own terms: three misses, state down.
  assert.match(notFound, /<span class="st-down">down<\/span>/);
  assert.match(notFound, /three consecutive misses/i);
});

test('published docs tree excludes local workflow artifacts', () => {
  const publishedPaths = readdirSync('docs', { recursive: true }).map(String);
  assert.deepEqual(
    publishedPaths.filter((path) => (
      /(^|\/)\.impeccable(\/|$)|(^|\/)\.superpowers(\/|$)|signal-theatre/i.test(path)
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
  assert.match(history, /REFUSED:\n\s*return "!"/);
  const site = loadSiteModule();
  buckets.forEach(([limit, glyph], i) => {
    assert.equal(site.glyphFor(limit), glyph, 'glyph at ' + limit + 'ms');
    assert.equal(site.bandOf(limit), i);
    const above = i + 1 < buckets.length ? buckets[i + 1][1] : '█';
    assert.equal(site.glyphFor(limit + 0.01), above, 'glyph just above ' + limit + 'ms');
  });
  assert.equal(site.glyphFor(0), buckets[0][1]);
  assert.equal(site.glyphFor(null), '·');
  assert.equal(site.toneFor(null), 'fail');
  assert.equal(site.toneFor(slow), 'ok');
  assert.equal(site.toneFor(slow + 0.01), 'slow');
  assert.equal(site.toneFor(0), 'ok');

  // The scale lab's band labels are the same buckets.
  const labLimits = JSON.parse('[' + js.match(/const LIMITS = \[(.*?)\];/)[1] + ']');
  assert.deepEqual(labLimits, [...buckets.map(([limit]) => '≤' + limit + 'ms'), '>' + buckets.at(-1)[0] + 'ms']);
});

test('the printed scale on the page is the real scale', () => {
  const html = read('docs/index.html');
  const buckets = pythonBuckets();
  const slow = pythonNumber('src/pinghue/app.py', /^SLOW_LATENCY_MS = ([\d.]+)$/m);
  const steps = [...html.matchAll(/<li data-step>[\s\S]*?<span class="rw-limit"><span class="g-(\w+)">(.)<\/span> (≤|&gt;)(\d+)ms<\/span><\/li>/g)]
    .map(([, tone, glyph, op, limit]) => [tone, glyph, op, Number(limit)]);
  assert.deepEqual(steps, [
    ...buckets.map(([limit, glyph]) => [limit <= slow ? 'ok' : 'slow', glyph, '≤', limit]),
    ['slow', '█', '&gt;', buckets.at(-1)[0]],
  ]);
  assert.match(html, /<span class="rw-limit"><span class="g-fail">·<\/span> loss or down<\/span>/);
  assert.match(html, /<span class="rw-limit"><span class="g-slow">!<\/span> TCP refused<\/span>/);
  assert.match(html, /Green up to 100ms, amber above it, red for loss\./);

  const okGlyphs = buckets.filter(([limit]) => limit <= slow).map(([, glyph]) => glyph).join('');
  const slowGlyphs = buckets.filter(([limit]) => limit > slow).map(([, glyph]) => glyph).join('') + '█';
  // The footer mark is the whole ramp in ink: color is reserved for state.
  assert.match(html, new RegExp('<p class="foot-mark">pinghue <span aria-hidden="true">' + okGlyphs + slowGlyphs + '</span></p>'));

  // The TUI keys named on the page are real bindings.
  const app = read('src/pinghue/app.py');
  for (const key of ['r', 'R', 'a', 'b']) {
    assert.match(app, new RegExp('Binding\\("' + key + '",'));
    assert.match(html, new RegExp('<b>' + key + '</b>'));
  }
});

test('the no-JS table is the simulated frame at START', () => {
  const site = loadSiteModule();
  const actual = staticRows(read('docs/index.html'));
  const expected = expectedRows(site, site.START);
  assert.equal(actual.length, 6);
  assert.deepEqual(actual.map((row) => row.host), expected.map((row) => row.host));
  actual.forEach((row, i) => {
    assert.deepEqual(row.cells, expected[i].cells, row.host + ' cells');
    assert.equal(row.history.length, TABLE_TAIL, row.host + ' history width');
    assert.deepEqual(row.history, expected[i].history, row.host + ' history glyphs');
  });
});

test('the simulated night holds pinghue state rules', () => {
  const site = loadSiteModule();
  const run = site.buildRun();
  assert.equal(site.START, 176);
  assert.equal(site.PERIOD, 240);
  assert.equal(site.HISTORY, 56);
  assert.equal(run.frames.length, site.PERIOD);

  // Deterministic: every visitor sees the same night.
  assert.deepEqual(site.buildRun().samples, run.samples);

  const at = Object.fromEntries(run.frames[site.START].map((f) => [f.host, f]));
  assert.deepEqual(Object.keys(at), ['edge-router-1', 'core-sw-1', 'db-primary', 'api-gw', 'backup-nas', 'dns-resolver']);
  assert.equal(at['api-gw'].state, 'intermittent');
  assert.ok(at['api-gw'].loss > 0);
  assert.equal(at['backup-nas'].state, 'down');
  assert.equal(site.fmt(at['backup-nas'].last), '-');
  assert.equal(at['db-primary'].state, 'intermittent');
  for (const f of Object.values(at).filter((frame) => frame.state === 'healthy')) {
    assert.equal(site.fmtLoss(f.loss), '0.00%', f.host);
  }

  const failThreshold = cliDefault('--fail-threshold');
  run.frames.forEach((frame, t) => {
    frame.forEach((f, i) => {
      // One lost probe latches intermittent until reset: a lossy host is never healthy.
      if (f.loss > 0) assert.notEqual(f.state, 'healthy', f.host + ' at tick ' + t);
      // Down means the last FAIL_THRESHOLD probes all failed.
      const tail = run.samples[i].slice(Math.max(0, t - failThreshold + 1), t + 1);
      const down = tail.length === failThreshold && tail.every((ms) => ms === null);
      assert.equal(f.state === 'down', down, f.host + ' down at tick ' + t);
    });
  });

  // Every glyph in every ridge follows the tone rule.
  for (const row of run.samples) {
    for (const ms of row) {
      const tone = site.toneFor(ms);
      if (ms === null) assert.equal(tone, 'fail');
      else if (ms <= 100) assert.equal(tone, 'ok');
      else assert.equal(tone, 'slow');
    }
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
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
      contains: (name) => classes.has(name),
    },
  };
};

const createCopyHarness = (
  writeText,
  commands = ['uv tool install pinghue'],
) => {
  const status = { textContent: '' };
  const buttons = commands.map((command) => {
    const button = createStubElement({
      'aria-label': 'Copy install command',
      'data-copy': command,
    });
    button.textContent = 'Copy';
    return button;
  });
  const canceledTimerIds = [];
  const scheduledTimerIds = [];
  const timers = new Map();
  let nextTimerId = 1;

  // No "module" global here: site.js must run its DOM half.
  runInNewContext(read('docs/site.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: (selector) => selector === '.copy-btn' ? buttons : [],
      querySelector: (selector) => selector === '[data-copy-status]' ? status : null,
      addEventListener: () => {},
      hidden: false,
    },
    window: { matchMedia: () => ({ matches: false }), addEventListener: () => {} },
    navigator: { clipboard: { writeText } },
    clearTimeout: (id) => {
      if (id !== null && id !== undefined && timers.delete(id)) {
        canceledTimerIds.push(id);
      }
    },
    setTimeout: (handler) => {
      const id = nextTimerId;
      nextTimerId += 1;
      scheduledTimerIds.push(id);
      timers.set(id, handler);
      return id;
    },
    setInterval: () => 0,
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

test('copy buttons report a rejected clipboard write as a failure', async () => {
  const harness = createCopyHarness(() => Promise.reject(new Error('clipboard denied')));

  harness.button.listeners.get('click')();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(harness.button.textContent, 'Copy failed');
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy failed');
});

test('copy buttons announce success and reset their visible and accessible state', async () => {
  const harness = createCopyHarness(() => Promise.resolve());

  harness.button.listeners.get('click')();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(harness.button.textContent, 'Copied');
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.attributes.get('aria-label'), 'Command copied');
  assert.equal(harness.status.textContent, 'Install command copied.');

  harness.reset();
  assert.equal(harness.button.textContent, 'Copy');
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
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(copied, INSTALL_COMMANDS);
});

test('an older copy reset cannot clear a newer shared announcement', async () => {
  const firstCommand = 'uv tool install pinghue';
  const secondCommand = 'brew install inxbit/tap/pinghue';
  const harness = createCopyHarness(
    (command) => command === firstCommand
      ? Promise.resolve()
      : Promise.reject(new Error('clipboard denied')),
    [firstCommand, secondCommand],
  );

  harness.buttons[0].listeners.get('click')();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.status.textContent, 'Install command copied.');

  harness.buttons[1].listeners.get('click')();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');

  harness.reset(0);
  assert.equal(harness.status.textContent, 'Copy failed. Select the command and copy it manually.');

  harness.reset(1);
  assert.equal(harness.status.textContent, '');
});

test('a new click cancels the current button reset and neutralizes stale feedback', async () => {
  const harness = createCopyHarness(() => Promise.resolve());

  harness.button.listeners.get('click')();
  await new Promise((resolve) => setImmediate(resolve));
  const staleReset = harness.scheduledTimerIds[0];
  assert.deepEqual(harness.getActiveTimerIds(), [staleReset]);
  assert.equal(harness.button.textContent, 'Copied');

  harness.button.listeners.get('click')();
  assert.deepEqual(harness.canceledTimerIds, [staleReset]);
  assert.deepEqual(harness.getActiveTimerIds(), []);
  assert.equal(harness.button.textContent, 'Copy');
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy install command');
  assert.equal(harness.status.textContent, '');
  assert.equal(harness.runTimer(staleReset), false);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.button.textContent, 'Copied');
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
  await new Promise((resolve) => setImmediate(resolve));
  const currentReset = harness.scheduledTimerIds[0];
  assert.equal(harness.button.textContent, 'Copied');
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.attributes.get('aria-label'), 'Command copied');
  assert.equal(harness.status.textContent, 'Install command copied.');
  assert.deepEqual(harness.getActiveTimerIds(), [currentReset]);

  operations[0].reject(new Error('older clipboard failure'));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.button.textContent, 'Copied');
  assert.equal(harness.button.classes.has('copied'), true);
  assert.equal(harness.button.attributes.get('aria-label'), 'Command copied');
  assert.equal(harness.status.textContent, 'Install command copied.');
  assert.deepEqual(harness.canceledTimerIds, []);
  assert.deepEqual(harness.getActiveTimerIds(), [currentReset]);
  assert.deepEqual(harness.scheduledTimerIds, [currentReset]);

  assert.equal(harness.runTimer(currentReset), true);
  assert.equal(harness.button.textContent, 'Copy');
  assert.equal(harness.button.classes.has('copied'), false);
  assert.equal(harness.button.attributes.get('aria-label'), 'Copy install command');
  assert.equal(harness.status.textContent, '');
  assert.deepEqual(harness.getActiveTimerIds(), []);
  assert.equal(harness.runTimer(currentReset), false);
});

/* ------------------------------------------------ terrain and motion */

// Any 2D context call is accepted and counted.
const createContextStub = (calls) => new Proxy({}, {
  get: (target, prop) => {
    if (prop in target) return target[prop];
    return (...args) => {
      calls.set(prop, (calls.get(prop) || 0) + 1);
      return prop === 'getImageData' ? { data: new Uint8ClampedArray(4 * 4096) } : undefined;
    };
  },
  set: (target, prop, value) => {
    target[prop] = value;
    return true;
  },
});

const createTerrainHarness = ({ reduced }) => {
  const ctxCalls = new Map();
  const box = { left: 0, top: 0, right: 1200, bottom: 900, width: 1200, height: 900 };
  const canvas = createStubElement();
  canvas.getBoundingClientRect = () => box;
  canvas.getContext = () => createContextStub(ctxCalls);
  const stage = createStubElement({ 'data-clip': 'media/ridge.mp4' });
  stage.querySelector = (selector) => selector === 'canvas' ? canvas : null;
  const stream = createStubElement();
  stream.textContent = '2026-05-14T18:32:11.420000+00:00 example.com ok latency=14.08ms';

  const created = [];
  const createElement = (tag) => {
    created.push(tag);
    const element = createStubElement();
    element.tag = tag;
    element.paused = true;
    element.getContext = () => createContextStub(ctxCalls);
    element.play = () => Promise.reject(new Error('autoplay blocked'));
    return element;
  };

  const intervals = [];
  const timeouts = [];
  const htmlClasses = new Set();
  runInNewContext(read('docs/site.js'), {
    document: {
      documentElement: { classList: { add: (name) => htmlClasses.add(name) } },
      querySelectorAll: () => [],
      querySelector: (selector) => ({
        '[data-terrain]': stage,
        '[data-stream]': stream,
      })[selector] || null,
      addEventListener: () => {},
      createElement,
      hidden: false,
    },
    window: {
      matchMedia: (query) => ({ matches: reduced && query === '(prefers-reduced-motion: reduce)' }),
      addEventListener: () => {},
      devicePixelRatio: 1,
    },
    navigator: {},
    Path2D: function Path2D() { this.moveTo = () => {}; this.lineTo = () => {}; this.closePath = () => {}; },
    performance: { now: () => 0 },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => {},
    setTimeout: (handler, delay) => { timeouts.push(delay); return timeouts.length; },
    clearTimeout: () => {},
    setInterval: (handler, delay) => { intervals.push(delay); return intervals.length; },
    clearInterval: () => {},
  });

  return { created, ctxCalls, htmlClasses, intervals, stage, stream, timeouts };
};

test('reduced motion draws the terrain once with no clip and no clock', async () => {
  const harness = createTerrainHarness({ reduced: true });
  await new Promise((resolve) => setImmediate(resolve));

  // The terrain path really ran.
  assert.equal(harness.htmlClasses.has('js'), true);
  assert.equal(harness.stage.classes.has('is-drawn'), true);
  assert.ok((harness.ctxCalls.get('fillRect') || 0) > 0, 'terrain must be drawn');

  // ...and nothing moves.
  assert.equal(harness.created.filter((tag) => tag === 'video').length, 0);
  assert.deepEqual(harness.intervals, []);
  assert.equal(harness.stage.classes.has('is-clip'), false);
  assert.equal(harness.stream.textContent, '2026-05-14T18:32:11.420000+00:00 example.com ok latency=14.08ms');
});

test('without reduced motion the clip is attempted and the clock starts once it settles', async () => {
  const harness = createTerrainHarness({ reduced: false });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(harness.stage.classes.has('is-drawn'), true);
  assert.equal(harness.created.filter((tag) => tag === 'video').length, 1);
  // A blocked autoplay falls through to the live terrain: clock plus line stream.
  assert.equal(harness.stage.classes.has('is-live'), true);
  assert.deepEqual(harness.intervals, [1000, 1000]);
});
