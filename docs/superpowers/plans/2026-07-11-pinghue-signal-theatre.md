# PingHue Signal Theatre Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Rebuild pinghue.com as the Signal Theatre experience, with the live terminal as the dominant product artifact, an immediate install path, full mobile navigation, progressive enhancement, and stronger product proof.

**Architecture:** Keep the existing dependency-free GitHub Pages architecture. HTML owns all durable content and the no-JavaScript state, CSS owns the visual system and responsive composition, and one local JavaScript file progressively enhances navigation, copy feedback, reveals, and the deterministic terminal lifecycle. All media stays local and the current strict self-only Content Security Policy remains unchanged.

**Tech Stack:** Semantic HTML5, modern CSS, vanilla JavaScript, Node's built-in test runner, local image assets, headless Chrome for visual generation and verification, Python repository tests.

## Global Constraints

- Work only on feat/signal-theatre-website.
- Keep the site under docs/ with no production dependency and no build step.
- Preserve Slate + Signal values: #101418, #151b22, #1b2630, #2a313a, #e6edf3, #8ea0b8, #7ee787, #f2cc60, #ff7b72, and #58a6ff.
- Use Archivo and JetBrains Mono from the existing self-hosted font files.
- Preserve anchor IDs why, modes, scale, evidence, not, and install.
- Preserve exact install commands: uv tool install pinghue, pipx install pinghue, brew install inxbit/tap/pinghue, and python -m pip install pinghue.
- Preserve the exact self-only Content Security Policy on docs/index.html and docs/404.html.
- Keep green, amber, and red restricted to documented PingHue state meanings. Use blue for non-state interaction.
- Keep visible site copy free of em dash and en dash characters.
- Keep the hero headline to two lines or fewer, its description at 17 words, and the install action visible in the initial viewport.
- Honor prefers-reduced-motion for every automatic or scroll-driven animation.
- Animate only transform and opacity.
- Preserve current CLI, JSON, Python, packaging, and release behavior.
- Run a security scan over the complete contents of every modified first-party file before completion.

## File map

- Modify docs/index.html: metadata, navigation, page composition, progressive terminal markup, accessible statuses, and product media.
- Modify docs/styles.css: Signal Theatre tokens, double-bezel surfaces, asymmetric layout, motion, responsive behavior, and 404 styling.
- Modify docs/script.js: mobile menu, copy feedback, reveal orchestration, and visibility-aware terminal simulation.
- Modify docs/404.html: shared navigation identity and refined error composition.
- Modify tests/site_pages.test.mjs: static contract, progressive-enhancement checks, interaction behavior, metadata, and image validation.
- Create docs/robots.txt: crawler policy and sitemap location.
- Create docs/sitemap.xml: canonical root URL.
- Create docs/assets/slate-texture.jpg: optimized low-contrast monochrome texture.
- Create docs/assets/pinghue-social-card.png: 1200 by 630 social preview.
- Create scripts/site-social-card.html: deterministic source composition for the social preview.
- Create scripts/gen-site-social-card.sh: repeatable local renderer and dimension validator.

---

### Task 1: Lock the discovery and metadata contract

**Files:**

- Modify: tests/site_pages.test.mjs:8-87
- Modify: docs/index.html:3-23
- Create: docs/robots.txt
- Create: docs/sitemap.xml

**Interfaces:**

- Consumes: Existing read(path) helper and the canonical URL https://pinghue.com/.
- Produces: Exact title and description strings, crawler files, and social metadata hooks used by Task 5.

- [ ] **Step 1: Add failing discovery assertions**

Add these assertions inside GitHub Pages site has the expected static contract, immediately after const html = read('docs/index.html');:

~~~js
  const title = 'PingHue - concurrent ICMP and TCP ping monitor';
  const description = 'Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.';

  assert.equal(html.includes('<title>' + title + '</title>'), true);
  assert.equal(html.includes('<meta name="description" content="' + description + '">'), true);
  assert.equal(html.includes('<meta property="og:title" content="' + title + '">'), true);
  assert.equal(html.includes('<meta property="og:description" content="' + description + '">'), true);
  assert.equal(html.includes('<meta name="twitter:title" content="' + title + '">'), true);
  assert.equal(html.includes('<meta name="twitter:description" content="' + description + '">'), true);

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
~~~

Replace the old exact title assertion with the title assertion above. Keep the canonical URL assertion.

- [ ] **Step 2: Run the test and confirm the red state**

Run:

~~~bash
node --test tests/site_pages.test.mjs
~~~

Expected: FAIL because the new title, description, docs/robots.txt, and docs/sitemap.xml do not exist yet.

- [ ] **Step 3: Update the document metadata**

Replace the title, description, and social text block in docs/index.html with:

~~~html
  <title>PingHue - concurrent ICMP and TCP ping monitor</title>
  <meta name="description" content="Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.">
  <link rel="canonical" href="https://pinghue.com/">
  <link rel="icon" type="image/svg+xml" href="assets/pinghue-favicon.svg">

  <meta property="og:type" content="website">
  <meta property="og:url" content="https://pinghue.com/">
  <meta property="og:title" content="PingHue - concurrent ICMP and TCP ping monitor">
  <meta property="og:description" content="Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.">
  <meta property="og:image" content="https://pinghue.com/assets/pinghue-screenshot.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="PingHue - concurrent ICMP and TCP ping monitor">
  <meta name="twitter:description" content="Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.">
  <meta name="twitter:image" content="https://pinghue.com/assets/pinghue-screenshot.png">
~~~

Task 5 replaces the temporary social image URL with the final local social preview and adds its dimensions and alternative text.

- [ ] **Step 4: Create crawler files**

Create docs/robots.txt with exactly:

~~~text
User-agent: *
Allow: /
Sitemap: https://pinghue.com/sitemap.xml
~~~

Create docs/sitemap.xml with exactly:

~~~xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://pinghue.com/</loc></url>
</urlset>
~~~

- [ ] **Step 5: Run the focused contract**

Run:

~~~bash
node --test tests/site_pages.test.mjs
git diff --check
~~~

Expected: 2 tests pass and git diff --check prints no output.

- [ ] **Step 6: Commit the discovery increment**

~~~bash
git add docs/index.html docs/robots.txt docs/sitemap.xml tests/site_pages.test.mjs
git commit -m "feat: improve website discovery metadata"
~~~

---

### Task 2: Build the semantic Signal Theatre document

**Files:**

- Modify: tests/site_pages.test.mjs:8-100
- Modify: docs/index.html:26-245

**Interfaces:**

- Consumes: Metadata contract from Task 1 and all existing product facts in docs/index.html.
- Produces: Stable selectors data-nav, data-nav-toggle, data-nav-panel, data-nav-close, data-copy-status, data-reveal, data-terminal, data-rows, and data-clock for Tasks 3 and 4.

