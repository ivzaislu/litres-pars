# App-only LitRes series aggregator

This branch turns `litres-pars` into a cache-first backend service for the APK.

The APK does **not** call LitRes directly. It only calls this service. LitRes is
an internal provider used on cache misses and server-controlled refreshes.

## Data flow

```text
APK
 |
 | HTTPS + Authorization: Bearer <app token>
 v
LitRes Aggregator API
 |
 +--> PostgreSQL cache -----> normalized SeriesInfo
 |
 +--> LitRes Foundation API (cache miss / stale cache only)
        |
        +-- /search
        +-- /arts/{id}
        +-- /series/{id}
        +-- /series/{id}/arts?show_unavailable=true
```

A fresh cached series produces **zero LitRes requests**.

The default cache TTL is seven days and is controlled by the server. The APK
cannot request a forced refresh.

## API

### Health

```http
GET /health
```

Returns only service health and does not expose catalog data.

### Resolve a series from known book information

```http
POST /v1/series/resolve
Authorization: Bearer <token>
Content-Type: application/json

{
  "author": "Роман Злотников",
  "book_title": "Обреченный на бой",
  "series_name": "Грон"
}
```

The server first checks its local catalog.

On a cold miss it uses a two-stage resolver.

Fast path:

1. searches LitRes for the known book title;
2. verifies the author and a compatible title;
3. loads at most a small bounded number of candidate art details;
4. selects the provider series claim whose normalized name matches
   `series_name`.

Fallback path, used only when the fast path fails:

1. searches LitRes by author;
2. walks a bounded number of author-search results;
3. opens art detail for each candidate;
4. verifies the author from authoritative art detail;
5. scans the art's provider `series[]` claims for the normalized requested
   `series_name`;
6. stops as soon as the matching provider series is found.

After either path resolves a `series_id`, the server loads series detail and
the complete selected composition with `show_unavailable=true`, then caches
the result.

The normal successful cold path remains four requests when the title search
hits immediately:

```text
/search?q=<book title>
/arts/{art_id}
/series/{series_id}
/series/{series_id}/arts
```

A title miss adds author fallback work:

```text
/search?q=<book title>
/search?q=<author>
/arts/{candidate}
/arts/{candidate}
...
/series/{series_id}
/series/{series_id}/arts
```

The fallback is deliberately bounded. It currently inspects at most 24 author
candidates across at most three 50-row search pages. It is only entered after
the fast path misses, so successful common requests keep the original low-cost
behavior.

Composition pagination adds requests only when LitRes supplies a
`pagination.next_page`.

### Get a known series

```http
GET /v1/series/587
Authorization: Bearer <token>
```

If the cached entry is complete, has series detail and is inside the TTL, no
LitRes request is made.

## SeriesInfo response

The service exposes logical provider-local positions instead of pretending that
every LitRes art row is a different book.

Example shape:

```json
{
  "source": "litres",
  "series_id": 587,
  "name": "Грон",
  "parent_id": null,
  "provider_counts": {
    "arts_count": 12,
    "unique_arts_count": 6
  },
  "nested_series": [],
  "works": [
    {
      "position": 1,
      "title": "Обреченный на бой",
      "arts": [
        {
          "art_id": 123,
          "title": "Обреченный на бой",
          "art_type": 0,
          "format": "text",
          "url": "...",
          "authors": ["Роман Злотников"],
          "alternative_version_ids": [456]
        },
        {
          "art_id": 456,
          "title": "Обреченный на бой",
          "art_type": 1,
          "format": "audio",
          "url": "...",
          "authors": ["Роман Злотников"],
          "alternative_version_ids": [123]
        }
      ]
    }
  ],
  "unpositioned_arts": [],
  "expanded_arts": [],
  "stats": {
    "ordered_work_count": 6,
    "direct_art_count": 12,
    "unpositioned_direct_art_count": 0,
    "expanded_art_count": 0
  },
  "cached_at": "..."
}
```

## Provider semantics preserved in the database

The live probes established that `/series/{id}/arts` may contain both direct
members and expanded hierarchy rows.

The aggregator therefore stores them separately:

- `series_arts`: only rows whose own `series[]` contains the requested
  `series_id`;
- `series_expanded_arts`: endpoint rows that do not directly claim the
  requested series;
- `series.parent_id` and `nested_series_json`: provider hierarchy metadata;
- every `art_id` remains separate.

Ordered logical slots are derived only for the API response by grouping direct
members on `(series_id, art_order)`.

This does not create an Abred-style canonical work.

## Text/audio behavior

Live tests across Lukyanenko, Zlotnikov, Max Frei, Panov, Pekhov, Tarmashev,
Emets, Sapkowski and Kamsha confirmed that one ordered series position may
contain multiple provider arts.

Observed cases include:

- one text + one audio;
- one text + two audio editions.

`alternative_versions` is returned as supporting evidence but is not required
for grouping because it is incomplete in some live series.

## Running the server

Required for Northflank/server mode:

```bash
export DATABASE_URL='postgresql://user:password@host:5432/database'
export LITRES_APP_TOKEN='replace-with-a-long-random-secret'
```

Optional:

```bash
export LITRES_CACHE_TTL_SECONDS='604800'
```

For explicit local development only, `LITRES_DB_PATH` can select SQLite when
`DATABASE_URL` is absent.

Run:

```bash
uvicorn litres_parser.api:create_app --factory --host 0.0.0.0 --port 8000
```

Northflank production uses `PostgresCatalog`. Missing tables and indexes are
created idempotently at startup. The service does not silently fall back to
SQLite when neither `DATABASE_URL` nor an explicit local SQLite path is
configured.

## Authentication boundary

The API has no public catalog routes, no OpenAPI endpoint and no CORS setup.
Catalog routes require:

```http
Authorization: Bearer <LITRES_APP_TOKEN>
```

This is suitable as an initial private-service gate, but a secret embedded in an
APK can be extracted. It is **not** cryptographic proof that a request came from
the genuine application.

For production "only our APK" enforcement, keep the current API boundary and
replace the token verifier with short-lived server-issued credentials backed by
Android Play Integrity (or the application's existing authenticated user/device
session). TLS is required in deployment.

The important architectural point is that this can be changed inside the API
authentication layer without changing LitRes parsing, caching or the APK's
SeriesInfo contract.

## Request-stampede protection

The aggregator serializes concurrent cache misses:

- per resolution key: `(author, book_title, series_name)`;
- per resolved `series_id`.

Concurrent requests for the same uncached series therefore do not intentionally
fan out into duplicate LitRes composition downloads inside one server process.


## Northflank

See [`docs/northflank-deployment.md`](northflank-deployment.md). The repository also contains `northflank/litres-api.json` and `northflank/health-check.json` with the exact application-specific service settings.
