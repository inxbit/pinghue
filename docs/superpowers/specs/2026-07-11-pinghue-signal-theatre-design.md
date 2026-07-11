# PingHue Signal Theatre website redesign

Date: 2026-07-11
Status: Approved design
Target branch: `feat/signal-theatre-website`

## Objective

Turn the existing PingHue landing page into a memorable, high-end product story while preserving the product's operational credibility, fast load time, factual copy, and dependency-free static architecture.

The site has one conversion goal: help a terminal-native operator understand PingHue and install it in under one minute. The redesign must increase visual impact without obscuring that goal.

## Design read

This is a brand-preserving overhaul for network operators, SREs, and sysadmins. The visual language is cinematic, terminal-native, calm, and precise. The live PingHue terminal becomes the dominant product artifact.

Design dials:

- Design variance: 9
- Motion intensity: 7
- Visual density: 4

## Success criteria

- The install command and product purpose are visible in the initial viewport at desktop and mobile sizes.
- The live terminal is the hero's dominant visual element.
- Existing brand tokens, product facts, anchors, install commands, and external destinations remain stable.
- The page feels visibly more composed and tactile without becoming a generic SaaS site or design-studio portfolio.
- Navigation works at every supported viewport.
- The page remains useful without JavaScript.
- Motion communicates hierarchy, product state, or feedback and fully respects reduced-motion preferences.
- The strict self-only Content Security Policy remains intact.
- No production dependency or build step is introduced.
- Browser verification meets the accessibility, performance, and responsive criteria in this document.

## Existing commitments

The following product decisions are fixed:

- Static HTML, CSS, and JavaScript under `docs/`
- GitHub Pages deployment through the existing workflow
- Slate + Signal palette
- Archivo and JetBrains Mono, both self-hosted
- The gradient PingHue wordmark
- The deterministic live terminal simulation
- Existing anchor IDs: `why`, `modes`, `scale`, `evidence`, `not`, and `install`
- Exact install commands for uv, pipx, Homebrew, and pip
- Honest scope language, including "Small on purpose."
- No em dash or en dash characters in visible copy

## Visual system

### Theme

The page uses one locked dark theme. Section backgrounds may vary within the existing slate family, but no section switches to a light theme.

Color responsibilities:

- Blue is the single interface accent for focus, links, navigation, and non-state interaction.
- Green, amber, and red retain their documented product-state meanings.
- Signal colors are not used for ambient glows, decorative gradients, or unrelated highlights.
- The committed multicolor wordmark and its identity rule remain the sole decorative exception.

### Typography

- Archivo remains the display and body face.
- JetBrains Mono remains reserved for commands, telemetry, evidence, and compact operational labels.
- Display headings use Archivo's width axis, tight tracking, and balanced wrapping.
- Body copy stays within approximately 65 characters per line.
- The page uses sentence case and no decorative section numbering.
- At most two section eyebrows appear across the page.

### Material and shape

- The live terminal, product capture, and install station use concentric double-bezel construction.
- Outer shells provide a quiet machined edge and spacing.
- Inner cores use a smaller concentric radius and a subtle top-edge highlight.
- Informational sections rely on whitespace, alignment, and type hierarchy instead of generic bordered cards.
- Buttons use a consistent pill treatment and a nested trailing action area where an icon is present.
- A subtle local monochrome Slate texture adds physical depth at very low opacity.
- Shadows carry the slate hue and use one consistent upper-left light direction.

## Page composition

### Navigation

The navigation becomes a detached island that remains within the 80px height cap.

Desktop:

- Preserve the PingHue wordmark and all existing navigation labels.
- Keep the navigation on one line at 1024px and wider.
- Use `position: sticky` with a 1rem top offset, a stable translucent backdrop, and sufficient contrast.

Mobile:

- Replace the current hidden links with an accessible menu button.
- Open a focused menu surface containing the same destinations.
- Support Escape, backdrop-click dismissal, focus containment, and focus restoration.
- Keep all interactive targets at least 44px by 44px.
- Preserve a useful navigation fallback when JavaScript is unavailable.

### Hero

The hero fits within the initial viewport and contains only four content elements:

1. The existing maintenance-window kicker
2. The headline "Watch the whole window."
3. A tightened 17-word description
4. The install command and copy action

Approved hero description:

> Monitor every host in one live table, then export structured JSON evidence when the maintenance window closes.

The copy occupies an offset left column. The live terminal extends across the dominant right and lower portion of the hero canvas. The terminal receives the strongest double-bezel treatment on the page.

The terminal is not a fake product preview. It remains a real DOM representation driven by the existing deterministic PingHue state model.

### Product proof rail

A separate section immediately after the hero presents four verified facts:

- Up to 1024 concurrent probes
- Schema version 1 JSON
- macOS and Linux
- No server or daemon

The rail is not part of the hero and does not use invented performance claims.

### Why PingHue

Preserve the six verified product benefits but replace the uniform two-column ledger with an asymmetric open composition.

- One primary benefit receives stronger scale and width.
- The remaining benefits form offset groups with shared alignment.
- No enclosing cards.
- Signal colors appear only where the copy names a real state.

### Modes

The section contains exactly three mode surfaces:

- TUI is the dominant wide surface.
- TCP is a smaller offset surface.
- No-TUI automation is a second smaller surface with a distinct vertical position.

The arrangement forms a controlled Z-axis cascade on desktop. Rotations, overlap, and negative margins disappear below 768px. Mobile uses a strict single-column stack.

### Fixed latency scale

Replace ten equal tiles with one unified signal runway.

- Preserve every documented threshold and glyph.
- Show the latency progression as a continuous baseline composition.
- Keep loss and TCP-refused markers visually distinct from successful latency.
- Provide valid accessible semantics without misusing `role="table"`.
- Collapse into a compact, readable two-row layout on narrow screens.

### Product capture

Use the existing real PingHue screenshot as a full-width product-proof moment.

- Include intrinsic dimensions to prevent layout shift.
- Use meaningful alternative text.
- Load it lazily because it appears below the initial viewport.
- Do not overlay decorative labels or captions on the image.
- Keep the media within the same dark theme and concentric frame system.

### Evidence

Preserve the existing JSON command, schema fields, and three evidence claims.

- Use a staggered asymmetric composition rather than a repeated split-section template.
- Let the JSON sample occupy the primary visual plane.
- Place the evidence claims in a compact supporting cluster.
- Keep long code horizontally scrollable without causing page-level overflow.

### Scope

Preserve "Small on purpose." and the five scope boundaries.

- Present the section as a restrained manifesto within the Slate theme.
- Avoid cards, fake quotes, or decorative status dots.
- Keep the concluding sentence direct and factual.

### Install

Create a machined install station with four verified commands.

- Feature `uv tool install pinghue` as the recommended command.
- Preserve pipx, Homebrew, and pip as visible alternatives.
- Keep command strings exact.
- Copy labels never wrap.
- Success and failure feedback is visible and announced to assistive technology.
- Clipboard failure leaves the command selectable and provides a direct recovery message.

### Footer

- Preserve GitHub, PyPI, Security model, and MIT license destinations.
- Preserve the PingHue wordmark.
- Reduce decorative copy and keep the footer visually quiet.
- Do not add version numbers, weather, locale, or decorative build metadata.

## Motion choreography

Motion has four purposes: hierarchy, product storytelling, interaction feedback, and state communication.

### Initial load

- Hero copy resolves first.
- The live terminal settles into the product stage second.
- The sequence uses opacity and transforms only.
- The install action remains visible throughout the sequence.

### Scroll entry

- Use `IntersectionObserver` to apply one-time reveal states to selected sections.
- Use opacity and transform properties only.
- Do not add a global scroll listener or custom scroll progress loop.
- Do not introduce GSAP, Motion, or another animation dependency.

### Terminal lifecycle

- Preserve the deterministic probe story and fixed glyph scale.
- Render a meaningful initial terminal state in HTML before JavaScript runs.
- Pause simulation updates while the terminal is offscreen.
- Pause while the document is hidden.
- Resume only when both visibility conditions are satisfied.

### Reduced motion