- [ ] **Step 1: Add failing structure assertions**

Add the following assertions after the metadata checks:

~~~js
  for (const id of ['why', 'modes', 'scale', 'evidence', 'not', 'install']) {
    assert.match(html, new RegExp('id="' + id + '"'));
  }

  assert.match(html, /data-nav\b/);
  assert.match(html, /data-nav-toggle\b/);
  assert.match(html, /aria-controls="site-menu"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /id="site-menu"[^>]*data-nav-panel/);
  assert.match(html, /data-nav-close\b/);
  assert.match(html, /role="status"[^>]*aria-live="polite"[^>]*data-copy-status/);
  assert.match(html, /Monitor every host in one live table, then export structured JSON evidence when the maintenance window closes\./);
  assert.match(html, /class="proof-rail"/);
  assert.match(html, /Up to 1024/);
  assert.match(html, /Schema version 1/);
  assert.match(html, /No server or daemon/);
  assert.ok((html.match(/data-static-row/g) || []).length >= 6);
  assert.match(html, /src="assets\/pinghue-screenshot\.png"/);
  assert.match(html, /width="1600" height="560" loading="lazy" decoding="async"/);
  assert.match(html, /class="mode mode-primary"/);
  assert.match(html, /class="signal-runway"/);
  assert.match(html, /data-reveal/g);
  assert.ok((html.match(/class="kicker"/g) || []).length <= 2);
~~~

- [ ] **Step 2: Run the contract and confirm the red state**

Run:

~~~bash
node --test tests/site_pages.test.mjs
~~~

Expected: FAIL at the first missing navigation or structural selector.

- [ ] **Step 3: Replace the navigation shell**

Replace the current header block with:

~~~html
<header class="nav-shell" data-nav>
  <div class="nav">
    <a class="wordmark" href="/" aria-label="PingHue home">
      <span class="wm-ping">ping</span><span class="wm-hue">hue</span>
    </a>
    <button class="nav-toggle" type="button" aria-controls="site-menu" aria-expanded="false" data-nav-toggle>
      <span>Menu</span>
      <span class="menu-mark" aria-hidden="true"><i></i><i></i></span>
    </button>
    <div class="nav-panel" id="site-menu" data-nav-panel>
      <button class="nav-close" type="button" aria-label="Close navigation" data-nav-close>Close</button>
      <nav class="nav-links" aria-label="Site">
        <a href="#why">Why</a>
        <a href="#modes">Modes</a>
        <a href="#evidence">Evidence</a>
        <a href="#install">Install</a>
        <a class="nav-gh" href="https://github.com/inxbit/pinghue">GitHub</a>
      </nav>
    </div>
  </div>
</header>
~~~

The toggle remains hidden unless JavaScript adds .js to the root element. Without JavaScript, the link list remains visible and wraps safely.

- [ ] **Step 4: Recompose the hero and add a static terminal state**

Use this complete hero shell and retain the current seven-column headings:

~~~html
<section class="hero" aria-labelledby="hero-title">
  <div class="hero-copy" data-reveal>
    <p class="kicker"><span class="glyph green">▁▃▅</span> # for the 02:00 change window</p>
    <h1 id="hero-title">Watch the whole window.</h1>
    <p class="lede">Monitor every host in one live table, then export structured JSON evidence when the maintenance window closes.</p>
    <div class="install-line">
      <code id="hero-install">uv tool install pinghue</code>
      <button class="copy-btn" type="button" data-copy="uv tool install pinghue" aria-label="Copy install command">Copy</button>
    </div>
  </div>
  <div class="terminal-shell" data-reveal>
    <figure class="terminal" data-terminal aria-label="Simulated PingHue maintenance-window run">
      <figcaption class="term-bar">
        <span class="term-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        <span class="term-title">pinghue - 8 targets - ICMP - 1.0s</span>
        <span class="term-clock" data-clock>00:04</span>
      </figcaption>
      <div class="term-body" role="img" aria-label="Terminal table showing healthy, intermittent, and down hosts with latency, loss, jitter, state, and colored history bars.">
        <table class="term-table" aria-hidden="true">
          <thead><tr><th>HOST</th><th>LAST</th><th>AVG</th><th>LOSS</th><th>JITTER</th><th>STATE</th><th class="th-hist">HISTORY</th></tr></thead>
          <tbody data-rows>
            <tr data-static-row><td class="t-host">edge-router-1</td><td class="t-num">9.1ms</td><td class="t-num">9.4ms</td><td class="t-num">0%</td><td class="t-num">0.2ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▃▃▃▃</span></td></tr>
            <tr data-static-row><td class="t-host">edge-router-2</td><td class="t-num">12.4ms</td><td class="t-num">11.8ms</td><td class="t-num">0%</td><td class="t-num">0.3ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▄▄▄▄</span></td></tr>
            <tr data-static-row><td class="t-host">core-sw-1</td><td class="t-num">2.1ms</td><td class="t-num">2.0ms</td><td class="t-num">0%</td><td class="t-num">0.1ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▂▂▂▂</span></td></tr>
            <tr data-static-row><td class="t-host">db-primary</td><td class="t-num">18.2ms</td><td class="t-num">17.9ms</td><td class="t-num">0%</td><td class="t-num">0.4ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▄▄▄▄</span></td></tr>
            <tr data-static-row><td class="t-host">cdn-edge</td><td class="t-num">23.8ms</td><td class="t-num">24.1ms</td><td class="t-num">0%</td><td class="t-num">0.5ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▄▄▄▄</span></td></tr>
            <tr data-static-row><td class="t-host">api-gw</td><td class="t-num">30.7ms</td><td class="t-num">31.0ms</td><td class="t-num">0%</td><td class="t-num">0.4ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▅▅▅▅</span></td></tr>
            <tr data-static-row><td class="t-host">backup-nas</td><td class="t-num">6.2ms</td><td class="t-num">6.1ms</td><td class="t-num">0%</td><td class="t-num">0.2ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▃▃▃▃</span></td></tr>
            <tr data-static-row><td class="t-host">dns-resolver</td><td class="t-num">1.0ms</td><td class="t-num">1.1ms</td><td class="t-num">0%</td><td class="t-num">0.1ms</td><td class="s-healthy">healthy</td><td class="t-hist"><span class="g-ok">▁▁▁▁</span></td></tr>
          </tbody>
        </table>
      </div>
      <div class="term-footer" aria-hidden="true">
        <span><b>q</b> quit</span><span><b>a</b> addresses</span><span><b>r</b> reset</span><span><b>R</b> reset all</span><span><b>b</b> probe</span><span><b>B</b> probe all</span>
      </div>
    </figure>
  </div>
</section>
~~~

- [ ] **Step 5: Add the proof rail and section composition hooks**

Insert this section immediately after the hero:

