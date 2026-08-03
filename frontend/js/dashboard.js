/**
 * dashboard.js — Chart.js charts for env data and report severity.
 * Loads on dashboard.html.
 */

let pollenChart, aqiChart, weatherChart, severityChart, forecastPollenChart, forecastWeatherChart;

function destroyAll() {
  [pollenChart, aqiChart, weatherChart, severityChart].forEach((c) => c?.destroy());
}

function destroyForecast() {
  forecastPollenChart?.destroy();
  forecastWeatherChart?.destroy();
}

function labels(docs) {
  return docs.map((d) => new Date(d.timestamp).toLocaleDateString("el-GR"));
}

function buildPollenChart(docs) {
  const ctx = document.getElementById("pollenChart");
  const pollenTypes = ["grass_pollen", "olive_pollen", "ragweed_pollen", "birch_pollen", "alder_pollen", "mugwort_pollen"];
  const colors = ["#43a047", "#8d6e63", "#ef9a9a", "#90caf9", "#ffe082", "#ce93d8"];
  pollenChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels(docs),
      datasets: pollenTypes.map((key, i) => ({
        label: key.replace("_pollen", "").replace("_", " "),
        data: docs.map((d) => d.pollen?.[key] ?? null),
        borderColor: colors[i],
        backgroundColor: colors[i] + "33",
        tension: 0.3,
        fill: false,
        spanGaps: true,
      })),
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

function buildAqiChart(docs) {
  const ctx = document.getElementById("aqiChart");
  aqiChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels(docs),
      datasets: [
        { label: "European AQI", data: docs.map((d) => d.air_quality?.european_aqi ?? null), backgroundColor: "#42a5f5" },
        { label: "PM10", data: docs.map((d) => d.air_quality?.pm10 ?? null), backgroundColor: "#ef9a9a" },
        { label: "PM2.5", data: docs.map((d) => d.air_quality?.pm2_5 ?? null), backgroundColor: "#ce93d8" },
      ],
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });
}

function buildWeatherChart(docs) {
  const ctx = document.getElementById("weatherChart");
  weatherChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels(docs),
      datasets: [
        {
          label: "Temperature (°C)",
          data: docs.map((d) => d.weather?.temperature_2m ?? null),
          borderColor: "#ef5350", backgroundColor: "#ef535033",
          yAxisID: "yTemp", tension: 0.3, fill: false, spanGaps: true,
        },
        {
          label: "Humidity (%)",
          data: docs.map((d) => d.weather?.relative_humidity_2m ?? null),
          borderColor: "#29b6f6", backgroundColor: "#29b6f633",
          yAxisID: "yHum", tension: 0.3, fill: false, spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      scales: {
        yTemp: { type: "linear", position: "left", title: { display: true, text: "°C" } },
        yHum:  { type: "linear", position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "%" } },
      },
      plugins: { legend: { position: "bottom" } },
    },
  });
}

async function buildSeverityChart(city) {
  const ctx = document.getElementById("severityChart");
  try {
    const reports = await API.getCityReports(city, 200);
    // Bucket by day
    const byDay = {};
    reports.forEach((r) => {
      const day = r.timestamp?.slice(0, 10) ?? "unknown";
      if (!byDay[day]) byDay[day] = [];
      byDay[day].push(r.overall_severity);
    });
    const days = Object.keys(byDay).sort();
    const avgs = days.map((d) => {
      const vals = byDay[d];
      return +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2);
    });
    severityChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: days,
        datasets: [{ label: "Avg Severity", data: avgs, backgroundColor: "#66bb6a" }],
      },
      options: {
        responsive: true,
        scales: { y: { min: 0, max: 12 } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  } catch {
    severityChart = new Chart(ctx, {
      type: "bar",
      data: { labels: [], datasets: [{ label: "No data", data: [] }] },
      options: { responsive: true },
    });
  }
}

// ---------------------------------------------------------------------------
// Custom date range (task: "custom date range for pollen forecasts")
// ---------------------------------------------------------------------------

const startDateInput = document.getElementById("startDateInput");
const endDateInput = document.getElementById("endDateInput");
const rangeHintEl = document.getElementById("rangeHint");