- Disable smooth scrolling under `prefers-reduced-motion: reduce`.
- Render the terminal's complete static end-state immediately.
- Remove reveal translations and staged delays.
- Preserve all information and hierarchy without animation.

### Interaction feedback

- Buttons use a physical pressed state.
- Copy buttons update visible labels and an `aria-live` status region.
- Mobile menu transitions preserve focus and do not animate layout dimensions.

## Progressive enhancement and states

The page has no remote data dependency, so loading and network-error interfaces are unnecessary.

Required non-success states:

- JavaScript unavailable: initial terminal rows, visible navigation, selectable commands, and all content remain present.
- Clipboard unavailable or rejected: show "Copy failed" through visible and accessible feedback without hiding the command.
- Product image unavailable: intrinsic layout space remains reserved and alternative text communicates the content.
- Reduced motion: all content renders in its resolved state.
- Narrow viewport: no page-level horizontal overflow, and the hero terminal switches to an intentional compact projection.

## Mobile behavior

Below 768px:

- All asymmetric grids collapse to one column.
- Z-axis overlaps and rotations are removed.
- Section padding uses the compact mobile scale.
- Hero copy precedes the terminal.
- The terminal presents essential columns without requiring page-level horizontal scrolling.
- Secondary terminal metrics may remain available within the terminal's own contained overflow region.
- Navigation remains fully accessible.
- Code samples and commands scroll only inside their own containers.
- Touch targets meet the 44px minimum.

## Content policy

- Preserve the existing factual, operator-oriented voice.
- Keep section introductions under 25 words unless technical accuracy requires more.
- Use no testimonials, customer logos, invented adoption claims, or fake benchmarks.
- Use no generic marketing verbs or decorative operational metadata.
- Use one copy register across the page.
- Audit every visible string for grammar, clarity, factual support, and forbidden dash characters.

## Media plan

- Reuse `docs/assets/pinghue-screenshot.png` as visible product proof.
- Keep the live DOM terminal as the hero visual.
- Create one local monochrome Slate texture with no text, logos, signal colors, or identifiable infrastructure.
- Optimize the texture before use and keep it subtle enough that disabling it does not change readability.
- Create an updated 1200 by 630 social preview using deterministic local composition and the real product screenshot.
- Add explicit social-image width, height, and alternative-text metadata.
- Do not load external images or fonts.

## SEO and discovery

Preserve the canonical URL and update the title and description to concise, factual variants.

Proposed title:

> PingHue - concurrent ICMP and TCP ping monitor

Proposed description:

> Monitor many hosts in one colored terminal table, run ICMP or TCP probes, and export schema-versioned JSON evidence for maintenance windows.

Add:

- Open Graph description and image metadata
- Twitter description and image alternative text
- `robots.txt`
- `sitemap.xml`

Do not weaken the Content Security Policy to add inline structured data. Semantic HTML remains the primary machine-readable representation.

## Accessibility

- Preserve the skip link and logical heading hierarchy.
- Maintain WCAG AA contrast for all text and controls.
- Maintain visible focus indicators on every interactive element.
- Ensure focus is not hidden behind the navigation island.
- Use `scroll-margin-top` for anchored sections.
- Announce copy success and failure.
- Give the mobile menu a unique accessible name and correct expanded state.
- Preserve keyboard access to every action.
- Use meaningful image alternative text and intrinsic dimensions.
- Avoid invalid ARIA table semantics in the latency scale.
- Test both normal and reduced-motion modes.

## Performance

- Keep all assets local.
- Introduce no production dependency, framework, or build pipeline.
- Preload only the fonts needed above the fold.
- Keep the hero free of below-fold product media downloads.
- Lazy-load the product screenshot.
- Animate only transforms and opacity.
- Pause terminal work while it is offscreen.
- Target LCP below 2.5 seconds, CLS below 0.1, and responsive interaction feedback.

## Security

- Preserve the exact self-only Content Security Policy unless a stricter compatible policy is available.
- Keep all script sources first-party and local.
- Keep install commands pinned in the deploy-gating test.
- Do not introduce analytics, remote embeds, CDNs, forms, cookies, or external requests.
- Treat all modified first-party HTML, CSS, JavaScript, tests, and asset-generation scripts as security-scan inputs.
- Remediate confirmed findings before completion.

