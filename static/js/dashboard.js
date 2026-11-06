/* Dashboard charts + live refresh of the figures (uses Chart.js from the CDN). */
(function () {
  "use strict";
  const root = document.getElementById("dashboard-root");
  if (!root || !window.Chart) return;

  const COLORS = ["#8fa3b8", "#3b82f6", "#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#9ca3af"];
  let data = JSON.parse(document.getElementById("dashboard-data").textContent);

  const statusChart = new Chart(document.getElementById("statusChart"), {
    type: "doughnut",
    data: { labels: data.status.labels, datasets: [{ data: data.status.values, backgroundColor: COLORS, borderWidth: 0 }] },
    options: { plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } }, cutout: "62%", maintainAspectRatio: false },
  });

  const dailyChart = new Chart(document.getElementById("dailyChart"), {
    type: "bar",
    data: {
      labels: data.daily.labels,
      datasets: [
        { label: "Registered", data: data.daily.created, backgroundColor: "#93c5fd", borderRadius: 4 },
        { label: "Delivered", data: data.daily.delivered, backgroundColor: "#0e9f8e", borderRadius: 4 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { grid: { display: false } } },
    },
  });

  function paint(d) {
    document.querySelectorAll("[data-card]").forEach((el) => {
      const key = el.dataset.card;
      if (d.cards[key] !== undefined) el.textContent = d.cards[key] + (el.dataset.suffix || "");
    });
    statusChart.data.datasets[0].data = d.status.values;
    statusChart.update();
    dailyChart.data.datasets[0].data = d.daily.created;
    dailyChart.data.datasets[1].data = d.daily.delivered;
    dailyChart.update();
    if (d.issues) {
      Object.entries(d.issues).forEach(([k, v]) => {
        const el = document.querySelector(`[data-issue="${k}"]`);
        if (el) el.textContent = v.count;
      });
      const ring = document.getElementById("health-ring");
      if (ring) { ring.style.setProperty("--p", d.cards.health); ring.firstElementChild.textContent = d.cards.health + "%"; }
    }
  }

  // Refresh the numbers regularly so managers always see current data.
  window.CBSRMS.every(Math.max(window.CBSRMS.pollMs * 2, 15000), async function () {
    data = await window.CBSRMS.getJSON(root.dataset.apiUrl);
    paint(data);
  });
})();
