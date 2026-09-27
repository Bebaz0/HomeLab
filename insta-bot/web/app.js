"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const DECISIONS = {
  "reply": ["Respondeu", "live"],
  "cooldown": ["Cooldown", "wait"],
  "paused": ["Pausa", "wait"],
  "ignored:self": ["Própria", "off"],
  "ignored:laugh": ["Riso", "off"],
  "ignored:no-trigger": ["Sem gatilho", "off"],
  "ignored:long": ["Longa", "off"],
};

// Which config keys each card owns, so saving one card never clobbers another.
const CARDS = {
  hours: ["start", "end", "cooldown_minutes"],
  replies: ["replies"],
  triggers: ["triggers", "short_triggers", "max_words", "max_words_short"],
};

let cfg = null;              // last config from the server
const draft = {};            // list edits not yet saved
let busy = false;

// ---------- helpers ----------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body || {});
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data.errors || [data.error || res.statusText]).join(" · "));
  return data;
}

let toastTimer;
function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isErr ? 6000 : 2500);
}

const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
const dayHhmm = (ts) => {
  const d = new Date(ts * 1000), today = new Date();
  const time = hhmm(ts);
  return d.toDateString() === today.toDateString() ? time
    : `${d.toLocaleDateString("pt-PT", { day: "2-digit", month: "2-digit" })} ${time}`;
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter((c) => c != null));
  return node;
}

function setDirty(card, dirty) {
  const section = $(`[data-card="${card}"]`);
  $(".dirty", section).hidden = !dirty;
  section.dataset.dirty = dirty ? "1" : "";
}
const isDirty = (card) => $(`[data-card="${card}"]`).dataset.dirty === "1";

// ---------- status ----------

function renderStatus(s) {
  const badge = $("#status-badge");
  let label, cls, line;
  if (s.stopped) {
    [label, cls, line] = ["Parado", "err", "Parou por segurança. Precisa de ti."];
  } else if (s.running) {
    [label, cls, line] = ["Em jogo", "live", "O Chrome está aberto na conversa e a ouvir."];
  } else if (!s.enabled) {
    [label, cls, line] = ["Desligado", "off", "Não arranca até o ligares."];
  } else if (s.in_window) {
    [label, cls, line] = ["A entrar", "wait", "Ligado: arranca no próximo tick do timer (≤ 5 min)."];
  } else {
    const next = s.next_start ? new Date(s.next_start) : null;
    [label, cls, line] = ["Em espera", "wait",
      next ? `Ligado. Próxima sessão às ${next.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" })}.` : "Ligado."];
  }
  badge.textContent = label;
  badge.className = `badge big ${cls}`;
  $("#status-line").textContent = line;
  $("#window-line").textContent = `Janela ${s.start} → ${s.end}` + (s.dry_run ? " · modo treino" : " · envia a sério");

  const power = $("#power");
  power.disabled = busy;
  power.classList.toggle("on", !s.enabled);
  power.classList.toggle("off", s.enabled);
  $("#power-label").textContent = s.enabled ? "Desligar" : "Ligar";

  $("#dry-run").checked = s.dry_run;

  $("#stopped").hidden = !s.stopped;
  $("#stopped-reason").textContent = s.stopped || "";

  const left = s.cooldown_left;
  $("#cooldown-text").textContent = left > 0
    ? `Cooldown: ${Math.ceil(left / 60)} min`
    : "Sem cooldown · pronto a responder";
  $("#reset-cooldown").disabled = left <= 0;
}

async function refreshStatus() {
  try {
    renderStatus(await api("GET", "/api/status"));
  } catch (e) {
    $("#status-badge").textContent = "Sem ligação";
    $("#status-badge").className = "badge big err";
  }
}

// ---------- config cards ----------

function renderList(key) {
  const ul = $(`[data-list="${key}"]`);
  ul.replaceChildren();
  const items = draft[key];
  items.forEach((value, i) => {
    const input = el("input", { type: "text", maxlength: "100", "aria-label": `Item ${i + 1}` });
    input.value = value;
    input.addEventListener("input", () => {
      draft[key][i] = input.value;
      input.setAttribute("aria-invalid", String(!input.value.trim()));
      setDirty(cardOf(key), true);
    });
    const del = el("button", { type: "button", class: "icon-btn", "aria-label": `Apagar “${value}”`, title: "Apagar", text: "×" });
    del.addEventListener("click", () => {
      draft[key].splice(i, 1);
      renderList(key);
      setDirty(cardOf(key), true);
    });
    ul.append(el("li", {}, input, del));
  });
  if (!items.length) ul.append(el("li", { class: "empty", text: "Lista vazia: adiciona pelo menos um." }));
}

const cardOf = (key) => Object.keys(CARDS).find((c) => CARDS[c].includes(key));

function renderConfig(force = false) {
  if (force || !isDirty("hours")) {
    $("#start").value = cfg.start;
    $("#end").value = cfg.end;
    $("#cooldown").value = cfg.cooldown_minutes;
    setDirty("hours", false);
  }
  if (force || !isDirty("triggers")) {
    $("#max-words").value = cfg.max_words;
    $("#max-words-short").value = cfg.max_words_short;
  }
  for (const key of ["replies", "triggers", "short_triggers"]) {
    if (force || !isDirty(cardOf(key))) {
      draft[key] = [...cfg[key]];
      renderList(key);
      setDirty(cardOf(key), false);
    }
  }
}

