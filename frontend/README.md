# frontend

Plain HTML/CSS/JS frontend for AllergyMap. No build step -- served as static
files (nginx in Docker, or `python -m http.server` locally).

## Pages

| Page             | Purpose                                             |
|-------------------|------------------------------------------------------|
| `index.html`       | Leaflet map (symptom report locations / allergen heatmap) |
| `report.html`       | Symptom report form (VAS 0-12 sliders, geolocation)    |
| `dashboard.html`     | Environmental charts (Chart.js) + AI pollen forecast, with a relative "Days" window or a custom date-range picker |

## JS (`js/`)

| File            | Purpose                                                    |
|------------------|--------------------------------------------------------------|
| `api.js`          | Thin `fetch` wrapper around the backend API (single source of truth for all endpoint URLs) |
| `map.js`           | Leaflet map + allergen heatmap layer                          |
| `report.js`         | Form sliders, geolocation, submission                          |
| `dashboard.js`      | Chart.js charts, date-range picker wiring, AI forecast section |

`window.ALLERGYMAP_API_BASE` (unset by default, falls back to
`http://localhost:5000`) lets you point the frontend at a different backend
without editing `api.js` -- set it via a `<script>` tag before `api.js` loads.

## Design system (`css/style.css`)

A clinical dark theme: white/near-white text on a deep navy background, teal
accent, generous whitespace, single type/spacing scale via CSS custom
properties (`:root` block at the top of the file).

**Contrast:** every text/background pairing actually used in the stylesheet
was checked against WCAG 2.1 AA (4.5:1 for normal text, 3:1 for large
text/UI) using the standard relative-luminance formula -- all pass, several
at AAA (7:1+). One non-obvious result worth keeping in mind if you touch the
palette: **white text on the bright teal accent fails contrast (1.9:1)** --
buttons use dark navy text on teal (`--text-on-accent`, 9.5:1) instead.
Status badges (`.form-msg`, `.info-box.error`) use the `-dark` status
variants as the *background* with light text on top, not the bright variant.

**Chart.js:** the library's own defaults (dark tick/legend text, light grid
lines) are invisible on a dark page. `dashboard.js` reads
`--text-secondary`/`--border` from the CSS custom properties at load time
and applies them as `Chart.defaults.color`/`Chart.defaults.borderColor`, so
the chart theme can never drift from the CSS theme.

**Leaflet map tiles** are left in their natural OpenStreetMap colors
(real-world map imagery needs color fidelity); only the surrounding chrome
(navbar, legend card) follows the clinical theme.

## Navbar

Single shared markup block (`<nav class="navbar">`, copy-pasted identically
across the three pages -- there's no templating layer) and a single style
block in `css/style.css`. `.navbar a.active` marks the current page.