~~~html
<section class="proof-rail" aria-label="Verified product facts" data-reveal>
  <p><strong>Up to 1024</strong><span>concurrent probes</span></p>
  <p><strong>Schema version 1</strong><span>stable JSON evidence</span></p>
  <p><strong>macOS and Linux</strong><span>supported platforms</span></p>
  <p><strong>No server or daemon</strong><span>local by design</span></p>
</section>
~~~

Apply these exact classes to the existing sections while retaining their current factual text:

~~~html
<section class="section why-section" id="why" data-reveal>
<dl class="ledger ledger-asymmetric">
<section class="section modes-section" id="modes" data-reveal>
<div class="modes mode-cascade">
<article class="mode mode-primary">
<article class="mode mode-tcp">
<article class="mode mode-automation">
<section class="section scale-section" id="scale" data-reveal>
<section class="section evidence-section" id="evidence" data-reveal>
<section class="section scope-section" id="not" data-reveal>
<section class="section install-section" id="install" data-reveal>
~~~

For the scale, retain all ten current glyph and threshold pairs but replace the ARIA table roles with:

~~~html
<div class="signal-runway" aria-label="Fixed latency scale" data-scale>
  <div class="signal-step"><span class="glyph green">▁</span><span>≤1ms</span></div>
  <div class="signal-step"><span class="glyph green">▂</span><span>≤3ms</span></div>
  <div class="signal-step"><span class="glyph green">▃</span><span>≤10ms</span></div>
  <div class="signal-step"><span class="glyph green">▄</span><span>≤30ms</span></div>
  <div class="signal-step"><span class="glyph green">▅</span><span>≤100ms</span></div>
  <div class="signal-step"><span class="glyph amber">▆</span><span>≤300ms</span></div>
  <div class="signal-step"><span class="glyph amber">▇</span><span>≤1000ms</span></div>
  <div class="signal-step"><span class="glyph amber">█</span><span>&gt;1000ms</span></div>
  <div class="signal-step"><span class="glyph red">·</span><span>loss / down</span></div>
  <div class="signal-step"><span class="glyph amber">!</span><span>TCP refused</span></div>
</div>
~~~

Insert the real product capture between the scale and evidence sections:

~~~html
<section class="product-proof section" aria-label="PingHue product capture" data-reveal>
  <div class="media-shell">
    <img src="assets/pinghue-screenshot.png" width="1600" height="560" loading="lazy" decoding="async" alt="PingHue monitoring sixteen hosts in a dense terminal table with latency, loss, state, and history columns.">
  </div>
</section>
~~~

- [ ] **Step 6: Add one shared copy status**

Insert this element immediately before closing main:

~~~html
<p class="copy-status" role="status" aria-live="polite" aria-atomic="true" data-copy-status></p>
~~~

- [ ] **Step 7: Verify the document contract**

Run:

~~~bash
node --test tests/site_pages.test.mjs
git diff --check
~~~

Expected: 2 tests pass. The page is structurally complete but still uses the old visual styling.

- [ ] **Step 8: Commit the semantic document**

~~~bash
git add docs/index.html tests/site_pages.test.mjs
git commit -m "feat: compose Signal Theatre page structure"
~~~

---

### Task 3: Implement the Signal Theatre visual system

**Files:**

- Modify: tests/site_pages.test.mjs:8-110
- Modify: docs/styles.css:1-644

**Interfaces:**

- Consumes: All structural classes and data attributes from Task 2.
- Produces: Responsive CSS contracts for Task 4's .js, .menu-open, .reveal-ready, .is-revealed, .in-view, and data-open states.

- [ ] **Step 1: Add failing visual-contract assertions**

Add these checks after const css = read('docs/styles.css');:

~~~js
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
~~~

- [ ] **Step 2: Run the contract and confirm the red state**

Run:

~~~bash
node --test tests/site_pages.test.mjs
~~~

Expected: FAIL on --nav-height or --radius-shell.

- [ ] **Step 3: Replace the root tokens and global layout rules**

Keep the current font-face declarations, then use this foundation:

~~~css
:root {
  --bg: #101418;
  --panel: #151b22;
  --header: #1b2630;
  --border: #2a313a;
  --text: #e6edf3;
  --muted: #8ea0b8;
  --green: #7ee787;
  --amber: #f2cc60;
  --red: #ff7b72;
  --blue: #58a6ff;
  --blue-soft: rgba(88, 166, 255, 0.12);
  --shell: rgba(230, 237, 243, 0.045);
  --sans: "Archivo", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace;
  --maxw: 1280px;
  --nav-height: 64px;
  --radius-shell: 24px;
  --radius-core: 18px;
  --radius-control: 999px;
  --ease-heavy: cubic-bezier(0.32, 0.72, 0, 1);
  --shadow-stage: 0 32px 90px rgba(4, 12, 20, 0.46);
  --layer-base: 0;
  --layer-nav: 20;
  --layer-menu: 30;
  color-scheme: dark;
}

* { box-sizing: border-box; }

html {
  scroll-behavior: smooth;
  scroll-padding-top: calc(var(--nav-height) + 2rem);
}

body {
  min-width: 320px;
  margin: 0;
  overflow-x: clip;
  background:
    radial-gradient(circle at 72% 7%, rgba(88, 166, 255, 0.055), transparent 28rem),
    var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 17px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

body::before {
  position: fixed;
  inset: 0;
  z-index: var(--layer-base);
  pointer-events: none;
  content: "";
  background: url("assets/slate-texture.jpg") center / 512px 512px repeat;
  mix-blend-mode: soft-light;
  opacity: 0.035;
}

main, .nav-shell, .footer { position: relative; z-index: 1; }
section[id] { scroll-margin-top: calc(var(--nav-height) + 2rem); }
h1, h2, h3 { margin-top: 0; font-stretch: 112%; text-wrap: balance; }
p { text-wrap: pretty; }

a, button {
  transition:
    color 420ms var(--ease-heavy),
    background-color 420ms var(--ease-heavy),
    border-color 420ms var(--ease-heavy),
    opacity 420ms var(--ease-heavy),
    transform 420ms var(--ease-heavy);
}

:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 4px;
}

.copy-status {
  position: fixed;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
~~~

Retain the existing semantic state selectors .s-healthy, .s-intermittent, .s-down, .g-ok, .g-slow, and .g-fail with their committed colors.

- [ ] **Step 4: Implement the detached navigation and mobile menu states**

Replace the current nav section with:

~~~css
.nav-shell {
  position: sticky;
  top: 1rem;
  z-index: var(--layer-nav);
  width: min(calc(100% - 2rem), var(--maxw));
  min-height: var(--nav-height);
  margin: 1rem auto 0;
  padding: 5px;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-control);
  background: rgba(16, 20, 24, 0.82);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.08), 0 16px 50px rgba(4, 12, 20, 0.24);
  backdrop-filter: blur(18px) saturate(145%);
  -webkit-backdrop-filter: blur(18px) saturate(145%);
}