function collect(card) {
  if (card === "hours") {
    return { start: $("#start").value, end: $("#end").value, cooldown_minutes: Number($("#cooldown").value) };
  }
  if (card === "replies") return { replies: draft.replies.map((s) => s.trim()) };
  return {
    triggers: draft.triggers.map((s) => s.trim()),
    short_triggers: draft.short_triggers.map((s) => s.trim()),
    max_words: Number($("#max-words").value),
    max_words_short: Number($("#max-words-short").value),
  };
}

async function save(card) {
  const btn = $(`[data-save="${card}"]`);
  btn.disabled = true;
  try {
    cfg = await api("PUT", "/api/config", collect(card));
    setDirty(card, false);
    renderConfig();
    // Re-render this card from the server copy (trimmed values etc.).
    for (const key of CARDS[card]) if (key in draft) { draft[key] = [...cfg[key]]; renderList(key); }
    toast("Guardado. O bot usa isto já na próxima mensagem.");
    refreshStatus();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------- feeds ----------

function renderRecent(rows) {
  const ul = $("#recent");
  ul.replaceChildren();
  if (!rows.length) { ul.append(el("li", { class: "empty", text: "Ainda não respondeu a ninguém." })); return; }
  for (const r of rows) {
    ul.append(el("li", {},
      el("span", { class: "t", text: dayHhmm(r.ts) }),
      el("span", { class: "reply" }, r.reply, r.dry_run ? el("span", { class: "badge dry", text: "Treino", style: "margin-left:8px" }) : null),
      el("span", { class: "ctx", text: `a ${r.sender}: “${r.text}”` }),
    ));
  }
}

function renderHistory(rows) {
  const tbody = $("#history");
  tbody.replaceChildren();
  if (!rows.length) {
    tbody.append(el("tr", {}, el("td", { colspan: "4", class: "empty", text: "Sem mensagens registadas ainda." })));
    return;
  }
  for (const r of rows) {
    const [label, cls] = DECISIONS[r.decision] || [r.decision, "off"];
    const msg = el("td", {}, r.text, r.reply ? el("span", { class: "sent", text: r.reply + (r.dry_run ? " (treino)" : "") }) : null);
    tbody.append(el("tr", { class: r.reply ? "hit" : "" },
      el("td", { text: dayHhmm(r.ts) }),
      el("td", { text: r.sender === "me" ? "tu (bot)" : r.sender }),
      msg,
      el("td", {}, el("span", { class: `badge ${cls}`, text: label })),
    ));
  }
}

async function refreshFeeds() {
  try {
    const [replies, history] = await Promise.all([api("GET", "/api/replies?limit=15"), api("GET", "/api/history?limit=100")]);
    renderRecent(replies);
    renderHistory(history);
  } catch { /* status badge already shows connectivity problems */ }
}

// ---------- actions ----------

async function action(path, okMsg) {
  busy = true;
  $("#power").disabled = true;
  try {
    renderStatus(await api("POST", path));
    if (okMsg) toast(okMsg);
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    refreshStatus();
  }
}

$("#power").addEventListener("click", () => {
  const turningOff = $("#power").classList.contains("off");
  if (turningOff) action("/api/bot/off", "Desligado. O Chrome foi fechado.");
  else action("/api/bot/on", "Ligado. Arranca já se estiver dentro do horário.");
});

$("#dry-run").addEventListener("change", async (e) => {
  try {
    cfg = await api("PUT", "/api/config", { dry_run: e.target.checked });
    toast(e.target.checked ? "Modo treino: só regista, não envia." : "Atenção: agora envia respostas a sério.");
    refreshStatus();
  } catch (err) {
    e.target.checked = !e.target.checked;
    toast(err.message, true);
  }
});

$("#reset-cooldown").addEventListener("click", () => action("/api/cooldown/reset", "Cooldown limpo. Responde à próxima."));
$("#clear-stopped").addEventListener("click", () => action("/api/stopped/clear", "Erro limpo. O timer volta a arrancar o bot."));

for (const btn of $$("[data-save]")) btn.addEventListener("click", () => save(btn.dataset.save));

for (const form of $$("[data-add]")) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const key = form.dataset.add;
    const input = $("input", form);
    const value = input.value.trim();
    if (!value) return;
    draft[key].push(value);
    input.value = "";
    renderList(key);
    setDirty(cardOf(key), true);
  });
}

for (const id of ["#start", "#end", "#cooldown"]) {
  $(id).addEventListener("input", (e) => {
    setDirty("hours", true);
    e.target.setAttribute("aria-invalid", String(!e.target.checkValidity()));
  });
}
for (const id of ["#max-words", "#max-words-short"]) $(id).addEventListener("input", () => setDirty("triggers", true));

window.addEventListener("beforeunload", (e) => {
  if (Object.keys(CARDS).some(isDirty)) e.preventDefault();
});

function tickClock() {
  $("#clock").textContent = new Date().toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
}

// ---------- boot ----------

(async () => {
  tickClock();
  setInterval(tickClock, 10_000);
  try {
    cfg = await api("GET", "/api/config");
    renderConfig(true);
  } catch (e) {
    toast("Não consegui carregar a configuração: " + e.message, true);
  }
  await Promise.all([refreshStatus(), refreshFeeds()]);
  setInterval(() => { refreshStatus(); refreshFeeds(); }, 10_000);
})();
