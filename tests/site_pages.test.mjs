import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const read = (path) => readFileSync(path, 'utf8');
const stripCssComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, '');
const cssRuleBody = (source, selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(escaped + '\\s*\\{([^}]*)\\}'));
  assert.ok(match, 'missing CSS rule for ' + selector);
  return match[1];
};
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
  const css = stripCssComments(read('docs/styles.css'));
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
  assert.match(html, /"exit_reason"<\/span>: <span class="json-string">"deadline"<\/span>/);
  assert.match(html, /"status"<\/span>: <span class="json-string c-amber">"intermittent"<\/span>/);
  assert.match(html, /"loss_pct"<\/span>: <span class="jn">1\.11<\/span>/);
  assert.doesNotMatch(html, /"exit_reason"<\/span>: <span class="json-string">"duration"<\/span>/);
  assert.doesNotMatch(html, /"state"<\/span>: <span class="json-string c-amber">"intermittent"<\/span>/);
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
  assert.match(js, /documentElement\.classList\.add\("js"\)/);
  assert.match(js, /data-nav-toggle/);
  assert.match(js, /aria-expanded/);
  assert.match(js, /data-copy-status/);
  assert.match(js, /reveal-ready/);
  assert.match(js, /terminalVisible/);
  assert.match(js, /documentVisible/);
  assert.match(js, /updateTerminalTimer/);
  assert.match(js, /navPanel\.hidden/);
  assert.match(js, /IntersectionObserver/);
  assert.doesNotMatch(js, /addEventListener\(["']scroll/);

  const readme = read('README.md');
  assert.match(readme, /https:\/\/pinghue\.com/);
});

test('Signal Theatre visual contracts preserve meaning and accessibility', () => {
  const html = read('docs/index.html');
  const css = stripCssComments(read('docs/styles.css'));
  const gradientPattern = /linear-gradient\((?:[^()]|\([^()]*\))*\)/gs;
  const signalReference = /var\(--(?:green|amber|red|blue)\)|rgba\(\s*88\s*,\s*166\s*,\s*255\b/;
  const signalGradients = (css.match(gradientPattern) || []).filter((gradient) => (
    signalReference.test(gradient)
  ));

  // The identity wordmark is the sole decorative use of the signal palette.
  assert.equal(signalGradients.length, 1);
  const wordmarkRule = cssRuleBody(css, '.wm-hue');
  assert.match(wordmarkRule, /linear-gradient/);
  assert.equal(wordmarkRule.includes(signalGradients[0]), true);

  for (const selector of [
    'body',
    '.hue-ribbon',
    '.ledger-item dt',
    '.install-line code',
    '.install-row code',
    '.not-list li::before',
    '.mode-tag',
    '.jq',
    '.json-string',
    '.jn',
    '.copy-btn.copied',
  ]) {
    assert.doesNotMatch(cssRuleBody(css, selector), signalReference, selector + ' must stay neutral');
  }

  assert.equal((html.match(/<span class="mode-tag">/g) || []).length, 3);
  assert.doesNotMatch(html, /class="mode-tag c-(?:green|amber|red|blue)"/);
  const scopeSection = html.match(/<section(?=[^>]*\sid="not")[^>]*>[\s\S]*?<\/section>/);
  assert.ok(scopeSection);
  assert.doesNotMatch(scopeSection[0], /class="glyph (?:green|amber|red|blue)"/);

  const heroTitle = /<h1 id="hero-title">\s*<span class="hero-line">Watch the whole<\/span>\s*<span class="hero-line">window\.<\/span>\s*<\/h1>/;
  assert.match(html, heroTitle);
  assert.equal((html.match(/class="hero-line"/g) || []).length, 2);
  assert.match(cssRuleBody(css, '.hero-line'), /display:\s*block/);
  assert.match(cssRuleBody(css, '.hero-line'), /white-space:\s*nowrap/);

  assert.match(cssRuleBody(css, '.nav-links a'), /min-height:\s*44px/);
  assert.match(cssRuleBody(css, '.install-row .copy-btn'), /min-height:\s*44px/);

  const classNames = [...html.matchAll(/\bclass="([^"]+)"/g)]
    .flatMap((match) => match[1].split(/\s+/));
  assert.equal(classNames.includes('js'), false);
  assert.equal(classNames.filter((name) => name === 'json-string').length, 5);

  assert.match(
    css,
    /@media\s*\(prefers-reduced-transparency:\s*reduce\)\s*\{[\s\S]*?\.js \.nav-panel\s*\{[^}]*background:\s*var\(--bg\)/,
  );

  const transitionProperties = [...css.matchAll(/\btransition\s*:\s*([^;]+);/gs)]
    .flatMap((match) => match[1].split(','))
    .map((declaration) => declaration.trim().split(/\s+/)[0]);
  assert.equal(transitionProperties.length > 0, true);
  assert.deepEqual(
    transitionProperties.filter((property) => !['opacity', 'transform'].includes(property)),
    [],
  );

  // The mobile headline size and bounded shells are the static 320px layout contract.
  assert.match(cssRuleBody(css, 'body'), /min-width:\s*0/);
  assert.match(
    css,
    /@media\s*\(max-width:\s*767px\)[\s\S]*?\.hero h1\s*\{[^}]*font-size:\s*clamp\(2\.15rem,\s*10\.5vw,\s*4\.5rem\)/,
  );
});

test('copy buttons report a rejected clipboard write as a failure', async () => {
  let click;
  const classes = new Set();
  const attributes = new Map([['aria-label', 'Copy install command']]);
  const status = { textContent: '' };
  const button = {
    textContent: 'Copy',
    getAttribute: (name) => name === 'data-copy' ? 'uv tool install pinghue' : attributes.get(name),
    setAttribute: (name, value) => attributes.set(name, value),
    addEventListener: (_event, handler) => { click = handler; },
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
    },
  };

  runInNewContext(read('docs/script.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: (selector) => selector === '.copy-btn' ? [button] : [],
      querySelector: (selector) => selector === '[data-copy-status]' ? status : null,
      addEventListener: () => {},
      hidden: false,
    },
    window: {
      matchMedia: () => ({ matches: false }),
    },
    navigator: {
      clipboard: {
        writeText: () => Promise.reject(new Error('clipboard denied')),
      },
    },
    clearTimeout: () => {},
    setTimeout: () => 0,
  });

  click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(button.textContent, 'Copy failed');
  assert.equal(classes.has('copied'), false);
  assert.equal(status.textContent, 'Copy failed. Select the command and copy it manually.');
  assert.equal(attributes.get('aria-label'), 'Copy failed');
});

test('mobile menu synchronizes visibility, ARIA, focus trap, Escape, and resize', () => {
  const element = (initialAttributes = {}) => {
    const attributes = new Map(Object.entries(initialAttributes));
    const classes = new Set();
    const listeners = new Map();
    return {
      attributes,
      classes,
      listeners,
      hidden: false,
      getAttribute: (name) => attributes.get(name),
      setAttribute: (name, value) => attributes.set(name, value),
      addEventListener: (event, handler) => listeners.set(event, handler),
      classList: {
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
        toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
      },
    };
  };

  const nav = element();
  const toggle = element({ 'aria-expanded': 'false' });
  const panel = element();
  const close = element();
  const link = element();
  const documentListeners = new Map();
  const mediaListeners = new Map();
  const bodyClasses = new Set();
  const mobileMenu = {
    matches: true,
    addEventListener: (event, handler) => mediaListeners.set(event, handler),
  };
  const document = {
    documentElement: { classList: { add: () => {} } },
    body: {
      classList: {
        toggle: (name, enabled) => enabled ? bodyClasses.add(name) : bodyClasses.delete(name),
      },
    },
    activeElement: toggle,
    hidden: false,
    querySelectorAll: () => [],
    querySelector: (selector) => ({
      '[data-nav]': nav,
      '[data-nav-toggle]': toggle,
      '[data-nav-panel]': panel,
      '[data-nav-close]': close,
    })[selector] || null,
    addEventListener: (event, handler) => documentListeners.set(event, handler),
  };
  close.focus = () => { document.activeElement = close; };
  toggle.focus = () => { document.activeElement = toggle; };
  link.focus = () => { document.activeElement = link; };
  panel.querySelectorAll = (selector) => (
    selector === 'a[href]' ? [link] : [close, link]
  );

  runInNewContext(read('docs/script.js'), {
    document,
    window: {
      matchMedia: (query) => query === '(max-width: 767px)'
        ? mobileMenu
        : { matches: false },
    },
    navigator: {},
    clearTimeout: () => {},
    setTimeout: () => 0,
  });

  assert.equal(panel.hidden, true);
  assert.equal(panel.attributes.get('data-open'), 'false');
  assert.equal(toggle.attributes.get('aria-expanded'), 'false');

  toggle.listeners.get('click')();
  assert.equal(panel.hidden, false);
  assert.equal(panel.attributes.get('data-open'), 'true');
  assert.equal(toggle.attributes.get('aria-expanded'), 'true');
  assert.equal(bodyClasses.has('menu-open'), true);
  assert.equal(document.activeElement, close);

  document.activeElement = link;
  let prevented = false;
  documentListeners.get('keydown')({
    key: 'Tab',
    shiftKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(document.activeElement, close);

  documentListeners.get('keydown')({
    key: 'Escape',
    shiftKey: false,
    preventDefault: () => {},
  });
  assert.equal(panel.hidden, true);
  assert.equal(toggle.attributes.get('aria-expanded'), 'false');
  assert.equal(bodyClasses.has('menu-open'), false);
  assert.equal(document.activeElement, toggle);

  toggle.listeners.get('click')();
  mobileMenu.matches = false;
  mediaListeners.get('change')();
  assert.equal(panel.hidden, false);
  assert.equal(panel.attributes.get('data-open'), 'false');
  assert.equal(document.activeElement, toggle);
});
