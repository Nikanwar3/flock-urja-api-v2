# Urja Meter Ops API

A clean, documented REST API in front of **Urja Meter Ops** — an ageing
internal SvelteKit portal that a distribution utility's field/ops staff use
to look up smart-meter data, with no API of its own. This service logs in
on the utility's behalf, talks to the portal's (undocumented, internal)
JSON endpoints, normalises the results, and re-exposes them as a small,
sensible, OpenAPI-documented REST API that any engineer or program can
consume without ever touching the portal.

- **How the portal itself works** (auth, endpoints, quirks) → [`PROTOCOL.md`](PROTOCOL.md) — read this first if you're curious how any of this was figured out.
- **This API's contract** → [`openapi.json`](openapi.json), or run the service and open `/docs` (Swagger UI) / `/redoc`.
- **Reflection questions** → [`REFLECTION.md`](REFLECTION.md).

## What this is, structurally

```
flock-urja-api/
├── app/
│   ├── main.py            FastAPI app, routing, error → HTTP mapping
│   ├── config.py          Settings (env vars), see .env.example
│   ├── portal_client.py   The ONLY module that knows the portal exists:
│   │                      login/session mgmt, HMAC signing, retries
│   ├── portal_errors.py   Typed exceptions raised by the adapter
│   ├── service.py         Wires client + caches + index together
│   ├── cache.py           Small TTL caches (see "Freshness / caching")
│   ├── index.py           In-memory query layer + hierarchy reconstruction
│   ├── consumption.py     Raw readings -> parsed + summarised consumption
│   ├── models.py          Pydantic response schemas (our clean shapes)
│   └── routers/           meters.py, transformers.py, hierarchy.py, health.py
├── tests/                 Pure-logic + mocked-adapter tests (no live calls)
├── scripts/export_openapi.py
├── openapi.json           Exported OpenAPI 3.1 spec
├── PROTOCOL.md
└── REFLECTION.md
```

**Layering, and why:** `portal_client.py` is a thin, honest HTTP adapter —
it knows URLs, cookies, and the HMAC scheme, and nothing about our API's
shape. `index.py` holds a normalised, queryable in-memory snapshot of the
portal's data — this is what lets `/api/v1/meters` support filters (by
status, phase, make, zone, circle, ...) the portal's own UI can't combine.
`service.py` is the only place that decides *when* to refetch from the
portal (via the caches). Routers are thin — they validate query params and
call into `service`. If the portal ever needs to be swapped for something
else (a real API, a DB dump), only `portal_client.py` + `service.py`'s
loaders would need to change.

## Running it

Requires Python 3.11+.

```bash
git clone <this repo> && cd flock-urja-api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults already match the assignment's credentials
uvicorn app.main:app --reload --port 8000
```

Then:
- `http://localhost:8000/docs` — interactive Swagger UI
- `http://localhost:8000/redoc` — Redoc
- `http://localhost:8000/healthz` — service + upstream portal health

### Deploying it somewhere

