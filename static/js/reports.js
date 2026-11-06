/* Status distribution chart on the management report page. */
(function () {
  "use strict";
  const el = document.getElementById("reportChart");
  if (!el || !window.Chart) return;
  const d = JSON.parse(document.getElementById("report-chart-data").textContent);
  new Chart(el, {
    type: "bar",
    data: { labels: d.labels, datasets: [{ label: "Shipments", data: d.values, backgroundColor: "#0e9f8e", borderRadius: 4 }] },
    options: {
      maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } },
    },
  });
})();
