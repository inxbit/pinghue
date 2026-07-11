import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const read = (path) => readFileSync(path, 'utf8');
const attributeCount = (source, attribute) => (
  source.match(new RegExp('\\s' + attribute + '(?=[\\s=>])', 'g')) || []
).length;

test('GitHub Pages site has the expected static contract', () => {
  assert.equal(existsSync('docs/index.html'), true);
  assert.equal(existsSync('docs/styles.css'), true);
  assert.equal(existsSync('docs/script.js'), true);
  assert.equal(existsSync('docs/404.html'), true);
  assert.equal(existsSync('docs/.nojekyll'), true);
  assert.equal(read('docs/CNAME').trim(), 'pinghue.com');
  assert.equal(existsSync('docs/assets/pinghue-favicon.svg'), true);
  assert.equal(existsSync('docs/assets/pinghue-hero.svg'), true);
  assert.equal(existsSync('docs/assets/pinghue-screenshot.png'), true);

  assert.equal(existsSync('docs/fonts/archivo-var-latin.woff2'), true);
  assert.equal(existsSync('docs/fonts/jetbrains-mono-var-latin.woff2'), true);

  const html = read('docs/index.html');
  const css = read('docs/styles.css');
  assert.match(css, /--nav-height:\s*64px/);
  assert.match(css, /--radius-shell:\s*24px/);
  assert.match(css, /--radius-core:\s*18px/);
  assert.match(css, /\.terminal-shell/);
  assert.match(css, /\.proof-rail/);
  assert.match(css, /\.mode-cascade/);
  assert.match(css, /\.signal-runway/);
  assert.match(css, /\.media-shell/);
  assert.match(css, /\.js \.reveal-ready/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /scroll-behavior:\s*auto/);
  assert.doesNotMatch(css, /height:\s*100vh/);
  assert.doesNotMatch(css, /rgba\(0,\s*0,\s*0,\s*0\.45\)/);
  const title = 'PingHue - concurrent ICMP and TCP ping monitor';
  const description = 'Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.';

  assert.equal(html.includes('<title>' + title + '</title>'), true);
  assert.equal(html.includes('<meta name="description" content="' + description + '">'), true);
  assert.equal(html.includes('<meta property="og:title" content="' + title + '">'), true);
  assert.equal(html.includes('<meta property="og:description" content="' + description + '">'), true);
  assert.equal(html.includes('<meta name="twitter:title" content="' + title + '">'), true);
  assert.equal(html.includes('<meta name="twitter:description" content="' + description + '">'), true);

  // Mobile navigation links must remain visible when JavaScript is unavailable.
  assert.doesNotMatch(css, /^\s*\.nav nav a:not\(\.nav-gh\)\s*\{/m);

  for (const id of ['why', 'modes', 'scale', 'evidence', 'not', 'install']) {
    assert.match(html, new RegExp('id="' + id + '"'));
  }

  const navShell = html.match(/<header(?=[^>]*\sdata-nav(?:\s|>))[^>]*>[\s\S]*?<\/header>/);
  assert.ok(navShell);
  assert.equal(attributeCount(html, 'data-nav'), 1);
  assert.equal(attributeCount(html, 'data-nav-toggle'), 1);
  assert.equal(attributeCount(html, 'data-nav-panel'), 1);
  assert.equal(attributeCount(html, 'data-nav-close'), 1);
  assert.equal(attributeCount(navShell[0], 'data-nav-toggle'), 1);
  assert.equal(attributeCount(navShell[0], 'data-nav-panel'), 1);
  assert.equal(attributeCount(navShell[0], 'data-nav-close'), 1);
  assert.match(html, /aria-controls="site-menu"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /id="site-menu"[^>]*data-nav-panel/);

  const terminal = html.match(/<figure(?=[^>]*\sdata-terminal(?:\s|>))[^>]*>[\s\S]*?<\/figure>/);
  assert.ok(terminal);
  assert.equal(attributeCount(html, 'data-terminal'), 1);
  assert.equal(attributeCount(html, 'data-rows'), 1);
  assert.equal(attributeCount(html, 'data-clock'), 1);
  assert.equal(attributeCount(terminal[0], 'data-rows'), 1);
  assert.equal(attributeCount(terminal[0], 'data-clock'), 1);
  assert.equal(attributeCount(html, 'data-static-row'), 8);
  assert.equal(attributeCount(terminal[0], 'data-static-row'), 8);
  assert.match(html, /aria-label="Terminal table showing host latency, loss, jitter, state, and colored history bars during a simulated monitoring run\."/);

  assert.equal(attributeCount(html, 'data-copy-status'), 1);
  assert.match(html, /role="status"[^>]*aria-live="polite"[^>]*data-copy-status/);
  assert.match(html, /Monitor every host in one live table, then export structured JSON evidence when the maintenance window closes\./);
  assert.match(html, /class="proof-rail"/);
  assert.match(html, /Up to 1024/);
  assert.match(html, /Schema version 1/);
  assert.match(html, /No server or daemon/);
  assert.match(html, /src="assets\/pinghue-screenshot\.png"/);
  assert.match(html, /width="1600" height="560" loading="lazy" decoding="async"/);

  for (const variant of ['mode-primary', 'mode-tcp', 'mode-automation']) {
    assert.equal((html.match(new RegExp('<article class="mode ' + variant + '">', 'g')) || []).length, 1);
  }
  assert.equal((html.match(/<article class="mode mode-[^"]+">/g) || []).length, 3);

  assert.match(html, /class="signal-runway"/);
  assert.equal(attributeCount(html, 'data-scale'), 1);
  assert.equal((html.match(/class="signal-step"/g) || []).length, 10);

  assert.equal(attributeCount(html, 'data-reveal'), 10);
  for (const className of ['hero-copy', 'terminal-shell', 'proof-rail', 'product-proof']) {
    assert.match(
      html,
      new RegExp('<(?:div|section)(?=[^>]*class="[^"]*' + className + '[^"]*")(?=[^>]*\\sdata-reveal(?:\\s|>))[^>]*>'),
    );
  }
  for (const id of ['why', 'modes', 'scale', 'evidence', 'not', 'install']) {
    assert.match(
      html,
      new RegExp('<section(?=[^>]*\\sid="' + id + '")(?=[^>]*\\sdata-reveal(?:\\s|>))[^>]*>'),
    );
  }
  assert.equal((html.match(/class="kicker"/g) || []).length, 2);

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
  // Visible copy carries no em/en dashes (design contract).
  assert.doesNotMatch(html, /[—–]/);
  assert.doesNotMatch(read('docs/404.html'), /[—–]/);
  assert.match(html, /Watch the/);
  assert.match(html, /uv tool install pinghue/);
  assert.match(html, /brew install inxbit\/tap\/pinghue/);
  // Copyable install commands are pinned exactly; a poisoned command fails the deploy.
  const copyCommands = [...html.matchAll(/data-copy="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(new Set(copyCommands), new Set([
    'uv tool install pinghue',
    'pipx install pinghue',
    'brew install inxbit/tap/pinghue',
    'python -m pip install pinghue',
  ]));
  // Only the local first-party script runs on the page.
  const scriptSrcs = [...html.matchAll(/<script[^>]*\bsrc="([^"]*)"/g)].map((m) => m[1]);
  assert.deepEqual(scriptSrcs, ['script.js']);
  // Both pages ship the identical strict self-only Content-Security-Policy.
  const csp = 'default-src \'none\'; script-src \'self\'; style-src \'self\'; img-src \'self\'; font-src \'self\'; base-uri \'none\'; form-action \'none\'';
  const cspTag = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
  assert.equal(html.includes(cspTag), true);
  const notFound = read('docs/404.html');
  assert.equal(notFound.includes(cspTag), true);
  // No inline style/script blocks anywhere, so 'unsafe-inline' is never needed.
  assert.doesNotMatch(html, /<style/);
  assert.doesNotMatch(notFound, /<style/);
  assert.match(html, /href="https:\/\/github\.com\/inxbit\/pinghue"/);
  assert.match(html, /href="https:\/\/pypi\.org\/project\/pinghue\/"/);
  assert.match(html, /https:\/\/pinghue\.com\/assets\/pinghue-screenshot\.png/);
  assert.match(html, /rel="canonical" href="https:\/\/pinghue\.com\/"/);
  // The hero demonstrates the product with a JS-driven simulated run.
  assert.match(html, /data-terminal\b/);
  assert.match(html, /schema_version/);
  assert.match(html, /"exit_reason"<\/span>: <span class="js">"deadline"<\/span>/);
  assert.match(html, /"status"<\/span>: <span class="js c-amber">"intermittent"<\/span>/);
  assert.match(html, /"loss_pct"<\/span>: <span class="jn">1\.11<\/span>/);
  assert.doesNotMatch(html, /"exit_reason"<\/span>: <span class="js">"duration"<\/span>/);
  assert.doesNotMatch(html, /"state"<\/span>: <span class="js c-amber">"intermittent"<\/span>/);
  assert.match(html, /What pinghue is not/i);

  // Self-hosted variable fonts, no third-party font CDN.
  assert.match(css, /@font-face/);
  assert.match(css, /Archivo/);
  assert.doesNotMatch(html, /fonts\.googleapis\.com/);
  // Slate + Signal palette from the README is the site palette.
  assert.match(css, /#101418/);
  assert.match(css, /#7ee787/);
  assert.match(css, /#f2cc60/);
  assert.match(css, /#ff7b72/);
  assert.match(css, /#58a6ff/);
  assert.match(css, /prefers-reduced-motion/);

  const js = read('docs/script.js');
  // The simulation must follow the documented fixed latency scale.
  assert.match(js, /▁/);
  assert.match(js, /glyphFor/);
  assert.match(js, /ms > SLOW_MS \? "g-slow" : "g-ok"/);
  assert.match(js, /peakJitter/);
  assert.doesNotMatch(js, /everSlow/);
  assert.match(js, /prefers-reduced-motion/);

  const readme = read('README.md');
  assert.match(readme, /https:\/\/pinghue\.com/);
});

test('copy buttons report a rejected clipboard write as a failure', async () => {
  let click;
  const classes = new Set();
  const button = {
    textContent: 'Copy',
    getAttribute: () => 'uv tool install pinghue',
    addEventListener: (_event, handler) => {
      click = handler;
    },
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
    },
  };

  runInNewContext(read('docs/script.js'), {
    document: {
      querySelectorAll: () => [button],
      querySelector: () => null,
    },
    navigator: {
      clipboard: {
        writeText: () => Promise.reject(new Error('clipboard denied')),
      },
    },
    setTimeout: () => 0,
  });

  click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(button.textContent, 'Copy failed');
  assert.equal(classes.has('copied'), false);
});