/** Read the active custom range, or null if either field is empty. */
function getActiveRange() {
  const start = startDateInput.value;
  const end = endDateInput.value;
  return start && end ? { start, end } : null;
}

document.getElementById("clearRangeBtn").addEventListener("click", () => {
  startDateInput.value = "";
  endDateInput.value = "";
  rangeHintEl.textContent = "";
  loadDashboard();
});

/** Clamp the date pickers to what the providers can realistically populate. */
async function initCapabilityHints() {
  try {
    const caps = await API.getCapabilities();
    const today = new Date();
    const toISO = (d) => d.toISOString().slice(0, 10);

    const minDate = new Date(today);
    minDate.setDate(minDate.getDate() - caps.open_meteo.max_past_days);
    const maxDate = new Date(today);
    maxDate.setDate(maxDate.getDate() + caps.open_meteo.forecast_days);

    [startDateInput, endDateInput].forEach((el) => {
      el.min = toISO(minDate);
      el.max = toISO(maxDate);
    });

    rangeHintEl.textContent =
      `Pollen forecast: Google covers the next ${caps.google_pollen.max_forecast_days} days (primary); ` +
      `Open-Meteo covers ${caps.open_meteo.max_past_days} days back to ${caps.open_meteo.forecast_days} days ` +
      `ahead (fallback + history). Dates outside Google's window use Open-Meteo automatically.`;
  } catch (err) {
    console.warn("Could not load provider capabilities:", err.message);
  }
}

async function loadDashboard() {
  const city = document.getElementById("citySelect").value;
  const range = getActiveRange();
  destroyAll();

  if (range && range.end < range.start) {
    rangeHintEl.textContent = "End date must not be before start date.";
    return;
  }

  try {
    const docs = range
      ? await API.getCityTimeseriesRange(city, range.start, range.end)
      : await API.getCityTimeseries(city, parseInt(document.getElementById("daysSelect").value, 10));
    buildPollenChart(docs);
    buildAqiChart(docs);
    buildWeatherChart(docs);
  } catch (err) {
    console.warn("Env data unavailable:", err.message);
    // Render empty charts so the page still loads
    ["pollenChart", "aqiChart", "weatherChart"].forEach((id) => {
      new Chart(document.getElementById(id), {
        type: "line",
        data: { labels: [], datasets: [{ label: "No data available", data: [] }] },
        options: { responsive: true },
      });
    });
  }

  await buildSeverityChart(city);
  await loadForecast(city);
}

document.getElementById("loadBtn").addEventListener("click", loadDashboard);
initCapabilityHints();
loadDashboard();

// ---------------------------------------------------------------------------
// AI Forecast section
// ---------------------------------------------------------------------------

/**
 * Build Chart.js labels from ISO timestamp strings, showing date + hour.
 * To keep the x-axis readable, show a label only every 24th point (daily tick).
 */
function forecastLabels(forecasts) {
  return forecasts.map((f, i) => {
    const d = new Date(f.forecast_timestamp);
    // Show "Mar 15" label once per day; blank otherwise
    return d.getHours() === 0
      ? d.toLocaleDateString("en-GB", { month: "short", day: "numeric" })
      : "";
  });
}

function buildForecastPollenChart(variables) {
  destroyForecast();
  const ctx = document.getElementById("forecastPollenChart");

  const datasets = [];
  const palettePollen = { grass_pollen: "#43a047", olive_pollen: "#8d6e63" };

  for (const [varName, color] of Object.entries(palettePollen)) {
    const series = variables[varName];
    if (!series || !series.length) continue;
    datasets.push({
      label: varName === "grass_pollen" ? "Grass Pollen" : "Olive Pollen",
      data: series.map((f) => ({ x: f.forecast_timestamp, y: f.predicted_value })),
      borderColor: color,
      backgroundColor: color + "33",
      tension: 0.3,
      fill: false,
      pointRadius: 0,
    });
  }

  if (!datasets.length) return;

  forecastPollenChart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      parsing: false,
      plugins: {
        legend: { position: "bottom" },
        title: { display: true, text: "7-Day Pollen Forecast (grains/m³)" },
        tooltip: {
          callbacks: {
            title: (items) => new Date(items[0].raw.x).toLocaleString("en-GB"),
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: "day", displayFormats: { day: "MMM d" } },
          title: { display: true, text: "Date" },
        },
        y: { min: 0, title: { display: true, text: "grains/m³" } },
      },
    },
  });

  // Weather forecast chart (temperature + humidity)
  buildForecastWeatherChart(variables);
}

