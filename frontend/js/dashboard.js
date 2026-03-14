/**
 * dashboard.js — Chart.js charts for env data and report severity.
 * Loads on dashboard.html.
 */

let pollenChart, aqiChart, weatherChart, severityChart;

function destroyAll() {
  [pollenChart, aqiChart, weatherChart, severityChart].forEach((c) => c?.destroy());
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

async function loadDashboard() {
  const city = document.getElementById("citySelect").value;
  const days = parseInt(document.getElementById("daysSelect").value, 10);
  destroyAll();

  try {
    const docs = await API.getCityTimeseries(city, days);
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
}

document.getElementById("loadBtn").addEventListener("click", loadDashboard);
loadDashboard();
