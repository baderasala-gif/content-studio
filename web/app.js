/* Content Studio · הפנים של המנוע המקומי. כל העבודה האמיתית קורית בשרת. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const live = $("live");
  const liveError = $("live-error");
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const state = {
    health: null,
    session: new Date().toISOString().slice(0, 10) + "-" + Math.random().toString(36).slice(2, 6),
    post: null,          // {text, research}
    prompt: null,        // תוצאת visual-prompt
    refs: [],            // [{id,name,url,description}]
    images: [],          // [{index, url, path, ...}]
    consistencyUrl: null, // סלייד 1 האחרון שנוצר, לרפרנס עקביות בקרוסלה
    uploading: 0,
    busy: false,
    runId: null,         // הריצה הנוכחית בהיסטוריה (output/<id>/run.json)
    runCost: 0,          // עלות מצטברת של הריצה הנוכחית (דולר, הערכה)
    video: { plan: null, shots: {}, final: null }, // שלב הוידאו לטיקטוק
  };

  // ---------- עלות ----------
  function fmtUsd(v) {
    const n = Number(v) || 0;
    if (n === 0) return "$0";
    return "$" + (n < 0.01 ? n.toFixed(3) : n.toFixed(2));
  }
  function costTag(cost) {
    if (!cost || typeof cost.usd !== "number") return "";
    const est = (cost.entries || []).some((e) => e.basis === "estimate");
    return ` · ≈ ${fmtUsd(cost.usd)}${est ? " (הערכה)" : ""}`;
  }
  function addRunCost(cost) {
    if (!cost || typeof cost.usd !== "number") return;
    state.runCost += cost.usd;
    renderRunCost();
  }
  function renderRunCost() {
    const p = $("run-cost");
    if (!state.runId && !state.runCost) { p.hidden = true; return; }
    p.innerHTML = "";
    p.appendChild(document.createTextNode("עלות הריצה הזו עד עכשיו: "));
    p.appendChild(el("bdi", { dir: "ltr", text: "≈ " + fmtUsd(state.runCost) }));
    p.appendChild(el("span", { class: "cost-tag", text: " · הערכה לפי מחירוני Anthropic ו-fal.ai, לא חשבונית" }));
    p.hidden = false;
  }

  // ---------- עזרים ----------
  function announce(msg) { live.textContent = msg; }
  function announceError(msg) { liveError.textContent = "שגיאה: " + msg; }
  function show(el, on) { el.hidden = !on; }
  function setStatus(el, msg, kind) {
    if (!msg) { el.hidden = true; el.textContent = ""; el.className = "status"; return; }
    el.hidden = false; el.textContent = msg; el.className = "status " + (kind || "");
    if (kind === "error") announceError(msg); else announce(msg);
  }
  function setBusy(on, label) {
    state.busy = on;
    document.querySelectorAll("button").forEach((b) => { b.disabled = on; });
    if (on) $("start-btn").querySelector(".btn-label").textContent = label || "עובד...";
    else syncModeUI();
    syncPromptBtn();
    if (typeof syncVideoButtons === "function") syncVideoButtons();
  }
  function syncPromptBtn() {
    const b = $("prompt-btn");
    b.disabled = state.busy || state.uploading > 0;
    b.title = state.uploading > 0 ? "ממתין לסיום העלאת תמונות הרפרנס" : "";
  }
  // מעבירים פוקוס רק אם המשתמש לא ניווט בינתיים למקום אחר
  function focusIfIdle(target) {
    const a = document.activeElement;
    if (!a || a === document.body || a === $("start-btn") || $("results").contains(a)) target.focus({ preventScroll: false });
  }
  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: body ? "POST" : "GET",
        headers: body ? { "Content-Type": "application/json", "X-Content-Studio": "1" } : { "X-Content-Studio": "1" },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      showOffline();
      throw new Error("אין חיבור למנוע. הפעל את Content Studio.bat ופתח את http://127.0.0.1:8765/");
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* לא JSON */ }
    if (!res.ok || data.ok === false) throw new Error(data.error || ("שגיאה " + res.status));
    return data;
  }
  function showOffline() { $("offline-banner").hidden = false; }
  function mode() { return document.querySelector('input[name="mode"]:checked').value; }
  function wantsPost() { return mode() !== "image"; }
  function wantsImage() { return mode() !== "post"; }
  function imgMode() { return document.querySelector('input[name="imgmode"]:checked').value; }
  function fillSelect(sel, items, keyName, labelName) {
    sel.innerHTML = "";
    items.forEach((it) => {
      const o = document.createElement("option");
      o.value = it[keyName]; o.textContent = it[labelName]; sel.appendChild(o);
    });
  }
  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(new Error("הקובץ לא נקרא"));
      r.readAsDataURL(file);
    });
  }
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    });
    (children || []).forEach((c) => e.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return e;
  }

  // ---------- בריאות המנוע ----------
  async function loadHealth() {
    // בלי שרת מאחור (קובץ מקומי, או עותק סטטי ב-GitHub Pages): רק ההודעה, בלי קריאות ל-API
    const local = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
    if (location.protocol === "file:" || !local) { showOffline(); return; }
    try {
      const h = await api("/api/health");
      state.health = h;
      $("offline-banner").hidden = true;
      renderChips(h);
      fillSelect($("engine"), h.engines, "key", "label");
      fillSelect($("ratio"), h.ratios, "key", "label");
      fillSelect($("profile"), h.profiles, "key", "label");
      if (h.platforms && h.platforms.length) fillSelect($("platform"), h.platforms, "key", "label");
      if (h.video) {
        if (h.video.models) fillSelect($("video-model"), h.video.models, "key", "label");
        if (h.video.fonts) { fillSelect($("caption-font"), h.video.fonts, "key", "label"); if (h.video.default_font) $("caption-font").value = h.video.default_font; }
      }
      $("engine").addEventListener("change", updateEngineHint);
      updateEngineHint();
      $("foot-url").textContent = location.host;
    } catch (e) {
      showOffline();
    }
  }
  function renderChips(h) {
    const chips = $("status-chips");
    chips.innerHTML = "";
    const add = (label, on, title) => chips.appendChild(el("span", { class: "chip " + (on ? "on" : "off"), title: title || "" }, [
      label, el("span", { class: "chip-state", text: on ? " (נמצא)" : " (חסר)" })]));
    add("מפתח Anthropic", h.keys.anthropic, h.keys.anthropic ? "נמצא" : "חסר: הוסף ANTHROPIC_API_KEY לקובץ .env");
    add("מפתח FAL", h.keys.fal, h.keys.fal ? "נמצא" : "חסר: הוסף FAL_KEY לקובץ .env");
    add("קובץ הקול", h.sources.voice, "voice-and-style.md");
    add("style.md", h.sources.visual_style, "visual-maker/style.md");
    add(h.sources.post_writer_skill ? "post-writer: סקיל" : "post-writer: לפי קובץ הקול", true,
        h.sources.post_writer_skill ? "נמצא סקיל post-writer" : "לא נמצא סקיל post-writer במחשב; הכתיבה לפי voice-and-style.md");
    const hints = [];
    if (!h.keys.anthropic) hints.push("חסר מפתח Anthropic: כתיבה, מחקר ובניית פרומפט לא יעבדו עד שיתווסף לקובץ .env.");
    if (!h.keys.fal) hints.push("חסר מפתח FAL: יצירת תמונות לא תעבוד.");
    $("start-hint").textContent = hints.join(" ");
  }
  function updateEngineHint() {
    const h = state.health; if (!h) return;
    const e = h.engines.find((x) => x.key === $("engine").value);
    $("engine-hint").textContent = e ? e.price_hint : "";
  }

  // ---------- תצוגת הטופס ----------
  function syncModeUI() {
    show($("post-options"), wantsPost());
    const m = mode();
    $("start-btn").querySelector(".btn-label").textContent =
      m === "post" ? "כתוב פוסט" : m === "image" ? "המשך לתמונה" : "התחל";
  }
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", syncModeUI));
  document.querySelectorAll('input[name="imgmode"]').forEach((r) => r.addEventListener("change", syncImgModeUI));
  function headlineOn() { return $("headline-on").checked; }
  function cleanToo() { return headlineOn() && $("clean-too").checked; }
  function syncHeadlineUI() {
    show($("headline-options"), headlineOn());
    document.querySelectorAll(".headline-field").forEach((f) => { f.hidden = !headlineOn(); });
  }
  $("headline-on").addEventListener("change", syncHeadlineUI);
  function syncImgModeUI() {
    const car = imgMode() === "carousel";
    show($("slides-wrap"), car);
    $("prompt-btn").textContent = car ? "צור פרומפטים לקרוסלה" : "צור פרומפט לתמונה";
    $("generate-btn").textContent = car ? "צור קרוסלה" : "צור תמונה";
  }

  // ---------- התחלה ----------
  $("settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (state.busy) return;
    const topic = $("topic").value.trim();
    const content = $("content").value.trim();
    if (!topic && !content) {
      $("topic").focus();
      $("start-hint").textContent = "כתוב לפחות נושא או תוכן.";
      return;
    }
    show($("empty-state"), false);
    closeHistory();
    state.post = null; state.prompt = null; state.images = []; state.consistencyUrl = null;
    state.refs = []; renderRefs();
    resetStep("text"); resetStep("prompt"); resetStep("image"); resetVideo();
    show($("step-text"), wantsPost());
    show($("step-prompt"), wantsImage());
    show($("step-image"), false);
    // ריצה חדשה בהיסטוריה
    state.runId = null; state.runCost = 0; renderRunCost();
    try {
      const r = await api("/api/runs", {
        topic, content, mode: mode(), platform: $("platform").value,
        research: document.querySelector('input[name="research"]:checked').value === "on",
      });
      state.runId = r.run.id;
    } catch (e) {
      setStatus(wantsPost() ? $("text-status") : $("prompt-status"), "ההיסטוריה לא נשמרת: " + e.message, "error");
    }
    if (wantsPost()) {
      await writePost(false);
    } else {
      announce("אפשר לבחור הגדרות לתמונה וללחוץ על יצירת פרומפט.");
      $("engine").focus();
    }
  });

  function resetStep(which) {
    if (which === "text") {
      setStatus($("text-status"), ""); show($("text-body"), false); show($("research-box"), false);
      $("post-text").value = ""; $("text-meta").textContent = ""; $("copy-ok").textContent = "";
      show($("enforcement-note"), false);
    } else if (which === "prompt") {
      setStatus($("prompt-status"), ""); $("prompt-items").innerHTML = ""; show($("prompt-actions"), false);
      show($("prompt-summary"), false); $("prompt-meta").textContent = "";
    } else {
      setStatus($("image-status"), ""); $("image-grid").innerHTML = ""; show($("image-actions"), false);
      $("image-meta").textContent = "";
    }
  }

  // ---------- שלב 1: טקסט ----------
  async function writePost(regenerate) {
    const research = document.querySelector('input[name="research"]:checked').value === "on";
    const platform = $("platform").value;
    const body = {
      topic: $("topic").value.trim(), content: $("content").value.trim(), platform, research,
      run_id: state.runId,
    };
    if (regenerate && state.post) {
      body.previous = $("post-text").value;
      if (state.post.research) { body.research_brief = state.post.research.brief; body.research_sources = state.post.research.sources; }
    }
    setBusy(true, research && !body.research_brief ? "חוקר..." : "כותב...");
    setStatus($("text-status"), research && !body.research_brief
      ? "חוקר את הנושא באינטרנט ואז כותב. זה יכול לקחת דקה עד שלוש."
      : "כותב את הפוסט בקול שלך...", "busy");
    try {
      const r = await api("/api/write", body);
      state.post = { text: r.text, research: r.research || null, platform };
      $("post-text").value = r.text;
      show($("text-body"), true);
      setStatus($("text-status"), "");
      const platLabel = $("platform").selectedOptions[0].textContent;
      $("text-meta").textContent = `${platLabel} · ${r.text.length} תווים · ${r.model}${costTag(r.cost)}`;
      addRunCost(r.cost);
      const enf = r.enforcement || {};
      if (enf.changed) {
        $("enforcement-note").textContent =
          `המנוע אכף את הכללים: ${enf.emojis_removed} אימוג'ים הוסרו, ${enf.dashes_replaced} קווים מפרידים הוחלפו.`;
        show($("enforcement-note"), true);
      }
      if (r.research) renderResearch(r.research);
      syncVideoStep();
      announce("הפוסט מוכן. אפשר לערוך, להעתיק או לנסח מחדש.");
      focusIfIdle($("post-text"));
    } catch (e) {
      setStatus($("text-status"), e.message, "error");
    } finally {
      setBusy(false);
    }
  }
  function renderResearch(res) {
    $("research-brief").textContent = res.brief || "";
    const ul = $("research-sources"); ul.innerHTML = "";
    (res.sources || []).forEach((s) => {
      ul.appendChild(el("li", {}, [el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.title || s.url })]));
    });
    $("research-count").textContent = res.sources && res.sources.length ? `(${res.sources.length} מקורות)` : "";
    show($("research-box"), true);
  }
  $("rewrite-btn").addEventListener("click", () => { if (!state.busy) writePost(true); });
  // עריכה ידנית של הפוסט נשמרת להיסטוריה
  $("post-text").addEventListener("change", async () => {
    if (!state.runId) return;
    try { await api(`/api/runs/${state.runId}/post`, { text: $("post-text").value }); }
    catch (e) { announceError("העריכה לא נשמרה: " + e.message); }
  });
  $("copy-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("post-text").value);
      $("copy-ok").textContent = "הועתק.";
      setTimeout(() => { $("copy-ok").textContent = ""; }, 2500);
    } catch (e) {
      $("post-text").select();
      $("copy-ok").textContent = "סמן והעתק ידנית (Ctrl+C).";
    }
  });

  // ---------- רפרנסים ----------
  $("refs").addEventListener("change", async (ev) => {
    const files = Array.from(ev.target.files || []);
    ev.target.value = "";
    for (const f of files) {
      const li = el("li", {}, [el("span", { text: "מעלה..." }), el("span", { class: "ref-name", text: f.name }), el("span")]);
      $("ref-list").appendChild(li);
      state.uploading += 1; syncPromptBtn();
      try {
        const data = await fileToBase64(f);
        const r = await api("/api/upload-ref", { name: f.name, data, run_id: state.runId });
        state.refs.push(r.ref);
        addRunCost(r.cost);
        renderRefs();
      } catch (e) {
        li.remove();
        setStatus($("prompt-status"), "העלאת רפרנס נכשלה: " + e.message, "error");
      } finally {
        state.uploading -= 1; syncPromptBtn();
      }
    }
  });
  function renderRefs() {
    const ul = $("ref-list"); ul.innerHTML = "";
    state.refs.forEach((r, i) => {
      ul.appendChild(el("li", {}, [
        el("img", { src: r.url, alt: "" }),
        el("div", {}, [
          el("div", { class: "ref-name", text: r.name }),
          el("div", { class: "ref-desc", text: r.description || r.note || "" }),
        ]),
        el("button", { type: "button", class: "btn btn-small", text: "הסר", "aria-label": "הסר רפרנס " + r.name,
          onclick: () => { state.refs.splice(i, 1); renderRefs(); } }),
      ]));
    });
  }

  // ---------- שלב 2: פרומפט ----------
  $("prompt-btn").addEventListener("click", () => buildPrompt(false));
  $("reprompt-btn").addEventListener("click", () => buildPrompt(true));

  async function buildPrompt(regenerate) {
    if (state.busy) return;
    const car = imgMode() === "carousel";
    const body = {
      mode: car ? "carousel" : "single",
      slides: parseInt($("slides").value, 10),
      topic: $("topic").value.trim(),
      content: $("content").value.trim(),
      post_text: wantsPost() && state.post ? $("post-text").value : null,
      platform: wantsPost() ? $("platform").value : null,
      profile: $("profile").value,
      ratio: $("ratio").value,
      engine: $("engine").value,
      ref_ids: state.refs.map((r) => r.id),
      instructions: $("img-instructions").value.trim() || null,
      previous: regenerate && state.prompt ? collectItems().map((it) => ({ index: it.index, prompt_en: it.prompt_en })) : null,
      run_id: state.runId,
      text_in_image: headlineOn(),
    };
    if (!body.topic && !body.content && !body.post_text) {
      setStatus($("prompt-status"), "חסר נושא או תוכן לבניית הפרומפט.", "error"); return;
    }
    setBusy(true, "בונה פרומפט...");
    setStatus($("prompt-status"), car ? "בונה רצף פרומפטים לקרוסלה לפי style.md..." : "בונה פרומפט לפי style.md...", "busy");
    $("prompt-items").innerHTML = ""; show($("prompt-actions"), false);
    try {
      const r = await api("/api/visual-prompt", body);
      state.prompt = r.prompt;
      renderPromptItems(r.prompt);
      $("prompt-meta").textContent += costTag(r.cost);
      addRunCost(r.cost);
      setStatus($("prompt-status"), "");
      announce("הפרומפט מוכן. אפשר לערוך ואז ליצור.");
    } catch (e) {
      setStatus($("prompt-status"), e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function renderPromptItems(p) {
    const sum = $("prompt-summary");
    const parts = [];
    if (p.summary_he) parts.push(p.summary_he);
    if (p.light_mode_he) parts.push("מצב אור: " + p.light_mode_he);
    if (p.notes_he) parts.push(p.notes_he);
    sum.textContent = parts.join(" · ");
    show(sum, parts.length > 0);
    $("prompt-meta").textContent = `פרופיל: ${p.profile_name_he || p.profile_key || ""}`;

    const wrap = $("prompt-items"); wrap.innerHTML = "";
    p.items.forEach((it) => {
      const i = it.index;
      const he = el("textarea", { id: `he-${i}`, rows: 5 });
      he.value = it.description_he || "";
      const en = el("textarea", { id: `en-${i}`, rows: 7, dir: "ltr", lang: "en" });
      en.value = it.prompt_en || "";
      const title = el("input", { id: `title-${i}`, type: "text" });
      title.value = it.title_he || "";
      const alt = el("input", { id: `alt-${i}`, type: "text" });
      alt.value = it.alt_he || "";
      const headline = el("input", { id: `headline-${i}`, type: "text", maxlength: "60" });
      headline.value = it.headline_he || "";
      const translateBtn = el("button", { type: "button", class: "btn btn-small", text: "עדכן את האנגלית לפי העברית",
        onclick: async () => {
          if (state.busy) return;
          setBusy(true, "מתרגם...");
          translateBtn.textContent = "מתרגם...";
          try {
            const r = await api("/api/visual-translate", {
              description_he: he.value, ratio: $("ratio").value,
              profile_name_he: state.prompt && state.prompt.profile_name_he, prompt_en_before: en.value,
            });
            en.value = r.prompt_en; addRunCost(r.cost); announce("הפרומפט באנגלית עודכן.");
          } catch (e) { setStatus($("prompt-status"), e.message, "error"); }
          finally { translateBtn.textContent = "עדכן את האנגלית לפי העברית"; setBusy(false); }
        } });
      const unit = p.items.length > 1 ? `סלייד ${i}` : "תמונה";
      const item = el("div", { class: "prompt-item", "data-index": i }, [
        el("h4", {}, [el("span", { class: "n", text: unit }),
          el("label", { for: `title-${i}`, class: "visually-hidden", text: `כותרת ${unit}` }), title]),
        el("div", { class: "grid-2" }, [
          el("div", { class: "field" }, [el("label", { for: `he-${i}`, text: `תיאור בעברית (מה ייראה ב${unit})` }), he,
            el("div", { class: "row" }, [translateBtn])]),
          el("div", { class: "field" }, [el("label", { for: `en-${i}`, text: `הפרומפט שנשלח למנוע (אנגלית), ${unit}` }), en]),
        ]),
        el("div", { class: "field headline-field", hidden: "" }, [
          el("label", { for: `headline-${i}`, text: `כותרת בתוך התמונה (עד 6 מילים), ${unit}` }), headline,
          el("p", { class: "hint", text: "נשלחת למנוע כטקסט מדויק, בשליש העליון של התמונה. אפשר לערוך." })]),
        el("div", { class: "field" }, [el("label", { for: `alt-${i}`, text: `תיאור alt (לנגישות), ${unit}` }), alt]),
      ]);
      item._fields = { title, he, en, alt, headline, index: i };
      wrap.appendChild(item);
    });
    if (p.text_in_image && !headlineOn()) { $("headline-on").checked = true; }
    syncHeadlineUI();
    show($("prompt-actions"), true);
  }
  function collectItems() {
    return Array.from($("prompt-items").querySelectorAll(".prompt-item")).map((n) => ({
      index: n._fields.index, title_he: n._fields.title.value.trim(), description_he: n._fields.he.value.trim(),
      prompt_en: n._fields.en.value.trim(), alt_he: n._fields.alt.value.trim(),
      headline_he: headlineOn() ? n._fields.headline.value.trim() : "",
    }));
  }

  // ---------- שלב 3: תמונה ----------
  $("generate-btn").addEventListener("click", () => generateAll());
  $("regen-all-btn").addEventListener("click", () => generateAll());

  async function generateAll() {
    if (state.busy) return;
    const items = collectItems();
    if (!items.length || items.some((it) => !it.prompt_en)) {
      setStatus($("prompt-status"), "יש פריט בלי פרומפט באנגלית.", "error"); return;
    }
    const car = items.length > 1;
    show($("step-image"), true);
    $("step-image").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
    $("image-grid").innerHTML = ""; state.images = []; state.consistencyUrl = null; show($("image-actions"), false);
    const cards = items.map((it) => makeCard(it, car));
    cards.forEach((c) => $("image-grid").appendChild(c.root));
    setBusy(true, "יוצר...");
    const engineLabel = $("engine").selectedOptions[0].textContent;
    $("image-meta").textContent = `${engineLabel} · ${$("ratio").value} · ${$("quality").selectedOptions[0].textContent}`;
    const consistency = car && $("consistency").checked;
    let failedFirst = false;
    for (let i = 0; i < items.length; i++) {
      setStatus($("image-status"), car ? `יוצר סלייד ${i + 1} מתוך ${items.length}...` : "יוצר את התמונה...", "busy");
      const ok = await generateOne(items[i], cards[i], car, consistency);
      if (!ok && i === 0 && consistency) { failedFirst = true; break; }
    }
    if (failedFirst) {
      setStatus($("image-status"), "סלייד 1 נכשל, ולכן שאר הסליידים לא נוצרו כדי לשמור על עקביות. נסה שוב את סלייד 1 ואז 'צור מחדש'.", "error");
    } else {
      setStatus($("image-status"), "");
      announce(car ? "הקרוסלה מוכנה." : "התמונה מוכנה.");
    }
    show($("image-actions"), true);
    $("regen-all-btn").textContent = car ? "צור קרוסלה חדשה" : "צור תמונה חדשה";
    setBusy(false);
  }

  function makeCard(it, car) {
    const frame = el("div", { class: "frame", text: "ממתין..." });
    const titleTxt = car ? `סלייד ${it.index}${it.title_he ? ": " + it.title_he : ""}` : (it.title_he || "תמונה");
    const meta = el("div", { class: "card-meta" });
    const actions = el("div", { class: "actions" });
    const root = el("div", { class: "image-card" }, [frame, el("div", { class: "card-body" }, [
      el("div", { class: "card-title", text: titleTxt }), meta, actions])]);
    return { root, frame, meta, actions, it };
  }

  function makeRerun(it, card, car, consistency) {
    return async (label) => {
      if (state.busy) return;
      setBusy(true, "יוצר...");
      setStatus($("image-status"), label, "busy");
      const latest = collectItems().find((x) => x.index === it.index) || it;
      await generateOne(latest, card, car, consistency);
      setStatus($("image-status"), "");
      setBusy(false);
    };
  }

  // ממלא כרטיס בתמונה קיימת (אחרי יצירה או בטעינה מההיסטוריה)
  // img: הגרסה הראשית (עם כותרת אם ביקשו); clean: גרסה נקייה בלי טקסט, אם נוצרה
  function fillCard(card, it, img, car, consistency, cost, clean) {
    const rerun = makeRerun(it, card, car, consistency);
    state.images[it.index - 1] = img;
    // רפרנס עקביות לקרוסלה: הגרסה הנקייה של סלייד 1 עדיפה, כדי שהכותרת לא תזלוג לסליידים הבאים
    if (it.index === 1) state.consistencyUrl = (clean && clean.url) || img.url;
    card.frame.innerHTML = "";
    card.frame.appendChild(el("img", { src: img.url, alt: it.alt_he || it.title_he || "" }));
    card.meta.innerHTML = "";
    card.meta.appendChild(el("bdi", { dir: "ltr", text: `${img.width || "?"}×${img.height || "?"}` }));
    const usd = cost && typeof cost.usd === "number" ? cost.usd : (typeof img.usd === "number" ? img.usd : null);
    card.meta.appendChild(document.createTextNode(` · ${img.seconds} ש'${img.edit_mode ? " · מצב עריכה (רפרנס)" : ""}${usd !== null ? " · ≈ " + fmtUsd(usd) : ""}`));
    if (img.headline_he) card.meta.appendChild(el("div", { text: `כותרת בתמונה: „${img.headline_he}". בדוק אות-אות.` }));
    card.actions.innerHTML = "";
    card.actions.appendChild(el("a", { class: "btn btn-link btn-small", href: img.url, download: img.filename || "image.png", text: img.headline_he ? "הורד עם כותרת" : "הורד תמונה" }));
    if (clean && clean.url) {
      card.actions.appendChild(el("a", { class: "btn btn-link btn-small", href: clean.url, download: clean.filename || "image-clean.png", text: "הורד בלי טקסט" }));
    }
    card.actions.appendChild(el("button", { type: "button", class: "btn btn-small", text: "צור מחדש",
      onclick: () => rerun(car ? `יוצר מחדש סלייד ${it.index}...` : "יוצר מחדש את התמונה...") }));
    card.actions.querySelectorAll("button").forEach((b) => { b.disabled = state.busy; });
  }

  // consistency: בקרוסלה, סליידים 2 ומעלה מקבלים את סלייד 1 העדכני כרפרנס (נקרא בזמן הלחיצה)
  async function generateOne(it, card, car, consistency) {
    card.frame.textContent = "יוצר...";
    card.actions.innerHTML = "";
    const consistencyPath = consistency && it.index > 1 ? state.consistencyUrl : null;
    const headline = headlineOn() ? (it.headline_he || "").trim() : "";
    const base = {
      prompt_en: it.prompt_en, engine: $("engine").value, ratio: $("ratio").value, quality: $("quality").value,
      ref_ids: state.refs.map((x) => x.id), consistency_path: consistencyPath,
      session: state.session, run_id: state.runId, index: it.index,
      label: ($("topic").value.trim() || "תמונה") + (car ? "-" + it.index : ""),
    };
    try {
      let clean = null, totalCost = { usd: 0, entries: [] };
      if (headline && cleanToo()) {
        // 1) גרסה נקייה; 2) אותה תמונה עם הכותרת (מצב עריכה על הגרסה הנקייה)
        card.frame.textContent = "יוצר גרסה נקייה...";
        const rc = await api("/api/image", { ...base, variant: "clean" });
        clean = rc.image; totalCost.usd += rc.cost.usd; totalCost.entries.push(...rc.cost.entries);
        card.frame.textContent = "מוסיף את הכותרת...";
        const rt = await api("/api/image", { ...base, headline_he: headline, variant: "text", base_path: clean.url });
        totalCost.usd += rt.cost.usd; totalCost.entries.push(...rt.cost.entries);
        fillCard(card, it, rt.image, car, consistency, totalCost, clean);
        addRunCost(totalCost);
        return rt.image;
      }
      const r = await api("/api/image", headline ? { ...base, headline_he: headline, variant: "text" } : base);
      fillCard(card, it, r.image, car, consistency, r.cost, null);
      addRunCost(r.cost);
      return r.image;
    } catch (e) {
      const rerun = makeRerun(it, card, car, consistency);
      card.frame.textContent = "נכשל: " + e.message;
      card.actions.appendChild(el("button", { type: "button", class: "btn btn-small", text: "נסה שוב",
        onclick: () => rerun(car ? `מנסה שוב סלייד ${it.index}...` : "מנסה שוב...") }));
      card.actions.querySelectorAll("button").forEach((b) => { b.disabled = state.busy; });
      announceError("יצירת תמונה נכשלה: " + e.message);
      return null;
    }
  }

  // ---------- היסטוריה ----------
  function fmtRunDate(id) {
    const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id || "");
    return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : id;
  }
  function labelOf(list, key) {
    const h = state.health; if (!h || !h[list]) return key;
    const x = h[list].find((i) => i.key === key); return x ? x.label : key;
  }
  function setRadio(name, value) {
    const r = document.querySelector(`input[name="${name}"][value="${value}"]`);
    if (r) { r.checked = true; r.dispatchEvent(new Event("change")); }
  }
  function openHistory() {
    show($("history"), true);
    $("history-btn").setAttribute("aria-expanded", "true");
    show($("empty-state"), false);
    refreshHistory();
    $("history").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
  }
  function closeHistory() {
    show($("history"), false);
    $("history-btn").setAttribute("aria-expanded", "false");
    if (!state.runId && $("step-text").hidden && $("step-prompt").hidden) show($("empty-state"), true);
  }
  async function refreshHistory() {
    const ul = $("history-list"); ul.innerHTML = "";
    let runs = [];
    try { runs = (await api("/api/runs")).runs || []; }
    catch (e) { ul.appendChild(el("li", { class: "hint", text: "ההיסטוריה לא נטענה: " + e.message })); return; }
    show($("history-empty"), runs.length === 0);
    $("history-count").textContent = runs.length ? `${runs.length} ריצות` : "";
    renderCostSummary();
    runs.forEach((r) => {
      const thumb = r.thumbnail ? el("img", { class: "history-thumb", src: r.thumbnail, alt: "" })
        : el("div", { class: "history-thumb", "aria-hidden": "true", text: r.has_post ? "א" : "·" });
      const metaParts = [fmtRunDate(r.id), labelOf("platforms", r.platform),
        r.mode === "post" ? "פוסט בלבד" : r.mode === "image" ? "תמונה בלבד" : "פוסט ותמונה"];
      if (r.research) metaParts.push("עם מחקר");
      if (r.has_post) metaParts.push(r.post_versions > 1 ? `${r.post_versions} גרסאות טקסט` : "טקסט");
      if (r.image_count) metaParts.push(`${r.image_count} תמונות`);
      if (r.cost_usd) metaParts.push(`≈ ${fmtUsd(r.cost_usd)}`);
      const title = el("div", { class: "history-title", text: r.topic || "(בלי נושא)" });
      if (r.id === state.runId) title.appendChild(el("span", { class: "badge", text: "הריצה הנוכחית" }));
      const li = el("li", { class: "history-item" + (r.id === state.runId ? " current" : "") }, [
        thumb,
        el("div", {}, [title, el("div", { class: "history-meta", text: metaParts.join(" · ") })]),
        el("div", { class: "actions" }, [
          el("button", { type: "button", class: "btn btn-small btn-primary", text: "פתח", "aria-label": "פתח את הריצה " + (r.topic || r.id),
            onclick: () => loadRun(r.id) }),
          el("button", { type: "button", class: "btn btn-small", text: "מחק", "aria-label": "מחק את הריצה " + (r.topic || r.id),
            onclick: () => deleteRun(r) }),
        ]),
      ]);
      ul.appendChild(li);
    });
  }
  async function renderCostSummary() {
    const p = $("history-costs");
    try {
      const c = (await api("/api/costs")).costs;
      p.innerHTML = "";
      p.appendChild(document.createTextNode(`החודש (${c.month.label}): `));
      p.appendChild(el("bdi", { dir: "ltr", text: "≈ " + fmtUsd(c.month.usd) }));
      p.appendChild(document.createTextNode(` ב-${c.month.runs} ריצות (Anthropic ${fmtUsd(c.month.anthropic)}, fal ${fmtUsd(c.month.fal)}) · סך הכול: `));
      p.appendChild(el("bdi", { dir: "ltr", text: "≈ " + fmtUsd(c.total.usd) }));
      p.appendChild(el("span", { class: "cost-tag", text: ` · הערכה לפי מחירונים מ-${c.pricing_date}; הסכום המחייב הוא בחשבונות Anthropic ו-fal` }));
      p.hidden = false;
    } catch (e) { p.hidden = true; }
  }
  async function deleteRun(r) {
    if (state.busy) return;
    if (!window.confirm(`להעביר לאשפה את הריצה "${r.topic || r.id}"? הקבצים יעברו לתיקייה output\\_trash ולא יימחקו.`)) return;
    try {
      await api(`/api/runs/${r.id}/delete`, {});
      if (state.runId === r.id) state.runId = null;
      announce("הריצה הועברה לאשפה.");
      refreshHistory();
    } catch (e) { announceError(e.message); }
  }
  async function loadRun(id) {
    if (state.busy) return;
    let run;
    try { run = (await api(`/api/runs/${id}`)).run; }
    catch (e) { announceError("הריצה לא נטענה: " + e.message); return; }
    const b = run.brief || {};
    state.runId = run.id; state.post = null; state.prompt = null; state.images = []; state.consistencyUrl = null;
    state.runCost = (run.usage || []).reduce((s, u) => s + (Number(u.usd) || 0), 0); renderRunCost();
    state.refs = (run.refs || []).map((x) => ({ id: x.id, name: x.name, url: x.url, description: x.description }));
    // טופס
    $("topic").value = b.topic || ""; $("content").value = b.content || "";
    setRadio("mode", b.mode || "both"); $("platform").value = b.platform || "linkedin";
    setRadio("research", b.research ? "on" : "off");
    syncModeUI();
    // שלבים
    show($("empty-state"), false);
    resetStep("text"); resetStep("prompt"); resetStep("image"); resetVideo();
    const car = !!(run.prompt && run.prompt.current && (run.prompt.current.items || []).length > 1);
    show($("step-text"), b.mode !== "image");
    if (run.post && run.post.current) {
      $("post-text").value = run.post.current;
      show($("text-body"), true);
      state.post = { text: run.post.current, research: run.post.research || null, platform: run.post.platform || b.platform };
      $("text-meta").textContent = `${labelOf("platforms", run.post.platform || b.platform)} · ${run.post.current.length} תווים` + (run.post.edited ? " · נערך ידנית" : "");
      if (run.post.research) renderResearch(run.post.research);
    }
    show($("step-prompt"), b.mode !== "post");
    renderRefs();
    if (run.prompt && run.prompt.current) {
      const s = run.prompt.settings || {};
      if (s.engine) $("engine").value = s.engine;
      if (s.ratio) $("ratio").value = s.ratio;
      if (s.profile) $("profile").value = s.profile;
      $("img-instructions").value = s.instructions || "";
      $("headline-on").checked = !!s.text_in_image; syncHeadlineUI();
      setRadio("imgmode", s.mode === "carousel" ? "carousel" : "single");
      if (s.slides) $("slides").value = String(s.slides);
      syncImgModeUI(); updateEngineHint();
      state.prompt = run.prompt.current;
      renderPromptItems(run.prompt.current);
    }
    const imgs = run.images || {};
    const keys = Object.keys(imgs).filter((k) => imgs[k] && imgs[k].current).sort((x, y) => +x - +y);
    if (keys.length) {
      const first = imgs[keys[0]].current;
      if (first.settings && first.settings.quality) $("quality").value = first.settings.quality;
      if (first.settings && first.settings.engine && !(run.prompt && run.prompt.settings && run.prompt.settings.engine)) {
        $("engine").value = first.settings.engine; updateEngineHint();
      }
      show($("step-image"), true);
      const items = collectItems();
      const consistency = car && $("consistency").checked;
      keys.forEach((k) => {
        const idx = +k;
        const it = items.find((x) => x.index === idx) || { index: idx, title_he: "", alt_he: "", prompt_en: (imgs[k].current.settings || {}).prompt_en || "" };
        const card = makeCard(it, car);
        $("image-grid").appendChild(card.root);
        fillCard(card, it, imgs[k].current, car, consistency, null, imgs[k].clean || null);
      });
      $("image-meta").textContent = `${labelOf("engines", (first.settings || {}).engine)} · ${(first.settings || {}).ratio || ""} · נטען מההיסטוריה`;
      show($("image-actions"), true);
      $("regen-all-btn").textContent = car ? "צור קרוסלה חדשה" : "צור תמונה חדשה";
    }
    if (run.video) restoreVideo(run.video);
    syncVideoStep();
    closeHistory();
    announce(`הריצה "${b.topic || run.id}" נטענה. אפשר להמשיך ממנה.`);
    $("results").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
  }
  $("history-btn").addEventListener("click", () => { if ($("history").hidden) openHistory(); else closeHistory(); });
  $("history-close").addEventListener("click", closeHistory);
  // עריכה ידנית של פרומפט נשמרת להיסטוריה
  let promptSaveTimer = null;
  $("prompt-items").addEventListener("change", () => {
    if (!state.runId) return;
    clearTimeout(promptSaveTimer);
    promptSaveTimer = setTimeout(async () => {
      try { await api(`/api/runs/${state.runId}/prompt`, { items: collectItems() }); }
      catch (e) { announceError("עריכת הפרומפט לא נשמרה: " + e.message); }
    }, 400);
  });

  // ---------- שלב 4: סרטון לטיקטוק ----------
  function videoReady() { return state.health && state.health.video; }
  function syncVideoStep() {
    const hasPost = !!(state.post && $("post-text").value.trim());
    const isTiktok = $("platform").value === "tiktok" && (hasPost || !!state.video.plan);
    show($("step-video"), isTiktok);
    if (!isTiktok || !videoReady()) return;
    const v = state.health.video;
    syncVideoModelHint();
    const notes = [];
    if (!v.higgsfield) notes.push("אין מפתח Higgsfield API בקובץ .env (HIGGSFIELD_KEY_ID ו-HIGGSFIELD_KEY_SECRET מ-console.higgsfield.ai). ההנפשה תדלג, והסרטון יורכב מהסטילים עם תנועת זום.");
    if (!v.ffmpeg) notes.push("ffmpeg לא נמצא במחשב: ההרכבה לא תעבוד.");
    $("video-key-note").textContent = notes.join(" ");
    show($("video-key-note"), notes.length > 0);
    $("clips-btn").title = v.higgsfield ? "" : "דורש מפתח Higgsfield API";
    syncVideoButtons();
  }
  function syncVideoModelHint() {
    const v = videoReady(); if (!v) return;
    const m = v.models.find((x) => x.key === $("video-model").value);
    $("video-model-hint").textContent = m ? m.credits_hint : "";
  }
  $("video-model").addEventListener("change", syncVideoModelHint);
  function resetVideo() {
    state.video = { plan: null, shots: {}, final: null };
    $("shot-list").innerHTML = ""; show($("video-final"), false); setStatus($("video-status"), "");
    $("video-meta").textContent = "";
    syncVideoButtons();
  }
  function shotsFromUI() {
    return Array.from($("shot-list").querySelectorAll(".shot")).map((n) => ({
      index: n._f.index, caption_he: n._f.caption.value.trim(), prompt_en: n._f.prompt.value.trim(), motion_en: n._f.motion.value.trim(),
    }));
  }
  function syncVideoButtons() {
    const hasPlan = !!(state.video.plan && $("shot-list").children.length);
    const stillsDone = hasPlan && shotsFromUI().every((s) => state.video.shots[s.index] && state.video.shots[s.index].still);
    const hf = !!(videoReady() && state.health.video.higgsfield);
    $("stills-btn").disabled = state.busy || !hasPlan;
    $("clips-btn").disabled = state.busy || !stillsDone || !hf;
    $("assemble-btn").disabled = state.busy || !stillsDone;
  }
  let planSaveTimer = null;
  function savePlanEdits() {
    if (!state.runId) return;
    clearTimeout(planSaveTimer);
    planSaveTimer = setTimeout(async () => {
      try { await api(`/api/runs/${state.runId}/video-plan`, { shots: shotsFromUI() }); }
      catch (e) { announceError("עריכת התוכנית לא נשמרה: " + e.message); }
    }, 400);
  }
  $("shot-list").addEventListener("change", savePlanEdits);

  function renderShots(shots) {
    const wrap = $("shot-list"); wrap.innerHTML = "";
    shots.forEach((s) => {
      const i = s.index;
      const caption = el("input", { id: `shot-cap-${i}`, type: "text", maxlength: "80" }); caption.value = s.caption_he || "";
      const prompt = el("textarea", { id: `shot-prompt-${i}`, rows: 4, dir: "ltr", lang: "en" }); prompt.value = s.prompt_en || "";
      const motion = el("input", { id: `shot-motion-${i}`, type: "text", dir: "ltr", lang: "en" }); motion.value = s.motion_en || "";
      const frame = el("div", { class: "frame", text: "עדיין אין סטיל" });
      const clipFrame = el("div", { class: "frame", text: "עדיין אין קליפ" });
      const actions = el("div", { class: "actions" });
      const node = el("div", { class: "shot" }, [
        el("div", {}, [
          el("h4", {}, [el("span", { class: "n", text: `שוט ${i}` })]),
          el("div", { class: "field" }, [el("label", { for: `shot-cap-${i}`, text: "כיתוב מסך" }), caption]),
          el("div", { class: "field" }, [el("label", { for: `shot-prompt-${i}`, text: "פרומפט הסטיל (אנגלית)" }), prompt]),
          el("div", { class: "field" }, [el("label", { for: `shot-motion-${i}`, text: "תנועה (אנגלית, להנפשה)" }), motion]),
          actions,
        ]),
        el("div", { class: "media" }, [frame, clipFrame]),
      ]);
      node._f = { index: i, caption, prompt, motion, frame, clipFrame, actions };
      wrap.appendChild(node);
      renderShotActions(node);
    });
  }
  function shotNode(i) { return Array.from($("shot-list").querySelectorAll(".shot")).find((n) => n._f.index === i); }
  function renderShotActions(node) {
    const i = node._f.index; const st = state.video.shots[i] || {};
    node._f.actions.innerHTML = "";
    node._f.actions.appendChild(el("button", { type: "button", class: "btn btn-small", text: st.still ? "צור סטיל מחדש" : "צור סטיל",
      onclick: () => runGuarded(() => makeStill(i), "יוצר סטיל...") }));
    if (st.still) {
      const animBtn = el("button", { type: "button", class: "btn btn-small", text: st.clip ? "הנפש מחדש" : "הנפש",
        onclick: () => runGuarded(() => makeClip(i), "מנפיש...") });
      animBtn.disabled = !(videoReady() && state.health.video.higgsfield);
      if (animBtn.disabled) animBtn.title = "דורש מפתח Higgsfield API";
      node._f.actions.appendChild(animBtn);
      node._f.actions.appendChild(el("a", { class: "btn btn-link btn-small", href: st.still.url, download: st.still.filename || "still.png", text: "הורד סטיל" }));
    }
    if (st.clip) node._f.actions.appendChild(el("a", { class: "btn btn-link btn-small", href: st.clip.url, download: "clip.mp4", text: "הורד קליפ" }));
  }
  function fillStill(node, img) {
    node._f.frame.innerHTML = ""; node._f.frame.appendChild(el("img", { src: img.url, alt: "" }));
  }
  function fillClip(node, clip) {
    node._f.clipFrame.innerHTML = "";
    node._f.clipFrame.appendChild(el("video", { src: clip.url, controls: "", muted: "", playsinline: "", preload: "metadata" }));
  }
  async function runGuarded(fn, label) {
    if (state.busy) return;
    setBusy(true, label);
    try { await fn(); }
    catch (e) {
      const msg = /אין חיבור/.test(e.message)
        ? e.message + " אם הפעולה כבר התחילה, התוצאה נשמרת בריצה: פתח אותה מחדש מההיסטוריה."
        : e.message;
      setStatus($("video-status"), msg, "error");
    }
    finally { setBusy(false); syncVideoButtons(); }
  }

  async function planShots() {
    setStatus($("video-status"), "ממיר את חבילת הטיקטוק לתוכנית שוטים...", "busy");
    const prof = $("profile").value === "auto" ? "personal" : $("profile").value;
    const r = await api("/api/video/plan", { run_id: state.runId, post_text: $("post-text").value, profile: prof });
    state.video.plan = r.plan; state.video.shots = {};
    renderShots(r.plan.shots);
    addRunCost(r.cost);
    $("video-meta").textContent = `${r.plan.shots.length} שוטים${costTag(r.cost)}`;
    setStatus($("video-status"), "");
    announce("תוכנית השוטים מוכנה. אפשר לערוך ואז ליצור סטילים.");
  }
  async function makeStill(i) {
    const node = shotNode(i); const s = shotsFromUI().find((x) => x.index === i);
    node._f.frame.textContent = "יוצר סטיל...";
    const r = await api("/api/video/still", { run_id: state.runId, index: i, prompt_en: s.prompt_en, engine: $("engine").value, quality: $("quality").value });
    state.video.shots[i] = Object.assign(state.video.shots[i] || {}, { still: r.image, clip: null });
    node._f.clipFrame.textContent = "עדיין אין קליפ";
    fillStill(node, r.image); renderShotActions(node); addRunCost(r.cost);
  }
  async function makeClip(i) {
    const node = shotNode(i); const s = shotsFromUI().find((x) => x.index === i);
    node._f.clipFrame.textContent = "Higgsfield מנפיש... (דקה עד שלוש)";
    const r = await api("/api/video/clip", { run_id: state.runId, index: i, motion_en: s.motion_en, model: $("video-model").value, duration: parseInt($("shot-seconds").value, 10) });
    state.video.shots[i].clip = r.clip;
    fillClip(node, r.clip); renderShotActions(node); addRunCost(r.cost);
  }
  async function makeStills() {
    const shots = shotsFromUI();
    for (const s of shots) {
      setStatus($("video-status"), `יוצר סטיל ${s.index} מתוך ${shots.length}...`, "busy");
      await makeStill(s.index);
    }
    setStatus($("video-status"), ""); announce("הסטילים מוכנים.");
  }
  async function makeClips() {
    const shots = shotsFromUI();
    for (const s of shots) {
      setStatus($("video-status"), `מנפיש שוט ${s.index} מתוך ${shots.length} ב-Higgsfield...`, "busy");
      await makeClip(s.index);
    }
    setStatus($("video-status"), ""); announce("הקליפים מוכנים.");
  }
  async function assembleVideo() {
    setStatus($("video-status"), "מרכיב את הסרטון עם הכיתובים (ffmpeg)...", "busy");
    const r = await api("/api/video/assemble", { run_id: state.runId, seconds_per_still: parseInt($("shot-seconds").value, 10), captions: $("video-captions").checked, font: $("caption-font").value || undefined });
    state.video.final = r.final;
    showFinal(r.final);
    setStatus($("video-status"), ""); announce("הסרטון מוכן.");
  }
  function showFinal(f) {
    $("final-video").src = f.url;
    $("final-download").href = f.url; $("final-download").download = f.url.split("/").pop();
    const parts = [];
    if (f.duration) parts.push(`${f.duration} שניות`);
    parts.push(`${f.segments} שוטים`);
    if (f.shots_with_clip) parts.push(`${f.shots_with_clip} מונפשים ב-Higgsfield`);
    if (f.shots_from_still) parts.push(`${f.shots_from_still} עם זום על סטיל`);
    parts.push("1080×1920");
    $("final-meta").textContent = parts.join(" · ");
    show($("video-final"), true);
  }
  function restoreVideo(v) {
    if (!v || !v.plan || !(v.plan.shots || []).length) return;
    state.video.plan = v.plan;
    renderShots(v.plan.shots);
    Object.entries(v.shots || {}).forEach(([k, s]) => {
      const i = +k; const node = shotNode(i); if (!node) return;
      state.video.shots[i] = { still: s.still || null, clip: s.clip || null };
      if (s.still) fillStill(node, s.still);
      if (s.clip) fillClip(node, s.clip);
      renderShotActions(node);
    });
    if (v.final) { state.video.final = v.final; showFinal(v.final); }
    $("video-meta").textContent = `${v.plan.shots.length} שוטים · נטען מההיסטוריה`;
    syncVideoButtons();
  }
  $("plan-btn").addEventListener("click", () => runGuarded(planShots, "מתכנן..."));
  $("stills-btn").addEventListener("click", () => runGuarded(makeStills, "יוצר סטילים..."));
  $("clips-btn").addEventListener("click", () => runGuarded(makeClips, "מנפיש..."));
  $("assemble-btn").addEventListener("click", () => runGuarded(assembleVideo, "מרכיב..."));
  $("video-all-btn").addEventListener("click", () => runGuarded(async () => {
    if (!state.video.plan) await planShots();
    await makeStills();
    if (videoReady() && state.health.video.higgsfield) await makeClips();
    await assembleVideo();
  }, "מפיק סרטון..."));
  $("platform").addEventListener("change", syncVideoStep);

  // ---------- אתחול ----------
  syncModeUI();
  syncImgModeUI();
  loadHealth();
})();
