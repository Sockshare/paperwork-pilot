/* Paperwork Pilot front-end. Plain JavaScript, no frameworks, no build step. */
(function () {
  "use strict";

  // ---------- tiny helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const show = (el) => el.removeAttribute("hidden");
  const hide = (el) => el.setAttribute("hidden", "");

  let toastTimer = null;
  function toast(msg, kind) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (kind ? " " + kind : "");
    show(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => hide(t), kind === "bad" ? 7000 : 3500);
  }

  const SERVER_DOWN = "The app's server isn't answering. Go back to the terminal window and start it again with: python app.py";
  async function api(path, opts = {}) {
    let res;
    try {
      res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error(SERVER_DOWN);
    }
    let data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const msg = (data && (data.detail || data.message)) || `Something went wrong (${res.status}).`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  // ---------- state ----------
  const state = {
    status: null,
    current: null,       // {id, filename, text, result}
    working: false,
    abort: null,
  };

  const screens = ["setup", "home", "working", "results"];
  function go(name) {
    hide($("toast"));
    screens.forEach((s) => hide($("screen-" + s)));
    show($("screen-" + name));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- status / engine pill ----------
  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
    } catch (e) {
      $("engineText").textContent = "Can't reach the app";
      $("engineDot").className = "dot bad";
      toast(e.message, "bad");
      return;
    }
    const p = state.status.provider;
    const names = { ollama: "On my computer", openai: "Cloud (OpenAI)", gemini: "Cloud (Gemini)", offline: "No AI (basic)" };
    $("engineText").textContent = names[p.name] + (p.ok ? "" : " — problem");
    $("engineDot").className = "dot " + (p.ok ? (p.name === "offline" ? "warn" : "ok") : "bad");
    $("enginePill").title = p.message;
    $("privacyText").textContent = p.local
      ? "Your documents never leave this computer."
      : "Cloud mode is on: document text is sent to " + names[p.name].replace("Cloud ", "").replace(/[()]/g, "") + ". Switch to 'On my computer' in Settings for full privacy.";
  }

  // ---------- first run ----------
  async function initSetup() {
    const st = state.status;
    const o = st.ollama;
    const el = $("ollamaStatus");
    if (o.running && o.models.length) {
      el.textContent = "✓ Found Ollama with " + o.models[0];
      el.className = "choice-status ok";
    } else if (o.running) {
      el.textContent = "Ollama is running but has no model yet (we'll show you how)";
      el.className = "choice-status";
    } else {
      el.textContent = "Not found — we'll show you how to install it (free)";
      el.className = "choice-status bad";
    }
    document.querySelectorAll(".choice").forEach((btn) => {
      btn.onclick = async () => {
        try { await chooseSetup(btn.dataset.choice); }
        catch (e) { toast("Couldn't save that choice: " + e.message, "bad"); }
      };
    });

    async function chooseSetup(c) {
      {
        if (c === "ollama") {
          const model = o.models.length ? o.models[0] : "llama3.2:3b";
          await api("/api/settings", { method: "POST", body: JSON.stringify({ provider: "ollama", model, setup_complete: true }) });
          await refreshStatus();
          if (!(o.running && o.models.length)) { openSettings(); toast("Follow the steps in the box to install Ollama, then click Test.", ""); }
          go("home");
        } else if (c === "cloud") {
          await api("/api/settings", { method: "POST", body: JSON.stringify({ provider: "openai", setup_complete: true }) });
          await refreshStatus();
          go("home");
          openSettings();
          toast("Paste your API key, click Test, then Save.", "");
        } else {
          await api("/api/settings", { method: "POST", body: JSON.stringify({ provider: "offline", setup_complete: true }) });
          await refreshStatus();
          go("home");
        }
      }
    }
  }

  // ---------- home ----------
  async function initHome() {
    // samples
    try {
      const samples = await api("/api/samples");
      $("sampleRow").innerHTML = samples.map((s) => `<button class="sample-btn" data-sample="${esc(s.name)}"><span>${s.emoji}</span>${esc(s.title)}</button>`).join("");
      document.querySelectorAll(".sample-btn").forEach((b) => (b.onclick = () => runSample(b.dataset.sample)));
    } catch (_) { /* samples are optional */ }
    loadRecent();
  }

  async function loadRecent() {
    try {
      const items = await api("/api/history");
      if (!items.length) { hide($("recentBox")); return; }
      $("recentList").innerHTML = items.slice(0, 5).map((d) => `
        <div class="recent-item">
          <div><div class="t">${esc(d.title || d.filename)}</div><div class="muted small">${esc(d.filename)} · ${new Date(d.created * 1000).toLocaleString()}</div></div>
          <button class="btn small" data-open="${d.id}">Open</button>
        </div>`).join("");
      $("recentList").querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => openHistory(b.dataset.open)));
      show($("recentBox"));
    } catch (_) { hide($("recentBox")); }
  }

  const dz = $("dropzone");
  const fileInput = $("fileInput");
  dz.onclick = () => fileInput.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) runFile(f); });
  fileInput.onchange = () => { if (fileInput.files[0]) runFile(fileInput.files[0]); fileInput.value = ""; };

  $("pasteText").oninput = () => {
    const n = $("pasteText").value.trim().length;
    $("pasteCount").textContent = n ? `${n.toLocaleString()} characters` : "";
  };
  $("pasteBtn").onclick = () => {
    const text = $("pasteText").value;
    if (text.trim().length < 20) { toast("Paste a bit more text first — at least a sentence or two.", "bad"); return; }
    runText(text);
  };

  // ---------- running an analysis ----------
  function startWorking(title) {
    state.working = true;
    $("workingTitle").textContent = title || "Reading your document…";
    const steps = $("workingSteps").children;
    [...steps].forEach((li, i) => (li.className = i === 0 ? "done" : i === 1 ? "active" : ""));
    const p = state.status && state.status.provider;
    $("workingSub").textContent = p && p.name === "offline"
      ? "Basic mode is instant."
      : p && p.name === "ollama"
        ? "Running on your own computer. You'll see the quick scan right away."
        : "Usually 5–20 seconds.";
    go("working");
    let i = 1;
    state._stepTimer = setInterval(() => {
      if (i < steps.length - 1) { steps[i].className = "done"; i++; steps[i].className = "active"; }
    }, 9000);
  }
  function stopWorking() {
    state.working = false;
    clearInterval(state._stepTimer);
  }
  $("cancelWorking").onclick = () => { if (state.abort) state.abort.abort(); stopWorking(); go("home"); };

  async function runWith(fetcher, title) {
    startWorking(title);
    state.abort = new AbortController();
    try {
      const data = await fetcher(state.abort.signal);
      stopWorking();
      showResults(data);
      loadRecent();
      if (data.ai_pending) enhance(data);
    } catch (e) {
      stopWorking();
      if (e.name === "AbortError") return;
      go("home");
      toast(e.message || "Something went wrong.", "bad");
    }
  }

  // Phase 2: ask the AI to upgrade the instant result. The page stays usable meanwhile.
  let enhanceSeq = 0;
  async function enhance(data) {
    const seq = ++enhanceSeq;
    const p = state.status.provider;
    const banner = $("aiBanner");
    const started = Date.now();
    banner.innerHTML = `<div class="spinner small"></div><div><b>The AI is reading your document now.</b> The dates, money and watch-outs below are ready. The plain-language explanation will appear here ${p.name === "ollama" ? "in about 1–4 minutes on a normal computer" : "in a few seconds"}. <span class="muted" id="aiTimer"></span></div><button class="btn small ghost" id="aiSkip">Skip the AI</button>`;
    show(banner);
    const timer = setInterval(() => { const el = $("aiTimer"); if (el) el.textContent = `(${Math.round((Date.now() - started) / 1000)}s)`; }, 1000);
    const ctrl = new AbortController();
    $("aiSkip").onclick = () => { ctrl.abort(); };
    try {
      const res = await fetch("/api/enhance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: data.id, text: data.text, warnings: data.warnings || [] }), signal: ctrl.signal }).catch((e) => { throw e.name === "AbortError" ? e : new Error(SERVER_DOWN); });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.detail) || "The AI step failed.");
      if (seq !== enhanceSeq || state.current !== data) return; // user moved on
      showResults({ ...data, result: body.result, ai_pending: false }, true); // keeps the Q&A thread intact
      if (body.result.meta && body.result.meta.fallback) toast("The AI had a problem, so you're seeing the basic scan. Details are in the yellow box.", "bad");
      else toast("The AI finished — the explanation is updated.", "ok");
    } catch (e) {
      if (seq !== enhanceSeq || state.current !== data) return;
      data.ai_pending = false;
      $("rMeta").textContent = $("rMeta").textContent.replace("quick scan (AI coming)", e.name === "AbortError" ? "quick scan (AI skipped)" : "quick scan (AI failed)");
      if (e.name !== "AbortError") toast("The AI step didn't finish: " + e.message, "bad");
    } finally {
      clearInterval(timer);
      if (seq === enhanceSeq) hide(banner);
    }
  }

  function runFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    runWith(async (signal) => {
      const res = await fetch("/api/analyze/file", { method: "POST", body: fd, signal }).catch((e) => { throw e.name === "AbortError" ? e : new Error(SERVER_DOWN); });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || "Could not read that file.");
      return data;
    }, `Reading "${file.name}"…`);
  }
  function runText(text) {
    runWith(async (signal) => {
      const res = await fetch("/api/analyze/text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }), signal }).catch((e) => { throw e.name === "AbortError" ? e : new Error(SERVER_DOWN); });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || "Could not analyze that text.");
      return data;
    }, "Reading your text…");
  }
  function runSample(name) {
    runWith(async (signal) => {
      const res = await fetch("/api/analyze/sample/" + encodeURIComponent(name), { method: "POST", signal }).catch((e) => { throw e.name === "AbortError" ? e : new Error(SERVER_DOWN); });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || "Could not load the example.");
      return data;
    }, "Reading the example…");
  }

  // ---------- results ----------
  function li(items, empty) {
    if (!items || !items.length) return `<li class="empty">${esc(empty)}</li>`;
    return items.map((x) => `<li>${esc(x)}</li>`).join("");
  }

  function showResults(data, isUpgrade) {
    state.current = data;
    const r = data.result;
    $("rDocType").textContent = r.document_type_label || "Document";
    $("rTitle").textContent = r.title || "Your document";
    const m = r.meta || {};
    const engineNames = { ollama: "AI on your computer", openai: "cloud AI", gemini: "cloud AI (Gemini)", offline: "basic mode (no AI)" };
    const engineLabel = data.ai_pending ? "quick scan (AI coming)" : m.fallback ? "basic scan (the AI had a problem)" : (engineNames[m.engine] || m.engine);
    $("rMeta").textContent = `${data.filename} · ${(data.chars || 0).toLocaleString()} characters · ${engineLabel}${m.model && m.engine !== "offline" && !m.fallback ? " (" + m.model + ")" : ""}${m.seconds && m.engine !== "offline" ? " · " + m.seconds + "s" : ""}`;

    const notes = (r.notes || []).slice();
    if (r.confidence === "low" && m.engine !== "offline" && !m.fallback && !data.ai_pending) notes.push("The AI wasn't very sure about this one. Double-check the important parts against the original.");
    if (notes.length) { $("rNotes").innerHTML = notes.map((n) => `<div>ℹ️ ${esc(n)}</div>`).join(""); show($("rNotes")); } else hide($("rNotes"));

    $("rSummary").textContent = r.plain_summary || "No summary could be written.";
    const sw = [];
    if (r.who_sent_it) sw.push(`<div><b>Who sent it:</b> ${esc(r.who_sent_it)}</div>`);
    if (r.what_they_want) sw.push(`<div><b>What they want:</b> ${esc(r.what_they_want)}</div>`);
    $("rSenderWant").innerHTML = sw.join("");

    $("rWatch").innerHTML = r.watch_outs && r.watch_outs.length
      ? r.watch_outs.map((w) => `
        <div class="watch ${esc(w.severity)}">
          <div class="wt">${esc(w.title)} <span class="sev ${esc(w.severity)}">${w.severity === "high" ? "important" : w.severity === "medium" ? "worth knowing" : "minor"}</span></div>
          ${w.why ? `<div>${esc(w.why)}</div>` : ""}
          ${w.quote ? `<div class="wq">“${esc(w.quote)}”</div>` : ""}
        </div>`).join("")
      : `<p class="empty">Nothing alarming was found. Still read the dates and money carefully.</p>`;

    const urgLabel = { urgent: "soon!", soon: "coming up", later: "later", none: "passed / info" };
    $("rDeadlines").innerHTML = r.deadlines && r.deadlines.length
      ? r.deadlines.map((d) => `<div class="dl"><div class="date">${esc(d.date || "—")}</div><div>${esc(d.what)}<span class="urg ${esc(d.urgency)}">${urgLabel[d.urgency] || ""}</span></div></div>`).join("")
      : `<p class="empty">No dates or deadlines were found.</p>`;

    $("rMoney").innerHTML = r.money && r.money.length
      ? r.money.map((x) => `<div class="money-row"><div class="amt">${esc(x.amount || "—")}</div><div>${esc(x.what)}${x.recurring ? '<span class="rec">repeats</span>' : ""}</div></div>`).join("")
      : `<p class="empty">No dollar amounts were found.</p>`;

    if (r.key_facts && r.key_facts.length) {
      $("rFacts").innerHTML = r.key_facts.map((f) => `<div><b>${esc(f.label)}</b>${esc(f.value)}</div>`).join("");
      show($("cardFacts"));
    } else hide($("cardFacts"));

    $("rOptions").innerHTML = li(r.your_options, "No specific options were listed.");
    $("rQuestions").innerHTML = li(r.questions_to_ask, "No questions suggested.");
    $("rSteps").innerHTML = li(r.next_steps, "Read the document again carefully and keep a copy.");

    if (r.glossary && r.glossary.length) {
      $("rGlossary").innerHTML = "<dl>" + r.glossary.map((g) => `<dt>${esc(g.term)}</dt><dd>${esc(g.meaning)}</dd>`).join("") + "</dl>";
      show($("cardGlossary"));
    } else hide($("cardGlossary"));

    $("rRaw").textContent = data.text || "";
    if (!isUpgrade) {
      $("askThread").innerHTML = "";
      $("askInput").value = "";
      hide($("askBox"));
      hide($("aiBanner"));
      go("results");
    }
  }

  $("backBtn").onclick = () => { enhanceSeq++; hide($("aiBanner")); $("pasteText").value = ""; $("pasteCount").textContent = ""; go("home"); };
  $("brandLink").onclick = (e) => { e.preventDefault(); if (state.status && state.status.settings.setup_complete) go("home"); };
  $("printBtn").onclick = () => window.print();
  $("copySummaryBtn").onclick = async () => {
    const r = state.current.result;
    const lines = [r.title, "", r.plain_summary, ""];
    if (r.watch_outs.length) { lines.push("WATCH OUT FOR:"); r.watch_outs.forEach((w) => lines.push(`- ${w.title}: ${w.why}`)); lines.push(""); }
    if (r.deadlines.length) { lines.push("DATES:"); r.deadlines.forEach((d) => lines.push(`- ${d.date}: ${d.what}`)); lines.push(""); }
    if (r.money.length) { lines.push("MONEY:"); r.money.forEach((m) => lines.push(`- ${m.amount}: ${m.what}`)); lines.push(""); }
    if (r.next_steps.length) { lines.push("NEXT STEPS:"); r.next_steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`)); }
    await copyText(lines.join("\n"));
  };

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied!", "ok"); }
    catch (_) { toast("Couldn't copy automatically — select the text and press Ctrl+C.", "bad"); }
  }

  // ---------- ask ----------
  $("askToggle").onclick = () => { const b = $("askBox"); b.hidden ? (show(b), $("askInput").focus()) : hide(b); };
  $("askBtn").onclick = async () => {
    const q = $("askInput").value.trim();
    if (!q) { toast("Type a question first.", "bad"); return; }
    $("askBtn").disabled = true; $("askBtn").textContent = "Thinking…";
    const block = document.createElement("div");
    block.className = "qa";
    block.innerHTML = `<div class="q">${esc(q)}</div><div class="a muted">…</div>`;
    $("askThread").prepend(block);
    try {
      const data = await api("/api/ask", { method: "POST", body: JSON.stringify({ text: state.current.text, result: state.current.result, question: q }) });
      block.querySelector(".a").textContent = data.answer;
      block.querySelector(".a").classList.remove("muted");
      $("askInput").value = "";
    } catch (e) {
      block.querySelector(".a").textContent = "Sorry — " + e.message;
    } finally {
      $("askBtn").disabled = false; $("askBtn").textContent = "Ask";
    }
  };
  $("askInput").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("askBtn").click(); };

  // ---------- modals ----------
  function openModal(id) { show($("modalBack")); show($(id)); }
  function closeModals() { hide($("modalBack")); document.querySelectorAll(".modal").forEach(hide); }
  $("modalBack").onclick = closeModals;
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModals));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });

  // ---------- reply ----------
  $("replyBtn").onclick = () => {
    const st = state.status;
    const goalSel = $("replyGoal");
    goalSel.innerHTML = Object.entries(st.reply_goals).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    const dt = state.current.result.document_type;
    const def = { medical_bill: "payment_plan", insurance: "appeal", debt: "validate", lease: "question", job_offer: "negotiate", terms: "cancel" }[dt];
    if (def) goalSel.value = def;
    $("replyTone").innerHTML = Object.entries(st.reply_tones).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    hide($("replyOut"));
    openModal("modal-reply");
  };
  $("replyGo").onclick = async () => {
    const btn = $("replyGo");
    btn.disabled = true; btn.textContent = "Writing… (this can take up to a minute)";
    try {
      const data = await api("/api/reply", { method: "POST", body: JSON.stringify({
        result: state.current.result, goal: $("replyGoal").value, tone: $("replyTone").value, details: $("replyDetails").value, kind: $("replyKind").value,
      }) });
      $("replyText").value = data.text;
      $("replyNote").textContent = data.engine === "template"
        ? (data.fallback ? "The AI had a problem (" + (data.error || "") + "), so here is a fill-in-the-blanks template." : "Basic mode: here is a fill-in-the-blanks template.")
        : "Written by the AI. Read it carefully before sending.";
      show($("replyOut"));
      $("replyText").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) { toast(e.message, "bad"); }
    finally { btn.disabled = false; btn.textContent = "Write it for me"; }
  };
  $("replyCopy").onclick = () => copyText($("replyText").value);
  $("replyAgain").onclick = () => $("replyGo").click();
  $("replyDownload").onclick = () => {
    const blob = new Blob([$("replyText").value], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = ($("replyKind").value === "email" ? "email" : "letter") + "-draft.txt";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // ---------- settings ----------
  function openSettings() {
    if (!state.status) { toast("Settings aren't available until the app's server answers. Run: python scripts/selfcheck.py", "bad"); return; }
    const s = state.status.settings;
    document.querySelector(`input[name=prov][value=${s.provider}]`).checked = true;
    showProvPanel(s.provider);
    const o = state.status.ollama;
    const models = o.models.length ? o.models : [s.provider === "ollama" ? s.model : "llama3.2:3b"];
    $("setOllamaModel").innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    if (s.provider === "ollama" && models.includes(s.model)) $("setOllamaModel").value = s.model;
    $("setOllamaHost").value = s.ollama_host;
    $("setOllamaInfo").textContent = o.running ? (o.models.length ? `Found ${o.models.length} model${o.models.length > 1 ? "s" : ""}.` : "Running, but no model downloaded.") : "Ollama is not running.";
    o.running && o.models.length ? hide($("ollamaHelp")) : show($("ollamaHelp"));
    $("setOpenaiKey").value = s.openai_api_key || "";
    $("setOpenaiModel").value = s.provider === "openai" ? s.model : "";
    $("setOpenaiUrl").value = s.openai_base_url;
    $("setGeminiKey").value = s.gemini_api_key || "";
    $("setGeminiModel").value = s.provider === "gemini" ? s.model : "";
    $("setSaveHistory").checked = !!s.save_history;
    $("setTestOut").textContent = "";
    openModal("modal-settings");
  }
  function showProvPanel(p) {
    document.querySelectorAll(".prov-panel").forEach((el) => el.classList.toggle("show", el.dataset.prov === p));
  }
  document.querySelectorAll("input[name=prov]").forEach((r) => (r.onchange = () => { showProvPanel(r.value); $("setTestOut").textContent = ""; }));
  $("settingsBtn").onclick = openSettings;
  $("enginePill").onclick = openSettings;

  function collectSettings() {
    const provider = document.querySelector("input[name=prov]:checked").value;
    const body = { provider, ollama_host: $("setOllamaHost").value.trim() || "http://127.0.0.1:11434", openai_base_url: $("setOpenaiUrl").value.trim() || "https://api.openai.com/v1", save_history: $("setSaveHistory").checked };
    if (provider === "ollama") body.model = $("setOllamaModel").value;
    if (provider === "openai") body.model = $("setOpenaiModel").value.trim();
    if (provider === "gemini") body.model = $("setGeminiModel").value.trim();
    if (provider === "offline") body.model = "";
    const ok = $("setOpenaiKey").value.trim(); if (ok) body.openai_api_key = ok;
    const gk = $("setGeminiKey").value.trim(); if (gk) body.gemini_api_key = gk;
    return body;
  }
  $("setTest").onclick = async () => {
    $("setTestOut").textContent = "Testing…"; $("setTestOut").className = "small";
    try {
      const d = await api("/api/test-connection", { method: "POST", body: JSON.stringify(collectSettings()) });
      $("setTestOut").textContent = (d.ok ? "✓ " : "✗ ") + d.message;
      $("setTestOut").className = "small " + (d.ok ? "status-ok" : "status-bad");
    } catch (e) { $("setTestOut").textContent = "✗ " + e.message; $("setTestOut").className = "small status-bad"; }
  };
  $("setSave").onclick = async () => {
    try {
      const d = await api("/api/settings", { method: "POST", body: JSON.stringify({ ...collectSettings(), setup_complete: true }) });
      await refreshStatus();
      closeModals();
      toast(d.provider.ok ? "Saved. " + d.provider.message : "Saved, but: " + d.provider.message, d.provider.ok ? "ok" : "bad");
    } catch (e) { toast(e.message, "bad"); }
  };
  document.querySelectorAll("[data-clear-key]").forEach((b) => (b.onclick = async () => {
    const field = b.dataset.clearKey; // openai_api_key | gemini_api_key
    try {
      await api("/api/settings", { method: "POST", body: JSON.stringify({ [field]: "" }) });
      await refreshStatus();
      $(field === "openai_api_key" ? "setOpenaiKey" : "setGeminiKey").value = "";
      toast("Key removed from this computer.", "ok");
    } catch (e) { toast(e.message, "bad"); }
  }));
  $("setClearHistory").onclick = async () => {
    if (!confirm("Delete every saved document from this computer? This cannot be undone.")) return;
    await api("/api/history", { method: "DELETE" });
    toast("All saved documents were deleted.", "ok");
    loadRecent();
  };

  // ---------- history ----------
  $("historyBtn").onclick = async () => {
    const items = await api("/api/history");
    $("historyList").innerHTML = items.length
      ? items.map((d) => `<div class="hist-item"><div><div class="t">${esc(d.title || d.filename)}</div><div class="muted small">${esc(d.filename)} · ${new Date(d.created * 1000).toLocaleString()}</div></div><div class="row-gap" style="margin:0"><button class="btn small" data-open="${d.id}">Open</button><button class="btn small danger-ghost" data-del="${d.id}">Delete</button></div></div>`).join("")
      : `<p class="muted">Nothing saved yet. Documents you analyze will show up here (if "Remember documents" is on in Settings).</p>`;
    $("historyList").querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => { closeModals(); openHistory(b.dataset.open); }));
    $("historyList").querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api("/api/history/" + b.dataset.del, { method: "DELETE" }); $("historyBtn").click(); loadRecent(); }));
    openModal("modal-history");
  };
  async function openHistory(id) {
    try {
      const d = await api("/api/history/" + id);
      showResults({ id: d.id, filename: d.filename, text: d.text, chars: d.text.length, result: d.result });
    } catch (e) { toast(e.message, "bad"); }
  }

  $("helpBtn").onclick = () => openModal("modal-help");

  // ---------- boot ----------
  (async function boot() {
    await refreshStatus();
    if (!state.status) {
      // Still show the home screen so the user isn't stuck looking at nothing.
      await initHome();
      go("home");
      toast("The app couldn't load its settings. Try again in a moment, or run: python scripts/selfcheck.py", "bad");
      return;
    }
    // Keep the file picker in sync with what the server can read.
    if (state.status.supported_files) fileInput.setAttribute("accept", state.status.supported_files.join(","));
    await initHome();
    if (!state.status.settings.setup_complete) { await initSetup(); go("setup"); }
    else go("home");
  })();
})();
