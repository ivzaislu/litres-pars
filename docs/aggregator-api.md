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

On a cold miss it:

1. searches LitRes for the known book;
2. verifies the author;
3. loads at most a bounded number of candidate art details;
4. selects the provider series claim whose normalized name matches
   `series_name`;
5. loads series detail;
6. loads the complete selected series composition with
   `show_unavailable=true`;
7. caches the result.

The normal cold path is four requests when the first verified search candidate
is correct:

```text
/search
/arts/{art_id}
/series/{series_id}
/series/{series_id}/arts
```

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