.nav {
  min-height: calc(var(--nav-height) - 12px);
  padding: 0.35rem 0.55rem 0.35rem 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.nav-panel, .nav-links { display: flex; align-items: center; }
.nav-links {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: clamp(0.75rem, 2vw, 1.5rem);
}
.nav-toggle, .nav-close { display: none; }

.nav-links a {
  color: var(--muted);
  font-size: 0.9rem;
  font-weight: 600;
  text-decoration: none;
}

.nav-links a:hover { color: var(--text); }
.nav-gh {
  padding: 0.55rem 0.95rem;
  border-radius: var(--radius-control);
  background: var(--blue-soft);
}

.menu-mark {
  position: relative;
  width: 16px;
  height: 10px;
  display: inline-block;
}

.menu-mark i {
  position: absolute;
  left: 0;
  width: 16px;
  height: 1px;
  background: currentColor;
  transition: transform 420ms var(--ease-heavy);
}

.menu-mark i:first-child { top: 2px; }
.menu-mark i:last-child { bottom: 2px; }
.nav-toggle[aria-expanded="true"] .menu-mark i:first-child { transform: translateY(2.5px) rotate(45deg); }
.nav-toggle[aria-expanded="true"] .menu-mark i:last-child { transform: translateY(-2.5px) rotate(-45deg); }
~~~

Add this mobile enhancement inside media max-width 767px:

~~~css
  .js .nav-toggle {
    min-width: 88px;
    min-height: 44px;
    border: 0;
    border-radius: var(--radius-control);
    background: var(--blue-soft);
    color: var(--text);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.7rem;
    font: 600 0.85rem var(--sans);
  }

  .js .nav-panel {
    position: fixed;
    inset: 0;
    z-index: var(--layer-menu);
    padding: 5rem 1rem 1rem;
    display: grid;
    place-items: start center;
    background: rgba(10, 14, 18, 0.9);
    opacity: 0;
    pointer-events: none;
  }

  .js .nav-panel[hidden] { display: none; }
  .js .nav-panel[data-open="true"] { opacity: 1; pointer-events: auto; }

  .js .nav-panel .nav-links {
    width: min(100%, 28rem);
    padding: 1rem;
    display: grid;
    gap: 0.4rem;
    border: 1px solid rgba(230, 237, 243, 0.08);
    border-radius: var(--radius-shell);
    background: var(--panel);
    box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.08), var(--shadow-stage);
    transform: translateY(24px) scale(0.98);
  }

  .js .nav-panel[data-open="true"] .nav-links { transform: translateY(0) scale(1); }

  .js .nav-links a {
    min-height: 52px;
    padding: 0.8rem 1rem;
    display: flex;
    align-items: center;
    border-radius: 14px;
    color: var(--text);
    font-size: 1.15rem;
  }

  .js .nav-close {
    position: fixed;
    top: 1.25rem;
    right: 1.25rem;
    min-width: 72px;
    min-height: 44px;
    border: 0;
    border-radius: var(--radius-control);
    background: var(--header);
    color: var(--text);
    display: inline-grid;
    place-items: center;
  }

  .menu-open { overflow: hidden; }
~~~

- [ ] **Step 5: Implement the hero and double-bezel stage**

Use this hero layout contract:

~~~css
.hero {
  width: min(calc(100% - 3rem), var(--maxw));
  min-height: calc(100dvh - var(--nav-height) - 2rem);
  margin: 0 auto;
  padding: clamp(3.5rem, 7vh, 5.5rem) 0 4rem;
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  grid-template-rows: auto 1fr;
  align-items: center;
  gap: 2rem 1rem;
}

.hero-copy {
  grid-column: 1 / span 5;
  grid-row: 1 / span 2;
  align-self: center;
}

.hero h1 {
  max-width: 8ch;
  margin: 0 0 1.2rem;
  font-size: clamp(3.25rem, 6vw, 6rem);
  line-height: 0.94;
  letter-spacing: -0.055em;
}

.lede {
  max-width: 34rem;
  margin: 0 0 1.8rem;
  color: var(--muted);
  font-size: clamp(1rem, 1.7vw, 1.18rem);
}

.terminal-shell {
  grid-column: 5 / -1;
  grid-row: 1 / span 2;
  min-width: 0;
  padding: 7px;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-shell);
  background: var(--shell);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.1), var(--shadow-stage);
  transform: perspective(1400px) rotateY(-1.6deg) rotateX(0.8deg);
}

.terminal {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-core);
  background: var(--panel);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.07);
}

.install-line {
  max-width: 31rem;
  padding: 5px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.4rem;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-control);
  background: var(--shell);
}

.install-line code {
  min-width: 0;
  padding: 0.75rem 1rem;
  overflow-x: auto;
  color: var(--green);
  white-space: nowrap;
}

.copy-btn {
  min-height: 44px;
  padding: 0 1rem;
  border: 0;
  border-radius: var(--radius-control);
  background: var(--blue);
  color: var(--bg);
  font: 700 0.82rem var(--sans);
  white-space: nowrap;
  cursor: pointer;
}

.copy-btn:hover { transform: translateY(-1px); }
.copy-btn:active { transform: scale(0.98); }
~~~

At max-width 980px, set .hero to one column, place hero copy before the terminal, remove terminal perspective, and use min-height auto. At max-width 640px, hide terminal columns 3, 4, and 5 with matching th and td selectors, reduce table padding, and keep the terminal at width 100%.

- [ ] **Step 6: Implement section rhythm and distinct layout families**

Use these core layouts and preserve existing text styling where it still applies:

~~~css
.section, .proof-rail {
  width: min(calc(100% - 3rem), var(--maxw));
  margin-inline: auto;
}

.section { padding-block: clamp(6rem, 10vw, 9rem); }
.section h2 {
  max-width: 15ch;
  margin-bottom: 1.5rem;
  font-size: clamp(2.35rem, 5vw, 4.8rem);
  line-height: 0.98;
  letter-spacing: -0.045em;
}

.proof-rail {
  padding: 1.4rem 0;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
  border-top: 1px solid rgba(230, 237, 243, 0.08);
  border-bottom: 1px solid rgba(230, 237, 243, 0.08);
}

.proof-rail p { margin: 0; display: grid; gap: 0.2rem; }
.proof-rail strong { color: var(--text); font-size: 1rem; }
.proof-rail span { color: var(--muted); font: 0.74rem var(--mono); }

.ledger-asymmetric {
  display: grid;
  grid-template-columns: 1.4fr 0.8fr 0.8fr;
  gap: 3rem 2rem;
}

.ledger-asymmetric .ledger-item:first-child {
  grid-row: span 2;
  padding-right: clamp(1rem, 4vw, 4rem);
}

