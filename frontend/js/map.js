/**
 * map.js — Leaflet map with symptom report markers.
 * Loads on index.html.
 */

const GREECE_CENTER = [38.5, 24.0];

const map = L.map("map").setView(GREECE_CENTER, 6);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 18,
}).addTo(map);

function severityColor(s) {
  if (s <= 4) return "#43a047";
  if (s <= 8) return "#fb8c00";
  return "#e53935";
}

function renderHeatmap(geojson) {
  geojson.features.forEach(({ geometry, properties }) => {
    const [lon, lat] = geometry.coordinates;
    const color = severityColor(properties.severity);
    L.circleMarker([lat, lon], {
      radius: 8,
      fillColor: color,
      color: "#fff",
      weight: 1,
      opacity: 0.9,
      fillOpacity: 0.7,
    })
      .bindPopup(`Severity: <strong>${properties.severity}</strong> / 12`)
      .addTo(map);
  });
}

async function init() {
  try {
    const geojson = await API.getHeatmap(30);
    renderHeatmap(geojson);
  } catch (err) {
    console.warn("Could not load heatmap data:", err.message);
  }
}

init();
