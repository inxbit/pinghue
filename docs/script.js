/* pinghue.com — The Window.
   One scripted maintenance window, 02:00 to 02:47. The hero terminal replays
   the night as you scroll: the whole probe timeline is precomputed with the
   real fixed latency scale and host-state rules from the README (whole-run
   states, fail-threshold 3, glyphs ▁▂▃▄▅▆▇█), then rendered at whatever tick
   the story has reached. Everything degrades: no JavaScript shows the final
   frame and the full story text; reduced motion renders the closed window. */

(() => {
  "use strict";

  document.documentElement.classList.add("js");

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

  /* ------------------------------------------------ mobile navigation */

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
        returnFocus = navToggle;
        navClose.focus();
      } else if (returnFocus && typeof returnFocus.focus === "function") {
        const target = returnFocus;
        returnFocus = null;
        target.focus();
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
      mobileMenu.addEventListener("change", () => setMenu(false));
    } else if (typeof mobileMenu.addListener === "function") {
      mobileMenu.addListener(() => setMenu(false));
    }
    syncMenu();
  }

  /* ------------------------------------------------ reveal orchestration */

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

  /* ------------------------------------------------ the fixed scale */

  const SLOW_MS = 100;

  const glyphFor = (ms) => {
    if (ms <= 1) return "▁";
    if (ms <= 3) return "▂";
    if (ms <= 10) return "▃";
    if (ms <= 30) return "▄";
    if (ms <= 100) return "▅";
    if (ms <= 300) return "▆";
    if (ms <= 1000) return "▇";
    return "█";
  };

  const bandFor = (ms) => {
    if (ms <= 1) return "within the 1ms band";
    if (ms <= 3) return "within the 3ms band";
    if (ms <= 10) return "within the 10ms band";
    if (ms <= 30) return "within the 30ms band";
    if (ms <= 100) return "within the 100ms band";
    if (ms <= 300) return "within the 300ms band";
    if (ms <= 1000) return "within the 1000ms band";
    return "beyond the 1000ms band";
  };

  const bandIndex = (ms) => {
    if (ms <= 1) return 0;
    if (ms <= 3) return 1;
    if (ms <= 10) return 2;
    if (ms <= 30) return 3;
    if (ms <= 100) return 4;
    if (ms <= 300) return 5;
    if (ms <= 1000) return 6;
    return 7;
  };

  /* ------------------------------------------------ scale laboratory */

  const lab = document.querySelector("[data-lab]");
  if (lab) {
    const slider = lab.querySelector("[data-lab-slider]");
    const labGlyph = lab.querySelector("[data-lab-glyph]");
    const labMs = lab.querySelector("[data-lab-ms]");
    const labBand = lab.querySelector("[data-lab-band]");
    const labStatus = lab.querySelector("[data-lab-status]");
    const labTrail = lab.querySelector("[data-lab-trail]");
    const steps = [...document.querySelectorAll(".signal-step")];
    const TRAIL_LENGTH = 26;

    if (slider && labGlyph && labMs && labBand) {
      const applyLatency = () => {
        const ms = Number(slider.value);
        const index = bandIndex(ms);
        labGlyph.textContent = glyphFor(ms);
        labGlyph.classList.toggle("green", ms <= SLOW_MS);
        labGlyph.classList.toggle("amber", ms > SLOW_MS);
        labMs.textContent = ms > 1000 ? ">1000ms" : ms + "ms";
        labBand.textContent = bandFor(ms);
        if (labStatus) {
          labStatus.textContent = ms + " milliseconds maps to glyph "
            + (index + 1) + " of 8, " + (ms <= SLOW_MS ? "green" : "amber") + ".";
        }
        steps.forEach((step, i) => step.classList.toggle("is-hit", i === index));
        // Dragging writes a history bar, the same way a run would.
        if (labTrail) {
          const mark = document.createElement("span");
          mark.className = ms > SLOW_MS ? "g-slow" : "g-ok";
          mark.textContent = glyphFor(ms);
          labTrail.appendChild(mark);
          while (labTrail.childNodes.length > TRAIL_LENGTH) {
            labTrail.removeChild(labTrail.firstChild);
          }
        }
      };
      slider.addEventListener("input", applyLatency);
      applyLatency();
    }
  }

  /* ------------------------------------------------ automation line stream */

  const stream = document.querySelector("[data-stream]");
  if (stream && !reduced && "IntersectionObserver" in window) {
    const streamLines = [
      "2026-05-14T18:32:11.420000+00:00 example.com ok latency=14.08ms",
      "2026-05-14T18:32:12.423000+00:00 example.com ok latency=13.77ms",
      "2026-05-14T18:32:13.425000+00:00 example.com ok latency=14.92ms",
    ];
    let streamIndex = 0;
    let streamTimer = null;
    const streamObserver = new IntersectionObserver((entries) => {
      const visible = entries.some((entry) => entry.isIntersecting);
      if (visible && streamTimer === null) {
        streamTimer = setInterval(() => {
          streamIndex = (streamIndex + 1) % streamLines.length;
          const shownLines = [];
          for (let i = 2; i >= 0; i -= 1) {
            shownLines.push(streamLines[(streamIndex + streamLines.length - i) % streamLines.length]);
          }
          stream.textContent = shownLines.join("\n");
        }, 1700);
      }
      if (!visible && streamTimer !== null) {
        clearInterval(streamTimer);
        streamTimer = null;
      }
    }, { threshold: 0.3 });
    streamObserver.observe(stream);
  }

  /* ------------------------------------------------ terminal simulation */

  const root = document.querySelector("[data-terminal]");
  if (!root) return;

  const tbody = root.querySelector("[data-rows]");
  const clock = root.querySelector("[data-clock]");
  if (!tbody) return;

  const HISTORY = 26;
  const TICK_MS = 900;
  const SEEK_MS = 90;
  const FAIL_THRESHOLD = 3;
  const MAX_TICK = 34;
  const SEC_PER_TICK = 84.6;

  // Deterministic LCG so every visitor sees the same scripted window.
  let seed = 20260703;
  const rand = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };

  // The story: api-gw blips once, db-primary drags mid-window,
  // backup-nas goes down and stays down. Everything else is boring — as it should be.
  const hosts = [
    { name: "edge-router-1", base: 9, wobble: 3 },
    { name: "edge-router-2", base: 12, wobble: 4 },
    { name: "core-sw-1", base: 2, wobble: 1 },
    { name: "db-primary", base: 18, wobble: 5, spike: { from: 13, to: 18, ms: 340 } },
    { name: "cdn-edge", base: 24, wobble: 6 },
    { name: "api-gw", base: 31, wobble: 5, lossAt: [9] },
    { name: "backup-nas", base: 6, wobble: 2, downAt: 22 },
    { name: "dns-resolver", base: 1, wobble: 1 },
  ];

  hosts.forEach((h) => {
    h.hist = [];
    h.sent = 0;
    h.recv = 0;
    h.sum = 0;
    h.last = null;
    h.prev = null;
    h.jitter = 0;
    h.peakJitter = 0;
    h.consecFail = 0;
    h.everLoss = false;
    h.down = false;
  });

  const probe = (h, tick) => {
    h.sent += 1;
    const lost =
      (h.downAt !== undefined && tick >= h.downAt) ||
      (h.lossAt !== undefined && h.lossAt.includes(tick));
    if (lost) {
      h.consecFail += 1;
      h.everLoss = true;
      if (h.consecFail >= FAIL_THRESHOLD && h.downAt !== undefined) h.down = true;
      h.last = null;
      h.hist.push({ g: "·", c: "g-fail" });
    } else {
      h.consecFail = 0;
      let ms = h.base + (rand() * 2 - 1) * h.wobble;
      if (h.spike && tick >= h.spike.from && tick <= h.spike.to) {
        ms = h.spike.ms + rand() * 120;
      }
      ms = Math.max(0.4, ms);
      if (h.prev !== null) {
        h.jitter += (Math.abs(ms - h.prev) - h.jitter) / 16;
        h.peakJitter = Math.max(h.peakJitter, h.jitter);
      }
      h.prev = ms;
      h.last = ms;
      h.recv += 1;
      h.sum += ms;
      h.hist.push({ g: glyphFor(ms), c: ms > SLOW_MS ? "g-slow" : "g-ok" });
    }
    if (h.hist.length > HISTORY) h.hist.shift();
  };

  const stateOf = (h) => {
    if (h.down) return { label: "down", cls: "s-down" };
    if (h.everLoss || h.peakJitter > 50) {
      return { label: "intermittent", cls: "s-intermittent" };
    }
    return { label: "healthy", cls: "s-healthy" };
  };

  // Precompute the whole window once: snapshots[t] is the table at tick t,
  // so scrolling can seek forward and backward through the night.
  const snapshots = [hosts.map((h) => ({
    name: h.name, last: null, avg: null, loss: 0, jitter: 0,
    state: stateOf(h), hist: [],
  }))];

  for (let t = 1; t <= MAX_TICK; t += 1) {
    hosts.forEach((h) => probe(h, t));
    snapshots.push(hosts.map((h) => ({
      name: h.name,
      last: h.last,
      avg: h.recv ? h.sum / h.recv : null,
      loss: h.sent ? ((h.sent - h.recv) / h.sent) * 100 : 0,
      jitter: h.recv > 1 ? h.jitter : 0,
      state: stateOf(h),
      hist: h.hist.slice(),
    })));
  }

  const fmt = (n, unit) => (n === null ? "-" : n.toFixed(1) + unit);

  const render = (tick) => {
    const frag = document.createDocumentFragment();
    snapshots[tick].forEach((h) => {
      const tr = document.createElement("tr");
      const cells = [
        ["t-host", h.name],
        ["t-num", fmt(h.last, "ms")],
        ["t-num", fmt(h.avg, "ms")],
        [h.loss > 0 ? "t-num g-fail" : "t-num", h.loss.toFixed(0) + "%"],
        ["t-num", fmt(h.jitter, "ms")],
        [h.state.cls, h.state.label],
      ];
      cells.forEach(([cls, text]) => {
        const td = document.createElement("td");
        td.className = cls;
        td.textContent = text;
        tr.appendChild(td);
      });

      const hist = document.createElement("td");
      hist.className = "t-hist";
      h.hist.forEach((p) => {
        const s = document.createElement("span");
        s.className = p.c;
        s.textContent = p.g;
        hist.appendChild(s);
      });
      tr.appendChild(hist);
      frag.appendChild(tr);
    });
    tbody.replaceChildren(frag);

    if (clock) {
      const secs = Math.floor(tick * SEC_PER_TICK);
      const mm = String(Math.floor(secs / 60)).padStart(2, "0");
      const ss = String(secs % 60).padStart(2, "0");
      clock.textContent = mm + ":" + ss;
    }
  };

  /* ------------------------------------------------ pulse field */

  const pulseCanvas = document.querySelector("[data-pulse]");
  let emitPulse = () => {};

  if (pulseCanvas && !reduced && typeof pulseCanvas.getContext === "function") {
    const ctx = pulseCanvas.getContext("2d");
    const pulses = [];
    let raf = null;

    const sizeCanvas = () => {
      const box = pulseCanvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      pulseCanvas.width = Math.max(1, Math.round(box.width * dpr));
      pulseCanvas.height = Math.max(1, Math.round(box.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    sizeCanvas();
    window.addEventListener("resize", sizeCanvas);

    const drawPulses = () => {
      const box = pulseCanvas.getBoundingClientRect();
      ctx.clearRect(0, 0, box.width, box.height);
      for (let i = pulses.length - 1; i >= 0; i -= 1) {
        const p = pulses[i];
        p.r += p.speed;
        const life = 1 - p.r / p.max;
        if (life <= 0) {
          pulses.splice(i, 1);
          continue;
        }
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(230, 237, 243, " + (0.09 * life).toFixed(3) + ")";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      if (pulses.length > 0) {
        raf = requestAnimationFrame(drawPulses);
      } else {
        raf = null;
      }
    };

    emitPulse = () => {
      if (document.hidden) return;
      const box = pulseCanvas.getBoundingClientRect();
      if (box.width < 1) return;
      if (pulses.length > 14) return;
      pulses.push({
        x: box.width * (0.18 + rand() * 0.72),
        y: box.height * (0.12 + rand() * 0.55),
        r: 2,
        max: 90 + rand() * 150,
        speed: 0.9 + rand() * 0.7,
      });
      if (raf === null) raf = requestAnimationFrame(drawPulses);
    };
  }

  /* ------------------------------------------------ the conductor */

  // `shown` is what the table displays; `target` is where the story says time
  // should be. When they differ, the seek timer replays ticks quickly, like
  // tape shuttling to the right part of the night.
  let shown = 0;
  let target = 0;
  let driftMax = MAX_TICK;
  let autoplay = true;
  let holdTicks = 0;
  const HOLD_TICKS = 7;

  let seekTimer = null;
  const seekStep = () => {
    if (shown === target) {
      clearInterval(seekTimer);
      seekTimer = null;
      return;
    }
    shown += shown < target ? 1 : -1;
    render(shown);
    emitPulse();
  };
  const startSeek = () => {
    if (seekTimer === null && shown !== target) {
      seekTimer = setInterval(seekStep, SEEK_MS);
    }
  };

  let timer = null;
  const observesTerminal = "IntersectionObserver" in window;
  let terminalVisible = !observesTerminal;
  let documentVisible = !document.hidden;

  const step = () => {
    // While rewinding, let the seek finish before time moves forward again.
    if (shown > target) {
      startSeek();
      return;
    }
    if (target < driftMax) {
      holdTicks = 0;
      target += 1;
      if (shown === target - 1 && seekTimer === null) {
        shown = target;
        render(shown);
        emitPulse();
      } else {
        startSeek();
      }
      return;
    }
    // Autoplay reaches the closed window, holds the final frame, then
    // rewinds the tape and replays the night.
    if (autoplay && shown === MAX_TICK && target === MAX_TICK) {
      holdTicks += 1;
      if (holdTicks > HOLD_TICKS) {
        holdTicks = 0;
        target = 0;
        startSeek();
      }
    }
  };

  const updateTerminalTimer = () => {
    const shouldRun = !reduced && terminalVisible && documentVisible;
    if (shouldRun && timer === null) timer = setInterval(step, TICK_MS);
    if (!shouldRun && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    if (!documentVisible && seekTimer !== null) {
      clearInterval(seekTimer);
      seekTimer = null;
    }
    if (documentVisible) startSeek();
  };

  const chapters = [...document.querySelectorAll("[data-chapter]")];

  if (reduced) {
    // Static end-of-run frame: the whole story, already told.
    shown = MAX_TICK;
    target = MAX_TICK;
    render(MAX_TICK);
    chapters.forEach((chapter) => chapter.classList.add("is-live"));
  } else {
    // The hero autoplays the whole night; scrolling into a chapter takes over.
    driftMax = MAX_TICK;
    shown = Math.min(4, driftMax);
    target = shown;
    render(shown);

    if (observesTerminal) {
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

    if (chapters.length > 0 && observesTerminal) {
      chapters.forEach((chapter) => chapter.classList.add("chapter-armed"));
      const chapterObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const chapter = entry.target;
          chapters.forEach((c) => c.classList.toggle("is-live", c === chapter));
          if (chapter === chapters[0]) {
            // Back at the hero: hand time back to the autoplay loop.
            autoplay = true;
            holdTicks = 0;
            driftMax = MAX_TICK;
            return;
          }
          autoplay = false;
          const start = Number(chapter.getAttribute("data-tick-start")) || 0;
          const end = Number(chapter.getAttribute("data-tick-end")) || MAX_TICK;
          target = Math.max(start, Math.min(target, end));
          if (shown > end) target = end;
          if (shown < start) target = start;
          driftMax = end;
          if (documentVisible) startSeek();
        });
      }, { rootMargin: "-42% 0px -42% 0px", threshold: 0 });
      chapters.forEach((chapter) => chapterObserver.observe(chapter));
    }
  }
})();
