// Injected into the thread page. Watches the conversation for new message rows and hands
// them to Python via window.onNewMessage({sender, text}).
//
// ponytail: Instagram's DOM has no stable ids and the class names are obfuscated, so this
// only relies on roles, aria-labels and dir="auto" text nodes. Observed structure (2026-09):
//   <main> ... <div role="group">                       one message row
//                 <div role="group" aria-label="Message actions">
//                   <div role="button" aria-label="Reagir à mensagem de <username>">
//                 <div dir="auto">text</div>
// Selectors are overridable from .env (CONTAINER_SELECTOR / ROW_SELECTOR).
//
// Instagram re-renders and virtualizes rows, so element identity means nothing. Instead
// each scan diffs the (sender, text) list against the previous one: whatever comes after
// the previous tail is new. If the tail can't be found, or too much looks new at once
// (reconnect burst, full re-render), we resync silently rather than answer stale messages.
(cfg) => {
  if (window.__lolBot) window.__lolBot.disconnect();

  const container = document.querySelector(cfg.containerSelector);
  if (!container) return false;

  const SENDER = /(?:mensagem de|message from)\s+(\S+)$/i;
  const ANCHOR = 8;       // how much of the previous tail must match
  const MAX_NEW = 5;      // more than this in one scan = resync, not new messages

  const read = (row) => {
    const leaves = [...row.querySelectorAll('div[dir="auto"]')]
      .filter((el) => !el.querySelector('div[dir="auto"]'));
    const text = leaves.map((el) => el.innerText.trim()).filter(Boolean).join(" ");
    if (!text) return null;   // images, stickers, rows still rendering
    const label = [...row.querySelectorAll("[aria-label]")]
      .map((el) => el.getAttribute("aria-label").match(SENDER))
      .find(Boolean);
    if (label) return { sender: label[1], text };
    // No "react to <user>" label: fall back to geometry, own bubbles sit on the right.
    const box = row.getBoundingClientRect();
    const bubble = leaves[0].getBoundingClientRect();
    return { sender: bubble.left - box.left > box.width * 0.45 ? "me" : "other", text };
  };

  const snapshot = () => [...container.querySelectorAll(cfg.rowSelector)]
    .filter((r) => !r.closest('[role="navigation"]'))    // skip the inbox list
    .map(read).filter(Boolean);
  const key = (m) => m.sender + "\u0000" + m.text;

  // Index in cur right after the previous tail, or -1.
  const afterTail = (prev, cur) => {
    for (let k = Math.min(ANCHOR, prev.length); k > 0; k--) {
      const tail = prev.slice(-k);
      for (let i = 0; i + k <= cur.length; i++) {
        if (tail.every((t, j) => t === cur[i + j])) return i + k;
      }
    }
    return -1;
  };

  // Baseline: everything already on screen is history.
  let prev = snapshot().map(key);
  window.__lastMutation = Date.now();

  let timer = null;
  const scan = () => {
    timer = null;
    const cur = snapshot();
    const keys = cur.map(key);
    const at = prev.length ? afterTail(prev, keys) : -1;
    const fresh = at < 0 ? [] : cur.slice(at);
    if (at < 0 && keys.length) {
      window.onNewMessage({ resync: true, why: prev.length ? "tail not found" : "empty baseline" });
    } else if (fresh.length > MAX_NEW) {
      window.onNewMessage({ resync: true, why: `${fresh.length} new rows at once` });
    } else {
      fresh.forEach((m) => window.onNewMessage(m));
    }
    if (keys.length) prev = keys;
  };

  const obs = new MutationObserver(() => {
    window.__lastMutation = Date.now();
    // Debounce: a row is inserted before its text finishes rendering.
    if (!timer) timer = setTimeout(scan, 400);
  });
  obs.observe(container, { childList: true, subtree: true, characterData: true });
  window.__lolBot = obs;
  return true;
}
