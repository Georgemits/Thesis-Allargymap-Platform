/**
 * api.js — thin fetch wrapper for the AllergyMap backend.
 * Exposes a global `API` object used by map.js, report.js, dashboard.js.
 *
 * Requests that concern the participant's own data pass `identified: true`,
 * which attaches the anonymous device identity from identity.js as the
 * `X-Device-Id` header. Everything else — environmental readings, forecasts,
 * the public map — is sent without it, so the backend never sees which
 * participant is *reading* the open data. That also avoids a CORS preflight
 * on the map's per-city fetches, since a request with no custom header is a
 * simple one. Load identity.js before this file.
 */
const API = (() => {
  const BASE = window.ALLERGYMAP_API_BASE || "http://localhost:5000";

  async function request(path, options = {}) {
    const { identified = false, headers: extraHeaders, ...rest } = options;

    const headers = { "Content-Type": "application/json", ...extraHeaders };
    if (identified && typeof Identity !== "undefined") {
      headers["X-Device-Id"] = Identity.getDeviceId();
    }

    const res = await fetch(BASE + path, { headers, ...rest });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const error = new Error(body.error || `HTTP ${res.status}`);
      error.status = res.status;
      throw error;
    }
    return res.json();
  }

  return {
    /** Submit a symptom report, attributed to this device. */
    submitReport: (payload) =>
      request("/api/reports/", {
        method: "POST",
        identified: true,
        body: JSON.stringify(payload),
      }),

    /**
     * Register this device with the backend, or refresh its last-seen time.
     * Idempotent — safe to call on every page load.
     * @param {string} [alias] optional short label, e.g. "phone"
     */
    registerDevice: (alias) =>
      request("/api/users/me", {
        method: "POST",
        identified: true,
        body: JSON.stringify(alias === undefined ? {} : { alias }),
      }),

    /** Read this device's identity record. Rejects with status 404 when unknown. */
    getMe: () => request("/api/users/me", { identified: true }),

    /**
     * Erase this participant server-side (identity + allergy profile).
     * @param {boolean} [deleteReports=false] also delete the symptom reports
     *   instead of unlinking them from the participant.
     */
    eraseMe: (deleteReports = false) =>
      request(`/api/users/me${deleteReports ? "?delete_reports=true" : ""}`, {
        method: "DELETE",
        identified: true,
      }),

    /** Fetch latest env snapshot per city. */
    getLatestEnv: () =>
      request("/api/env/latest"),

    /** Fetch time series for a city over a relative window (last `days` days). */
    getCityTimeseries: (city, days = 7) =>
      request(`/api/env/city/${encodeURIComponent(city)}?days=${days}`),

    /**
     * Fetch time series for a city over an explicit date range.
     * startDate/endDate: "YYYY-MM-DD" strings (inclusive).
     */
    getCityTimeseriesRange: (city, startDate, endDate) =>
      request(
        `/api/env/city/${encodeURIComponent(city)}` +
          `?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`
      ),

    /** Provider date-range limits (Google Pollen ~5-day forecast, Open-Meteo forecast/past/archive), for clamping the date picker. */
    getCapabilities: () => request("/api/env/capabilities"),

    /** Fetch recent reports for a city. */
    getCityReports: (city, limit = 200) =>
      request(`/api/reports?city=${encodeURIComponent(city)}&limit=${limit}`),

    /** Fetch the latest stored 7-day AI forecast for a city (all variables). */
    getPredictions: (city) =>
      request(`/api/predictions/${encodeURIComponent(city)}`),

    /** Fetch the latest stored AI forecast for a city, filtered to an explicit date range. */
    getPredictionsRange: (city, startDate, endDate) =>
      request(
        `/api/predictions/${encodeURIComponent(city)}` +
          `?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`
      ),

    /**
     * Trigger ML training + forecast for a city.
     * source: "csv" | "mongo"  (default "csv")
     */
    runPrediction: (city, source = "csv") =>
      request("/api/predictions/run", {
        method: "POST",
        body: JSON.stringify({ city, source }),
      }),
  };
})();
