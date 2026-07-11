import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const read = (path) => readFileSync(path, 'utf8');
const pngDimensions = (path) => {
  const png = readFileSync(path);
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
};
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
  assert.equal(existsSync('docs/assets/slate-texture.jpg'), true);
  assert.ok(statSync('docs/assets/slate-texture.jpg').size < 100_000);
  assert.equal(existsSync('docs/assets/pinghue-social-card.png'), true);
  assert.deepEqual(pngDimensions('docs/assets/pinghue-social-card.png'), { width: 1200, height: 630 });

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
  assert.match(notFound, /<a class="skip-link" href="#main">Skip to content<\/a>/);
  assert.match(notFound, /<main class="lost" id="main">/);
  assert.match(notFound, /class="wordmark lost-wordmark" href="\/" aria-label="PingHue home"/);
  assert.match(notFound, /<div class="lost-shell">/);
  assert.match(notFound, /class="lost-link" href="\/"/);
  assert.match(cssRuleBody(css, '.lost'), /width:\s*min\(calc\(100% - 2rem\),\s*720px\)/);
  assert.match(cssRuleBody(css, '.lost'), /display:\s*grid/);
  assert.match(cssRuleBody(css, '.lost-shell'), /padding:\s*clamp\(1\.5rem,\s*5vw,\s*3rem\)/);
  assert.match(cssRuleBody(css, '.lost-shell'), /min-width:\s*0/);
  assert.match(cssRuleBody(css, '.lost-shell h1'), /font-size:\s*clamp\(2\.25rem,\s*10vw,\s*5\.5rem\)/);
  assert.match(cssRuleBody(css, '.lost-shell h1'), /overflow-wrap:\s*anywhere/);
  assert.match(cssRuleBody(css, '.lost-link'), /min-height:\s*44px/);
  assert.match(cssRuleBody(css, '.lost-link'), /max-width:\s*100%/);
  assert.match(cssRuleBody(css, '.lost-link'), /text-align:\s*center/);
  assert.match(
    css,
    /@media\s*\(max-width:\s*400px\)[\s\S]*?\.lost-link\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0[^}]*justify-content:\s*center/,
  );
  // No inline style/script blocks anywhere, so 'unsafe-inline' is never needed.
  assert.doesNotMatch(html, /<style/);
  assert.doesNotMatch(notFound, /<style/);
  assert.match(html, /href="https:\/\/github\.com\/inxbit\/pinghue"/);
  assert.match(html, /href="https:\/\/pypi\.org\/project\/pinghue\/"/);
  assert.match(html, /<meta property="og:image" content="https:\/\/pinghue\.com\/assets\/pinghue-social-card\.png">/);
  assert.match(html, /<meta property="og:image:width" content="1200">/);
  assert.match(html, /<meta property="og:image:height" content="630">/);
  assert.match(html, /<meta property="og:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window\.">/);
  assert.match(html, /<meta name="twitter:image" content="https:\/\/pinghue\.com\/assets\/pinghue-social-card\.png">/);
  assert.match(html, /<meta name="twitter:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window\.">/);
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
  assert.match(js, /mobileMenu\.addListener/);
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
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
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

  runInNewContext(read('docs/script.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: (selector) => selector === '.copy-btn' ? buttons : [],
      querySelector: (selector) => selector === '[data-copy-status]' ? status : null,
      addEventListener: () => {},
      hidden: false,
    },
    window: { matchMedia: () => ({ matches: false }) },
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

const createMenuHarness = ({ legacyMedia = false } = {}) => {
  const nav = createStubElement();
  const toggle = createStubElement({ 'aria-expanded': 'false' });
  const panel = createStubElement();
  const close = createStubElement();
  const link = createStubElement();
  const unrelated = createStubElement();
  const documentListeners = new Map();
  const mediaListeners = new Map();
  const bodyClasses = new Set();
  let legacyChange;
  const mobileMenu = { matches: true };
  if (legacyMedia) {
    mobileMenu.addListener = (handler) => { legacyChange = handler; };
  } else {
    mobileMenu.addEventListener = (event, handler) => mediaListeners.set(event, handler);
  }
  const document = {
    documentElement: { classList: { add: () => {} } },
    body: {
      classList: {
        toggle: (name, enabled) => enabled ? bodyClasses.add(name) : bodyClasses.delete(name),
      },
    },
    activeElement: unrelated,
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
  for (const item of [toggle, close, link, unrelated]) {
    item.focus = () => { document.activeElement = item; };
  }
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

  return {
    bodyClasses,
    close,
    document,
    documentListeners,
    getMediaChange: () => legacyMedia ? legacyChange : mediaListeners.get('change'),
    link,
    mobileMenu,
    panel,
    toggle,
    unrelated,
  };
};

const assertClosedMobileMenu = ({ bodyClasses, document, panel, toggle }) => {
  assert.equal(panel.hidden, true);
  assert.equal(panel.attributes.get('data-open'), 'false');
  assert.equal(toggle.attributes.get('aria-expanded'), 'false');
  assert.equal(bodyClasses.has('menu-open'), false);
  assert.equal(document.activeElement, toggle);
};

test('mobile menu synchronizes ARIA, traps focus, and restores its opener', () => {
  const harness = createMenuHarness();

  assert.equal(harness.panel.hidden, true);
  assert.equal(harness.panel.attributes.get('data-open'), 'false');
  assert.equal(harness.toggle.attributes.get('aria-expanded'), 'false');
  assert.equal(harness.bodyClasses.has('menu-open'), false);
  assert.equal(harness.document.activeElement, harness.unrelated);
  harness.toggle.listeners.get('click')();
  assert.equal(harness.panel.hidden, false);
  assert.equal(harness.panel.attributes.get('data-open'), 'true');
  assert.equal(harness.toggle.attributes.get('aria-expanded'), 'true');
  assert.equal(harness.bodyClasses.has('menu-open'), true);
  assert.equal(harness.document.activeElement, harness.close);

  let prevented = false;
  harness.documentListeners.get('keydown')({
    key: 'Tab',
    shiftKey: true,
    preventDefault: () => { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(harness.document.activeElement, harness.link);

  prevented = false;
  harness.documentListeners.get('keydown')({
    key: 'Tab',
    shiftKey: false,
    preventDefault: () => { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(harness.document.activeElement, harness.close);

  harness.documentListeners.get('keydown')({
    key: 'Escape',
    shiftKey: false,
    preventDefault: () => {},
  });
  assertClosedMobileMenu(harness);
});

test('mobile menu closes through every pointer dismissal path and viewport reset', () => {
  const harness = createMenuHarness();
  const open = () => {
    harness.document.activeElement = harness.unrelated;
    harness.toggle.listeners.get('click')();
  };

  open();
  harness.close.listeners.get('click')();
  assertClosedMobileMenu(harness);

  open();
  harness.link.listeners.get('click')();
  assertClosedMobileMenu(harness);

  open();
  harness.panel.listeners.get('click')({ target: harness.panel });
  assertClosedMobileMenu(harness);

  open();
  harness.mobileMenu.matches = false;
  harness.getMediaChange()();
  assert.equal(harness.panel.hidden, false);
  assert.equal(harness.panel.attributes.get('data-open'), 'false');
  assert.equal(harness.toggle.attributes.get('aria-expanded'), 'false');
  assert.equal(harness.document.activeElement, harness.toggle);
});

test('mobile menu registers the legacy MediaQueryList change callback', () => {
  const harness = createMenuHarness({ legacyMedia: true });

  assert.equal(typeof harness.getMediaChange(), 'function');
  harness.toggle.listeners.get('click')();
  harness.mobileMenu.matches = false;
  harness.getMediaChange()();

  assert.equal(harness.panel.hidden, false);
  assert.equal(harness.panel.attributes.get('data-open'), 'false');
  assert.equal(harness.document.activeElement, harness.toggle);
});

test('reveal content resolves when motion or observer support is unavailable', () => {
  for (const { reduced, withObserver } of [
    { reduced: false, withObserver: false },
    { reduced: true, withObserver: true },
  ]) {
    const item = createStubElement();
    const strip = createStubElement();
    let observerConstructions = 0;
    function IntersectionObserver() { observerConstructions += 1; }
    const window = { matchMedia: () => ({ matches: reduced }) };
    const context = {
      document: {
        documentElement: { classList: { add: () => {} } },
        querySelectorAll: (selector) => selector === '[data-reveal]' ? [item] : [],
        querySelector: (selector) => selector === '[data-scale]' ? strip : null,
        addEventListener: () => {},
        hidden: false,
      },
      window,
      navigator: {},
      clearTimeout: () => {},
      setTimeout: () => 0,
    };
    if (withObserver) {
      window.IntersectionObserver = IntersectionObserver;
      context.IntersectionObserver = IntersectionObserver;
    }

    runInNewContext(read('docs/script.js'), context);

    assert.equal(item.classes.has('reveal-ready'), true);
    assert.equal(item.classes.has('is-revealed'), true);
    assert.equal(strip.classes.has('in-view'), true);
    assert.equal(observerConstructions, 0);
  }
});

test('reveal observer resolves each intersecting target only once', () => {
  const item = createStubElement();
  const strip = createStubElement();
  const observers = [];
  function IntersectionObserver(callback, options) {
    this.callback = callback;
    this.options = options;
    this.targets = new Set();
    this.unobserved = [];
    this.observe = (target) => this.targets.add(target);
    this.unobserve = (target) => {
      this.targets.delete(target);
      this.unobserved.push(target);
    };
    this.deliver = (entries) => {
      const observedEntries = entries.filter(({ target }) => this.targets.has(target));
      this.callback(observedEntries, this);
    };
    observers.push(this);
  }
  const window = {
    IntersectionObserver,
    matchMedia: () => ({ matches: false }),
  };

  runInNewContext(read('docs/script.js'), {
    document: {
      documentElement: { classList: { add: () => {} } },
      querySelectorAll: (selector) => selector === '[data-reveal]' ? [item] : [],
      querySelector: (selector) => selector === '[data-scale]' ? strip : null,
      addEventListener: () => {},
      hidden: false,
    },
    window,
    IntersectionObserver,
    navigator: {},
    clearTimeout: () => {},
    setTimeout: () => 0,
  });

  const revealObserver = observers.find(({ options }) => options.threshold === 0.16);
  const entries = [
    { target: item, isIntersecting: true },
    { target: strip, isIntersecting: true },
  ];
  assert.ok(revealObserver);
  assert.equal(revealObserver.targets.size, 2);

  revealObserver.deliver(entries);
  assert.equal(item.classes.has('is-revealed'), true);
  assert.equal(strip.classes.has('in-view'), true);
  assert.deepEqual(revealObserver.unobserved, [item, strip]);
  assert.equal(revealObserver.targets.size, 0);

  revealObserver.deliver(entries);
  assert.deepEqual(revealObserver.unobserved, [item, strip]);
});

const createTerminalHarness = ({
  initiallyHidden = false,
  reduced = false,
  withObserver = true,
} = {}) => {
  const createNode = () => {
    const node = createStubElement();
    node.children = [];
    node.appendChild = (child) => {
      node.children.push(child);
      return child;
    };
    node.replaceChildren = (...children) => { node.children = children; };
    return node;
  };
  const tbody = createNode();
  const clock = createNode();
  const root = createNode();
  root.querySelector = (selector) => ({
    '[data-rows]': tbody,
    '[data-clock]': clock,
  })[selector] || null;
  const documentListeners = new Map();
  const document = {
    documentElement: { classList: { add: () => {} } },
    hidden: initiallyHidden,
    querySelectorAll: () => [],
    querySelector: (selector) => selector === '[data-terminal]' ? root : null,
    addEventListener: (event, handler) => documentListeners.set(event, handler),
    createDocumentFragment: createNode,
    createElement: createNode,
  };
  const observers = [];
  function IntersectionObserver(callback, options) {
    this.callback = callback;
    this.options = options;
    this.targets = [];
    this.observe = (target) => this.targets.push(target);
    this.unobserve = (target) => {
      this.targets = this.targets.filter((candidate) => candidate !== target);
    };
    observers.push(this);
  }
  const activeIntervals = new Set();
  let intervalStarts = 0;
  let intervalStops = 0;
  let nextInterval = 1;
  const window = {
    matchMedia: (query) => ({
      matches: query === '(prefers-reduced-motion: reduce)' && reduced,
    }),
  };
  const context = {
    document,
    window,
    navigator: {},
    clearTimeout: () => {},
    setTimeout: () => 0,
    setInterval: () => {
      intervalStarts += 1;
      const id = nextInterval;
      nextInterval += 1;
      activeIntervals.add(id);
      return id;
    },
    clearInterval: (id) => {
      intervalStops += 1;
      activeIntervals.delete(id);
    },
  };
  if (withObserver) {
    window.IntersectionObserver = IntersectionObserver;
    context.IntersectionObserver = IntersectionObserver;
  }

  runInNewContext(read('docs/script.js'), context);

  return {
    activeIntervals,
    clock,
    document,
    documentListeners,
    getIntervalStarts: () => intervalStarts,
    getIntervalStops: () => intervalStops,
    observers,
    root,
  };
};

test('terminal interval follows observer and document visibility without duplication', () => {
  const harness = createTerminalHarness();
  const terminalObserver = harness.observers.find(({ options }) => options.threshold === 0.08);

  assert.ok(terminalObserver);
  assert.equal(harness.clock.textContent, '00:04');
  assert.equal(harness.getIntervalStarts(), 0);
  assert.equal(harness.activeIntervals.size, 0);

  terminalObserver.callback([{ target: harness.root, isIntersecting: true }]);
  assert.equal(harness.getIntervalStarts(), 1);
  assert.equal(harness.activeIntervals.size, 1);

  terminalObserver.callback([{ target: harness.root, isIntersecting: true }]);
  assert.equal(harness.getIntervalStarts(), 1);
  assert.equal(harness.activeIntervals.size, 1);

  harness.document.hidden = true;
  harness.documentListeners.get('visibilitychange')();
  assert.equal(harness.getIntervalStops(), 1);
  assert.equal(harness.activeIntervals.size, 0);

  terminalObserver.callback([{ target: harness.root, isIntersecting: true }]);
  assert.equal(harness.getIntervalStarts(), 1);

  harness.document.hidden = false;
  harness.documentListeners.get('visibilitychange')();
  assert.equal(harness.getIntervalStarts(), 2);
  assert.equal(harness.activeIntervals.size, 1);

  terminalObserver.callback([{ target: harness.root, isIntersecting: false }]);
  assert.equal(harness.getIntervalStops(), 2);
  assert.equal(harness.activeIntervals.size, 0);

  terminalObserver.callback([{ target: harness.root, isIntersecting: false }]);
  assert.equal(harness.getIntervalStops(), 2);
});

test('terminal interval waits for document visibility without observer support', () => {
  const harness = createTerminalHarness({
    initiallyHidden: true,
    withObserver: false,
  });

  assert.equal(harness.clock.textContent, '00:04');
  assert.equal(harness.getIntervalStarts(), 0);
  assert.equal(harness.activeIntervals.size, 0);

  harness.document.hidden = false;
  harness.documentListeners.get('visibilitychange')();
  assert.equal(harness.getIntervalStarts(), 1);
  assert.equal(harness.activeIntervals.size, 1);
});

test('reduced motion renders the static terminal frame without an interval', () => {
  const harness = createTerminalHarness({ reduced: true, withObserver: true });

  assert.equal(harness.clock.textContent, '00:30');
  assert.equal(harness.getIntervalStarts(), 0);
  assert.equal(harness.getIntervalStops(), 0);
  assert.equal(harness.activeIntervals.size, 0);
  assert.equal(harness.observers.length, 0);
  assert.equal(harness.documentListeners.has('visibilitychange'), false);
});
