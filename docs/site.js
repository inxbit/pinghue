/* pinghue.com: the table is the terrain.
   The page is drawn in pinghue's own fixed glyph scale (src/pinghue/history.py).
   A deterministic simulated run feeds a perspective terrain (one ridge per host)
   and the flat table under it; head-on, every ridge is the history column
   pinghue prints. On load a landscape clip is printed through the same glyph
   ramp, then the data repaints over it from the horizon forward. No JavaScript
   shows the final frame; reduced motion shows it without the clip or the clock. */

(() => {
  "use strict";

  /* ------------------------------------------------ the fixed scale */

  // Mirrors BAR_BUCKETS in src/pinghue/history.py and SLOW_LATENCY_MS in app.py.
  const BUCKETS = [[1, "▁"], [3, "▂"], [10, "▃"], [30, "▄"], [100, "▅"], [300, "▆"], [1000, "▇"]];
  const SLOW_MS = 100;
  const JITTER_THRESHOLD = 50;
  const FAIL_THRESHOLD = 3;

  const bandOf = (ms) => {
    for (let i = 0; i < BUCKETS.length; i++) if (ms <= BUCKETS[i][0]) return i;
    return 7;
  };
  const glyphFor = (ms) => (ms === null ? "·" : (BUCKETS[bandOf(ms)] || [0, "█"])[1]);
  const toneFor = (ms) => (ms === null ? "fail" : ms > SLOW_MS ? "slow" : "ok");

  /* ------------------------------------------------ the simulated run */

  // One deterministic window so every visitor sees the same night. Something
  // happens in every 56-probe stretch: db-primary climbs four ridges, api-gw
  // drops three probes, backup-nas goes down twice and stays down the second time.
  const PERIOD = 240;
  const START = 176;
  const HISTORY = 56;

  const buildRun = () => {
    let seed = 20261005;
    const rand = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    const wobble = (base, spread) => base + (rand() - 0.5) * 2 * spread;
    const ridge = (t, at, width, peak) => peak * Math.exp(-(((t - at) / width) ** 2));
    const hump = (t, period, phase, power) => Math.max(0, Math.sin(t / period + phase)) ** power;

    const hosts = [
      { name: "edge-router-1", at: (t) => wobble(6.8, 2) + 24 * hump(t, 5.6, 0, 2) },
      { name: "core-sw-1", at: (t) => wobble(1.7, 0.5) + 7 * hump(t, 4.3, 1, 2) },
      {
        name: "db-primary",
        at: (t) => {
          const climb = ridge(t, 150, 8.5, 1040) + ridge(t, 64, 4, 70) + ridge(t, 30, 6, 420)
            + ridge(t, 95, 5, 300) + ridge(t, 210, 5, 260);
          return Math.max(9, wobble(16, 5) + 50 * hump(t, 6.2, 2, 2) + climb * (0.86 + rand() * 0.28));
        },
      },
      { name: "api-gw", at: (t) => (t === 20 || t === 100 || t === 146 ? null : wobble(15, 4) + 68 * hump(t, 7.1, 0.4, 2)) },
      {
        name: "backup-nas",
        at: (t) => ((t >= 60 && t < 84) || (t >= 152 && t < 233) ? null : wobble(4.2, 1.4) + 24 * hump(t, 5.1, 2.2, 2)),
      },
      { name: "dns-resolver", at: (t) => Math.max(0.3, wobble(0.75, 0.25) + 5.5 * hump(t, 6.7, 0.8, 2)) },
    ];

    const samples = hosts.map((h) => {
      const row = [];
      for (let t = 0; t < PERIOD; t++) {
        const v = h.at(t);
        row.push(v === null ? null : Math.round(v * 100) / 100);
      }
      return row;
    });

    // Whole-run statistics, the way pinghue keeps them, for every tick.
    const frames = [];
    const acc = hosts.map(() => ({
      sent: 0, received: 0, sum: 0, last: null, prev: null, jitter: 0,
      fails: 0, seen: false, latched: false,
    }));
    for (let t = 0; t < PERIOD; t++) {
      frames.push(hosts.map((h, i) => {
        const a = acc[i];
        const ms = samples[i][t];
        a.sent += 1;
        if (ms === null) {
          a.fails += 1;
          a.last = null;
          a.latched = true;
        } else {
          a.received += 1;
          a.sum += ms;
          a.fails = 0;
          a.seen = true;
          if (a.prev !== null) a.jitter += (Math.abs(ms - a.prev) - a.jitter) / 16;
          if (a.jitter > JITTER_THRESHOLD) a.latched = true;
          a.prev = ms;
          a.last = ms;
        }
        const down = a.seen ? a.fails >= FAIL_THRESHOLD : true;
        return {
          host: h.name,
          state: down ? "down" : a.latched ? "intermittent" : "healthy",
          last: a.last,
          avg: a.received ? a.sum / a.received : null,
          jitter: a.received > 1 ? a.jitter : 0,
          loss: ((a.sent - a.received) / a.sent) * 100,
        };
      }));
    }

    const historyAt = (i, t) => {
      const out = [];
      for (let k = t - HISTORY + 1; k <= t; k++) out.push(samples[i][((k % PERIOD) + PERIOD) % PERIOD]);
      return out;
    };

    return { hosts, samples, frames, historyAt };
  };

  const fmt = (n) => (n === null ? "-" : n.toFixed(2));
  const fmtLoss = (n) => n.toFixed(2) + "%";

  // The pure core is importable (tests, the static-frame generator) without a DOM.
  if (typeof module === "object" && module.exports) {
    module.exports = { buildRun, glyphFor, toneFor, bandOf, fmt, fmtLoss, START, HISTORY, PERIOD };
    return;
  }

  const doc = document;
  doc.documentElement.classList.add("js");

  /* ------------------------------------------------ copy buttons */

  const copyStatus = document.querySelector("[data-copy-status]");
  let copyAnnouncementVersion = 0;

  document.querySelectorAll(".copy-btn").forEach((btn) => {
    const originalLabel = btn.getAttribute("aria-label") || "Copy command";
    let operationVersion = 0;
    let resetTimer = null;

    const resetButton = () => {
      btn.classList.remove("copied");
      btn.textContent = "Copy";
      btn.setAttribute("aria-label", originalLabel);
    };

    const report = (
      label,
      announcement,
      copied,
      announcementVersion,
      currentOperation,
    ) => {
      if (currentOperation !== operationVersion) return;
      clearTimeout(resetTimer);
      resetTimer = null;
      btn.classList.toggle("copied", copied);
      btn.textContent = label;
      btn.setAttribute("aria-label", label === "Copied" ? "Command copied" : label);
      if (copyStatus && announcementVersion === copyAnnouncementVersion) {
        copyStatus.textContent = announcement;
      }
      resetTimer = setTimeout(() => {
        if (currentOperation !== operationVersion) return;
        resetTimer = null;
        resetButton();
        if (copyStatus && announcementVersion === copyAnnouncementVersion) {
          copyStatus.textContent = "";
        }
      }, 1800);
    };

    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-copy") || "";
      const currentOperation = ++operationVersion;
      const announcementVersion = ++copyAnnouncementVersion;
      clearTimeout(resetTimer);
      resetTimer = null;
      resetButton();
      if (copyStatus) copyStatus.textContent = "";
      const done = () => report(
        "Copied",
        "Install command copied.",
        true,
        announcementVersion,
        currentOperation,
      );
      const failed = () => report(
        "Copy failed",
        "Copy failed. Select the command and copy it manually.",
        false,
        announcementVersion,
        currentOperation,
      );

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, failed);
      } else {
        failed();
      }
    });
  });

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const stacked = () => window.matchMedia("(max-width: 1099px)").matches;
  const run = buildRun();
  let paused = false;

  /* ------------------------------------------------ the flat table */

  const tableBody = doc.querySelector("[data-rows]");
  const TABLE_TAIL = 14;

  const renderTable = (t) => {
    if (!tableBody) return;
    const frame = run.frames[t % PERIOD];
    [...tableBody.rows].forEach((tr, i) => {
      const f = frame[i];
      if (!f) return;
      const cells = tr.cells;
      cells[1].textContent = f.state;
      cells[1].className = "st-" + f.state;
      cells[2].textContent = fmt(f.last);
      cells[2].className = f.last === null ? "t-fail" : f.last > SLOW_MS ? "t-slow" : "";
      cells[3].textContent = fmt(f.avg);
      cells[3].className = f.avg !== null && f.avg > SLOW_MS ? "t-slow" : "";
      cells[4].textContent = fmt(f.jitter);
      cells[4].className = f.jitter > JITTER_THRESHOLD ? "t-slow" : "";
      cells[5].textContent = fmtLoss(f.loss);
      cells[5].className = f.loss > 0 ? "t-fail" : "";
      const tail = run.historyAt(i, t).slice(-TABLE_TAIL);
      const hist = cells[6];
      if (hist.childElementCount !== TABLE_TAIL) {
        hist.textContent = "";
        for (let k = 0; k < TABLE_TAIL; k++) hist.appendChild(doc.createElement("span"));
      }
      tail.forEach((ms, k) => {
        const span = hist.children[k];
        span.textContent = glyphFor(ms);
        span.className = "g-" + toneFor(ms);
      });
    });
  };

  /* ------------------------------------------------ printing a clip in glyphs */

  // One frame of a video printed through pinghue's ramp: equalized so form
  // reads instead of one dark slab, ridgelines (light above dark) printed full,
  // the darkest tenths hatched. Bars are bottom-anchored like the real glyphs.
  const printClip = (ctx, video, scratch, f) => {
    const { x0, y0, cols, rows, cw, ch } = f;
    if (cols <= 0 || rows <= 0) return;
    if (scratch.canvas.width !== cols || scratch.canvas.height !== rows) {
      scratch.canvas.width = cols;
      scratch.canvas.height = rows;
    }
    const vw = video.videoWidth || 16;
    const vh = video.videoHeight || 9;
    const boxAspect = (cols * cw) / (rows * ch);
    let sh = vh * f.cropHeight;
    let sw = sh * boxAspect;
    if (sw > vw) {
      sw = vw;
      sh = sw / boxAspect;
    }
    const sy = Math.min(vh - sh, vh * f.cropTop);
    scratch.ctx.drawImage(video, (vw - sw) / 2, sy, sw, sh, 0, 0, cols, rows);
    const px = scratch.ctx.getImageData(0, 0, cols, rows).data;
    const n = cols * rows;
    if (!scratch.lums || scratch.lums.length !== n) {
      scratch.lums = new Float32Array(n);
      scratch.sorted = new Float32Array(n);
    }
    const lums = scratch.lums;
    for (let i = 0; i < n; i++) {
      const o = i * 4;
      lums[i] = (0.2126 * px[o] + 0.7152 * px[o + 1] + 0.0722 * px[o + 2]) / 255;
    }
    scratch.sorted.set(lums);
    scratch.sorted.sort();
    const cuts = [];
    for (let q = 1; q < 10; q++) cuts.push(scratch.sorted[Math.floor((n - 1) * (1 - q / 10))]);
    const barW = cw * 0.72;
    for (let y = 0; y < rows; y++) {
      const yb = y0 + (y + 1) * ch;
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x;
        const lum = lums[i];
        let level = 0;
        while (level < 9 && lum <= cuts[level]) level++;
        level -= 1;
        const ridge = y > 1 && lum < cuts[3] && lums[i - cols] - lum > 0.045;
        if (ridge) level = 9;
        else if (f.hatch && level >= 8 && (x + 2 * y) % 3 === 0) continue;
        if (level <= 0) continue;
        const cx = x0 + x * cw;
        if (level === 1) {
          ctx.fillRect(cx + cw * 0.3, yb - ch * 0.16, cw * 0.2, ch * 0.1);
          continue;
        }
        const h = (level - 1) / 8 * ch * 0.9;
        ctx.fillRect(cx, yb - h, barW, h);
      }
    }
  };

  const makeScratch = () => {
    const el = doc.createElement("canvas");
    return { canvas: el, ctx: el.getContext("2d", { willReadFrequently: true }), lums: null, sorted: null };
  };

  /* ------------------------------------------------ the terrain */

  const stage = doc.querySelector("[data-terrain]");
  const canvas = stage && stage.querySelector("canvas");
  const ctx = canvas && canvas.getContext("2d");
  const ink = {
    ink: "#161513",
    muted: "#5d5b55",
    floor: "#a9a59a",
    plane: "#c6c2b7",
    red: "#b8322b",
    ok: "#1d7a37",
    slow: "#9c5a00",
  };
  // Cells per glyph band: height still orders exactly like ▁▂▃▄▅▆▇█.
  const STACK = [1, 2, 4, 5, 7, 9, 11, 13];
  // Each stack shades from its band's light foot to a dark crest.
  const FOOT = ["#7dbb88", "#6db07a", "#5ea66d", "#4f9c60", "#409154", "#e5b24c", "#d08b2b", "#9a5518"];
  const CREST = ["#1f6a2c", "#1c6429", "#195e26", "#165823", "#135220", "#a8690d", "#7a3d08", "#3b1c04"];
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const SHADES = FOOT.map((foot, b) => {
    const a = hex(foot);
    const c = hex(CREST[b]);
    const n = STACK[b];
    return Array.from({ length: n }, (_, s) => {
      const k = n === 1 ? 1 : s / (n - 1);
      return "rgb(" + a.map((v, j) => Math.round(v + (c[j] - v) * k)).join(",") + ")";
    });
  });

  let geo = null;
  let dpr = 1;
  let shownTick = START;
  let tilt = 1; // 1 = perspective terrain, 0 = head-on (landed on the table)
  let landed = false;

  const lerp = (a, b, k) => a + (b - a) * k;
  // Row anchors measured from the approved comp, as fractions of the stage box.
  const ROWS = [
    { y: 0.0555, l: 0.4434, r: 0.9735 },
    { y: 0.1367, l: 0.392, r: 0.978 },
    { y: 0.272, l: 0.311, r: 0.983 },
    { y: 0.38, l: 0.26, r: 0.988 },
    { y: 0.495, l: 0.219, r: 0.988 },
    { y: 0.597, l: 0.178, r: 0.983 },
  ];
  // Stacked layouts have no table inside the stage, so the terrain takes its full height.
  const NARROW_ROWS = [
    { y: 0.22, l: 0.44, r: 0.985 },
    { y: 0.35, l: 0.41, r: 0.985 },
    { y: 0.52, l: 0.37, r: 0.985 },
    { y: 0.66, l: 0.34, r: 0.985 },
    { y: 0.8, l: 0.31, r: 0.985 },
    { y: 0.93, l: 0.28, r: 0.985 },
  ];

  const measure = () => {
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(box.width * dpr);
    canvas.height = Math.round(box.height * dpr);
    const table = doc.querySelector("[data-table]");
    const head = table ? table.querySelector("thead") : null;
    const hb = head ? head.getBoundingClientRect() : null;
    const tb = table ? table.getBoundingClientRect() : null;
    const histCells = table ? [...table.querySelectorAll("tbody td:last-child")] : [];
    geo = {
      w: box.width,
      h: box.height,
      narrow: stacked(),
      tableTop: hb ? hb.top - box.top : box.height,
      tableLeft: tb ? tb.left - box.left : box.width * 0.13,
      tableRight: tb ? tb.right - box.left : box.width,
      // Where the table's real history glyphs sit, so head-on lands on them.
      flat: histCells.map((td) => {
        const spans = td.children;
        if (!spans.length) return null;
        const first = spans[0].getBoundingClientRect();
        const last = spans[spans.length - 1].getBoundingClientRect();
        const fs = parseFloat(getComputedStyle(spans[0]).fontSize) || 16;
        const baseline = first.top + 0.859 * fs;
        return { x: first.left - box.left, w: last.right - first.left, y: baseline + 0.4 * fs - box.top, unit: 0.175 * fs };
      }),
    };
  };

  const rowGeometry = (r) => {
    const a = (geo.narrow ? NARROW_ROWS : ROWS)[r];
    const d = r / (ROWS.length - 1);
    const persp = {
      left: a.l * geo.w,
      right: a.r * geo.w,
      base: a.y * geo.h,
      unit: geo.narrow ? lerp(0.0105, 0.0155, d) * geo.h : lerp(0.0062, 0.0094, d) * geo.w,
    };
    const f = geo.flat[r];
    if (tilt >= 1 || !f) return persp;
    const k = tilt;
    return {
      left: lerp(f.x, persp.left, k),
      right: lerp(f.x + f.w, persp.right, k),
      base: lerp(f.y, persp.base, k),
      unit: lerp(f.unit, persp.unit, k),
    };
  };

  const drawLabels = () => {
    if (tilt <= 0.5 || landed) return;
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = (tilt - 0.5) * 2;
    ctx.fillStyle = ink.ink;
    ctx.font = Math.round(geo.narrow ? 11 : Math.max(11, geo.w * 0.0198)) + "px Inconsolata, ui-monospace, monospace";
    ctx.fontStretch = "semi-condensed";
    ctx.textAlign = "right";
    ctx.textBaseline = "alphabetic";
    for (let r = 0; r < ROWS.length; r++) {
      const g = rowGeometry(r);
      ctx.fillText(run.hosts[r].name, g.left - geo.w * 0.008, g.base - g.unit * 3);
    }
    ctx.restore();
  };

  const drawTerrain = (t, withLabels = true) => {
    if (!ctx || !geo) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, geo.w, geo.h);
    if (landed) return;

    const n = Math.max(TABLE_TAIL, Math.round(lerp(TABLE_TAIL, HISTORY, tilt)));
    const front = rowGeometry(ROWS.length - 1);
    const back = rowGeometry(0);

    // The plane: dotted column lines receding to the back row, and guides
    // that carry them down onto the table.
    if (tilt > 0.02) {
      ctx.save();
      ctx.globalAlpha = tilt;
      ctx.lineWidth = 1;
      ctx.setLineDash([1, 3]);
      ctx.strokeStyle = ink.plane;
      ctx.beginPath();
      for (let c = 0; c <= HISTORY; c += 11) {
        for (let r = 0; r < ROWS.length; r++) {
          const g = rowGeometry(r);
          const x = g.left + (c / HISTORY) * (g.right - g.left);
          if (r === 0) ctx.moveTo(x, g.base + 2);
          else ctx.lineTo(x, g.base + 2);
        }
      }
      ctx.stroke();
      if (!geo.narrow) {
        ctx.strokeStyle = ink.muted;
        ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(back.left, back.base + 2);
        ctx.lineTo(front.left, front.base + 2);
        ctx.moveTo(back.right, back.base + 2);
        ctx.lineTo(front.right, front.base + 2);
        for (let c = 0; c <= HISTORY; c += 11) {
          const k = c / HISTORY;
          ctx.moveTo(lerp(front.left, front.right, k), front.base + 6);
          ctx.lineTo(lerp(geo.tableLeft, geo.tableRight, k), geo.tableTop - 6);
        }
        ctx.stroke();
      }
      ctx.restore();
    }

    const flatness = 1 - tilt;
    for (let r = 0; r < ROWS.length; r++) {
      const g = rowGeometry(r);
      const hist = run.historyAt(r, t).slice(-n);
      const col = (g.right - g.left) / n;
      const cellW = Math.max(1, col * lerp(0.66, 1, flatness));
      const cellH = Math.max(1, g.unit * lerp(0.78, 1.02, flatness));

      // Floor: one dot per column.
      if (tilt > 0.3) {
        ctx.fillStyle = ink.floor;
        for (let c = 0; c < n; c++) ctx.fillRect(g.left + c * col + cellW / 2, g.base + 2, 1, 1);
      }

      for (let c = 0; c < n; c++) {
        const ms = hist[c];
        const x = g.left + c * col;
        if (ms === null) {
          ctx.fillStyle = ink.red;
          const s = Math.max(1.5, Math.min(cellW, g.unit * 2) * 0.42);
          ctx.fillRect(x + (cellW - s) / 2, g.base - s - g.unit * lerp(0.2, 2.6, flatness), s, s);
          continue;
        }
        const b = bandOf(ms);
        const stack = Math.max(b + 1, Math.round(lerp(b + 1, STACK[b], tilt)));
        const shades = SHADES[b];
        for (let s = 0; s < stack; s++) {
          ctx.fillStyle = tilt < 0.35
            ? (b > 4 ? ink.slow : ink.ok)
            : shades[Math.round((s / Math.max(1, stack - 1)) * (shades.length - 1))];
          ctx.fillRect(x, g.base - (s + 1) * g.unit, cellW, cellH);
        }
      }
    }
    if (withLabels) drawLabels();
  };

  /* ------------------------------------------------ the landscape clip, then the data */

  let intro = null;

  const playIntro = (onDone) => {
    const src = stage && stage.getAttribute("data-clip");
    if (!src || !ctx) return onDone();
    const video = doc.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = src;
    const scratch = makeScratch();
    let finished = false;
    let playing = false;
    let raf = 0;
    let layout = null;

    // The clip is printed only inside the terrain's footprint, from the horizon down.
    const plan = () => {
      const cw = geo.narrow ? 6 : Math.max(6, geo.w * 0.0072);
      const ch = cw * 1.62;
      const back = rowGeometry(0);
      const front = rowGeometry(ROWS.length - 1);
      const top = Math.max(0, back.base - back.unit * 9);
      const bottom = front.base + 6;
      const startCol = Math.floor(front.left / cw);
      const path = new Path2D();
      path.moveTo(back.left, top);
      path.lineTo(geo.w, top);
      path.lineTo(geo.w, bottom);
      path.lineTo(front.left, bottom);
      path.closePath();
      return {
        cw, ch, top, path,
        x0: startCol * cw,
        cols: Math.ceil(geo.w / cw) - startCol,
        rows: Math.ceil((bottom - top) / ch),
        gridCols: Math.ceil(geo.w / cw),
      };
    };

    const frame = () => {
      if (finished) return;
      layout = plan();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, geo.w, geo.h);
      ctx.save();
      ctx.clip(layout.path);
      ctx.fillStyle = ink.ink;
      printClip(ctx, video, scratch, {
        x0: layout.x0, y0: layout.top, cols: layout.cols, rows: layout.rows,
        cw: layout.cw, ch: layout.ch, cropTop: 0, cropHeight: 0.62, hatch: true,
      });
      ctx.restore();
      raf = requestAnimationFrame(frame);
    };

    const stopVideo = () => {
      try {
        video.pause();
        video.removeAttribute("src");
        video.load();
      } catch (e) { /* already gone */ }
    };

    const bail = () => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf);
      stopVideo();
      intro = null;
      onDone();
    };

    // The data prints over the clip line by line, from the horizon forward.
    const dissolve = () => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf);
      stopVideo();
      if (!layout) layout = plan();
      const { cw, ch, top, gridCols, rows } = layout;
      const snap = doc.createElement("canvas");
      snap.width = canvas.width;
      snap.height = canvas.height;
      snap.getContext("2d").drawImage(canvas, 0, 0);
      const count = gridCols * rows;
      const keys = new Float32Array(count);
      let s = 7;
      for (let i = 0; i < count; i++) {
        s = (Math.imul(s, 1103515245) + 12345) >>> 0;
        keys[i] = Math.floor(i / gridCols) + ((i % gridCols) / gridCols) * 0.9 + (s / 4294967296) * 0.35;
      }
      const order = Array.from({ length: count }, (_, i) => i).sort((a, b) => keys[a] - keys[b]);
      const shown = new Uint8Array(count);
      const t0 = performance.now();
      const DURATION = 1300;
      intro = { abort: () => { intro = null; onDone(); } };
      const step = (now) => {
        if (!intro) return;
        if (canvas.width !== snap.width || canvas.height !== snap.height) {
          intro.abort();
          return;
        }
        const k = Math.min(1, (now - t0) / DURATION);
        const upto = Math.floor(count * k);
        for (let i = 0; i < upto; i++) shown[order[i]] = 1;
        drawTerrain(shownTick, false);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        for (let i = 0; i < count; i++) {
          if (shown[i]) continue;
          const x = (i % gridCols) * cw * dpr;
          const y = (top + Math.floor(i / gridCols) * ch) * dpr;
          const w = cw * dpr + 1;
          const h = ch * dpr + 1;
          ctx.clearRect(x, y, w, h);
          ctx.drawImage(snap, x, y, w, h, x, y, w, h);
        }
        drawLabels();
        if (k < 1) requestAnimationFrame(step);
        else {
          intro = null;
          onDone();
        }
      };
      requestAnimationFrame(step);
    };

    intro = { abort: bail };
    video.addEventListener("playing", () => {
      if (finished) return;
      playing = true;
      stage.classList.add("is-drawn", "is-clip");
      raf = requestAnimationFrame(frame);
      setTimeout(dissolve, 4500);
    }, { once: true });
    // Hold the landscape about three seconds, then repaint it as data.
    video.addEventListener("timeupdate", () => {
      if (playing && video.currentTime >= 2.8) dissolve();
    });
    video.addEventListener("ended", dissolve, { once: true });
    video.addEventListener("error", bail, { once: true });
    const started = video.play();
    if (started && started.catch) started.catch(bail);
    setTimeout(() => { if (!playing) bail(); }, 2500);
  };

  /* ------------------------------------------------ clock, visibility, pause */

  let timer = null;
  let stageVisible = !("IntersectionObserver" in window);
  let documentVisible = !doc.hidden;
  let live = false;
  const toggle = doc.querySelector("[data-tilt]");
  const pauseBtn = doc.querySelector("[data-pause]");

  const tick = () => {
    shownTick = (shownTick + 1) % PERIOD;
    renderTable(shownTick);
    drawTerrain(shownTick);
  };

  const updateTimer = () => {
    const shouldRun = live && !reduced && !paused && stageVisible && documentVisible;
    if (shouldRun && !timer) timer = setInterval(tick, 1000);
    if (!shouldRun && timer) {
      clearInterval(timer);
      timer = null;
    }
  };

  const goLive = () => {
    stage.classList.remove("is-clip");
    stage.classList.add("is-drawn", "is-live");
    drawTerrain(shownTick);
    live = true;
    if (toggle) toggle.hidden = geo.narrow;
    updateTimer();
  };

  if (stage && ctx) {
    renderTable(shownTick);
    const ready = () => {
      measure();
      if (reduced) {
        drawTerrain(shownTick);
        stage.classList.add("is-drawn");
        if (toggle) toggle.hidden = geo.narrow;
      } else {
        playIntro(goLive);
      }
    };
    if (doc.fonts && doc.fonts.ready) {
      Promise.race([doc.fonts.ready, new Promise((resolve) => setTimeout(resolve, 3000))]).then(ready, ready);
    } else {
      ready();
    }

    const relayout = () => {
      measure();
      if (live || reduced) drawTerrain(shownTick);
      if (toggle && (live || reduced)) toggle.hidden = geo.narrow;
    };
    let resizeFrame = 0;
    window.addEventListener("resize", () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(relayout);
    });
    // A move to a screen with another pixel density fires no resize.
    const watchDpr = () => {
      const mq = window.matchMedia("(resolution: " + (window.devicePixelRatio || 1) + "dppx)");
      if (!mq || !mq.addEventListener) return;
      mq.addEventListener("change", () => {
        relayout();
        watchDpr();
      }, { once: true });
    };
    watchDpr();

    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        stageVisible = entries.some((e) => e.isIntersecting);
        updateTimer();
      }).observe(stage);
    }
    doc.addEventListener("visibilitychange", () => {
      documentVisible = !doc.hidden;
      updateTimer();
    });

    // Pause stops every moving thing on the page (WCAG 2.2.2), the clip included.
    if (pauseBtn && !reduced) {
      pauseBtn.hidden = false;
      pauseBtn.addEventListener("click", () => {
        paused = !paused;
        pauseBtn.textContent = paused ? "resume" : "pause";
        if (paused && intro) intro.abort();
        updateTimer();
      });
    }

    // Head-on: tilt the terrain flat onto the table's history column, then
    // hand over to the real glyphs.
    if (toggle) {
      let anim = 0;
      toggle.addEventListener("click", () => {
        const flat = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", String(flat));
        stage.classList.toggle("is-flat", flat);
        stage.classList.remove("is-landed");
        landed = false;
        measure();
        const from = tilt;
        const to = flat ? 0 : 1;
        const finish = () => {
          tilt = to;
          landed = flat;
          stage.classList.toggle("is-landed", flat);
          drawTerrain(shownTick);
        };
        if (reduced) {
          finish();
          return;
        }
        cancelAnimationFrame(anim);
        const t0 = performance.now();
        const step = (now) => {
          const k = Math.min(1, (now - t0) / 700);
          tilt = lerp(from, to, 1 - Math.pow(1 - k, 3));
          drawTerrain(shownTick);
          if (k < 1) anim = requestAnimationFrame(step);
          else finish();
        };
        anim = requestAnimationFrame(step);
      });
    }
  }

  /* ------------------------------------------------ dawn: the window closes */

  const dawn = doc.querySelector("[data-dawn]");
  if (dawn && !reduced && "IntersectionObserver" in window) {
    const dcanvas = dawn.querySelector("canvas");
    const dctx = dcanvas && dcanvas.getContext("2d");
    const src = dawn.getAttribute("data-clip");
    let video = null;
    let scratch = null;
    let playing = false;
    let raf = 0;

    const paint = () => {
      const box = dcanvas.getBoundingClientRect();
      const ddpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.round(box.width * ddpr);
      const h = Math.round(box.height * ddpr);
      if (dcanvas.width !== w || dcanvas.height !== h) {
        dcanvas.width = w;
        dcanvas.height = h;
      }
      const cw = Math.max(5, box.width * 0.0058);
      const ch = cw * 1.62;
      dctx.setTransform(ddpr, 0, 0, ddpr, 0, 0);
      dctx.clearRect(0, 0, box.width, box.height);
      dctx.fillStyle = ink.ink;
      printClip(dctx, video, scratch, {
        x0: 0, y0: 0, cols: Math.floor(box.width / cw), rows: Math.floor(box.height / ch),
        cw, ch, cropTop: 0.29, cropHeight: 1, hatch: false,
      });
    };

    const loop = () => {
      if (!playing) return;
      paint();
      raf = requestAnimationFrame(loop);
    };

    const start = () => {
      if (playing || paused || !src || !dctx) return;
      if (!video) {
        video = doc.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.preload = "auto";
        video.src = src;
        scratch = makeScratch();
        video.addEventListener("playing", () => {
          playing = true;
          dawn.classList.add("is-drawn");
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(loop);
        });
        video.addEventListener("ended", () => {
          playing = false;
          cancelAnimationFrame(raf);
          paint();
        });
        video.addEventListener("error", () => { playing = false; });
      }
      try { video.currentTime = 0; } catch (e) { /* not seekable yet */ }
      const p = video.play();
      if (p && p.catch) p.catch(() => {});
    };

    new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) start();
    }, { threshold: 0.45 }).observe(dawn);
    if (pauseBtn) {
      pauseBtn.addEventListener("click", () => {
        if (paused && video && playing) {
          video.pause();
          playing = false;
          cancelAnimationFrame(raf);
        }
      });
    }
  }

  /* ------------------------------------------------ --no-tui line stream */

  const stream = doc.querySelector("[data-stream]");
  if (stream && !reduced) {
    let n = 0;
    const lat = [14.08, 13.91, 14.22, 13.87, 14.4, 14.02];
    setInterval(() => {
      if (doc.hidden || paused) return;
      n += 1;
      const d = new Date(Date.UTC(2026, 4, 14, 18, 32, 11 + n, 420));
      const stamp = d.toISOString().replace("Z", "000+00:00");
      stream.textContent = stamp + " example.com ok latency=" + lat[n % lat.length].toFixed(2) + "ms";
    }, 1000);
  }

  /* ------------------------------------------------ the scale lab */

  const lab = doc.querySelector("[data-lab]");
  if (lab) {
    const slider = lab.querySelector("input");
    const glyph = lab.querySelector("[data-lab-glyph]");
    const readout = lab.querySelector("[data-lab-ms]");
    const band = lab.querySelector("[data-lab-band]");
    const trail = lab.querySelector("[data-lab-trail]");
    const steps = [...doc.querySelectorAll("[data-step]")];
    const LIMITS = ["≤1ms", "≤3ms", "≤10ms", "≤30ms", "≤100ms", "≤300ms", "≤1000ms", ">1000ms"];
    const history = [];
    // The slider is logarithmic: roughly equal travel per band, all eight reachable.
    const msAt = (v) => Math.round(Math.pow(10, (v / 1000) * 4.08 - 0.6) * 100) / 100;
    const update = (write) => {
      const ms = msAt(Number(slider.value));
      const b = bandOf(ms);
      const tone = toneFor(ms);
      glyph.textContent = glyphFor(ms);
      glyph.className = "lab-glyph g-" + tone;
      readout.textContent = (ms < 10 ? ms.toFixed(2) : ms < 100 ? ms.toFixed(1) : Math.round(ms)) + "ms";
      band.textContent = LIMITS[b] + (tone === "slow" ? ", slow" : ", ok");
      slider.setAttribute("aria-valuetext", readout.textContent + ", glyph " + (b + 1) + " of 8, " + (tone === "slow" ? "amber" : "green"));
      steps.forEach((el, i) => el.classList.toggle("is-on", i === b));
      if (write && trail) {
        history.push(ms);
        if (history.length > 40) history.shift();
        trail.textContent = "";
        history.forEach((h) => {
          const s = doc.createElement("span");
          s.textContent = glyphFor(h);
          s.className = "g-" + toneFor(h);
          trail.appendChild(s);
        });
      }
    };
    for (let i = 0; i < 40; i++) history.push(run.samples[2][120 + i]);
    slider.addEventListener("input", () => update(true));
    update(true);
  }

  /* ------------------------------------------------ the report prints */

  const report = doc.querySelector("[data-print]");
  if (report && !reduced && "IntersectionObserver" in window) {
    const lines = [...report.querySelectorAll(".jl")];
    report.classList.add("is-armed");
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      io.disconnect();
      let i = 0;
      const next = setInterval(() => {
        if (i >= lines.length) {
          clearInterval(next);
          report.classList.remove("is-armed");
          return;
        }
        lines[i].classList.add("is-printed");
        i += 1;
      }, 140);
    }, { threshold: 0.35 });
    io.observe(report);
  }
})();
