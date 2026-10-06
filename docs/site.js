/* pinghue.com: the table is the terrain.
   The page is drawn in pinghue's own fixed glyph scale (src/pinghue/history.py).
   A deterministic simulated run feeds a perspective terrain (one ridge per host)
   and the flat table under it; head-on, every ridge is the history column
   pinghue prints. On load a landscape clip is rendered through the same glyph
   ramp, then dissolves into the data. No JavaScript shows the final frame;
   reduced motion shows it without the clip or the clock. */

(() => {
  "use strict";

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

  // One deterministic window so every visitor sees the same night:
  // api-gw drops one probe, db-primary climbs a ridge, backup-nas goes down.
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
          const climb = ridge(t, 150, 8.5, 1040) + ridge(t, 64, 4, 70);
          return Math.max(9, wobble(16, 5) + 50 * hump(t, 6.2, 2, 2) + climb * (0.86 + rand() * 0.28));
        },
      },
      { name: "api-gw", at: (t) => (t === 146 ? null : wobble(15, 4) + 68 * hump(t, 7.1, 0.4, 2)) },
      { name: "backup-nas", at: (t) => (t >= 152 && t < 233 ? null : wobble(4.2, 1.4) + 24 * hump(t, 5.1, 2.2, 2)) },
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

  if (typeof module === "object" && module.exports) {
    module.exports = { buildRun, glyphFor, toneFor, bandOf, fmt, fmtLoss, START, HISTORY, PERIOD };
    return;
  }

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const run = buildRun();

  /* ------------------------------------------------ the flat table */

  const tableBody = doc.querySelector("[data-rows]");
  const clock = doc.querySelector("[data-clock]");
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
    if (clock) {
      const secs = t % PERIOD;
      clock.textContent = "02:" + String(Math.floor(secs / 60)).padStart(2, "0") + ":" + String(secs % 60).padStart(2, "0");
    }
  };

  /* ------------------------------------------------ the terrain */

  const stage = doc.querySelector("[data-terrain]");
  const canvas = stage && stage.querySelector("canvas");
  const ctx = canvas && canvas.getContext("2d");
  const ink = {
    paper: "#efede6",
    ink: "#161513",
    muted: "#5d5b55",
    floor: "#bcb8ad",
    guide: "#a29e93",
    red: "#c0302a",
    // Green carries every band at or under 100ms, amber every band over it.
    band: ["#5aa865", "#4d9f59", "#40964e", "#358c44", "#2b823b", "#e0a93a", "#c97f1f", "#86450f"],
    top: ["#1f6a2c", "#1c6429", "#195e26", "#165823", "#135220", "#a8690d", "#8a4c09", "#4f2605"],
  };

  let geo = null;
  let dpr = 1;
  let shownTick = START;
  let tilt = 1; // 1 = perspective terrain, 0 = head-on (flat over the table)

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
  // Phones have no table inside the stage, so the terrain takes its full height.
  const NARROW_ROWS = [
    { y: 0.2, l: 0.44, r: 0.985 },
    { y: 0.33, l: 0.41, r: 0.985 },
    { y: 0.5, l: 0.37, r: 0.985 },
    { y: 0.64, l: 0.34, r: 0.985 },
    { y: 0.78, l: 0.31, r: 0.985 },
    { y: 0.92, l: 0.28, r: 0.985 },
  ];

  const measure = () => {
    if (!canvas) return;
    const box = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(box.width * dpr);
    canvas.height = Math.round(box.height * dpr);
    const table = doc.querySelector("[data-table]");
    const histCells = table ? [...table.querySelectorAll("tbody td:last-child")] : [];
    const head = table ? table.querySelector("thead") : null;
    const hb = head ? head.getBoundingClientRect() : null;
    const tb = table ? table.getBoundingClientRect() : null;
    geo = {
      w: box.width,
      h: box.height,
      narrow: box.width < 640,
      tableTop: hb ? hb.top - box.top : box.height,
      tableLeft: tb ? tb.left - box.left : box.width * 0.13,
      tableRight: tb ? tb.right - box.left : box.width,
      flat: histCells.map((td) => {
        const r = td.getBoundingClientRect();
        return { x: r.left - box.left, w: r.width, y: r.bottom - box.top - r.height * 0.18, h: r.height * 0.62 };
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
      unit: lerp(0.0078, 0.0118, d) * geo.w * (geo.narrow ? 1.3 : 1),
    };
    if (tilt >= 1 || !geo.flat[r]) return persp;
    const f = geo.flat[r];
    const flat = { left: f.x, right: f.x + f.w, base: f.y, unit: f.h / 8 };
    const k = tilt;
    return {
      left: lerp(flat.left, persp.left, k),
      right: lerp(flat.right, persp.right, k),
      base: lerp(flat.base, persp.base, k),
      unit: lerp(flat.unit, persp.unit, k),
    };
  };

  const drawTerrain = (t) => {
    if (!ctx || !geo) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, geo.w, geo.h);

    // Guides from the terrain's front edge down to the table.
    if (tilt > 0.02) {
      const front = rowGeometry(ROWS.length - 1);
      ctx.save();
      ctx.globalAlpha = tilt;
      ctx.strokeStyle = ink.guide;
      ctx.lineWidth = 1;
      ctx.setLineDash([1, 4]);
      const targets = [
        [front.left, geo.tableLeft],
        [lerp(front.left, front.right, 0.25), lerp(geo.tableLeft, geo.tableRight, 0.25)],
        [lerp(front.left, front.right, 0.55), lerp(geo.tableLeft, geo.tableRight, 0.55)],
        [lerp(front.left, front.right, 0.8), lerp(geo.tableLeft, geo.tableRight, 0.8)],
        [front.right, geo.tableRight],
      ];
      const back = rowGeometry(0);
      ctx.beginPath();
      ctx.moveTo(back.left, back.base);
      ctx.lineTo(front.left, front.base);
      ctx.moveTo(back.right, back.base);
      ctx.lineTo(front.right, front.base);
      targets.forEach(([x0, x1]) => {
        ctx.moveTo(x0, front.base + 4);
        ctx.lineTo(x1, geo.tableTop - 6);
      });
      ctx.stroke();
      ctx.restore();
    }

    for (let r = 0; r < ROWS.length; r++) {
      const g = rowGeometry(r);
      const hist = run.historyAt(r, t);
      const col = (g.right - g.left) / HISTORY;
      const cellW = Math.max(1, col * 0.66);
      const cellH = Math.max(1, g.unit * 0.74);

      // Floor: one faint dot per column, plus a second line for depth.
      ctx.fillStyle = ink.floor;
      for (let c = 0; c < HISTORY; c++) {
        const x = g.left + c * col + cellW / 2;
        ctx.fillRect(x, g.base + 2, 1, 1);
        if (tilt > 0.5) ctx.fillRect(x, g.base + 2 + g.unit * 1.6, 1, 1);
      }

      for (let c = 0; c < HISTORY; c++) {
        const ms = hist[c];
        const x = g.left + c * col;
        if (ms === null) {
          ctx.fillStyle = ink.red;
          const s = Math.max(1.5, cellW * 0.42);
          ctx.fillRect(x + (cellW - s) / 2, g.base - s - 1, s, s);
          if (tilt > 0.5) ctx.fillRect(x + (cellW - s) / 2, g.base + g.unit * 0.9, s, s);
          continue;
        }
        const b = bandOf(ms);
        const stack = b + 1;
        for (let s = 0; s < stack; s++) {
          ctx.fillStyle = s === stack - 1 ? ink.top[b] : ink.band[b];
          ctx.fillRect(x, g.base - (s + 1) * g.unit, cellW, cellH);
        }
      }

      if (tilt > 0.5) {
        ctx.save();
        ctx.globalAlpha = (tilt - 0.5) * 2;
        ctx.fillStyle = ink.ink;
        ctx.font = Math.round(geo.narrow ? 11 : Math.max(11, geo.w * 0.0198)) + "px Inconsolata, ui-monospace, monospace";
        ctx.textAlign = "right";
        ctx.textBaseline = "alphabetic";
        ctx.fillText(run.hosts[r].name, g.left - geo.w * 0.008, g.base - g.unit * 2.2);
        ctx.restore();
      }
    }
  };

  /* ------------------------------------------------ the landscape clip, in glyphs */

  const RAMP = [" ", " ", "·", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];

  const playIntro = (onDone) => {
    const src = stage && stage.getAttribute("data-clip");
    if (!src || !ctx) return onDone();
    const video = doc.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = src;
    const sample = doc.createElement("canvas");
    const sctx = sample.getContext("2d", { willReadFrequently: true });
    let finished = false;
    let raf = 0;
    let lums = null;
    let sorted = null;

    const grid = () => {
      const cw = geo.narrow ? 6 : Math.max(6, geo.w * 0.0072);
      const ch = cw * 1.62;
      const zoneH = geo.tableTop - 4;
      return { cw, ch, cols: Math.ceil(geo.w / cw), rows: Math.ceil(zoneH / ch) };
    };

    // The terrain's footprint: the clip is drawn only where the ridges will stand.
    const footprint = () => {
      const back = rowGeometry(0);
      const front = rowGeometry(ROWS.length - 1);
      const top = Math.max(0, back.base - back.unit * 9);
      const path = new Path2D();
      path.moveTo(back.left, top);
      path.lineTo(geo.w, top);
      path.lineTo(geo.w, front.base + 6);
      path.lineTo(front.left, front.base + 6);
      path.closePath();
      return { path, top, bottom: front.base + 6 };
    };

    const frame = () => {
      if (finished) return;
      const { cw, ch } = grid();
      const fp = footprint();
      const x0 = Math.floor(rowGeometry(ROWS.length - 1).left / cw);
      const cols = Math.ceil(geo.w / cw) - x0;
      const rows = Math.ceil((fp.bottom - fp.top) / ch);
      if (sample.width !== cols || sample.height !== rows) {
        sample.width = cols;
        sample.height = rows;
      }
      // Cover-crop the 16:9 clip into the footprint's own box.
      const vw = video.videoWidth || 16;
      const vh = video.videoHeight || 9;
      const boxAspect = (cols * cw) / (rows * ch);
      let sw = vw;
      let sh = vw / boxAspect;
      if (sh > vh) {
        sh = vh;
        sw = vh * boxAspect;
      }
      sctx.drawImage(video, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, cols, rows);
      const px = sctx.getImageData(0, 0, cols, rows).data;
      // Equalize each frame so ridgelines and mist read as form, not one dark slab.
      const n = cols * rows;
      if (!lums || lums.length !== n) {
        lums = new Float32Array(n);
        sorted = new Float32Array(n);
      }
      for (let i = 0; i < n; i++) {
        const o = i * 4;
        lums[i] = (0.2126 * px[o] + 0.7152 * px[o + 1] + 0.0722 * px[o + 2]) / 255;
      }
      sorted.set(lums);
      sorted.sort();
      const cuts = [];
      for (let q = 1; q < 10; q++) cuts.push(sorted[Math.floor((n - 1) * (1 - q / 10))]);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, geo.w, geo.h);
      ctx.save();
      ctx.clip(fp.path);
      ctx.fillStyle = ink.ink;
      const barW = cw * 0.72;
      for (let y = 0; y < rows; y++) {
        const yb = fp.top + (y + 1) * ch;
        for (let x = 0; x < cols; x++) {
          const i = y * cols + x;
          const lum = lums[i];
          let level = 0;
          while (level < 9 && lum <= cuts[level]) level++;
          level -= 1;
          // A step from light to dark going down is a ridgeline: print it full.
          if (y > 1 && lum < cuts[3] && lums[i - cols] - lum > 0.045) level = 9;
          if (level <= 0) continue;
          const cx = (x0 + x) * cw;
          if (level === 1) {
            ctx.fillRect(cx + cw * 0.3, yb - ch * 0.16, cw * 0.2, ch * 0.1);
            continue;
          }
          const h = (level - 1) / 8 * ch * 0.9;
          ctx.fillRect(cx, yb - h, barW, h);
        }
      }
      ctx.restore();
      raf = requestAnimationFrame(frame);
    };

    const dissolve = () => {
      finished = true;
      cancelAnimationFrame(raf);
      // Snapshot the last glyph frame, then let the data show through it,
      // cell by cell, the way a terminal repaints.
      const snap = doc.createElement("canvas");
      snap.width = canvas.width;
      snap.height = canvas.height;
      snap.getContext("2d").drawImage(canvas, 0, 0);
      const { cw, ch, cols, rows } = grid();
      const top = footprint().top;
      const order = [];
      for (let i = 0; i < cols * rows; i++) order.push(i);
      let s = 7;
      for (let i = order.length - 1; i > 0; i--) {
        s = (Math.imul(s, 1103515245) + 12345) >>> 0;
        const j = s % (i + 1);
        [order[i], order[j]] = [order[j], order[i]];
      }
      const hidden = new Uint8Array(cols * rows);
      const t0 = performance.now();
      const DURATION = 1300;
      const step = (now) => {
        const k = Math.min(1, (now - t0) / DURATION);
        const upto = Math.floor(order.length * (1 - Math.pow(1 - k, 2)));
        for (let i = 0; i < upto; i++) hidden[order[i]] = 1;
        drawTerrain(shownTick);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        for (let i = 0; i < hidden.length; i++) {
          if (hidden[i]) continue;
          const x = (i % cols) * cw * dpr;
          const y = (top + Math.floor(i / cols) * ch) * dpr;
          const w = cw * dpr + 1;
          const h = ch * dpr + 1;
          ctx.clearRect(x, y, w, h);
          ctx.drawImage(snap, x, y, w, h, x, y, w, h);
        }
        if (k < 1) requestAnimationFrame(step);
        else onDone();
      };
      requestAnimationFrame(step);
    };

    const bail = () => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf);
      onDone();
    };

    video.addEventListener("playing", () => {
      stage.classList.add("is-clip");
      raf = requestAnimationFrame(frame);
    }, { once: true });
    video.addEventListener("ended", dissolve, { once: true });
    video.addEventListener("error", bail, { once: true });
    const started = video.play();
    if (started && started.catch) started.catch(bail);
    setTimeout(() => { if (!finished && video.paused) bail(); }, 2500);
  };

  /* ------------------------------------------------ clock and visibility */

  let timer = null;
  let stageVisible = !("IntersectionObserver" in window);
  let documentVisible = !doc.hidden;
  let live = false;

  const tick = () => {
    shownTick = (shownTick + 1) % PERIOD;
    renderTable(shownTick);
    drawTerrain(shownTick);
  };

  const updateTimer = () => {
    const shouldRun = live && !reduced && stageVisible && documentVisible;
    if (shouldRun && !timer) timer = setInterval(tick, 1000);
    if (!shouldRun && timer) {
      clearInterval(timer);
      timer = null;
    }
  };

  const goLive = () => {
    stage.classList.remove("is-clip");
    stage.classList.add("is-live");
    drawTerrain(shownTick);
    live = true;
    updateTimer();
  };

  if (stage && ctx) {
    renderTable(shownTick);
    const ready = () => {
      measure();
      stage.classList.add("is-drawn");
      if (reduced) {
        drawTerrain(shownTick);
      } else {
        playIntro(goLive);
      }
    };
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(ready, ready);
    else ready();

    let resizeFrame = 0;
    window.addEventListener("resize", () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        measure();
        if (live || reduced) drawTerrain(shownTick);
      });
    });

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

    // Head-on: tilt the terrain flat onto the table's history column.
    const toggle = doc.querySelector("[data-tilt]");
    if (toggle) {
      toggle.hidden = false;
      let anim = 0;
      toggle.addEventListener("click", () => {
        const flat = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", String(flat));
        toggle.textContent = flat ? "view terrain" : "view head-on";
        stage.classList.toggle("is-flat", flat);
        measure();
        const from = tilt;
        const to = flat ? 0 : 1;
        if (reduced) {
          tilt = to;
          drawTerrain(shownTick);
          return;
        }
        cancelAnimationFrame(anim);
        const t0 = performance.now();
        const step = (now) => {
          const k = Math.min(1, (now - t0) / 700);
          tilt = lerp(from, to, 1 - Math.pow(1 - k, 3));
          drawTerrain(shownTick);
          if (k < 1) anim = requestAnimationFrame(step);
        };
        anim = requestAnimationFrame(step);
      });
    }
  }

  /* ------------------------------------------------ --no-tui line stream */

  const stream = doc.querySelector("[data-stream]");
  if (stream && !reduced) {
    let n = 0;
    const lat = [14.08, 13.91, 14.22, 13.87, 14.4, 14.02];
    setInterval(() => {
      if (doc.hidden) return;
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
    // The slider is logarithmic: equal travel per band of the fixed scale.
    const msAt = (v) => Math.round(Math.pow(10, (v / 1000) * 3.6 - 0.6) * 100) / 100;
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
