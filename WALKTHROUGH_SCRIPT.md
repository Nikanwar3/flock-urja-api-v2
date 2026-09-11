# Screen recording walkthrough script

Not part of the submission — this is just your prep notes for recording
yourself explaining the project. Speak in your own words; this is a
running order + the key points to hit at each stop, not a script to read
verbatim. Aim for ~8-12 minutes.

## 1. Frame the problem (30s, no screen needed yet)

"Urja Meter Ops is an internal utility portal with no API — field staff
click through it manually. My job was to reverse-engineer how it actually
works under the hood and build a clean REST API in front of it, without
ever touching the portal's HTML by hand."

## 2. The portal itself (2 min) — have the portal open in a browser tab

- Log in, open DevTools → Network, **Preserve log** on.
- Click into a meter. Point out: "This *looks* like an old server-rendered
  app — but watch the Network tab" — show the `/portal/meters/search`,
  `/portal/meters/{id}/geo`, `/portal/meters/{id}/energy` calls firing.
  **Key point to say out loud:** "Despite the retro UI, everything is
  JSON under the hood — there's no HTML table anywhere I needed to parse."
- Open `/login`'s request in Network, show the `Origin`/cookie exchange.
  **Key point:** "SvelteKit rejects a POST without a matching `Origin`
  header — that's the first thing that tripped me up, a plain curl POST
  got a flat 403 with no explanation."

## 3. The bulk export find (2 min) — this is the "wow" moment, don't rush it

- Open `PROTOCOL.md` §3 on screen.
- "The transformers page has an 'Export all meters' button. I read the
  compiled JS behind it and found it does its own HMAC-SHA256 request
  signing — fetches a secret from `/portal/keys`, signs
  `method\npath\nquery\ntimestamp`, sends it as `x-timestamp`/`x-signature`
  headers." Show `app/portal_client.py::_sign` and `fetch_all_meters`.
- **Key point:** "This one endpoint returns the *entire* 403-meter dataset
  in one authenticated, signed call — so instead of scraping 400+
  individual meter pages, my adapter calls this once per cache refresh."
- Mention the quirk: "`page` is part of the signed string but the server
  ignores it — every call returns everything. I actually almost wrote a
  pagination loop assuming it worked like the DT listing does, and caught
  it by diffing page 1 vs page 2 before trusting the pattern."

## 4. Architecture tour (3 min) — have the repo open in an editor

Walk top-down, one sentence of "why" per file, not a full code read:

- `app/portal_client.py` — "the only module that knows the portal
  exists": login, session tracking, HMAC signing, retry-once-on-401.
- `app/index.py` — "an in-memory index over the bulk export, because the
  portal itself can only filter by one free-text field at a time." Show
  `MeterIndex.search` and how filters combine.
- `app/index.py::build_hierarchy_tree` — "~5% of meters are missing a
  hierarchy level in the source data — the portal represents that as an
  empty string, not null. I resolve each DT to the majority non-missing
  value per level and record every disagreement as a data-quality issue
  instead of hiding it." Show the `data_quality_issues` output from
  `GET /api/v1/hierarchy` in `/docs`.
- `app/consumption.py` — "readings are cumulative register values, not
  interval usage, so `kwh_consumed` is computed as last-minus-first. And
  the reading interval itself is inconsistent per meter for no
  discoverable reason — some meters get half-hourly data, most get daily
  — so I infer and report a `resolution` field rather than assuming one."
- `app/cache.py` / `app/service.py` — TTL caches, briefly explain the
  freshness trade-off (staleness bounded by TTL, not push-based).

## 5. Live demo (2 min) — hit the running service (local or deployed URL)

- `GET /healthz` — point out portal reachability + session state + cache
  ages.
- `GET /api/v1/meters?install_status=Faulty&phase_type=three&zone=Z-01` —
  "this exact filter combination isn't possible in the portal's own UI."
- `GET /api/v1/meters/near?lat=...&lng=...` — "the portal has no spatial
  query at all."
- Open `/docs` and show the OpenAPI schema is real, not hand-typed.

## 6. Wrap up (1 min)

- What you'd improve with more time (pick 1-2 from README, don't read the
  whole list): e.g. "the in-memory index is a linear scan — fine at 400
  meters, and I've noted where I'd swap it for something indexed if the
  dataset were orders of magnitude bigger."
- What you skipped and why: "I chose not to build the optional web-client
  extension — I put that time into the adapter's correctness and the
  write-up instead, since that's what's actually being evaluated first."
- The one mistake you'd flag unprompted (see REFLECTION.md — the
  `page`-parameter near-miss is a good, honest one to mention yourself
  before anyone asks).

## Things to have open/ready before you hit record

- [ ] Portal logged in, DevTools Network tab open, "Preserve log" on
- [ ] Editor open to: `PROTOCOL.md`, `app/portal_client.py`, `app/index.py`, `app/consumption.py`
- [ ] Service running locally (`uvicorn app.main:app --reload`) with `/docs` open in a tab
- [ ] A terminal with `curl` ready for the 2-3 sample requests in README
- [ ] `REFLECTION.md` open for the wrap-up section
