# litres-pars

Standalone async parser/client for the LitRes Foundation API.

The package is intentionally provider-only. It does **not** contain Abred database
models, reconciliation, canonical-work rules, title/author matching or decisions
about LitRes relations such as `LINKED`/`SYNCED`.

## Implemented endpoints

- `GET /foundation/api/search`
- `GET /foundation/api/arts/{id}`
- `GET /foundation/api/series/{id}`
- `GET /foundation/api/series/{id}/arts`
- `GET /foundation/api/arts/{id}/similar`
- `GET /foundation/api/arts/facets`
- `GET /foundation/api/genres`
- `GET /foundation/api/genres/{id}/arts/facets`

The client handles LitRes' `payload.data` envelope, retries temporary HTTP errors,
request throttling and pagination metadata. Returned art/series objects remain
provider dictionaries so application-specific interpretation stays outside this
repository.

## Example

```python
import asyncio
from litres_parser import LitResClient

async def main():
    async with LitResClient() as client:
        hits = await client.search("Метро 2033")
        art = await client.get_art(hits[0]["id"])
        print(art)

asyncio.run(main())
```