function buildForecastWeatherChart(variables) {
  const ctx = document.getElementById("forecastWeatherChart");
  const datasets = [];

  const tempSeries = variables["temperature_2m"];
  const humSeries  = variables["relative_humidity_2m"];

  if (tempSeries?.length) {
    datasets.push({
      label: "Temperature (°C)",
      data: tempSeries.map((f) => ({ x: f.forecast_timestamp, y: f.predicted_value })),
      borderColor: "#ef5350", backgroundColor: "#ef535033",
      yAxisID: "yTemp", tension: 0.3, fill: false, pointRadius: 0,
    });
  }
  if (humSeries?.length) {
    datasets.push({
      label: "Humidity (%)",
      data: humSeries.map((f) => ({ x: f.forecast_timestamp, y: f.predicted_value })),
      borderColor: "#29b6f6", backgroundColor: "#29b6f633",
      yAxisID: "yHum", tension: 0.3, fill: false, pointRadius: 0,
    });
  }

  if (!datasets.length) return;

  forecastWeatherChart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      parsing: false,
      plugins: {
        legend: { position: "bottom" },
        title: { display: true, text: "7-Day Temperature & Humidity Forecast" },
        tooltip: {
          callbacks: {
            title: (items) => new Date(items[0].raw.x).toLocaleString("en-GB"),
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: "day", displayFormats: { day: "MMM d" } },
          title: { display: true, text: "Date" },
        },
        yTemp: {
          type: "linear", position: "left",
          title: { display: true, text: "°C" },
        },
        yHum: {
          type: "linear", position: "right",
          grid: { drawOnChartArea: false },
          title: { display: true, text: "%" },
        },
      },
    },
  });
}

function setForecastStatus(msg, isError = false) {
  const el = document.getElementById("forecastStatus");
  el.textContent = msg;
  el.className = "info-box" + (isError ? " error" : "");
  el.classList.remove("hidden");
}

async function loadForecast(city) {
  setForecastStatus("Loading forecast…");
  const range = getActiveRange();
  try {
    const data = range
      ? await API.getPredictionsRange(city, range.start, range.end)
      : await API.getPredictions(city);
    if (!data.variables || !Object.keys(data.variables).length) {
      setForecastStatus("No predictions stored yet. Click 'Run Prediction' to generate.");
      return;
    }
    document.getElementById("forecastStatus").classList.add("hidden");
    buildForecastPollenChart(data.variables);
  } catch (err) {
    // 404 = not yet generated; any other error is a real failure
    if (err.message.includes("404") || err.message.includes("No predictions")) {
      setForecastStatus("No predictions stored yet. Click 'Run Prediction' to generate.");
    } else {
      setForecastStatus("Could not load forecast: " + err.message, true);
    }
  }
}

document.getElementById("runPredictionBtn").addEventListener("click", async () => {
  const city = document.getElementById("citySelect").value;
  const btn = document.getElementById("runPredictionBtn");
  btn.disabled = true;
  btn.textContent = "Running…";
  setForecastStatus(`Training model for ${city}… this may take a few seconds.`);
  try {
    await API.runPrediction(city, "csv");
    setForecastStatus(`Model trained. Loading forecast for ${city}…`);
    await loadForecast(city);
  } catch (err) {
    setForecastStatus("Prediction failed: " + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Prediction";
  }
});

// Load any existing forecast when the city selector changes
document.getElementById("citySelect").addEventListener("change", () => {
  loadForecast(document.getElementById("citySelect").value);
});

// Note: initial forecast load happens via loadDashboard() at the top of this
// file, which calls loadForecast() itself after the env charts are drawn.
