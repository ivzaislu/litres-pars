# litres-pars

Standalone async parser/client and local cache for the LitRes Foundation API.

This project is designed to work **without LitRes partner credentials**. Partner
catalog APIs are intentionally out of scope. The parser uses the Foundation
endpoints already used by the original Abred LitRes integration and keeps a
local SQLite catalog so normal lookups do not repeatedly hit LitRes.

It also deliberately contains no Abred database models, CanonicalWork rules,
reconciliation or application-specific identity decisions.

## Goal: series/cycles with a bounded number of requests

The normal flow is:

1. Search the local SQLite catalog for an art: **0 requests**.
2. Discover its LitRes series claims:
   - **0 requests** when already cached;
   - otherwise one `GET /foundation/api/arts/{id}`.
3. After the caller selects one `series_id`, load its composition:
   - **0 requests** when cached;
   - otherwise `GET /foundation/api/series/{id}/arts`, following pagination only when required.

The resolver never automatically expands every series attached to a book. This
prevents one art with several collections/series from causing request fan-out.

## Public Foundation endpoints implemented

- `GET /foundation/api/search`
- `GET /foundation/api/arts/{id}`
- `GET /foundation/api/series/{id}`
- `GET /foundation/api/series/{id}/arts`
- `GET /foundation/api/arts/{id}/similar`
- `GET /foundation/api/arts/facets`
- `GET /foundation/api/genres`
- `GET /foundation/api/genres/{id}/arts/facets`

The client handles LitRes' `payload.data` envelope, server-provided pagination,
temporary-error retries and throttling.

## Local catalog

`LitResCatalog` stores raw LitRes arts, series and art-to-series membership in
SQLite. `LitResCatalogCrawler` incrementally fills it from `/arts/facets`.

The crawler is resumable and segmented by art type/language and Russian leaf
genres instead of relying on one enormous deep-offset crawl. Art IDs are
deduplicated locally when segments overlap.

## Example

```python
import asyncio
from litres_parser import LitResCatalog, LitResClient, LitResSeriesResolver

async def main():
    with LitResCatalog("litres.sqlite3") as catalog:
        async with LitResClient() as client:
            resolver = LitResSeriesResolver(client, catalog)

            # Usually the art id is found locally first:
            candidates = catalog.find_arts("Метро 2033")
            art_id = candidates[0]["art_id"]

            # At most one request if art detail is not cached.
            claims = await resolver.discover_art_series(art_id)

            # Explicitly choose a series: no automatic fan-out.
            series_id = claims[0]["series_id"]

            # At most the actual pagination requests for this one series.
            entries = await resolver.load_series(series_id)
            print(entries)

asyncio.run(main())
```

## Tests

The test suite covers request parsing, retries, pagination-loop protection,
SQLite caching, segmented crawl/resume and the bounded two-step series flow.
GitHub Actions runs it on Python 3.11, 3.12 and 3.13.
