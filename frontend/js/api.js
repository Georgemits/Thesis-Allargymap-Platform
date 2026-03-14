/**
 * api.js — thin fetch wrapper for the AllergyMap backend.
 * Exposes a global `API` object used by map.js, report.js, dashboard.js.
 */
const API = (() => {
  const BASE = window.ALLERGYMAP_API_BASE || "http://localhost:5000";

  async function request(path, options = {}) {
    const res = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json();
  }

  return {
    /** Submit a symptom report. */
    submitReport: (payload) =>
      request("/api/reports/", { method: "POST", body: JSON.stringify(payload) }),

    /** Fetch reports as GeoJSON heatmap. */
    getHeatmap: (days = 30) =>
      request(`/api/reports/heatmap?days=${days}`),

    /** Fetch latest env snapshot per city. */
    getLatestEnv: () =>
      request("/api/env/latest"),

    /** Fetch time series for a city. */
    getCityTimeseries: (city, days = 7) =>
      request(`/api/env/city/${encodeURIComponent(city)}?days=${days}`),

    /** Fetch recent reports for a city. */
    getCityReports: (city, limit = 200) =>
      request(`/api/reports?city=${encodeURIComponent(city)}&limit=${limit}`),
  };
})();
