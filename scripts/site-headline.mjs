// Block-glyph headline generator for pinghue.com.
//
// Usage: node scripts/site-headline.mjs
//
// Prints the two <span class="hl-art ..."> lines (hl-wide, then hl-narrow)
// that form the "THE TABLE IS THE TERRAIN." headline. The site has no build
// step: paste the output by hand into the <h1 class="hl"> in docs/index.html,
// replacing the existing hl-art spans. docs/site.css masks every full-block
// glyph into its own cell.
//
// 7x10 bitmap font: each glyph has 10 rows, '#' = filled cell. Widths vary
// (I and . are 3 wide, N is 8, space is 2).
const F = {
  T: ['#######','#######','..###..','..###..','..###..','..###..','..###..','..###..','..###..','..###..'],
  H: ['###.###','###.###','###.###','###.###','#######','#######','###.###','###.###','###.###','###.###'],
  E: ['#######','#######','###....','###....','######.','######.','###....','###....','#######','#######'],
  A: ['.#####.','#######','###.###','###.###','###.###','#######','#######','###.###','###.###','###.###'],
  B: ['######.','#######','###.###','###.###','######.','######.','###.###','###.###','#######','######.'],
  L: ['###....','###....','###....','###....','###....','###....','###....','###....','#######','#######'],
  I: ['###','###','###','###','###','###','###','###','###','###'],
  S: ['.######','#######','###....','###....','######.','.######','....###','....###','#######','######.'],
  R: ['######.','#######','###.###','###.###','#######','######.','###.##.','###.###','###.###','###.###'],
  N: ['###..###','###..###','####.###','####.###','########','########','###.####','###.####','###..###','###..###'],
  '.': ['...','...','...','...','...','...','...','...','###','###'],
  ' ': ['..','..','..','..','..','..','..','..','..','..'],
};

export function renderLine(text) {
  const rows = Array.from({ length: 10 }, () => '');
  [...text].forEach((ch, i) => {
    const g = F[ch];
    if (!g) throw new Error('no glyph for ' + JSON.stringify(ch));
    for (let r = 0; r < 10; r++) rows[r] += (i ? ' ' : '') + g[r];
  });
  return rows.map((r) => r.replace(/#/g, '█').replace(/\./g, ' '));
}

export function renderBlock(lines) {
  const out = [];
  lines.forEach((line, i) => {
    if (i) out.push('');
    out.push(...renderLine(line));
  });
  return out;
}

if (process.argv[1] && process.argv[1].endsWith('site-headline.mjs')) {
  const variants = {
    wide: [['THE TABLE', 'hl-big'], ['IS THE TERRAIN.', '']],
    narrow: [['THE TABLE', ''], ['IS THE', ''], ['TERRAIN.', '']],
  };
  for (const [name, lines] of Object.entries(variants)) {
    const html = lines.map(([text, cls]) => {
      const rows = renderLine(text);
      return `<span class="hl-line${cls ? ' ' + cls : ''}">` +
        rows.map((r) => `<span class="hl-row">${r.replace(/\s+$/, '') || ' '}</span>`).join('') + '</span>';
    }).join('');
    console.log(`<span class="hl-art hl-${name}" aria-hidden="true">${html}</span>`);
  }
}