## Implementation surfaces

Expected modified files:

- `docs/index.html`
- `docs/styles.css`
- `docs/script.js`
- `docs/404.html`
- `tests/site_pages.test.mjs`

Expected added files:

- One optimized local Slate texture under `docs/assets/`
- One 1200 by 630 social preview under `docs/assets/`
- `docs/robots.txt`
- `docs/sitemap.xml`

No application Python module, CLI contract, JSON schema, packaging configuration, or release workflow should change.

## Verification plan

### Static and repository checks

- Run `node --test tests/site_pages.test.mjs` after each website increment.
- Run the relevant repository test suite.
- Run `git diff --check`.
- Verify no em dash or en dash characters appear in visible site copy.
- Verify exact install commands and current version claims.
- Verify every referenced local asset exists.

### Browser checks

Use a local HTTP server and real Chrome.

Test viewports:

- 1440 by 1000 desktop
- 1024 by 768 tablet
- 390 by 844 mobile
- 360 by 800 narrow mobile

For each relevant viewport, verify:

- Hero content and install action fit the initial viewport.
- Navigation remains usable and does not wrap.
- No page-level horizontal overflow occurs.
- Terminal content remains intentional and readable.
- Copy controls work and report both success and failure.
- Anchor navigation lands below the navigation island.
- Keyboard focus order is logical.
- Mobile menu opens, traps focus, closes with Escape, and restores focus.
- Console contains no errors or warnings.
- CSP produces no unexpected violations.
- Reduced-motion mode presents resolved content.

### Quality gates

- Capture desktop and mobile screenshots for visual review.
- Run local Lighthouse performance and accessibility checks when available.
- Run the repository's configured security scan over every modified first-party file or the complete diff.
- Rerun the same scan after any remediation until no confirmed unresolved finding remains.

## Risks and mitigations

### Hero overflow

Risk: A larger terminal could push the install action below the fold.

Mitigation: Plan copy scale and terminal scale together, cap hero text, and validate at all required viewports.

### Mobile density

Risk: The desktop terminal table could become an accidental horizontal-scroll experience.

Mitigation: Use a deliberate compact projection for essential columns and contain any secondary overflow within the terminal.

### Focus management

Risk: The mobile menu could trap or lose keyboard focus.

Mitigation: Use a small, isolated menu controller with explicit open, close, Escape, containment, and restoration behavior.

### Semantic color drift

Risk: Visual polish could turn green, amber, or red into decoration.

Mitigation: Audit each use against a documented state meaning and use blue for non-state interaction.

### Animation cost

Risk: New reveals plus the terminal loop could increase main-thread work.

Mitigation: Use one-time IntersectionObserver reveals, transform and opacity only, and pause terminal updates when offscreen.

### SEO regression

Risk: Recomposition could remove meaningful copy, metadata, or anchors.

Mitigation: Preserve anchor IDs and factual content, expand metadata tests, and compare the final document outline and canonical tags.

## Non-goals

- Rebranding PingHue
- Changing the information architecture or route structure
- Replacing the static site with a framework
- Adding a documentation portal, blog, pricing, account flow, analytics, or contact form
- Changing CLI behavior, Python code, JSON schemas, packaging, or release channels
- Adding invented social proof or external stock photography

## Final acceptance checklist

- [ ] The live terminal is the dominant hero visual.
- [ ] The install action is visible in the initial viewport.
- [ ] All committed brand tokens and product facts are preserved.
- [ ] Existing anchors and exact install commands remain stable.
- [ ] Mobile navigation exposes every destination.
- [ ] The no-JavaScript page remains understandable and usable.
- [ ] Copy feedback is visible and announced.
- [ ] Reduced motion resolves all animated content.
- [ ] No decorative use of semantic signal colors appears.
- [ ] No em dash or en dash appears in visible site copy.
- [ ] Desktop, tablet, and mobile browser checks pass.
- [ ] Console and CSP checks are clean.
- [ ] Static tests, repository tests, diff checks, and security scans pass.
- [ ] No production dependency or build step is added.
