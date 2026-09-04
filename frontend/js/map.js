/**
 * map.js — Leaflet allergen-concentration heatmap.
 * Loads on index.html.
 *
 * Replaces the earlier per-report severity circleMarkers with a real
 * heatmap (Leaflet.heat) keyed by pollen/dust concentration. A layer
 * toggle switches which allergen the heatmap is keyed by.
 *
 * Data source: GET /api/env/city/<city>?days=7 per city, not
 * GET /api/env/latest. The single "latest" row per city is often the very
 * edge of the fetched forecast window, where Open-Meteo's pollen/dust
 * models have no data yet (pollen forecasts are only reliable a few days
 * out) -- so "latest" is frequently null for every allergen at once. Instead
 * each city's last 7 days are fetched once and, per allergen, the most
 * recent *non-null* reading is used.
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

const HISTORY_DAYS = 7;
// Concentration is a magnitude, so the ramp is a single hue running light to
// dark -- the brand blue, deepening with the reading. The previous
// blue-cyan-yellow-red rainbow implied categories that do not exist and left
// colourblind readers unable to order the middle of the scale.
const HEAT_GRADIENT = { 0.2: "#cfe2f3", 0.4: "#8dbde4", 0.6: "#4a90cc", 0.8: "#1F6FB2", 1.0: "#004380" };

let heatLayer = null;
// { cityName: { coords: [lon, lat], docs: [envSnapshot, ...] (ascending by timestamp) } }
let cityHistories = {};

/** Walk a city's history backwards and return the most recent non-null value for `get`. */
function mostRecentValue(docs, get) {
  for (let i = docs.length - 1; i >= 0; i--) {
    const value = get(docs[i]);
    if (value != null) return value;
  }
  return null;
}

/** [lat, lon, intensity] triples for one allergen, skipping cities with no recent reading. */
function buildHeatPoints(allergenKey) {
  const { get } = ALLERGENS[allergenKey];
  return Object.values(cityHistories)
    .map(({ coords, docs }) => {
      const value = mostRecentValue(docs, get);
      if (value == null || !coords) return null;
      const [lon, lat] = coords;
      return [lat, lon, value];
    })
    .filter(Boolean);
}

function updateLegend(allergenKey, points, max) {
  const { unit, label } = ALLERGENS[allergenKey];
  const statusEl = document.getElementById("legendStatus");
  const gradientEl = document.getElementById("legendGradientWrap");

  if (!points.length) {
    statusEl.textContent = `No recent ${label.toLowerCase()} readings for any city (last ${HISTORY_DAYS} days).`;
    statusEl.classList.remove("hidden");
    gradientEl.classList.add("hidden");
    return;
  }

  if (max === 0) {
    // Real, confirmed readings -- not missing data -- every city just
    // reported zero (e.g. olive pollen well outside its April-June season).
    // Distinct from the "no data at all" case above: don't imply a heat
    // layer is showing something when every value is truly zero.
    statusEl.textContent = `All cities report 0 ${unit} ${label.toLowerCase()} right now -- nothing to show on the heatmap.`;
    statusEl.classList.remove("hidden");
    gradientEl.classList.add("hidden");
    return;
  }

  statusEl.classList.add("hidden");
  gradientEl.classList.remove("hidden");
  document.getElementById("legendMax").textContent = `${max.toFixed(1)} ${unit}`;
}

function renderAllergenLayer(allergenKey) {
  const points = buildHeatPoints(allergenKey);
  const max = points.length ? Math.max(...points.map((p) => p[2])) : 0;

  if (heatLayer) map.removeLayer(heatLayer);
  heatLayer = null;

  // Skip rendering a layer entirely when there's nothing (or only zeros) to
  // show -- with minOpacity a heat layer would otherwise paint a visible
  // blob even for confirmed-zero data, which contradicts a "0.0" legend.
  if (max > 0) {
    heatLayer = L.heatLayer(points, {
      radius: 50,
      blur: 30,
      maxZoom: 8,
      max,
      gradient: HEAT_GRADIENT,
    }).addTo(map);
  }

  updateLegend(allergenKey, points, max);
}

document.getElementById("allergenSelect").addEventListener("change", (e) => {
  renderAllergenLayer(e.target.value);
});

/** Fetch each city's recent history once; the allergen toggle then just re-reads this cache. */
async function loadCityHistories() {
  let latest = [];
  try {
    latest = await API.getLatestEnv();
  } catch (err) {
    console.warn("Could not load city list:", err.message);
    return;
  }

  const entries = await Promise.all(
    latest.map(async (doc) => {
      try {
        const docs = await API.getCityTimeseries(doc.city, HISTORY_DAYS);
        return [doc.city, { coords: doc.location?.coordinates, docs }];
      } catch (err) {
        console.warn(`Could not load history for ${doc.city}:`, err.message);
        return [doc.city, { coords: doc.location?.coordinates, docs: [] }];
      }
    })
  );
  cityHistories = Object.fromEntries(entries);
}

async function init() {
  await loadCityHistories();
  renderAllergenLayer(document.getElementById("allergenSelect").value);
}

init();
