/* pinghue.com
   The hero is the real TUI: same columns, states, glyph scale and keys as
   `pinghue` in a terminal, fed by one deterministic simulated run so every
   visitor sees the same night. The rules below mirror the Python sources:
   BAR_BUCKETS (src/pinghue/history.py), SLOW_LATENCY_MS (app.py), and
   classify_samples with the CLI defaults (models.py, cli.py). */

(() => {
  "use strict";

  /* ------------------------------------------------ the fixed scale */

  const BUCKETS = [[1, "▁"], [3, "▂"], [10, "▃"], [30, "▄"], [100, "▅"], [300, "▆"], [1000, "▇"]];
  const SLOW_MS = 100;
  const JITTER_THRESHOLD = 50;
  const FAIL_THRESHOLD = 3;

  // 1..8: the eighth-block level a latency prints at; 8 is "█" (> 1000 ms).
  const levelOf = (ms) => {
    for (let i = 0; i < BUCKETS.length; i++) if (ms <= BUCKETS[i][0]) return i + 1;
    return 8;
  };
  const glyphFor = (ms) => (ms === null ? "·" : levelOf(ms) === 8 ? "█" : BUCKETS[levelOf(ms) - 1][1]);
  const toneFor = (ms) => (ms === null ? "fail" : ms > SLOW_MS ? "slow" : "ok");

  /* ------------------------------------------------ the simulated run */

  // One hour of probes at the CLI's 1.0 s interval. The page opens at START
  // (02:31:04): db-primary has been slow for a while and has dropped probes,
  // backup-nas has been down since 02:30:38. Later in the hour backup-nas
  // answers again and stays intermittent, api-gw drops a probe, db-primary
  // settles.
  const RUN_SECONDS = 3600;
  const START = 176;
  const CLOCK_ORIGIN = 2 * 3600 + 28 * 60 + 8; // 02:28:08, so START reads 02:31:04
  const HISTORY = 11;

  const HOSTS = [
    ["edge-router-1", "192.168.1.1"],
    ["core-sw-1", "10.0.0.10"],
    ["db-primary", "10.0.1.20"],
    ["api-gw", "10.0.2.30"],
    ["backup-nas", "192.168.50.10"],
    ["dns-resolver", "8.8.8.8"],
  ];

  const buildRun = () => {
    let seed = 20261006;
    const rand = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    const wobble = (base, spread) => base + (rand() - 0.5) * 2 * spread;
    const ridge = (t, at, width, peak) => peak * Math.exp(-(((t - at) / width) ** 2));
    const hump = (t, period, phase) => Math.max(0, Math.sin(t / period + phase)) ** 2;
    const dbLost = new Set([90, 112, 140, 160, 1720]);
    const nasDown = (t) => (t >= 150 && t < 410) || (t >= 2300 && t < 2306);

    const latencyAt = [
      (t) => wobble(13.2, 1.6) + 4 * hump(t, 7.3, 0.2),
      (t) => wobble(11.6, 1.1) + 1.6 * hump(t, 5.1, 1.1),
      (t) => {
        if (dbLost.has(t)) return null;
        const climb = ridge(t, 173, 11, 300) + ridge(t, 179, 5, 160) + ridge(t, 96, 6, 140)
          + ridge(t, 640, 14, 210) + ridge(t, 1730, 8, 320) + ridge(t, 2900, 20, 120);
        return Math.max(14, wobble(24, 4) + 9 * hump(t, 6.2, 2) + climb * (0.85 + rand() * 0.3));
      },
      (t) => (t === 1250 ? null : wobble(37.5, 3.5) + 6 * hump(t, 8.4, 0.7)),
      (t) => (nasDown(t) ? null : wobble(8.6, 1.6) + 3 * hump(t, 5.7, 2.4)),
      (t) => Math.max(1, wobble(12.4, 1.8) + 3 * hump(t, 6.6, 0.9)),
    ];

    const samples = HOSTS.map((_, i) => {
      const row = new Array(RUN_SECONDS);
      for (let t = 0; t < RUN_SECONDS; t++) {
        const v = latencyAt[i](t);
        row[t] = v === null ? null : Math.round(v * 100) / 100;
      }
      return row;
    });

    // Whole-run statistics the way SampleWindow keeps them, folded tick by tick.
    const fold = (i, from, to) => {
      const s = {
        sent: 0, received: 0, mean: 0, min: null, max: null, last: null,
        prev: null, jitter: 0, jitterMax: 0, fails: 0, latestFailed: false,
      };
      for (let t = from; t <= to; t++) {
        const ms = samples[i][t];
        s.sent += 1;
        if (ms === null) {
          s.fails += 1;
          s.latestFailed = true;
          continue;
        }
        s.fails = 0;
        s.latestFailed = false;
        s.received += 1;
        s.mean += (ms - s.mean) / s.received;
        if (s.prev !== null) {
          s.jitter += (Math.abs(ms - s.prev) - s.jitter) / 16;
          s.jitterMax = Math.max(s.jitterMax, s.jitter);
        }
        s.prev = ms;
        s.last = ms;
        s.min = s.min === null ? ms : Math.min(s.min, ms);
        s.max = s.max === null ? ms : Math.max(s.max, ms);
      }
      return s;
    };

    const stateOf = (s) => {
      if (s.sent === 0 || s.fails >= FAIL_THRESHOLD || s.received === 0) return "down";
      if (s.received < s.sent) return "intermittent";
      if (s.received >= 2 && s.jitterMax > JITTER_THRESHOLD) return "intermittent";
      return "healthy";
    };

    // The row a host prints at tick t, counting from `since` (a reset).
    const rowAt = (i, t, since = 0) => {
      const s = fold(i, since, t);
      const tail = [];
      for (let k = Math.max(since, t - HISTORY + 1); k <= t; k++) tail.push(samples[i][k]);
      // ui.py: "last" is the newest sample that has a latency, red when the
      // newest probe failed and amber past SLOW_LATENCY_MS; avg and max go
      // amber past it, loss goes red, jitter goes amber past 50 ms.
      const avg = s.received ? s.mean : null;
      const jitter = s.received >= 2 ? s.jitter : null;
      return {
        host: HOSTS[i][0],
        address: HOSTS[i][1],
        state: stateOf(s),
        tones: {
          last: s.latestFailed ? "red" : s.last !== null && s.last > SLOW_MS ? "amber" : "",
          avg: avg !== null && avg > SLOW_MS ? "amber" : "",
          max: s.max !== null && s.max > SLOW_MS ? "amber" : "",
          jitter: jitter !== null && jitter > JITTER_THRESHOLD ? "amber" : "",
          loss: s.received < s.sent ? "red" : "",
        },
        last: s.last,
        min: s.min,
        avg,
        max: s.max,
        jitter,
        loss: ((s.sent - s.received) / s.sent) * 100,
        mode: "icmp",
        history: tail,
      };
    };

    return { samples, rowAt, fold, stateOf };
  };

  // What a screen reader hears for one history cell.
  const historyLabel = (history) => {
    const n = history.length;
    const lost = history.filter((ms) => ms === null).length;
    const slow = history.filter((ms) => ms !== null && ms > SLOW_MS).length;
    const parts = [];
    if (n - lost - slow) parts.push(`${n - lost - slow} ok`);
    if (slow) parts.push(`${slow} slow`);
    if (lost) parts.push(`${lost} lost`);
    return `last ${n} probes: ${parts.join(", ")}`;
  };

  const fmt = (n) => (n === null ? "-" : n.toFixed(2));
  const fmtLoss = (n) => n.toFixed(2) + "%";
  const clockAt = (t) => {
    const s = (CLOCK_ORIGIN + t) % 86400;
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  };

  // The pure core is importable (tests, the static-row generator) without a DOM.
  if (typeof module === "object" && module.exports) {
    module.exports = {
      BUCKETS, SLOW_MS, JITTER_THRESHOLD, FAIL_THRESHOLD, HOSTS, START, HISTORY, RUN_SECONDS,
      buildRun, levelOf, glyphFor, toneFor, historyLabel, fmt, fmtLoss, clockAt,
    };
    return;
  }

  const doc = document;
  doc.documentElement.classList.add("js");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------ copy buttons */

  const copyStatus = doc.querySelector("[data-copy-status]");
  let copyAnnouncementVersion = 0;

  doc.querySelectorAll(".copy-btn").forEach((btn) => {
    const originalLabel = btn.getAttribute("aria-label") || "Copy command";
    let operationVersion = 0;
    let resetTimer = null;

    const resetButton = () => {
      btn.classList.remove("copied", "failed");
      btn.setAttribute("aria-label", originalLabel);
    };

    const report = (state, announcement, announcementVersion, currentOperation) => {
      if (currentOperation !== operationVersion) return;
      clearTimeout(resetTimer);
      btn.classList.toggle("copied", state === "copied");
      btn.classList.toggle("failed", state === "failed");
      btn.setAttribute("aria-label", state === "copied" ? "Command copied" : "Copy failed");
      if (copyStatus && announcementVersion === copyAnnouncementVersion) copyStatus.textContent = announcement;
      resetTimer = setTimeout(() => {
        if (currentOperation !== operationVersion) return;
        resetButton();
        if (copyStatus && announcementVersion === copyAnnouncementVersion) copyStatus.textContent = "";
      }, 1800);
    };

    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-copy") || "";
      const currentOperation = ++operationVersion;
      const announcementVersion = ++copyAnnouncementVersion;
      clearTimeout(resetTimer);
      resetButton();
      if (copyStatus) copyStatus.textContent = "";
      const done = () => report("copied", "Install command copied.", announcementVersion, currentOperation);
      const failed = () => report("failed", "Copy failed. Select the command and copy it manually.", announcementVersion, currentOperation);
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, failed);
      else failed();
    });
  });

  /* ------------------------------------------------ scrolling windows */

  // A terminal window that has to scroll sideways becomes a named, focusable
  // region, so keyboard users can reach the columns past the edge.
  const scrollers = Array.from(doc.querySelectorAll("[data-scroll]"));
  const syncScrollers = () => {
    scrollers.forEach((el) => {
      const scrolls = el.scrollWidth > el.clientWidth + 1;
      if (scrolls) {
        el.setAttribute("role", "region");
        el.setAttribute("aria-label", el.getAttribute("data-scroll"));
        el.tabIndex = 0;
      } else {
        el.removeAttribute("role");
        el.removeAttribute("aria-label");
        el.removeAttribute("tabindex");
      }
    });
  };
  if (scrollers.length) {
    let pending = 0;
    window.addEventListener("resize", () => {
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(syncScrollers);
    });
    syncScrollers();
  }

  /* ------------------------------------------------ mode tabs */

  doc.querySelectorAll("[data-tabs]").forEach((list) => {
    const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
    const panes = tabs.map((tab) => doc.getElementById(tab.getAttribute("aria-controls")));
    const select = (index, focus) => {
      tabs.forEach((tab, k) => {
        const on = k === index;
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
        if (panes[k]) panes[k].hidden = !on;
      });
      syncScrollers();
      if (focus) tabs[index].focus();
    };
    tabs.forEach((tab, k) => {
      tab.addEventListener("click", () => select(k, false));
      tab.addEventListener("keydown", (e) => {
        const n = tabs.length;
        const next = { ArrowRight: (k + 1) % n, ArrowLeft: (k - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        select(next, true);
      });
    });
    select(0, false);
  });

  /* ------------------------------------------------ the scale, by hand */

  const lab = doc.querySelector("[data-lab]");
  if (lab) {
    const slider = lab.querySelector("[data-lab-slider]");
    const ms = lab.querySelector("[data-lab-ms]");
    const band = lab.querySelector("[data-lab-band]");
    const bar = lab.querySelector(".lab-bar i");
    // log scale, 0.3 ms at the left end to 3 s at the right
    const msAt = (v) => 0.3 * Math.pow(10000, v / 1000);
    const label = (x) => (x < 10 ? x.toFixed(1) : String(Math.round(x)));
    const update = () => {
      const v = Number(slider.value);
      const x = msAt(v);
      const level = levelOf(x);
      const color = toneFor(x) === "slow" ? "amber" : "green";
      bar.className = `${toneFor(x)} h${level}`;
      ms.textContent = `${label(x)} ms`;
      band.textContent = `glyph ${level} of 8, ${color}`;
      slider.style.setProperty("--fill", `${v / 10}%`);
      slider.setAttribute("aria-valuetext", `${label(x)} milliseconds: glyph ${level} of 8, ${color}`);
    };
    slider.addEventListener("input", update);
    update();
  }

  /* ------------------------------------------------ the live table */

  // Each host keeps its own probe counter, so "probe now" (b) can give one
  // host an extra probe the way the real key wakes that host's probe loop.
  const run = buildRun();
  const term = doc.querySelector("[data-term]");
  const tbody = doc.querySelector("[data-rows]");
  const clock = doc.querySelector("[data-clock]");
  const note = doc.querySelector("[data-term-note]");
  const pauseBtn = doc.querySelector("[data-pause]");
  const n = HOSTS.length;
  let elapsed = 0;
  let extra = HOSTS.map(() => 0);
  let since = HOSTS.map(() => 0);
  let showAddress = true;
  let cursor = 0;
  let quit = false;
  let paused = reduced;
  let timer = 0;

  const tickOf = (i) => START + elapsed + extra[i];

  const paintBars = (cell, history) => {
    let bars = cell.querySelector(".bars");
    if (!bars) {
      bars = doc.createElement("span");
      bars.className = "bars";
      bars.setAttribute("role", "img");
      cell.appendChild(bars);
    }
    while (bars.children.length < HISTORY) bars.appendChild(doc.createElement("i"));
    const pad = HISTORY - history.length;
    for (let k = 0; k < HISTORY; k++) {
      const ms = k < pad ? undefined : history[k - pad];
      bars.children[k].className = ms === undefined ? "" : ms === null ? "fail" : `${toneFor(ms)} h${levelOf(ms)}`;
    }
    bars.setAttribute("aria-label", history.length ? historyLabel(history) : "no probes yet");
  };

  const paint = () => {
    if (!tbody) return;
    Array.from(tbody.rows).forEach((tr, i) => {
      const cells = tr.cells;
      const tk = tickOf(i);
      cells[1].textContent = showAddress ? HOSTS[i][1] : "";
      tr.classList.toggle("is-cursor", term && term.classList.contains("is-engaged") && i === cursor);
      if (since[i] > tk) {
        // app.py: a reset target shows "resolving" until its next probe lands
        cells[2].textContent = "resolving";
        cells[2].className = "s-resolving";
        for (let k = 3; k <= 7; k++) {
          cells[k].textContent = "-";
          cells[k].className = "";
        }
        cells[8].textContent = fmtLoss(0);
        cells[8].className = "";
        paintBars(cells[10], []);
        return;
      }
      const row = run.rowAt(i, tk, since[i]);
      cells[2].textContent = row.state;
      cells[2].className = `s-${row.state}`;
      const nums = [["last", row.last], ["min", row.min], ["avg", row.avg], ["max", row.max], ["jitter", row.jitter]];
      nums.forEach(([key, value], k) => {
        cells[3 + k].textContent = fmt(value);
        cells[3 + k].className = row.tones[key] ? `t-${row.tones[key]}` : "";
      });
      cells[8].textContent = fmtLoss(row.loss);
      cells[8].className = row.tones.loss ? `t-${row.tones.loss}` : "";
      paintBars(cells[10], row.history);
    });
    if (clock) clock.textContent = clockAt(elapsed + START);
  };

  // When any host runs past the simulated hour, the same night starts again.
  const wrap = () => {
    if (!HOSTS.some((_, i) => tickOf(i) >= RUN_SECONDS)) return;
    elapsed = 0;
    extra = HOSTS.map(() => 0);
    since = HOSTS.map(() => 0);
  };

  const tick = () => {
    elapsed += 1;
    wrap();
    paint();
  };

  /* ------------------------------------------------ backdrop footage */

  // A data-center aisle at night (Higgsfield clip, see CONTRIBUTING.md). It
  // loads only when the hero is on screen and never under reduced motion
  // unless the visitor presses play; the poster underneath is its first
  // frame, so the fade-in does not jump.
  const hero = doc.querySelector("[data-hero]");
  const video = hero && hero.querySelector("video");

  const saveData = (navigator.connection && navigator.connection.saveData)
    || window.matchMedia("(prefers-reduced-data: reduce)").matches;

  const playVideo = () => {
    if (!video || saveData) return;
    if (!video.getAttribute("src")) {
      video.src = video.getAttribute("data-src") || "";
      video.addEventListener("playing", () => hero.classList.add("is-playing"), { once: true });
    }
    const p = video.play();
    if (p && p.catch) p.catch(() => {});
  };
  const pauseVideo = () => {
    if (video && !video.paused) video.pause();
  };

  /* ------------------------------------------------ running, paused, quit */

  let heroVisible = !("IntersectionObserver" in window);
  const sync = () => {
    const running = heroVisible && !doc.hidden && !paused && !quit;
    if (running && !timer) timer = setInterval(tick, 1000);
    if (!running && timer) {
      clearInterval(timer);
      timer = 0;
    }
    if (heroVisible && !doc.hidden && !paused) playVideo();
    else pauseVideo();
    if (pauseBtn) {
      pauseBtn.classList.toggle("is-paused", paused);
      pauseBtn.setAttribute("aria-label", paused ? "Play the live demo" : "Pause the live demo");
    }
  };

  if (pauseBtn) {
    pauseBtn.hidden = false;
    pauseBtn.addEventListener("click", () => {
      paused = !paused;
      sync();
    });
  }

  /* ------------------------------------------------ the keys */

  const keys = doc.querySelectorAll("[data-key]");
  const flash = (key) => {
    keys.forEach((btn) => {
      if (btn.getAttribute("data-key") !== key) return;
      btn.classList.add("is-active");
      setTimeout(() => btn.classList.remove("is-active"), 180);
    });
  };

  const press = (key) => {
    flash(key);
    if (key === "q") {
      quit = !quit;
      if (quit) {
        if (note) note.textContent = "pinghue exited: user_quit. Press q to run it again.";
      } else {
        // a new run: every host starts from an empty window
        since = HOSTS.map((_, i) => tickOf(i) + 1);
        if (note) note.textContent = "";
      }
      sync();
      paint();
      return;
    }
    if (quit) return;
    if (term) term.classList.add("is-engaged");
    if (key === "a") showAddress = !showAddress;
    else if (key === "r") since[cursor] = tickOf(cursor) + 1;
    else if (key === "R") since = HOSTS.map((_, i) => tickOf(i) + 1);
    else if (key === "b") extra[cursor] += 1;
    else if (key === "B") extra = extra.map((x) => x + 1);
    else if (key === "ArrowDown") cursor = (cursor + 1) % n;
    else if (key === "ArrowUp") cursor = (cursor - 1 + n) % n;
    wrap();
    paint();
  };

  keys.forEach((btn) => {
    btn.disabled = false;
    btn.addEventListener("click", () => press(btn.getAttribute("data-key")));
  });

  if (term) {
    term.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target !== term && e.target.closest("button")) return;
      if (!["q", "a", "r", "R", "b", "B", "ArrowDown", "ArrowUp"].includes(e.key)) return;
      e.preventDefault();
      press(e.key);
    });
    term.addEventListener("focus", () => {
      term.classList.add("is-engaged");
      paint();
    });
    if (tbody) {
      tbody.addEventListener("click", (e) => {
        const tr = e.target.closest("tr");
        if (!tr) return;
        cursor = Array.from(tbody.rows).indexOf(tr);
        term.classList.add("is-engaged");
        paint();
      });
    }
  }

  if (hero && "IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      heroVisible = entries.some((e) => e.isIntersecting);
      sync();
    }, { threshold: 0.1 }).observe(hero);
  }
  doc.addEventListener("visibilitychange", sync);
  sync();
})();