.mode-cascade {
  display: grid;
  grid-template-columns: 1.35fr 0.75fr;
  gap: 1.25rem;
  align-items: start;
}

.mode-primary { grid-row: span 2; min-height: 30rem; }
.mode-tcp { transform: translateY(2rem) rotate(0.35deg); }
.mode-automation { transform: translateY(3rem) rotate(-0.25deg); }

.mode {
  padding: 6px;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-shell);
  background: var(--shell);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.08);
}

.mode > * { margin-inline: 1.15rem; }
.mode > :first-child { margin-top: 1.15rem; }
.mode > :last-child { margin-bottom: 1.15rem; }

.signal-runway {
  padding: 1.2rem;
  display: grid;
  grid-template-columns: repeat(10, minmax(0, 1fr));
  gap: 0.35rem;
  border-radius: var(--radius-core);
  background: var(--panel);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.07);
}

.signal-step {
  min-width: 0;
  display: grid;
  align-content: end;
  justify-items: center;
  gap: 0.5rem;
}

.signal-step .glyph {
  font-size: clamp(1.8rem, 3vw, 3.6rem);
  line-height: 1;
  transform-origin: bottom;
}

.signal-step span:last-child {
  color: var(--muted);
  font: 0.68rem var(--mono);
  white-space: nowrap;
}

.media-shell {
  padding: 7px;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-shell);
  background: var(--shell);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.09), var(--shadow-stage);
}

.media-shell img {
  width: 100%;
  height: auto;
  display: block;
  border-radius: var(--radius-core);
}

.evidence {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(18rem, 0.7fr);
  gap: clamp(2rem, 6vw, 6rem);
  align-items: end;
}

.scope-section { width: min(calc(100% - 3rem), 62rem); }

.install-panel {
  max-width: 54rem;
  padding: 7px;
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-shell);
  background: var(--shell);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.08);
}
~~~

Add these explicit collapses:

~~~css
@media (max-width: 980px) {
  .hero {
    min-height: auto;
    grid-template-columns: 1fr;
    grid-template-rows: auto;
  }
  .hero-copy, .terminal-shell { grid-column: 1; grid-row: auto; }
  .terminal-shell { transform: none; }
  .ledger-asymmetric { grid-template-columns: 1.25fr 1fr; }
  .mode-cascade { grid-template-columns: 1fr 1fr; }
  .mode-primary { grid-column: 1 / -1; grid-row: auto; min-height: 0; }
  .evidence { grid-template-columns: 1fr; }
}

@media (max-width: 767px) {
  .hero, .section, .proof-rail {
    width: min(calc(100% - 2rem), var(--maxw));
  }
  .hero { padding-block: 3rem 4.5rem; }
  .hero h1 { font-size: clamp(3rem, 15vw, 4.5rem); }
  .proof-rail { grid-template-columns: 1fr 1fr; gap: 1.25rem; }
  .ledger-asymmetric, .mode-cascade { grid-template-columns: 1fr; }
  .ledger-asymmetric .ledger-item:first-child { grid-row: auto; padding-right: 0; }
  .mode-tcp, .mode-automation { transform: none; }
  .signal-runway { grid-template-columns: repeat(5, minmax(0, 1fr)); row-gap: 1.2rem; }
  .section { padding-block: 6rem; }
}

@media (max-width: 640px) {
  .term-table th:nth-child(3),
  .term-table th:nth-child(4),
  .term-table th:nth-child(5),
  .term-table td:nth-child(3),
  .term-table td:nth-child(4),
  .term-table td:nth-child(5) {
    display: none;
  }
  .term-table { width: 100%; font-size: 0.68rem; }
  .term-table th, .term-table td { padding-right: 0.65rem; }
  .term-body { padding-inline: 0.65rem; }
  .proof-rail { grid-template-columns: 1fr; }
}
~~~

- [ ] **Step 7: Add reveal and reduced-motion states**

~~~css
.js .reveal-ready {
  opacity: 0;
  transform: translateY(28px);
}

.js .reveal-ready.is-revealed {
  opacity: 1;
  transform: translateY(0);
  transition: opacity 820ms var(--ease-heavy), transform 820ms var(--ease-heavy);
}

.signal-step:nth-child(1) { --i: 0; }
.signal-step:nth-child(2) { --i: 1; }
.signal-step:nth-child(3) { --i: 2; }
.signal-step:nth-child(4) { --i: 3; }
.signal-step:nth-child(5) { --i: 4; }
.signal-step:nth-child(6) { --i: 5; }
.signal-step:nth-child(7) { --i: 6; }
.signal-step:nth-child(8) { --i: 7; }
.signal-step:nth-child(9) { --i: 8; }
.signal-step:nth-child(10) { --i: 9; }

@media (prefers-reduced-motion: no-preference) {
  .hero .terminal-shell.reveal-ready.is-revealed { transition-delay: 120ms; }
  .signal-runway.in-view .glyph {
    animation: signal-rise 620ms var(--ease-heavy) both;
    animation-delay: calc(var(--i, 0) * 45ms);
  }
  @keyframes signal-rise {
    from { opacity: 0.35; transform: scaleY(0.25); }
    to { opacity: 1; transform: scaleY(1); }
  }
}

@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
  .js .reveal-ready { opacity: 1; transform: none; }
  .terminal-shell, .mode-tcp, .mode-automation { transform: none; }
}

@media (prefers-reduced-transparency: reduce) {
  .nav-shell {
    background: var(--bg);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
}
~~~

- [ ] **Step 8: Verify and commit the visual system**

Run:

~~~bash
node --test tests/site_pages.test.mjs
git diff --check
~~~

Expected: 2 tests pass and the diff check is clean.

Commit:

~~~bash
git add docs/styles.css tests/site_pages.test.mjs
git commit -m "feat: add Signal Theatre visual system"
~~~

---

### Task 4: Add accessible interaction and lifecycle control

**Files:**

- Modify: tests/site_pages.test.mjs:89-123
- Modify: docs/script.js:1-225

**Interfaces:**

- Consumes: Task 2 data attributes and Task 3 state classes.
- Produces: Accessible menu state, shared copy announcements, reveal classes, and terminal timer control.

- [ ] **Step 1: Expand the JavaScript contract**

Add source assertions inside the static contract:

~~~js
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
~~~

Replace the copy-failure test button with:

~~~js
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
~~~

Use this document stub in the VM call:

~~~js
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
~~~

After the existing failure assertions, add:

~~~js
  assert.equal(status.textContent, 'Copy failed. Select the command and copy it manually.');
  assert.equal(attributes.get('aria-label'), 'Copy failed');
~~~

- [ ] **Step 2: Run the test and confirm the red state**

Run:

~~~bash
node --test tests/site_pages.test.mjs
~~~

Expected: FAIL because the script does not add .js, update the status, or expose the required lifecycle names.

- [ ] **Step 3: Add root enhancement and copy feedback**

At the start of the IIFE, add:

~~~js
  document.documentElement.classList.add("js");
  const copyStatus = document.querySelector("[data-copy-status]");
~~~

Replace the current copy-button block with:

~~~js
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    const originalLabel = btn.getAttribute("aria-label") || "Copy command";
    let resetTimer = null;

    const report = (label, announcement, copied) => {
      clearTimeout(resetTimer);
      btn.classList.toggle("copied", copied);
      btn.textContent = label;
      btn.setAttribute("aria-label", label === "Copied" ? "Command copied" : label);
      if (copyStatus) copyStatus.textContent = announcement;
      resetTimer = setTimeout(() => {
        btn.classList.remove("copied");
        btn.textContent = "Copy";
        btn.setAttribute("aria-label", originalLabel);
      }, 1800);
    };

    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-copy") || "";
      const done = () => report("Copied", "Install command copied.", true);
      const failed = () => report(
        "Copy failed",
        "Copy failed. Select the command and copy it manually.",
        false,
      );

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, failed);
      } else {
        failed();
      }
    });
  });
