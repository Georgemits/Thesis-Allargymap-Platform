# frontend

Plain HTML/CSS/JS frontend for AllergyMap. No build step -- served as static
files (nginx in Docker, or `python -m http.server` locally).

## Pages

| Page             | Purpose                                             |
|-------------------|------------------------------------------------------|
| `index.html`       | Leaflet map (symptom report locations / allergen heatmap) |
| `report.html`       | Symptom report form (VAS 0-10 sliders, geolocation)    |
| `dashboard.html`     | Environmental charts (Chart.js) + AI pollen forecast, with a relative "Days" window or a custom date-range picker |
| `profile.html`       | Allergy profile: which allergens affect you and how badly, the transfer code, and data deletion |
| `login.html`         | Optional account: sign in, create an account, sign out |

## JS (`js/`)

| File            | Purpose                                                    |
|------------------|--------------------------------------------------------------|
| `identity.js`      | Anonymous device identity (UUID v4 in `localStorage`) -- load before `api.js` |
| `api.js`          | Thin `fetch` wrapper around the backend API (single source of truth for all endpoint URLs) |
| `map.js`           | Leaflet allergen-concentration heatmap (Leaflet.heat)          |
| `navbar.js`         | Shared mobile nav toggle (identical include on all 3 pages)    |
| `report.js`         | Form sliders, geolocation, submission                          |
| `dashboard.js`      | Chart.js charts, date-range picker wiring, AI forecast section |
| `profile.js`        | Allergy profile form (built from the API's allergen catalogue), transfer code, erasure |
| `auth.js`           | Sign in / create account / sign out (login.html) |

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

### Accounts

An account is optional and sits on top of that identity. `Identity.signIn(id,
username)` adopts the identifier the backend returned for the account and
caches the username locally; `Identity.signOut()` forgets both and generates a
fresh anonymous identity. Nothing is deleted server-side by signing out.

The navbar reads the **cached** username rather than calling
`GET /api/auth/status` on every page: the status call is identified, and
issuing one on each page load would tell the backend which participant is
reading the public map. `login.html` and `profile.html` do call it, and correct
the cache when it disagrees -- an account erased from another device leaves a
stale username behind otherwise.

### Allergy profile page

`profile.js` builds the form from `GET /api/profiles/allergens` instead of
hard-coding the allergen list, which the backend already owns (it validates
against it, and the correlation engine scores against it). Severity is a
segmented control of real radio inputs -- visually replaced by chips, but still
in the DOM, so keyboard and screen-reader behaviour is the browser's rather
than something re-implemented in JavaScript.

Two secure-context traps are handled here, both of which would bite a
plain-HTTP deployment: `navigator.clipboard` is undefined outside a secure
context, so copying the transfer code falls back to a hidden textarea and
`document.execCommand("copy")`; and destructive actions arm themselves on a
first click and run on a second, instead of calling `confirm()` -- a native
modal blocks the page and reads as a browser warning rather than as part of the
app.


## Design system (`css/style.css`)

A light clinical theme in the visual language Greek diagnostic laboratories use.
The reference was **bioiatriki.gr**: its computed styles were measured and the
*language* adopted -- deep institutional blue for navigation and headings, white
cards on a faintly blue-grey page, warm grey body text rather than black, soft
shadows, 10px card corners, pill buttons, one geometric sans throughout. Nothing
of theirs is reproduced: no logo, no wordmark, no imagery, no copy. This is an
unaffiliated university project that should read as belonging to the same
profession, not to that company.

| Token | Value | Role |
|--------|--------|------|
| `--accent` | `#004380` | navbar, buttons, links, headings, focus rings |
| `--bg-canvas` / `--bg-surface` | `#f2f5f8` / `#ffffff` | page / cards |
| `--text-primary` / `--text-secondary` | `#545454` / `#5f6b76` | body / muted |
| `--radius` / `--radius-pill` | `10px` / `999px` | cards / buttons |

Typeface is **Montserrat** -- the closest freely licensed geometric sans to the
reference site's Gotham, and one of the few that ships a Greek subset, which
this project needs. **IBM Plex Mono** stays for anything that is a reading
rather than prose (symptom scores, the transfer code, map legend values).

**Contrast:** every pairing used in the stylesheet was checked against WCAG 2.1
AA with the relative-luminance formula. Brand blue on white is 9.9:1, body text
7.6:1, muted text 5.6:1. One inherited value was deliberately *not* copied: the
reference site's own muted grey (`#777`) reaches 4.48:1 on white and would fail
AA for body text, so ours is darkened to `#5f6b76`.

**Status surfaces are pale washes with dark ink** (`--success-tint` behind
`--success`, and so on). This is the inverse of how the previous dark theme had
to do it -- there, a filled badge needed a dark background with light text -- and
it is why those tokens are named `-tint`/ink rather than `-dark`.

**Chart.js:** `dashboard.js` reads `--text-secondary` and `--border` from the
custom properties at load time and applies them as `Chart.defaults.color` /
`Chart.defaults.borderColor`, so the chart theme cannot drift from the CSS
theme. Series colors live in one `CHART_COLORS` map keyed by **what is being
drawn** -- never by position in a list, so filtering one pollen out cannot
repaint the others. The six categorical hues were validated for a light surface:
lightness band, chroma floor, colour-vision separation (worst adjacent pair
ΔE 8.2 for deuteranopia) and at least 3:1 against white.

**Leaflet map tiles** are left in their natural OpenStreetMap colors (real-world
map imagery needs color fidelity); only the surrounding chrome follows the
theme. The heat ramp is a **single hue, light to dark** -- concentration is a
magnitude, and the earlier blue-cyan-yellow-red rainbow implied categories that
do not exist while leaving the middle of the scale unorderable for a colourblind
reader.

**Known issue:** the weather charts still plot temperature and humidity on two
y-axes in one chart. Dual-axis charts let the apparent crossing point be set by
the axis ranges rather than the data; they should become two charts.

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
