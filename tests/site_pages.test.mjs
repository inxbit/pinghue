import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const read = (path) => readFileSync(path, 'utf8');

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
  assert.match(html, /<title>pinghue - colored concurrent ping monitor for maintenance windows<\/title>/);
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

  const css = read('docs/styles.css');
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
