/* ==========================================================================
   CBSRMS – shared front-end behaviour
   - mobile sidebar toggle
   - confirm dialogs on destructive forms  (<form data-confirm="Are you sure?">)
   - live notification badge + dropdown (polls the server)
   - live shipment record updates (polls the server, swaps changed sections)
   - "new changes" banner on the shipment list
   ========================================================================== */
(function () {
  "use strict";

  const body = document.body;
  const pollMs = (parseInt(body.dataset.poll, 10) || 8) * 1000;

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function getJSON(url) {
    const res = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  // Poll only while the tab is visible (saves server load).
  function every(ms, fn) {
    const run = () => Promise.resolve().then(fn).catch(() => { /* network blip: try again next tick */ });
    run();
    setInterval(() => { if (!document.hidden) run(); }, ms);
  }

  // ---- mobile sidebar -------------------------------------------------------
  const toggle = document.getElementById("sidebar-toggle");
  if (toggle) toggle.addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));

  // ---- confirm dialogs ------------------------------------------------------
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset && e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // ---- auto-dismiss success/info alerts --------------------------------------
  setTimeout(() => {
    document.querySelectorAll(".messages .alert-success, .messages .alert-info").forEach((el) => {
      if (window.bootstrap) bootstrap.Alert.getOrCreateInstance(el).close();
    });
  }, 6000);

  // ---- notifications --------------------------------------------------------
  const notifUrl = body.dataset.notificationsUrl;
  const ICONS = {
    STALE: "bi-clock-history", OVERDUE: "bi-alarm", AWAITING_POD: "bi-patch-question", NO_WAYBILL: "bi-file-earmark-x",
    DUPLICATE: "bi-files", DELIVERED: "bi-check-circle", FAILED: "bi-exclamation-octagon", ASSIGNED: "bi-person-check", SYSTEM: "bi-info-circle",
  };
  let lastUnread = null;

  async function refreshNotifications() {
    const data = await getJSON(notifUrl);
    document.querySelectorAll("[data-unread-badge]").forEach((b) => {
      b.textContent = data.unread;
      b.classList.toggle("d-none", data.unread === 0);
    });
    const list = document.getElementById("notif-list");
    if (list) {
      list.innerHTML = data.items.length
        ? data.items.map((n) => `<a class="notif-item ${n.is_read ? "" : "unread"}" href="${n.url}">
            <i class="bi ${ICONS[n.kind] || "bi-bell"} fs-5 text-secondary"></i>
            <div><div>${esc(n.message)}</div><div class="text-muted small">${esc(n.created)}</div></div></a>`).join("")
        : '<div class="text-muted small p-3">No alerts. All clear.</div>';
    }
    if (lastUnread !== null && data.unread > lastUnread) flashBanner("You have a new alert.", "warning");
    lastUnread = data.unread;
  }
  if (notifUrl) every(Math.max(pollMs * 3, 20000), refreshNotifications);

  // ---- banner ---------------------------------------------------------------
  function flashBanner(text, kind) {
    const host = document.getElementById("live-banner");
    if (!host) return;
    host.innerHTML = `<div class="alert alert-${kind || "info"} alert-dismissible fade show py-2" role="alert">
      <i class="bi bi-broadcast"></i> ${esc(text)}
      <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
  }
  window.CBSRMS = { getJSON, every, flashBanner, esc, pollMs };

  // ---- live shipment record -------------------------------------------------
  // The detail page exposes #live-root[data-state-url] and an initial state JSON.
  // When another user changes the record (status, documents, edits, POD) the
  // read-only sections marked [data-live="..."] are refreshed in place, without
  // touching any form the current user may be typing in.
  const root = document.getElementById("live-root");
  if (root) {
    let known = JSON.parse(document.getElementById("initial-state").textContent);
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

    every(pollMs, async function poll() {
      const state = await getJSON(root.dataset.stateUrl);
      if (same(state, known)) return;
      known = state;
      const html = await (await fetch(location.href, { credentials: "same-origin" })).text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      doc.querySelectorAll("[data-live]").forEach((fresh) => {
        const current = document.querySelector(`[data-live="${fresh.dataset.live}"]`);
        if (current && current.innerHTML !== fresh.innerHTML) {
          current.innerHTML = fresh.innerHTML;
          current.classList.remove("live-flash");
          void current.offsetWidth;
          current.classList.add("live-flash");
        }
      });
      flashBanner("This record was just updated by another user. The latest data is shown.", "info");
    });
  }

  // ---- shipment list: "new changes" banner ----------------------------------
  const listRoot = document.getElementById("list-root");
  if (listRoot) {
    let baseline = null;
    every(pollMs * 2, async function () {
      const pulse = await getJSON(listRoot.dataset.pulseUrl);
      const sig = pulse.count + "|" + pulse.latest;
      if (baseline === null) baseline = sig;
      else if (sig !== baseline) {
        const host = document.getElementById("live-banner");
        host.innerHTML = `<div class="alert alert-info py-2 d-flex justify-content-between align-items-center">
          <span><i class="bi bi-broadcast"></i> Shipment records changed.</span>
          <a class="btn btn-sm btn-primary" href="${location.href}">Refresh list</a></div>`;
      }
    });
  }
})();
