# PROTOCOL.md — how Urja Meter Ops actually works

This documents what I found reverse-engineering the portal at
`https://urja-ops.flockenergy.tech`, using nothing but a normal logged-in
session (DevTools Network tab + reading the shipped JS bundles — no probing
beyond what a real user's browser does). Everything here is read-only
reconnaissance of an account I was given access to.

The raw capture behind this write-up (saved HTML, request/response headers,
JS bundles, HMAC sign-string workings) lived in `recon/` locally but is
gitignored rather than committed — one of those captures is a live
`better-auth` session cookie, which shouldn't end up in a public repo. This
document is the full write-up of what's in there.

## 0. The stack, at a glance

The portal is a **SvelteKit** app (you can tell from the `__sveltekit_*`
globals, the `_app/immutable/...` asset paths, and the hydration payload
embedded in every page's `<script>` tag). Auth is handled by
[**better-auth**](https://www.better-auth.com/) (visible from the
`__Secure-better-auth.session_token` cookie name and the
`/api/auth/get-session` / `/api/auth/sign-out` endpoints it exposes).

The important discovery: **despite looking like an old-school
server-rendered app, every piece of data on it is served as JSON from an
internal `/portal/*` API** that the page's own client-side JS calls after
mount. There is no HTML table to scrape anywhere. The "legacy" feel is
entirely in the UI (Tailwind + plain SvelteKit pages), not in how data is
transported.

## 1. Authentication

**Login** is a SvelteKit form action, not a REST endpoint you'd guess from
a spec: `POST /login`, form-encoded body:

```
POST /login HTTP/2
Content-Type: application/x-www-form-urlencoded
Origin: https://urja-ops.flockenergy.tech
Referer: https://urja-ops.flockenergy.tech/login

email=operator%40urja.local&password=urja-ops-2026
```

**Quirk #1 — CSRF guard on `Origin`.** SvelteKit rejects any POST whose
`Origin` header doesn't match the site's own origin, *before* the request
even reaches app code:

```
HTTP/2 403
Cross-site POST form submissions are forbidden
```

A plain `curl -d ...` without `Origin`/`Referer` gets this 403. Any client
has to set `Origin` (and it's harmless to also set `Referer`) to look like
a same-site form submit.

**On success**, the response is JSON (SvelteKit's form-action response
shape), not a redirect the HTTP client follows automatically:

```json
{"type":"redirect","status":303,"location":"/meters"}
```

...with a `Set-Cookie`:

```
__Secure-better-auth.session_token=<token>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
```

So: **the session cookie is good for 1 hour**, `HttpOnly` (can't be read by
page JS, only sent by the browser/HTTP client), `Secure` (HTTPS-only). On
failure, same URL, no cookie, and a body like:

```json
{"type":"failure","status":401,"data":"[{\"email\":1,\"error\":2},\"operator@urja.local\",\"Invalid email or password.\"]"}
```

**Sign-out** is `POST /api/auth/sign-out` (a better-auth default route,
called by the "Sign out" button). **Session introspection** is
`GET /api/auth/get-session`, which echoes the session/user record
including `expiresAt` — this is the cleanest way to confirm a session is
still alive without touching page routes, and is what this service's
`/healthz` uses.

**Behaviour when unauthenticated / expired:**
- Page routes (`/meters`, `/transformers`, `/meters/{id}`) → `302` redirect
  to `/login`.
- `/portal/*` JSON APIs → `401` with `{"error":"unauthorized","message":"A valid session is required."}`.
- `/api/auth/get-session` with no/expired cookie → `200` with no `session`
  key (rather than a 401).

This service's adapter (`app/portal_client.py`) treats any `401` from a
`/portal/*` call as "session died early" and re-logs in once before
failing the request outward.

## 2. Data endpoints

All confirmed by reading the compiled Svelte component JS for each route
(`/_app/immutable/nodes/*.js`), which contain the literal `fetch(...)`
calls the UI makes. None of this is documented anywhere in the portal
itself — there's no `/api` landing page, no OpenAPI spec, nothing.

| Method | Path | Used by | Notes |
|---|---|---|---|
| GET | `/portal/meters/search?q=&page=` | Meters list page | Paginated, 20/page. `q` matches meter ID or serial number (case-insensitive substring) — **not** DT code or make. |
| GET | `/portal/dts?page=` | Transformers list page | Paginated, 20/page, genuinely paginates (unlike export, below). |
| GET | `/portal/meters/{id}/geo` | Meter detail page | `{"data":{"latitude":"..","longitude":".."}}` — note: **strings**, not numbers. |
| GET | `/portal/meters/{id}/energy` | Meter detail page | Consumption readings — see §4, this is the interesting one. |
| GET | `/portal/keys` | Transformers page ("Export" button) | Returns an HMAC signing secret — see §3. |
| GET | `/portal/export?page=` | Transformers page ("Export" button) | **Bulk** meter export, HMAC-signed — see §3. This is the "bulk path" worth using well. |
| POST | `/api/auth/sign-out` | Header "Sign out" button | better-auth default route. |
| GET | `/api/auth/get-session` | (not called by any page — better-auth default) | Useful for our own health checks. |

A meter detail page (`GET /meters/{id}`) is itself server-rendered with
some data **already embedded** in the SSR payload (nameplate fields +
hierarchy breadcrumb, inside the `<script>` hydration blob as
`data:[...,{data:{meterId, detail, hierarchy}}]`) — geo and consumption are
fetched client-side afterwards, which is why a plain `curl` of that page
shows "Loading location…" / "Loading consumption…" placeholders. I didn't
end up needing to parse this SSR blob at all: the bulk export endpoint
(§3) returns the same nameplate+hierarchy data (plus geo) for every meter
in one shot, so scraping 400+ individual HTML pages is never necessary.

**Unauthenticated `/portal/*` calls** uniformly return
`401 {"error":"unauthorized","message":"A valid session is required."}`.
**Unknown meter IDs** return `404 {"error":"not_found","message":"Meter not found"}`
from the JSON endpoints, and a real 404 HTML page from `/meters/{id}`.

## 3. The bulk export — and its HMAC scheme

This was the most interesting find. The "Export all meters" button on
`/transformers` doesn't hit a plain authenticated endpoint — it first
fetches a signing secret, then computes a per-request signature client-side:

```js
async function sign(method, path, query, secret) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const message = [method, path, query, timestamp].join("\n");
  const key = await crypto.subtle.importKey("raw", utf8(secret), {name:"HMAC", hash:"SHA-256"}, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, utf8(message));
  return { timestamp, signature: toHex(sig) };
}
```

Concretely:

1. `GET /portal/keys` (needs a valid session cookie) →
   `{"data":{"signingSecret":"<32-char-secret>"}}`.
2. Build the string-to-sign: `"GET\n/portal/export\npage=1\n<unix-timestamp>"`.
3. HMAC-SHA256 it with the secret, hex-encode.
4. `GET /portal/export?page=1` with headers `x-timestamp: <ts>` and
   `x-signature: <hex>` (**plus** the session cookie — this is on top of
   auth, not instead of it).

Verified behaviour:
- **Quirk #2 — `page` is part of the signed string but has zero effect on
  the response.** `GET /portal/export?page=1` and `?page=2` (each signed
  correctly for its own query string) return the **same full list of all
  403 meters** every time. It's either a stub for pagination that was
  never wired up, or genuinely intended as "one full snapshot" and `page`
  is vestigial. Practically: **one call gets you the entire dataset**, so
  this adapter calls it once per cache refresh and never loops pages.
- The query string must match exactly what was signed — signing for
  `page=1` and requesting `page=2` (or omitting `page` entirely) both come
  back `401 {"error":"signature_invalid",...}`.
- **Timestamp tolerance is tight-ish but not exact-second**: a signature
  built with a timestamp 300s (5 min) in the past was still accepted;
  600s in the past was rejected. So there's a replay window of somewhere
  between 5 and 10 minutes — treat it as "sign right before you send,
  don't cache signatures."
- The export payload is richer than the paginated meter list: it includes
  the **full hierarchy** (zone → circle → division → subdivision →
  substation → feeder → DT, each as `{name, code}`) and **numeric** `geo`
  (`{lat, lng}` as floats, vs. the per-meter `/geo` endpoint which returns
  them as strings) for every meter, in addition to the nameplate fields.
  This is why this service treats `/portal/export` as the canonical
  source for meter data and doesn't bother with `/portal/meters/search`,
  `/meters/{id}/geo`, or scraping meter detail pages at all.

## 4. Consumption data (`/portal/meters/{id}/energy`)

Shape:

```json
{"data":[
  {"timestamp":"23/06/2026 23:30","kwh":"42594.05","kvah":"46001.58","voltR":"231"},
  {"timestamp":"24/06/2026 00:00","kwh":"42594.47","kvah":"46002.02","voltR":"230"},
  ...
]}
```

Things worth knowing before you use this:

- **All values are strings**, including the numeric ones.
- **`timestamp` has no timezone** and uses `dd/mm/yyyy HH:MM` (day-first —
  confirmed by checking it never emits a value >12 in the "month"
  position). This service parses it as a naive local timestamp and does
  **not** invent a timezone offset; see README "Assumptions."
- **`kwh`/`kvah` are cumulative meter-register readings, not per-interval
  usage.** They only increase across the returned window. To get "energy
  consumed," you subtract the first reading from the last — the portal
  itself never shows this; this service computes it (`ConsumptionSummary.kwh_consumed`).
- **Quirk #3 — the reading window is a fixed historical slice, not a
  rolling "last 7 days."** Every meter I checked returns data somewhere
  within `23/06/2026`–`30/06/2026`, regardless of when you call it (I
  called this well into September 2026 and still got June data back). So
  this is a fixed demo dataset window, not a live feed — a real
  integration should not assume "latest reading = now."
- **Quirk #4 — reading granularity is wildly inconsistent per meter, with
  no discoverable cause.** Out of a random sample of 40 meters:
  - ~17% return **337 half-hourly readings** spanning the full week
    (`23/06 23:30` → `30/06 23:30`).
  - ~83% return only **8 (occasionally 9) daily readings** over the same
    week.
  I checked this against every nameplate field available (`build`
  legacy/v2, `installType` CT-Operated/Whole-Current, `phaseType`,
  `installStatus`, `make`) looking for a pattern and found **none** — it
  doesn't correlate with anything exposed in the data. I'm treating this
  as an intentional "messy real-world data" characteristic rather than a
  bug to work around, and surface it explicitly: this service's
  consumption endpoint infers and reports a `resolution` field
  (`sub_hourly` / `daily` / `unknown`) per meter from the actual gaps
  between readings, rather than assuming a fixed interval anywhere.
- Decommissioned meters still return both `geo` and `energy` data (the
  portal doesn't hide history for retired meters) — no special-casing
  needed on our side.

## 5. The network hierarchy — and its mess

Each meter's `hierarchy` in the bulk export is a full path: `zone → circle
→ division → subdivision → substation → feeder → dt`, each `{name, code}`.
Cross-checking all 403 meters against the 40-entry `/portal/dts` list:

- Every `dtCode` referenced by a meter exists in `/portal/dts`, and vice
  versa — no orphans in either direction.
- Every DT's `feederCode` (from `/portal/dts`) agrees with the `feeder`
  code its meters report — no contradictions there.
- **But ~5.5% of meters (22/403) are missing at least one hierarchy
  level** — the portal represents a missing level as `{"name":"","code":""}`
  rather than omitting the key or using `null`. Breakdown: 11 meters
  missing `circle`, 8 missing `feeder`, 3 missing `substation`.
- As a result, **12 of the 40 DTs have meters that disagree with each
  other on part of the upstream path** — purely because some of that DT's
  meters have gaps and others don't, not because of genuinely conflicting
  non-empty values (I checked: I never saw two *different non-empty*
  codes claimed for the same level under the same DT).

This service's hierarchy-tree builder (`app/index.py::build_hierarchy_tree`)
resolves each DT to the majority non-missing value at each level and
records every such gap as a `data_quality_issues` entry rather than
silently picking one meter's version and hiding the disagreement.

## 6. Other observations

- No `/api` discovery document, no `robots.txt` hints, no versioned API
  path — everything is `/portal/*`, seemingly product-internal rather
  than meant for external integration (consistent with the assignment's
  premise that this portal "has no API").
- No obvious rate limiting was hit during normal exploration (a few dozen
  requests over a short session). Not stress-tested, on purpose — this
  was treated as a read-only account, not a load target.
- `meterId`s are sequential and gap-free: `J100000`–`J100402` (403 total,
  matching the reported `total`). Serial numbers have no duplicates in the
  full export.
- Geo coordinates all cluster realistically around Jaipur, India
  (lat ≈ 26.79–27.04, lng ≈ 75.66–75.91) — consistent with the "Jaipur
  Zone" naming and not obviously randomised/fake.