`render.yaml` is included as a one-click blueprint for [Render](https://render.com)'s
free tier: **New → Blueprint**, point it at this repo, fill in
`URJA_PORTAL_EMAIL` / `URJA_PORTAL_PASSWORD` when prompted (they're marked
`sync: false` in the blueprint so they aren't committed in plaintext), and
deploy. Free-tier services spin down when idle, so the first request after
a quiet period will be slow (cold start) — that's a hosting-tier
characteristic, not something the service itself does.

To run the tests (none of them hit the live portal — the adapter's auth
flow is tested against a mocked HTTP layer with `respx`):

```bash
pip install -r requirements-dev.txt
pytest
```

To regenerate `openapi.json` after changing routes/models:

```bash
python scripts/export_openapi.py
```

## Sample requests

```bash
# List faulty three-phase meters in zone Z-01 (a filter combination the
# portal's own UI cannot express — it only has one free-text search box)
curl "http://localhost:8000/api/v1/meters?install_status=Faulty&phase_type=three&zone=Z-01"

# A single meter's nameplate + hierarchy + geo
curl "http://localhost:8000/api/v1/meters/J100001"

# Consumption, with a computed summary (raw portal readings are cumulative
# register values, not interval usage — see PROTOCOL.md §4)
curl "http://localhost:8000/api/v1/meters/J100001/consumption"

# Distribution transformers, enriched with live meter counts by status
curl "http://localhost:8000/api/v1/transformers/DT-002"

# Full reconstructed network hierarchy, with data-quality gaps flagged
curl "http://localhost:8000/api/v1/hierarchy"

# Meters within 2km of a point — the portal has no spatial query at all
curl "http://localhost:8000/api/v1/meters/near?lat=26.9&lng=75.8&radius_km=2"
```

Sample response — `GET /api/v1/meters/J100001`:

```json
{
  "meter_id": "J100001",
  "serial_no": "GE84132",
  "make": "L&T",
  "phase_type": "single",
  "install_status": "Installed",
  "dt_code": "DT-002",
  "install_type": "CT Operated",
  "build": "legacy",
  "hierarchy": {
    "zone": {"code": "Z-02", "name": "Jaipur Zone 2"},
    "circle": {"code": "C-02", "name": "Circle 2"},
    "division": {"code": "D-02", "name": "Division 2"},
    "subdivision": {"code": "SD-02", "name": "Subdivision 2"},
    "substation": {"code": "SS-02", "name": "Substation 2"},
    "feeder": {"code": "F-002", "name": "Feeder 2"},
    "dt": {"code": "DT-002", "name": "Mansarovar DT 2"}
  },
  "geo": {"lat": 26.822136543835608, "lng": 75.90718190602279},
  "hierarchy_complete": true
}
```

## API surface

| Endpoint | What it does |
|---|---|
| `GET /api/v1/meters` | List/search/filter meters (status, phase, make, install type, and full hierarchy path — combinable, unlike the portal) |
| `GET /api/v1/meters/{id}` | Full meter record: nameplate + hierarchy + geo |
| `GET /api/v1/meters/{id}/consumption` | Raw readings + a computed usage summary (delta, inferred resolution) |
| `GET /api/v1/meters/near` | Meters within a radius of a lat/lng point |
| `GET /api/v1/transformers` | List DTs, each enriched with live meter counts |
| `GET /api/v1/transformers/{code}` | Single DT |
| `GET /api/v1/hierarchy` | Full zone→...→DT tree, with data-quality gaps flagged |
| `GET /healthz` | Service status, upstream reachability, session state, cache ages |

Full request/response schemas: [`openapi.json`](openapi.json) or `/docs`.

## Assumptions

- **This service manages one shared portal credential itself** (from env
  vars), rather than asking its own callers to authenticate against the
  portal. The assignment's brief is "programmatic access for other
  engineers/programs" to a single utility's data — there's one ops
  account for the whole desk, not per-caller portal logins, so it made
  more sense to treat the portal credential as this service's own secret
  (like a DB password) than to build a pass-through auth proxy. See
  "What I intentionally left out" for what this trades away.
- **The bulk `/portal/export` endpoint is authoritative for meter
  nameplate/hierarchy/geo**, in preference to the paginated
  `/portal/meters/search` + per-meter `/geo` calls the UI itself uses. It
  returns strictly more data (numeric geo, full hierarchy) in one request
  instead of 400+. See PROTOCOL.md §3.
- **Consumption timestamps are treated as naive (no timezone assumed)** —
  the portal never states one, and guessing IST (plausible, given the
  Jaipur location data) felt worse than being honest about not knowing.
- **The `/portal/export` `page` parameter's behaviour (ignored server-side)
  is treated as a stable characteristic, not a bug I should route
  around** — the adapter fetches once per cache refresh rather than
  looping pages that would return duplicate data anyway.
- **A meter missing part of its hierarchy is real data, not something to
  paper over** — `hierarchy_complete: false` and, at the tree level,
  `data_quality_issues`, surface this instead of silently defaulting
  missing levels to something plausible-looking.

## Design decisions & trade-offs

- **In-memory cache + index, no database.** At ~400 meters / ~40
  transformers, a full in-memory snapshot refreshed on a TTL is simpler
  than standing up Postgres/Redis for this exercise, and is fast enough
  that every list/filter/hierarchy query is a linear scan with no
  perceptible latency. The explicit trade-off: state is per-process (a
  restart re-fetches from the portal) and this would need to change well
  before the dataset reached, say, six figures of meters — see "What I'd
  improve."
- **TTL-based freshness, not push invalidation.** The portal gives no
  webhook/changefeed, so "freshness" here means "how stale are we willing
  to be." Defaults: meters/transformers 5 minutes, per-meter consumption 2
  minutes (configurable via env vars, see `.env.example`). This is a
  judgment call, not a measured number — the portal's actual update
  cadence is unknown from the outside.
  `/healthz` reports each cache's age so a caller (or you) can tell how
  stale a response might be, rather than guessing.
- **Session handling is transparent and self-healing.** The adapter logs
  in lazily on first use, tracks the ~1-hour session lifetime, and
  re-authenticates once, automatically, on any unexpected `401` — a long-
  running server process should survive indefinitely without anyone
  restarting it because a cookie expired. See PROTOCOL.md §1 for the
  auth flow itself, including the CSRF `Origin` requirement that a naive
  client would trip on.
  Portal unreachability / 5xx responses are translated to a `502` on our
  side (clearly "upstream problem," not "your request is wrong"); a bad
  service credential is a `500` (it's our fault, not the caller's).
- **The consumption endpoint reports an inferred `resolution`
  (`sub_hourly`/`daily`/`unknown`) rather than assuming one.** The portal
  itself returns wildly inconsistent reading granularity per meter with no
  discoverable cause (PROTOCOL.md §4) — pretending otherwise would produce
  a clean-looking but misleading API.
- **Hierarchy reconstruction resolves conflicts by majority vote per level
  and reports the disagreement, instead of picking one meter's version
  silently.** ~5% of meters have real gaps in the source data; hiding that
  would make the tree look more trustworthy than it is.

## What I intentionally left out (and why)

- **No per-caller authentication on this API.** Given the single-utility,
  single-credential, read-only nature of the exercise, adding an API-key
  layer would be boilerplate without a real access-control story behind
  it yet. If this went further than an exercise, the natural next step is
  an API key (or OAuth client-credentials) per consuming system, checked
  in a FastAPI dependency — the router layer is already structured so
  that's a small, localised change.
- **No persistent storage / history.** Everything is refetched from the
  portal on cache expiry; there's no local database recording historical
  snapshots (e.g. "what did this meter's status look like last week").
  Genuinely out of scope for "wrap the portal," but would matter for
  trend/anomaly analysis.
- **No modern web client.** Building a small dashboard on top of this API
  is a listed optional extension, and a genuinely fun one, but given the
  time available I chose to put that time into the adapter being
  correct and well-tested plus the write-up being thorough, rather than
  a UI on top — that felt like the better trade for what's being
  evaluated here (see the Reflection for more on this).
- **No load/rate-limit testing against the portal.** It's someone else's
  system and the brief says treat it as read-only; a few dozen exploratory
  requests were made, nothing resembling a stress test.
- **No write operations.** The portal appears to be read-only for this
  account anyway (no create/update UI observed), so the API mirrors that.

## What I'd improve with more time

- **Push the index past linear scan** once the dataset is large enough to
  matter (see the note in `app/index.py` and the point above) — an actual
  embedded index (SQLite with indexes, or just `bisect`-sorted lookups by
  the fields people filter on) once meter counts are large enough that
  a scan of the full set per request is measurable.
- **Persist a rolling history of consumption/status** so the API could
  answer trend questions ("has this meter's consumption dropped
  suddenly?") instead of only ever reflecting the portal's fixed
  historical window.
- **A small map/hierarchy-explorer front end** — the optional extension
  skipped above. The `/meters/near` and `/hierarchy` endpoints exist
  specifically because they'd be the backbone of that.
- **Smarter cache invalidation** than a flat TTL — e.g. a lightweight
  `HEAD`/ETag-style check against the portal to detect "nothing changed"
  and avoid a full re-signed export call when the 5-minute window lapses
  but the data hasn't moved.
- **More adversarial tests against the adapter** (partial responses,
  malformed JSON, a `/portal/keys` secret rotating mid-session) — the
  current tests cover the paths I found in real exploration, not every
  hypothetical failure mode.
