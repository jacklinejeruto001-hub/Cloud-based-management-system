/* Signature pad + optional GPS capture for the delivery confirmation form. */
(function () {
  "use strict";
  const canvas = document.getElementById("sig-canvas");
  if (!canvas) return;
  const form = canvas.closest("form");
  const hidden = document.getElementById("id_signature_data");
  const clearBtn = document.getElementById("sig-clear");
  const ctx = canvas.getContext("2d");
  let drawing = false;
  let dirty = false;

  // Match the drawing buffer to the displayed size (sharp lines on retina screens).
  function resize() {
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const keep = dirty ? canvas.toDataURL() : null;
    canvas.width = rect.width * ratio;
    canvas.height = rect.height * ratio;
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 2.4;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#0f2a43";
    if (keep) { const img = new Image(); img.onload = () => ctx.drawImage(img, 0, 0, rect.width, rect.height); img.src = keep; }
  }
  resize();
  window.addEventListener("resize", resize);

  const pos = (e) => { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };

  canvas.addEventListener("pointerdown", (e) => {
    drawing = true;
    canvas.setPointerCapture(e.pointerId);
    const p = pos(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    e.preventDefault();
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    const p = pos(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    dirty = true;
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((t) => canvas.addEventListener(t, () => { drawing = false; }));

  clearBtn.addEventListener("click", () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    hidden.value = "";
    dirty = false;
  });

  // Put the drawn signature (PNG data URL) into the hidden field on submit.
  form.addEventListener("submit", () => { hidden.value = dirty ? canvas.toDataURL("image/png") : ""; });

  // Optional GPS stamp (only if the user allows it).
  const gpsBtn = document.getElementById("gps-btn");
  if (gpsBtn && navigator.geolocation) {
    gpsBtn.addEventListener("click", () => {
      gpsBtn.disabled = true;
      navigator.geolocation.getCurrentPosition(
        (p) => {
          document.getElementById("id_latitude").value = p.coords.latitude.toFixed(6);
          document.getElementById("id_longitude").value = p.coords.longitude.toFixed(6);
          document.getElementById("gps-status").textContent = `Location captured (${p.coords.latitude.toFixed(4)}, ${p.coords.longitude.toFixed(4)})`;
          gpsBtn.disabled = false;
        },
        () => { document.getElementById("gps-status").textContent = "Location unavailable."; gpsBtn.disabled = false; },
        { enableHighAccuracy: true, timeout: 10000 }
      );
    });
  } else if (gpsBtn) { gpsBtn.classList.add("d-none"); }
})();
