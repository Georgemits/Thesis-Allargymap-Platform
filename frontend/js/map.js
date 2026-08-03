/**
 * map.js — Leaflet allergen-concentration heatmap.
 * Loads on index.html.
 *
 * Replaces the earlier per-report severity circleMarkers with a real
 * heatmap (Leaflet.heat) keyed by pollen/dust concentration, built from the
 * latest env_snapshots document per city (GET /api/env/latest). A layer
 * toggle switches which allergen the heatmap is keyed by.
 *
 * Data is only as granular as the ten predefined Greek cities in
 * data_collection/open_meteo_fetcher.py -- Leaflet.heat's blur turns those
 * ten points into a smooth-looking heatmap, which reads well but is not a
 * true continuous field; the legend note on the page says so.
 */

const GREECE_CENTER = [38.5, 24.0];

const map = L.map("map").setView(GREECE_CENTER, 6);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 18,
}).addTo(map);

// Allergen definitions: display label, unit, and how to read the value off
// an env_snapshots document (see backend/app/models/env_snapshot.py).
const ALLERGENS = {
  olive:   { label: "Olive pollen",   unit: "grains/m³", get: (d) => d.pollen?.olive_pollen },
  grass:   { label: "Grass pollen",   unit: "grains/m³", get: (d) => d.pollen?.grass_pollen },
  ragweed: { label: "Ragweed pollen", unit: "grains/m³", get: (d) => d.pollen?.ragweed_pollen },
  dust:    { label: "Saharan dust",   unit: "μg/m³",     get: (d) => d.air_quality?.dust },
};

const HEAT_GRADIENT = { 0.4: "#2563eb", 0.6: "#0ea5e9", 0.75: "#22d3ee", 0.9: "#facc15", 1.0: "#ef4444" };

let heatLayer = null;
let envDocs = [];

/** [lat, lon, intensity] triples for one allergen, skipping cities with no reading. */
function buildHeatPoints(allergenKey) {
  const { get } = ALLERGENS[allergenKey];
  return envDocs
    .map((doc) => {
      const value = get(doc);
      const coords = doc.location?.coordinates; // GeoJSON [lon, lat]
      if (value == null || !coords) return null;
      const [lon, lat] = coords;
      return [lat, lon, value];
    })
    .filter(Boolean);
}

function updateLegend(allergenKey, max) {
  const { unit } = ALLERGENS[allergenKey];
  document.getElementById("legendMax").textContent = `${max.toFixed(1)} ${unit}`;
}

function renderAllergenLayer(allergenKey) {
  const points = buildHeatPoints(allergenKey);
  const max = Math.max(1, ...points.map((p) => p[2]));

  if (heatLayer) map.removeLayer(heatLayer);
  heatLayer = L.heatLayer(points, {
    radius: 65,
    blur: 45,
    maxZoom: 8,
    max,
    minOpacity: 0.35,
    gradient: HEAT_GRADIENT,
  }).addTo(map);

  updateLegend(allergenKey, max);
}

document.getElementById("allergenSelect").addEventListener("change", (e) => {
  renderAllergenLayer(e.target.value);
});

async function init() {
  try {
    envDocs = await API.getLatestEnv();
  } catch (err) {
    console.warn("Could not load environmental data:", err.message);
    envDocs = [];
  }
  renderAllergenLayer(document.getElementById("allergenSelect").value);
}

init();
