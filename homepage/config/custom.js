// mobile tiles go 2-up; widget cards and a plain tile left without a partner take the full row.
// CSS can't see "no partner" (it needs nested :has), so mark it here. React re-renders, so keep watching.
(() => {
  const tag = () => {
    for (const list of document.querySelectorAll(".services-list")) {
      let run = [];
      const flush = () => { if (run.length % 2) run[run.length - 1].dataset.pdWide = ""; run = []; };
      for (const li of list.children) {
        delete li.dataset.pdWide;
        if (li.querySelector(".service-container, details")) { li.dataset.pdWide = ""; flush(); } else run.push(li);
      }
      flush();
    }
  };

  new MutationObserver(tag).observe(document.body, { childList: true, subtree: true });
  tag();
})();