~~~

Add clearTimeout: () => {} to the VM context.

- [ ] **Step 4: Add isolated mobile-menu control**

Add this block after copy handling:

~~~js
  const nav = document.querySelector("[data-nav]");
  const navToggle = document.querySelector("[data-nav-toggle]");
  const navPanel = document.querySelector("[data-nav-panel]");
  const navClose = document.querySelector("[data-nav-close]");

  if (nav && navToggle && navPanel && navClose) {
    const focusableSelector = "a[href], button:not([disabled])";
    const mobileMenu = window.matchMedia("(max-width: 767px)");
    let returnFocus = null;
    let menuOpen = false;

    const syncMenu = () => {
      const open = mobileMenu.matches && menuOpen;
      navPanel.hidden = mobileMenu.matches && !open;
      navPanel.setAttribute("data-open", String(open));
      navToggle.setAttribute("aria-expanded", String(open));
      document.body.classList.toggle("menu-open", open);
    };

    const setMenu = (open) => {
      menuOpen = mobileMenu.matches && open;
      syncMenu();
      if (menuOpen) {
        returnFocus = document.activeElement;
        navClose.focus();
      } else if (returnFocus && typeof returnFocus.focus === "function") {
        returnFocus.focus();
      }
    };

    navToggle.addEventListener("click", () => setMenu(true));
    navClose.addEventListener("click", () => setMenu(false));
    navPanel.addEventListener("click", (event) => {
      if (event.target === navPanel) setMenu(false);
    });
    navPanel.querySelectorAll("a[href]").forEach((link) => {
      link.addEventListener("click", () => setMenu(false));
    });
    document.addEventListener("keydown", (event) => {
      if (navPanel.getAttribute("data-open") !== "true") return;
      if (event.key === "Escape") {
        event.preventDefault();
        setMenu(false);
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...navPanel.querySelectorAll(focusableSelector)];
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    if (typeof mobileMenu.addEventListener === "function") {
      mobileMenu.addEventListener("change", () => {
        menuOpen = false;
        syncMenu();
      });
    }
    syncMenu();
  }
~~~

- [ ] **Step 5: Add one-time reveal orchestration**

Replace the scale-only observer with:

~~~js
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealItems = [...document.querySelectorAll("[data-reveal]")];
  const strip = document.querySelector("[data-scale]");

  revealItems.forEach((item) => item.classList.add("reveal-ready"));

  if (reduced || !("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-revealed"));
    if (strip) strip.classList.add("in-view");
  } else {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-revealed");
        if (entry.target === strip) strip.classList.add("in-view");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.16 });
    revealItems.forEach((item) => revealObserver.observe(item));
    if (strip && !revealItems.includes(strip)) revealObserver.observe(strip);
  }
~~~

- [ ] **Step 6: Make the terminal timer visibility-aware**

Keep the current deterministic host model, glyphFor, probe, stateOf, fmt, render, and step. Remove the terminal's old reduced declaration because the shared declaration now appears earlier. Replace the final timer block with:

~~~js
  let timer = null;
  let terminalVisible = true;
  let documentVisible = !document.hidden;

  const updateTerminalTimer = () => {
    const shouldRun = !reduced && terminalVisible && documentVisible;
    if (shouldRun && timer === null) timer = setInterval(step, TICK_MS);
    if (!shouldRun && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };

  if (reduced) {
    for (let i = 0; i < 30; i += 1) step();
  } else {
    for (let i = 0; i < 4; i += 1) step();
    if ("IntersectionObserver" in window) {
      const terminalObserver = new IntersectionObserver((entries) => {
        terminalVisible = entries.some((entry) => entry.isIntersecting);
        updateTerminalTimer();
      }, { threshold: 0.08 });
      terminalObserver.observe(root);
    }
    document.addEventListener("visibilitychange", () => {
      documentVisible = !document.hidden;
      updateTerminalTimer();
    });
    updateTerminalTimer();
  }
~~~

- [ ] **Step 7: Run interaction tests**

Run:

~~~bash
node --test tests/site_pages.test.mjs
git diff --check
~~~

Expected: all Node tests pass. The VM's querySelector returns null for all selectors except data-copy-status, and querySelectorAll returns an empty array except for .copy-btn.

- [ ] **Step 8: Commit the interaction increment**

~~~bash
git add docs/script.js tests/site_pages.test.mjs
git commit -m "feat: add accessible website interactions"
~~~

---

### Task 5: Add bespoke media and refine the 404 page

**Files:**

- Create: docs/assets/slate-texture.jpg
- Create: scripts/site-social-card.html
- Create: scripts/gen-site-social-card.sh
- Create: docs/assets/pinghue-social-card.png
- Modify: docs/index.html:12-21
- Modify: docs/404.html:1-22
- Modify: docs/styles.css
- Modify: tests/site_pages.test.mjs:8-130

**Interfaces:**

- Consumes: Existing docs/assets/pinghue-screenshot.png, local fonts, palette, and Chrome.
- Produces: Local texture and social preview referenced by CSS and metadata.

- [ ] **Step 1: Add failing media assertions**

Add statSync to the fs import and place this helper next to read:

~~~js
const pngDimensions = (path) => {
  const png = readFileSync(path);
  assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) };
};
~~~

Add:

~~~js
  assert.equal(existsSync('docs/assets/slate-texture.jpg'), true);
  assert.ok(statSync('docs/assets/slate-texture.jpg').size < 100_000);
  assert.equal(existsSync('docs/assets/pinghue-social-card.png'), true);
  assert.deepEqual(pngDimensions('docs/assets/pinghue-social-card.png'), { width: 1200, height: 630 });
  assert.match(html, /<meta property="og:image" content="https:\/\/pinghue\.com\/assets\/pinghue-social-card\.png">/);
  assert.match(html, /<meta property="og:image:width" content="1200">/);
  assert.match(html, /<meta property="og:image:height" content="630">/);
  assert.match(html, /<meta property="og:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window\.">/);
  assert.match(html, /<meta name="twitter:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window\.">/);
~~~

Replace the earlier Open Graph assertion for pinghue-screenshot.png with the pinghue-social-card.png assertion above. The screenshot remains a visible page asset, but no longer serves as the social preview.

- [ ] **Step 2: Run the contract and confirm the red state**

~~~bash
node --test tests/site_pages.test.mjs
~~~

Expected: FAIL because both new image assets are absent.

- [ ] **Step 3: Generate and optimize the Slate texture**

Use the imagegen skill and built-in image tool with:

~~~text
Use case: stylized-concept
Asset type: seamless website background texture
Primary request: create a nearly black monochrome slate surface with extremely subtle fine mineral grain and faint machined-fiber variation
Composition/framing: square seamless-looking field with even visual density and no focal point
Lighting/mood: low-contrast soft grazing light, calm and technical
Color palette: charcoal and cool slate only, centered around #101418
Materials/textures: microscopic mineral grain, very restrained brushed surface
Constraints: no text, no logos, no icons, no objects, no visible color, and no identifiable infrastructure
Avoid: stars, smoke, clouds, circuitry, grids, scratches, dust clumps, watermarks, and high contrast
~~~

Copy the output path reported by the tool to /private/tmp/pinghue-slate-texture-source.png, then run:

~~~bash
/usr/bin/sips -Z 512 -s format jpeg -s formatOptions 36 /private/tmp/pinghue-slate-texture-source.png --out docs/assets/slate-texture.jpg
file docs/assets/slate-texture.jpg
wc -c docs/assets/slate-texture.jpg
~~~

Expected: 512px or smaller, below 100,000 bytes, and visually free of color, text, or a focal object.

- [ ] **Step 4: Create the deterministic social-card source**

Create scripts/site-social-card.html:

~~~html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>
    @font-face { font-family: Archivo; src: url("../docs/fonts/archivo-var-latin.woff2") format("woff2"); }
    @font-face { font-family: JetBrains; src: url("../docs/fonts/jetbrains-mono-var-latin.woff2") format("woff2"); }
    * { box-sizing: border-box; }
    html, body { width: 1200px; height: 630px; margin: 0; overflow: hidden; }
    body { padding: 54px 58px; background: #101418; color: #e6edf3; font-family: Archivo, sans-serif; }
    .top { display: flex; align-items: center; justify-content: space-between; }
    .brand { font-size: 34px; font-weight: 750; letter-spacing: -0.04em; }
    .brand span { background: linear-gradient(90deg, #7ee787, #f2cc60 38%, #ff7b72 68%, #58a6ff); color: transparent; background-clip: text; }
    .kind { color: #8ea0b8; font: 18px JetBrains, monospace; }
    h1 { width: 620px; margin: 54px 0 18px; font-size: 74px; line-height: 0.95; letter-spacing: -0.055em; }
    p { margin: 0; color: #8ea0b8; font: 21px JetBrains, monospace; }
    .frame { position: absolute; right: 58px; bottom: 46px; width: 690px; padding: 7px; border: 1px solid rgba(230,237,243,.12); border-radius: 24px; background: rgba(230,237,243,.045); box-shadow: inset 0 1px rgba(230,237,243,.1), 0 30px 80px rgba(4,12,20,.48); transform: rotate(-1.2deg); }
    .frame img { width: 100%; display: block; border-radius: 18px; }
    .signal { position: absolute; left: 58px; bottom: 54px; font: 34px JetBrains, monospace; color: #7ee787; letter-spacing: .03em; }
  </style>
</head>
<body>
  <div class="top"><div class="brand">ping<span>hue</span></div><div class="kind">maintenance-window monitor</div></div>
  <h1>Watch the whole window.</h1>
  <p>Concurrent ICMP and TCP monitoring</p>
  <div class="signal">▁▂▃▄▅▆▇█</div>
  <div class="frame"><img src="../docs/assets/pinghue-screenshot.png" alt=""></div>
</body>
</html>
~~~

- [ ] **Step 5: Create and run the renderer**

Create scripts/gen-site-social-card.sh:

~~~bash
#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
source_html="$root/scripts/site-social-card.html"
output="$root/docs/assets/pinghue-social-card.png"
chrome="$(printenv CHROME_BIN 2>/dev/null || true)"
if [ -z "$chrome" ]; then
  chrome="$(command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || true)"
fi
if [ -z "$chrome" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi
[ -x "$chrome" ] || { printf 'Chrome not found; set CHROME_BIN\n' >&2; exit 1; }

"$chrome" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
  --force-device-scale-factor=1 --window-size=1200,630 --screenshot="$output" "file://$source_html"

python3 - "$output" <<'PY'
import struct
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()
if data[:8] != b"\x89PNG\r\n\x1a\n":
    raise SystemExit(f"{path} is not a PNG")
width, height = struct.unpack(">II", data[16:24])
if (width, height) != (1200, 630):
    raise SystemExit(f"expected 1200x630, got {width}x{height}")
print(f"wrote {path} ({width}x{height})")
PY
~~~

Run:

~~~bash
chmod +x scripts/gen-site-social-card.sh
scripts/gen-site-social-card.sh
~~~

Expected: the script prints 1200x630.

- [ ] **Step 6: Point metadata at the final social preview**

Use https://pinghue.com/assets/pinghue-social-card.png for both image URLs. Add:

~~~html
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window.">
  <meta name="twitter:image:alt" content="PingHue terminal monitoring multiple hosts during a maintenance window.">
~~~

- [ ] **Step 7: Recompose the 404 body**

Preserve its head and use:

~~~html
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <main class="lost" id="main">
    <a class="wordmark lost-wordmark" href="/" aria-label="PingHue home">
      <span class="wm-ping">ping</span><span class="wm-hue">hue</span>
    </a>
    <div class="lost-shell">
      <p class="kicker"><span class="glyph red">· · ·</span> # host state: down</p>
      <h1>404: not found.</h1>
      <pre><code><span class="prompt">$</span> pinghue this-page
<span class="out">this-page</span> <span class="c-red">resolve failed: NXDOMAIN</span></code></pre>
      <p><a class="lost-link" href="/">Reset and go back to pinghue.com</a></p>
    </div>
  </main>
</body>
~~~

Add these styles, with no new script:

~~~css
.lost {
  width: min(calc(100% - 2rem), 720px);
  min-height: 100dvh;
  margin: 0 auto;
  padding-block: clamp(2rem, 8vh, 6rem);
  display: grid;
  align-content: center;
  gap: 2rem;
}

.lost-wordmark {
  width: max-content;
  color: var(--text);
  font-size: 1.35rem;
  font-weight: 750;
  text-decoration: none;
}

.lost-shell {
  padding: clamp(1.5rem, 5vw, 3rem);
  border: 1px solid rgba(230, 237, 243, 0.08);
  border-radius: var(--radius-shell);
  background: var(--shell);
  box-shadow: inset 0 1px 0 rgba(230, 237, 243, 0.08), var(--shadow-stage);
}

.lost-shell h1 {
  margin-bottom: 1.5rem;
  font-size: clamp(3rem, 10vw, 5.5rem);
  line-height: 0.95;
  letter-spacing: -0.05em;
}

.lost-shell pre {
  padding: 1rem 1.2rem;
  overflow-x: auto;
  border-radius: var(--radius-core);
  background: var(--panel);
}

.lost-link {
  min-height: 44px;
  width: max-content;
  padding: 0.7rem 1rem;
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius-control);
  background: var(--blue-soft);
  color: var(--text);
  text-decoration: none;
}
~~~

- [ ] **Step 8: Verify and commit**

~~~bash
node --test tests/site_pages.test.mjs
git diff --check
git add docs/index.html docs/404.html docs/styles.css docs/assets/slate-texture.jpg docs/assets/pinghue-social-card.png scripts/site-social-card.html scripts/gen-site-social-card.sh tests/site_pages.test.mjs
git commit -m "feat: add branded website media"
~~~

Expected: tests pass, the diff check is clean, the PNG is 1200 by 630, and the JPEG is below 100,000 bytes.

---

### Task 6: Complete browser, repository, and security verification

**Files:**

- Modify only when a confirmed issue is found: docs/index.html, docs/styles.css, docs/script.js, docs/404.html, tests/site_pages.test.mjs, scripts/site-social-card.html, or scripts/gen-site-social-card.sh

**Interfaces:**

- Consumes: Completed site and all preceding contracts.
- Produces: Evidence that the approved design, accessibility, performance, security, and responsive requirements are met.

- [ ] **Step 1: Run static and repository tests**

~~~bash
node --test tests/site_pages.test.mjs
pytest
git diff --check
~~~

Expected: all Node and Python tests pass, with no diff-check output.

- [ ] **Step 2: Run lint and dependency checks**

Use the repository's installed environment:

~~~bash
ruff check .
pyright
pip-audit
~~~

Expected: zero actionable findings. If a command is unavailable, record that exact limitation and continue with available checks. Do not add a project dependency.

- [ ] **Step 3: Start a local server**

Run in a persistent terminal session:

~~~bash
python3 -m http.server 4173 --directory docs
~~~

Expected: http://127.0.0.1:4173/ serves the page and /404.html serves the error composition.

- [ ] **Step 4: Capture required Chrome viewports**

Write screenshots outside the repository:

~~~bash
chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1440,1000 --screenshot=/private/tmp/pinghue-desktop.png http://127.0.0.1:4173/
"$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1024,768 --screenshot=/private/tmp/pinghue-tablet.png http://127.0.0.1:4173/
"$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=390,844 --screenshot=/private/tmp/pinghue-mobile.png http://127.0.0.1:4173/
"$chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=360,800 --screenshot=/private/tmp/pinghue-mobile-narrow.png http://127.0.0.1:4173/
"$chrome" --headless=new --disable-gpu --hide-scrollbars --force-prefers-reduced-motion --window-size=390,844 --screenshot=/private/tmp/pinghue-mobile-reduced-motion.png http://127.0.0.1:4173/
~~~

Inspect every screenshot. Confirm the hero command is visible, navigation fits, content does not clip, mobile terminal columns are intentional, section layouts have distinct rhythm, and the 360px viewport has no page-level horizontal overflow.

- [ ] **Step 5: Exercise keyboard and interaction behavior**

Use the configured browser-testing skill against the local URL:

1. Tab from the skip link through navigation and the hero install action.
2. At 390 by 844, open the menu and verify the close button receives focus.
3. Tab through every menu link and confirm focus wraps inside the menu.
4. Press Escape and confirm focus returns to the menu button.
5. Activate a copy button and confirm visible text, accessible status, and reset behavior.
6. Scroll the terminal offscreen and confirm its clock stops; return onscreen and confirm it resumes.
7. Confirm the console contains no errors or CSP warnings.

- [ ] **Step 6: Run Lighthouse**

~~~bash
npx --yes lighthouse http://127.0.0.1:4173/ --chrome-flags="--headless=new --disable-gpu" --only-categories=performance,accessibility,best-practices,seo --output=json --output-path=/private/tmp/pinghue-lighthouse.json
~~~

Targets:

- Performance at least 0.90
- Accessibility 1.00
- Best practices at least 0.95
- SEO 1.00
- LCP below 2.5 seconds
- CLS below 0.1

If network restrictions block Lighthouse, request command approval and rerun. If it remains unavailable, report the limitation and do not claim it passed.

- [ ] **Step 7: Run the required security diff scan**

Invoke codex-security:security-diff-scan over origin/main...HEAD and the complete current contents of:

- docs/index.html
- docs/styles.css
- docs/script.js
- docs/404.html
- scripts/site-social-card.html
- scripts/gen-site-social-card.sh
- tests/site_pages.test.mjs

Treat findings in modified code as blocking. Fix confirmed findings, rerun focused tests, then rerun the same scan until no confirmed unresolved finding remains.

- [ ] **Step 8: Run the final design preflight**

~~~bash
rg -n '[—–]' docs/index.html docs/404.html
rg -n 'height:[[:space:]]*100vh' docs/styles.css
rg -n 'addEventListener.*scroll' docs/script.js
rg -n 'Inter|Roboto|Arial|Open Sans|Helvetica|lucide|fontawesome|material-icons' docs/index.html docs/styles.css docs/script.js
git diff --stat origin/main...HEAD
git status --short --branch
~~~

Expected: the first four searches return no matches, the diff contains only approved website and planning surfaces, and the worktree is clean after the final commit.

- [ ] **Step 9: Commit remediation only if verification changed source**

~~~bash
git add docs/index.html docs/styles.css docs/script.js docs/404.html tests/site_pages.test.mjs scripts/site-social-card.html scripts/gen-site-social-card.sh
git commit -m "fix: resolve website verification findings"
~~~

Do not create an empty commit when no source change was needed.

- [ ] **Step 10: Record final evidence**

Report:

- Branch and commit list
- Exact changed files
- Node and Python test results
- Lint and dependency-audit results
- Browser viewports inspected
- Keyboard, copy, menu, timer, console, and CSP results
- Lighthouse scores or exact limitation
- Security scan result and remediation cycle count
- Remaining risks
