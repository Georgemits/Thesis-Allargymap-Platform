# frontend

Plain HTML/CSS/JS frontend for AllergyMap. No build step -- served as static
files (nginx in Docker, or `python -m http.server` locally).

## Pages

| Page             | Purpose                                             |
|-------------------|------------------------------------------------------|
| `index.html`       | Leaflet map (symptom report locations / allergen heatmap) |
| `report.html`       | Symptom report form (VAS 0-10 sliders, geolocation)    |
| `dashboard.html`     | Environmental charts (Chart.js) + AI pollen forecast, with a relative "Days" window or a custom date-range picker |

## JS (`js/`)

| File            | Purpose                                                    |
|------------------|--------------------------------------------------------------|
| `identity.js`      | Anonymous device identity (UUID v4 in `localStorage`) -- load before `api.js` |
| `api.js`          | Thin `fetch` wrapper around the backend API (single source of truth for all endpoint URLs) |
| `map.js`           | Leaflet allergen-concentration heatmap (Leaflet.heat)          |
| `navbar.js`         | Shared mobile nav toggle (identical include on all 3 pages)    |
| `report.js`         | Form sliders, geolocation, submission                          |
| `dashboard.js`      | Chart.js charts, date-range picker wiring, AI forecast section |

`window.ALLERGYMAP_API_BASE` (unset by default, falls back to
`http://localhost:5000`) lets you point the frontend at a different backend
without editing `api.js` -- set it via a `<script>` tag before `api.js` loads.
Each page includes `env.local.js` for this (gitignored, optional -- a missing
file is a harmless 404, nothing breaks). Useful if your local Docker ports
are remapped, e.g. because something else on the machine already holds 5000
or 8080 (macOS's AirPlay Receiver commonly squats on 5000) -- see
`docker/README.md` and `docker/docker-compose.override.yml`.

## Participant identity (`js/identity.js`)

There are no accounts. On first visit `identity.js` generates a UUID v4 and
stores it under `allergymap_device_id`; that value *is* the participant. See
`backend/README.md` for why the platform is built this way and what the
trade-off is.

Three details that are easy to get wrong:

- **`crypto.randomUUID()` needs a secure context.** A deployment reached over
  plain HTTP at an IP address does not have it, but `crypto.getRandomValues()`
  works there, so that is the fallback -- unpredictability is the requirement,
  since the identifier doubles as a bearer credential. A `Math.random()` path
  exists only so an ancient browser still renders the page, and it warns.
- **The old key is migrated, not ignored.** Before the identity layer,
  `report.js` minted its own UUID under `allergymap_uid`. Reports already in
  the database are attributed to that value, so `getDeviceId()` adopts it when
  it is a valid UUID v4 -- otherwise every existing participant would look
  like a new one after deploying this build. The old `Math.random()` fallback
  values are not valid UUIDs and are replaced.
- **`localStorage` can throw.** Safari private browsing denies access outright,
  so reads and writes are wrapped and fall back to a tab-scoped identity
  rather than breaking every request.

`api.js` attaches the identifier as the `X-Device-Id` header **only** for
calls that pass `identified: true` -- reports and `/api/users/me`. Public
reads (environment data, forecasts, the map) go without it, so the backend
never learns which participant is reading open data, and those requests stay
"simple" in CORS terms instead of paying for a preflight round trip on every
per-city fetch.

`Identity.adopt(code)` switches this browser to an identifier typed by the
participant (the profile *transfer code*); `Identity.reset()` abandons the
current identity locally. Neither touches the server -- erasing server-side
data is `API.eraseMe()`.


## Design system (`css/style.css`)

A clinical dark theme: white/near-white text on a deep navy background, teal
accent, generous whitespace, single type/spacing scale via CSS custom
properties (`:root` block at the top of the file). Typeface is IBM Plex Sans
for text and IBM Plex Mono for anything that's a reading rather than prose
(symptom scores, the map legend's value, dashboard selects) -- loaded from
Google Fonts via a `<link>` in each page's `<head>`.

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

## Allergen heatmap (`index.html`)

`map.js` renders a real heatmap (the [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat)
plugin), keyed by allergen concentration -- **not** the earlier per-report
severity `circleMarker`s (that function was misleadingly named
`renderHeatmap`; it's gone now, along with the now-unused
`api.js:getHeatmap()`/`GET /api/reports/heatmap` frontend call). Data comes
from `GET /api/env/city/<city>?days=7` per city (fetched once via
`GET /api/env/latest` for the city list, then in parallel) -- **not** the
single latest row per city. That row is frequently the very edge of the
fetched forecast window, where the pollen/dust models have no data yet, so
`map.js` instead walks each city's last 7 days backwards and uses the most
recent *non-null* reading per allergen. When every city is truly zero for an
allergen (e.g. olive well outside its April-June season), the heat layer
isn't rendered at all and the legend says so explicitly -- a heat layer with
`minOpacity` would otherwise paint a visible blob even for confirmed-zero
data, which is misleading.

The legend panel's `<select>` toggles which allergen the heat layer is keyed
by: olive, grass, or ragweed pollen (grains/m3), or Saharan dust (ug/m3, see
the CAMS provenance note in `data_collection/README.md`); the gradient bar
and max value/unit update to match.

The ten predefined Greek cities (`data_collection/open_meteo_fetcher.py:GREEK_LOCATIONS`)
are the only real data points -- Leaflet.heat's blur turns them into a
smooth-looking field, which reads well but isn't a true continuous
measurement; the legend says so ("blurred into a heatmap for display").

## Navbar

Single shared markup block (`<nav class="navbar">`, copy-pasted identically
across the three pages -- there's no templating layer) and a single style
block in `css/style.css`. `.navbar a.active` marks the current page.
