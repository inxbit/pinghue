import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

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

  const html = read('docs/index.html');
  assert.match(html, /<title>pinghue — colored concurrent ping monitor for maintenance windows<\/title>/);
  assert.match(html, /Watch the/);
  assert.match(html, /uv tool install pinghue/);
  assert.match(html, /brew install inxbit\/tap\/pinghue/);
  assert.match(html, /href="https:\/\/github\.com\/inxbit\/pinghue"/);
  assert.match(html, /href="https:\/\/pypi\.org\/project\/pinghue\/"/);
  assert.match(html, /https:\/\/pinghue\.com\/assets\/pinghue-screenshot\.png/);
  assert.match(html, /rel="canonical" href="https:\/\/pinghue\.com\/"/);
  // The hero demonstrates the product with a JS-driven simulated run.
  assert.match(html, /data-terminal\b/);
  assert.match(html, /schema_version/);
  assert.match(html, /What pinghue is not/i);

  const css = read('docs/styles.css');
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
  assert.match(js, /prefers-reduced-motion/);

  const readme = read('README.md');
  assert.match(readme, /https:\/\/pinghue\.com/);
});
