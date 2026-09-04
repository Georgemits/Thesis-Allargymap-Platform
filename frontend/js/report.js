/**
 * report.js — Handles geolocation, slider updates, and form submission.
 * Loads on report.html, after identity.js and api.js.
 */

// ── Sliders ────────────────────────────────────────────────────────────────
// Color-code the live reading so severity is visible at a glance, not just
// as a number: 0-3 mild (success), 4-7 moderate (warn), 8-10 severe (danger).
function severityClass(value) {
  if (value <= 3) return "sev-low";
  if (value <= 7) return "sev-mid";
  return "sev-high";
}

document.querySelectorAll(".symptom-row").forEach((row) => {
  const slider = row.querySelector("input[type=range]");
  const output = row.querySelector("output");

  const update = () => {
    const value = parseInt(slider.value, 10);
    output.textContent = value;
    output.classList.remove("sev-low", "sev-mid", "sev-high");
    output.classList.add(severityClass(value));
  };

  slider.addEventListener("input", update);
  update();
});

// ── Geolocation ───────────────────────────────────────────────────────────
const statusEl = document.getElementById("locationStatus");
const latInput  = document.getElementById("lat");
const lonInput  = document.getElementById("lon");

if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      latInput.value = pos.coords.latitude.toFixed(6);
      lonInput.value = pos.coords.longitude.toFixed(6);
      statusEl.textContent = `Location set: ${latInput.value}, ${lonInput.value}`;
      statusEl.style.borderColor = "#43a047";
    },
    () => {
      statusEl.textContent = "Could not detect location. Please enter city manually.";
      statusEl.style.borderColor = "#fb8c00";
    }
  );
} else {
  statusEl.textContent = "Geolocation not supported. Please enter city manually.";
}

// ── Form submission ───────────────────────────────────────────────────────
const form   = document.getElementById("reportForm");
const msgEl  = document.getElementById("formMsg");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  msgEl.className = "form-msg hidden";

  const lat = parseFloat(latInput.value);
  const lon = parseFloat(lonInput.value);

  if (isNaN(lat) || isNaN(lon)) {
    msgEl.textContent = "Location is required. Please allow geolocation or type a city.";
    msgEl.className = "form-msg error";
    return;
  }

  const symptoms = {};
  document.querySelectorAll(".symptom-row").forEach((row) => {
    const key   = row.dataset.symptom;
    const value = parseInt(row.querySelector("input[type=range]").value, 10);
    symptoms[key] = value;
  });

  // No user_id here on purpose: the backend attributes the report to the
  // device that sent it (identity.js -> X-Device-Id header), so a client
  // cannot report on another participant's behalf.
  const payload = {
    lat,
    lon,
    city: document.getElementById("city").value.trim(),
    symptoms,
    notes: document.getElementById("notes").value.trim(),
  };

  try {
    await API.submitReport(payload);
    msgEl.textContent = "Report submitted successfully. Thank you!";
    msgEl.className = "form-msg success";
    form.reset();
    document.querySelectorAll(".symptom-row output").forEach((o) => {
      o.textContent = "0";
      o.classList.remove("sev-mid", "sev-high");
      o.classList.add("sev-low");
    });
  } catch (err) {
    msgEl.textContent = `Error: ${err.message}`;
    msgEl.className = "form-msg error";
  }
});
