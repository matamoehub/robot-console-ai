(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const POLL_MS = 15000;

  function isJSONResponse(r) {
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    return ct.includes("application/json");
  }

  async function postJSON(url, body = null) {
    const opts = {
      method: "POST",
      credentials: "same-origin", // IMPORTANT: send session cookie
      headers: {}
    };
    if (body && typeof body === "object") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }

    const r = await fetch(url, opts);
    const txt = await r.text();

    // If we got HTML back (common when redirected to /login), treat as failure
    if (!isJSONResponse(r)) {
      const short = txt.slice(0, 200).replace(/\s+/g, " ");
      return {
        ok: false,
        status: r.status,
        data: { error: "Non-JSON response (likely login redirect or server error)", preview: short }
      };
    }

    let data;
    try { data = JSON.parse(txt); } catch { data = { error: "Bad JSON", raw: txt.slice(0, 200) }; }
    return { ok: r.ok, status: r.status, data };
  }

  async function getJSON(url) {
    const r = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store"
    });

    if (!isJSONResponse(r)) throw new Error(`Non-JSON ${r.status}`);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  // --- Uptime formatting (minutes-only) ---
  function fmtDuration(seconds) {
    const m = Math.floor((Number(seconds) || 0) / 60);
    return `${m}m`;
  }

  function paintUptimeCell(tr, sec) {
    const span = tr.querySelector(".uptime");
    if (!span) return;
    const secs = Math.max(0, Math.floor(Number(sec) || 0));
    span.setAttribute("data-seconds", String(secs));
    span.textContent = fmtDuration(secs);
    span.title = `${secs} seconds`;
  }

  function setRowEnabled(tr, enabled) {
    tr.classList.toggle("row-disabled", !enabled);
    $$("button, input, select, textarea", tr).forEach(el => {
      el.disabled = !enabled;
      if (!enabled && !el.title) el.title = "Robot offline";
      if (enabled && el.title === "Robot offline") el.title = "";
    });
  }

  function getCaps(tr) {
    const raw = tr.getAttribute("data-cap-services") || "";
    const caps = raw.split(",").map(s => s.trim()).filter(Boolean);
    const allowSay = tr.getAttribute("data-cap-say") === "1";
    const allowSound = tr.getAttribute("data-cap-soundoff") === "1";
    const allowStop = tr.getAttribute("data-cap-allstop") === "1";
    return { services: caps, allowSay, allowSound, allowStop };
  }

  // --- service button appearance/state ---
  function paintSvcButton(btn, state) {
    btn.classList.remove("svc-running", "svc-stopped", "svc-error", "svc-starting");
    const label = btn.getAttribute("data-svc") || "svc";
    let wantStart = true;

    switch ((state || "").toLowerCase()) {
      case "running":
        btn.classList.add("svc-running");
        btn.textContent = `${label} (running)`;
        wantStart = false;
        break;
      case "error":
        btn.classList.add("svc-error");
        btn.textContent = `${label} (error)`;
        wantStart = true;
        break;
      case "starting":
        btn.classList.add("svc-starting");
        btn.textContent = `${label} (starting)`;
        wantStart = true;
        break;
      default:
        btn.classList.add("svc-stopped");
        btn.textContent = `${label} (stopped)`;
        wantStart = true;
    }
    btn.setAttribute("data-next-op", wantStart ? "start" : "restart");
  }

  function recomputeStopMotorEnable(tr) {
    // Only enable STOP Motors when BOTH jupyter and ros_move are running
    const stopBtn = $(".act-allstop", tr);
    if (!stopBtn) return;

    const j = $('.act-svc-toggle[data-svc="jupyter"]', tr);
    const m = $('.act-svc-toggle[data-svc="ros_move"]', tr);
    const jRun = j && j.classList.contains("svc-running");
    const mRun = m && m.classList.contains("svc-running");
    const online = tr.getAttribute("data-online") === "1";

    stopBtn.disabled = !(online && jRun && mRun);
    stopBtn.title = stopBtn.disabled ? "Requires jupyter AND ros_move running" : "";
  }

  function setResult(tr, msg, isErr = false) {
    const result = $("[data-result]", tr);
    if (!result) return;
    result.textContent = msg || "";
    result.classList.toggle("text-danger", !!isErr);
    result.classList.toggle("text-success", !isErr && !!msg);
  }

  function setFleetResult(msg, isErr = false) {
    const el = document.getElementById("fleet-result");
    if (!el) return;
    el.textContent = msg || "";
    el.classList.toggle("text-danger", !!isErr);
    el.classList.toggle("text-success", !isErr && !!msg);
  }

  function bindRowHandlers(tr) {
    const rid = tr.getAttribute("data-rid");
    const online = tr.getAttribute("data-online") === "1";
    const caps = getCaps(tr);

    setRowEnabled(tr, online);
    if (!online) return;

    // Remove service buttons that aren’t supported
    $$(".act-svc-toggle", tr).forEach(btn => {
      const svc = btn.getAttribute("data-svc");
      if (!caps.services.includes(svc)) btn.remove();
    });

    // Service toggle (start/stop)
    $$(".act-svc-toggle", tr).forEach(btn => {
      btn.addEventListener("click", async () => {
        const svc = btn.getAttribute("data-svc");
        const op = btn.getAttribute("data-next-op") || "start";
        btn.disabled = true;
        setResult(tr, `${svc} ${op}...`);

        try {
          const res = await postJSON(`/act/${encodeURIComponent(rid)}/service/${encodeURIComponent(svc)}/${encodeURIComponent(op)}`);
          if (res.ok) {
            paintSvcButton(btn, res.data.active || null);
            setResult(tr, `${svc} ${op} OK`);
            await pollOnce(); // refresh row state
          } else {
            paintSvcButton(btn, "error");
            setResult(tr, `${svc} ${op} FAIL: ${(res.data && (res.data.error || res.data.preview)) || res.status}`, true);
          }
        } catch (e) {
          paintSvcButton(btn, "error");
          setResult(tr, `${svc} ${op} ERR: ${e}`, true);
        } finally {
          btn.disabled = false;
          recomputeStopMotorEnable(tr);
        }
      });
    });

    // Sound off
    const soundBtn = $(".act-soundoff", tr);
    if (soundBtn && caps.allowSound) {
      soundBtn.addEventListener("click", async () => {
        soundBtn.disabled = true;
        setResult(tr, "Sound off...");
        try {
          const res = await postJSON(`/act/${encodeURIComponent(rid)}/soundoff`);
          setResult(tr, res.ok ? "Sound off OK" : `Sound off FAIL: ${(res.data && (res.data.error || res.data.preview)) || res.status}`, !res.ok);
        } catch (e) {
          setResult(tr, `Sound off ERR: ${e}`, true);
        } finally {
          soundBtn.disabled = false;
        }
      });
    }

    // Speak text
    const sayBtn = $(".act-say", tr);
    const sayInput = $(".say-text", tr);
    if (sayBtn && sayInput && caps.allowSay) {
      sayBtn.addEventListener("click", async () => {
        const text = (sayInput.value || "").trim();
        if (!text) { sayInput.focus(); return; }
        sayBtn.disabled = true;
        setResult(tr, "Speaking...");
        try {
          const res = await postJSON(`/act/${encodeURIComponent(rid)}/say`, { text });
          setResult(tr, res.ok ? "Said ✓" : `Say FAIL: ${(res.data && (res.data.error || res.data.preview)) || res.status}`, !res.ok);
        } catch (e) {
          setResult(tr, `Say ERR: ${e}`, true);
        } finally {
          sayBtn.disabled = false;
        }
      });
    }

    // Per-robot ALL STOP
    const stopBtn = $(".act-allstop", tr);
    if (stopBtn && caps.allowStop) {
      stopBtn.addEventListener("click", async () => {
        stopBtn.disabled = true;
        setResult(tr, "STOP Motors...");
        try {
          const res = await postJSON(`/act/${encodeURIComponent(rid)}/allstop`);
          setResult(tr, res.ok ? "STOP ✓" : `STOP FAIL: ${(res.data && (res.data.error || res.data.preview)) || res.status}`, !res.ok);
        } catch (e) {
          setResult(tr, `STOP ERR: ${e}`, true);
        } finally {
          stopBtn.disabled = false;
        }
      });
    }

    recomputeStopMotorEnable(tr);
  }

  function applySnapshotToRow(tr, snap) {
    const isOnline = !!snap.online;
    tr.setAttribute("data-online", isOnline ? "1" : "0");
    setRowEnabled(tr, isOnline);

    const dot = tr.querySelector(".badge-dot");
    if (dot) {
      dot.classList.toggle("dot-on", isOnline);
      dot.classList.toggle("dot-off", !isOnline);
    }

    if (typeof snap.uptime === "number") paintUptimeCell(tr, snap.uptime);

    // Update service button state
    const svcs = snap.services || {};
    $$(".act-svc-toggle", tr).forEach(btn => {
      const svc = btn.getAttribute("data-svc");
      paintSvcButton(btn, svcs[svc]);
    });

    recomputeStopMotorEnable(tr);
  }

  function getRobotName(tr) {
    const rid = tr.getAttribute("data-rid") || "";
    const attrName = tr.getAttribute("data-rname") || "";
    return (attrName || rid).trim();
  }

  function robotShortCode(robotId) {
    const rid = String(robotId || "");
    const m = rid.match(/(\d+)\s*$/);
    if (m && m[1]) return m[1].padStart(2, "0");
    return rid.slice(-2).toUpperCase();
  }

  function batteryPct(snap) {
    const b = snap && snap.battery;
    const p = b && typeof b.percent === "number" ? b.percent : null;
    return Number.isFinite(p) ? Math.max(0, Math.min(100, Math.round(p))) : null;
  }

  function tileClassForBattery(pct) {
    if (typeof pct !== "number") return "fleet-green";
    if (pct < 20) return "fleet-red";
    if (pct < 50) return "fleet-orange";
    return "fleet-green";
  }

  function jumpToRobotRow(robotId) {
    const rid = String(robotId || "");
    const tr = Array.from(document.querySelectorAll("#robots-tbody > tr"))
      .find(row => (row.getAttribute("data-rid") || "") === rid);
    if (!tr) return;
    tr.scrollIntoView({ behavior: "smooth", block: "center" });
    tr.classList.remove("row-focus-flash");
    // Force reflow so repeated clicks re-trigger animation
    void tr.offsetWidth;
    tr.classList.add("row-focus-flash");
  }

  function renderFleetStatusBoard(list) {
    const board = document.getElementById("fleet-status-board");
    if (!board) return;

    const online = (Array.isArray(list) ? list : [])
      .filter(r => !!r.online)
      .sort((a, b) => String(a.robot_id || "").localeCompare(String(b.robot_id || ""), undefined, { numeric: true, sensitivity: "base" }))
      .slice(0, 16);

    board.innerHTML = "";
    if (online.length === 0) {
      const empty = document.createElement("div");
      empty.className = "fleet-empty";
      empty.textContent = "No online robots";
      board.appendChild(empty);
      return;
    }

    for (const r of online) {
      const pct = batteryPct(r);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `fleet-tile ${tileClassForBattery(pct)}`;
      btn.title = `${r.robot_id || ""}${typeof pct === "number" ? ` · ${pct}%` : ""}`;
      btn.innerHTML = `
        <div class="fleet-tile-id">${robotShortCode(r.robot_id)}</div>
        <div class="fleet-tile-batt">${typeof pct === "number" ? `${pct}%` : "--%"}</div>
      `;
      btn.addEventListener("click", () => jumpToRobotRow(r.robot_id));
      board.appendChild(btn);
    }
  }

  function sortAndGroupRows() {
    const tbody = document.querySelector("#robots-tbody");
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll(":scope > tr"));
    const online = [], offline = [];

    for (const tr of rows) {
      (tr.getAttribute("data-online") === "1" ? online : offline).push(tr);
    }

    const byName = (a, b) => getRobotName(a).localeCompare(getRobotName(b), undefined, {
      numeric: true, sensitivity: "base"
    });

    online.sort(byName);
    offline.sort(byName);

    const frag = document.createDocumentFragment();
    online.forEach(tr => frag.appendChild(tr));
    offline.forEach(tr => frag.appendChild(tr));
    tbody.appendChild(frag);
  }

  async function pollOnce() {
    try {
      const list = await getJSON("/api/robots");
      renderFleetStatusBoard(list);
      const byId = {};
      for (const r of list) byId[r.robot_id] = r;

      $$("#robots-tbody > tr").forEach(tr => {
        const rid = tr.getAttribute("data-rid");
        if (rid && byId[rid]) applySnapshotToRow(tr, byId[rid]);
      });

      sortAndGroupRows();
    } catch (e) {
      // Usually means not logged in / transient error
    }
  }
  function init() {
    $$("#robots-tbody > tr").forEach(bindRowHandlers);

    // initial uptime formatting
    $$("#robots-tbody > tr").forEach(tr => {
      const span = tr.querySelector(".uptime");
      if (span && span.hasAttribute("data-seconds")) {
        paintUptimeCell(tr, Number(span.getAttribute("data-seconds")));
      }
    });

    pollOnce();
    setInterval(pollOnce, POLL_MS);

    const fleetSoundBtn = document.getElementById("btn-fleet-soundoff");
    if (fleetSoundBtn) {
      fleetSoundBtn.addEventListener("click", async () => {
        fleetSoundBtn.disabled = true;
        setFleetResult("Fleet Sound Off...");
        try {
          const res = await postJSON("/act/fleet/soundoff");
          if (res.ok) {
            const count = (res.data && typeof res.data.count === "number") ? res.data.count : "?";
            const items = Array.isArray(res.data?.results) ? res.data.results : [];
            const failed = items.filter(x => !x || !x.ok);
            if (!failed.length) {
              setFleetResult(`Fleet Sound Off complete (${count} robots).`);
            } else {
              const details = failed.map(f => {
                const rid = f?.robot_id || "?";
                const why = f?.error || f?.status || "unknown";
                return `${rid}(${why})`;
              }).join(", ");
              setFleetResult(`Fleet Sound Off partial (${count} robots, failed ${failed.length}): ${details}`, true);
            }
          } else {
            setFleetResult(`Fleet Sound Off failed: ${(res.data && (res.data.error || res.data.preview)) || res.status}`, true);
          }
        } catch (e) {
          setFleetResult(`Fleet Sound Off error: ${e}`, true);
        } finally {
          fleetSoundBtn.disabled = false;
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
