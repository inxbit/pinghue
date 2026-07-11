/* pinghue.com — hero terminal simulation + copy buttons.
   The simulation follows the real fixed latency scale and host-state rules
   described in the README: whole-run states, fail-threshold 3, glyphs ▁▂▃▄▅▆▇█. */

(() => {
  "use strict";

  /* ------------------------------------------------ copy buttons */

  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-copy");
      const done = () => {
        btn.classList.add("copied");
        btn.textContent = "Copied";
        setTimeout(() => {
          btn.classList.remove("copied");
          btn.textContent = "Copy";
        }, 1600);
      };
      const failed = () => {
        btn.classList.remove("copied");
        btn.textContent = "Copy failed";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, failed);
      } else {
        failed();
      }
    });
  });

  /* ------------------------------------------------ scale strip reveal */

  const strip = document.querySelector("[data-scale]");
  if (strip && "IntersectionObserver" in window) {
    strip.querySelectorAll(".scale-cell").forEach((cell, i) => {
      cell.style.setProperty("--i", i);
    });
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            strip.classList.add("in-view");
            io.disconnect();
          }
        });
      },
      { threshold: 0.35 }
    );
    io.observe(strip);
  }

  /* ------------------------------------------------ terminal simulation */

  const root = document.querySelector("[data-terminal]");
  if (!root) return;

  const tbody = root.querySelector("[data-rows]");
  const clock = root.querySelector("[data-clock]");
  if (!tbody) return;

  const HISTORY = 26;
  const TICK_MS = 700;
  const FAIL_THRESHOLD = 3;
  const SLOW_MS = 100;

  // Deterministic LCG so every visitor sees the same scripted window.
  let seed = 20260703;
  const rand = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };

  // The story: db-primary spikes mid-run, api-gw blips once,
  // backup-nas goes down and stays down. Everything else is boring — as it should be.
  const hosts = [
    { name: "edge-router-1", base: 9, wobble: 3 },
    { name: "edge-router-2", base: 12, wobble: 4 },
    { name: "core-sw-1", base: 2, wobble: 1 },
    { name: "db-primary", base: 18, wobble: 5, spike: { from: 12, to: 17, ms: 340 } },
    { name: "cdn-edge", base: 24, wobble: 6 },
    { name: "api-gw", base: 31, wobble: 5, lossAt: [9] },
    { name: "backup-nas", base: 6, wobble: 2, downAt: 8 },
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

  const fmt = (n, unit) => (n === null ? "-" : n.toFixed(1) + unit);

  const render = (tick) => {
    const frag = document.createDocumentFragment();
    hosts.forEach((h) => {
      const st = stateOf(h);
      const loss = h.sent ? ((h.sent - h.recv) / h.sent) * 100 : 0;
      const avg = h.recv ? h.sum / h.recv : null;
      const tr = document.createElement("tr");

      const cells = [
        ["t-host", h.name],
        ["t-num", fmt(h.last, "ms")],
        ["t-num", fmt(avg, "ms")],
        [loss > 0 ? "t-num g-fail" : "t-num", loss.toFixed(0) + "%"],
        ["t-num", fmt(h.recv > 1 ? h.jitter : 0, "ms")],
        [st.cls, st.label],
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
      const secs = tick;
      const mm = String(Math.floor(secs / 60)).padStart(2, "0");
      const ss = String(secs % 60).padStart(2, "0");
      clock.textContent = mm + ":" + ss;
    }
  };

  let tick = 0;
  const step = () => {
    tick += 1;
    hosts.forEach((h) => probe(h, tick));
    render(tick);
  };

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduced) {
    // Static end-of-run frame: the whole story, already told.
    for (let i = 0; i < 30; i += 1) step();
  } else {
    for (let i = 0; i < 4; i += 1) step(); // start with a little history on screen
    let timer = setInterval(step, TICK_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearInterval(timer);
        timer = null;
      } else if (!timer) {
        timer = setInterval(step, TICK_MS);
      }
    });
  }
})();
