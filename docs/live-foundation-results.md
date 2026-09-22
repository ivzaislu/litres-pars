# LitRes Foundation live probe results

Branch: `live-foundation-probe`

This document records observed LitRes Foundation API behavior used to design the
standalone parser and local SQLite cache. It records provider behavior only;
Abred identity/canonicalization rules do not belong here.

## Probe constraints

- Public Foundation API only; no partner credentials.
- Hard network budget: 30 HTTP requests per CI run.
- Requests are throttled.
- Temporary failures are retried by the parser and retries count against the budget.
- Raw JSON responses and `report.json` are preserved as a CI artifact.
- The probe is not a bulk crawler.

## CI runs

### Run 1 — baseline endpoint/schema probe

- Commit: `b217dc90236a9b950ca499d0ee1ddbf9b2b75755`
- GitHub Actions run: https://github.com/ivzaislu/litres-pars/actions/runs/35677816612
- Result: **success**
- Requests: **12**
- HTTP statuses: **12 × 200**
- Response bytes: **1,007,838**
- Sum of measured HTTP elapsed time: **14,386.79 ms**
- Artifact: `litres-foundation-live-probe`, artifact id `10673927403`

### Run 2 — nested-series and unavailable-content probe

- Commit: `d80ab2925069c518d947dcd1a19b1d5a5bd3eb7f`
- GitHub Actions run: https://github.com/ivzaislu/litres-pars/actions/runs/35677979199
- Result: **success**
- Requests: **19 / 30 budget**
- HTTP statuses: **19 × 200**
- Response bytes: **2,024,565**
- Sum of measured HTTP elapsed time: **16,325.41 ms**
- Artifact: `litres-foundation-live-probe`, artifact id `10673932567`
- Artifact digest: `sha256:a19959c9ed9a03e6b06243df5ae89e1232647e051dbca510ad402732b02c23be`

No HTTP retry was required in either successful run.

## 1. `GET /search`

Probe query: `Метро 2033`, limit 10.

Observed:

- 10 rows returned.
- Every returned art object contained a `series` key.
- **All 10 had an empty `series` value.**
- The search result did expose useful art identifiers and general art metadata.
- Sample IDs included:
  - `56420524` — audio `Метро 2033`
  - `128391` — text `Метро 2033`

### Conclusion

Search cards cannot currently be used as the source of `art -> series`
membership. The presence of the key must not be confused with populated series
metadata.

A search result is enough to discover an art ID, but a detail lookup is still
required when its series claims are not already cached locally.

## 2. `GET /arts/{id}`

Live probes included:

- `/arts/128391`
- `/arts/56420524`
- three additional art IDs selected from the facets sample.

The known text and audio `Метро 2033` arts returned non-empty series claims.

Observed claim keys:

- `id`
- `uuid`
- `name`
- `url`
- `art_order`
- `arts_count`
- `unique_arts_count`
- `top_arts`

### Conclusion

`/arts/{id}` is the reliable public endpoint observed so far for discovering
provider-side series claims for one selected art.

For request budgeting:

- locally cached detail: **0 requests**
- uncached selected art: **1 request**

The returned claim should be stored verbatim in SQLite.

## 3. `GET /arts/facets`

Probe:

`/arts/facets?art_types=text_book&languages=ru&offset=0&limit=50`

Observed:

- 50 rows.
- `series` key present in all 50 rows.
- **0/50 rows contained a non-empty series claim.**
- `counters.all = 716216` for this filtered Russian text-book query.
- server next offset: `50`.

Three facet rows were cross-checked with `/arts/{id}`:

| art id | facets series IDs | detail series IDs |
| --- | --- | --- |
| 74246531 | none | 919996, 920006 |
| 74305631 | none | 919996, 920006 |
| 74403339 | none | 906723 |

All three comparisons proved that an empty facets `series` field does **not**
mean that the art has no series.

The discovery request exposed these art types:

- `text_book`
- `audiobook`
- `podcast`
- `webtoon`

It also exposed language facets including `ru`, `de`, `en`, `es`, `fr`,
`zh` and others.

### Conclusion

The facets catalog is useful for building a large local **art index**, but it
cannot currently build a trustworthy `art -> series` index.

Therefore the local bulk cache should store:

- art ID
- title
- art type
- authors/persons
- language
- URL
- update timestamp
- raw facets JSON

but must not interpret `series=[]` from facets as evidence that the art has no
series.

Series membership should remain unknown until a detail request has populated it.

## 4. `GET /genres` and genre facets

`GET /genres?art_group=1&api_version=2` returned a tree with **1291 leaf
genres** in this run.

Observed leaf keys:

- `id`
- `uuid`
- `name`
- `token`
- `url`
- `arts_count`
- `subgenres`
- `is_root`
- cover/logo fields

Live sample leaf:

- id: `5219`
- name: `зарубежные детективы`

Probe:

`/genres/5219/arts/facets?art_types=text_book&languages=ru&offset=0&limit=10`

Observed:

- 10 rows.
- series key present but empty in all 10.
- **server next offset was 8 despite limit=10**.

### Conclusion

The genre endpoint remains useful for segmented bulk crawling.

The crawler must always follow the server's `pagination.next_page`; it must
never assume `next_offset = offset + limit`.

This is now confirmed by live Foundation API behavior, not only by old Abred
tests.

## 5. `GET /series/{id}`

Primary live series:

- id: `1505`
- name: `Метро`
- `arts_count = 368`
- `unique_arts_count = 96`
- `parent_id = null`

Observed nested series:

| id | name | summary arts_count | summary unique_arts_count |
| ---: | --- | ---: | ---: |
| 25565 | Вселенная «Метро 2033» | 153 | 151 |
| 545606 | Вселенная «Метро 2035» | 28 | 23 |

Series-detail keys included:

- `id`
- `uuid`
- `name`
- `subtitle`
- `description_html`
- `parent_id`
- `nested_series`
- `arts_count`
- `unique_arts_count`
- `top_arts`
- ratings/review fields

### Conclusion

`/series/{id}` is not required merely to fetch the composition endpoint, but it
is required if we want to understand the **series hierarchy**:

- parent series
- nested series
- metadata/description

Hierarchy must be cached separately from art membership.

## 6. `GET /series/1505/arts`: direct membership vs expanded content

### Default request

`/series/1505/arts?offset=0&limit=100`

Observed:

- 85 rows.
- no next page.
- only **6/85** rows explicitly contained a series claim with `id=1505`.
- 79 rows did not contain the requested series claim.

The six directly claimed rows were the three ordered works of the core trilogy
in both text and audio forms:

- position 1: `Метро 2033` text + audio
- position 2: `Метро 2034` text + audio
- position 3: `Метро 2035` text + audio

For those direct claims, ordering was provided through `art_order`.

### With unavailable content

`/series/1505/arts?offset=0&limit=100&show_unavailable=true`

Observed:

- first page: 100 rows.
- server next offset: 100.
- second page: 4 rows.
- total observed: **104 rows**.
- still only **6** rows directly claimed `series.id=1505`.
- 98 rows lacked the requested direct claim.

### Conclusion

This endpoint does **not** mean “every returned row is a direct member of this
series”.

It can return expanded content associated with nested series/hierarchy.

Therefore:

> A row is a verified direct membership only when its own `series[]` contains
> the requested `series_id`.

This is the most important live-probe finding.

The current local database implementation must not insert every row returned by
`/series/{id}/arts` into the direct `series_arts` relation.

## 7. Nested-series probes

### Series 25565 — Вселенная «Метро 2033»

Detail:

- `parent_id = 1505`
- detail `arts_count = 241`
- `unique_arts_count = 151`
- nested series count: 29

With `show_unavailable=true`:

- page 1: 100 rows
- next offset: 100
- page 2: 80 rows
- total observed: **180 rows**
- rows directly claiming series 25565: **82**
- expanded/unclaimed rows: **98**
- direct-claim ratio: 45.56%
- no `art_order`/number ordering was observed on the direct claims in this sample

Note: the parent series' nested-series summary reported `arts_count=153` for
25565, while direct `/series/25565` detail reported `arts_count=241`.
The meaning of `arts_count` is therefore context-sensitive enough that it must
be treated as provider metadata, not as an invariant membership count.

### Series 545606 — Вселенная «Метро 2035»

Detail:

- `parent_id = 1505`
- detail `arts_count = 42`
- `unique_arts_count = 23`
- nested series count: 4

With `show_unavailable=true`:

- 27 rows.
- direct claims: **16**
- expanded/unclaimed rows: **11**
- no direct ordering field observed in this sample.

### Conclusion

Expanded results are not a special case of series 1505; the same behavior occurs
inside its nested series.

We need separate concepts:

1. **direct membership** — confirmed by the art's own matching series claim;
2. **expanded/descendant result** — returned by a series endpoint but without a
   matching direct claim;
3. **series hierarchy** — parent/nested relationships from `/series/{id}`.

These must not be collapsed into one database relation.

## 8. Effect of `show_unavailable=true`

For series 1505:

- default: 85 rows
- with unavailable: 104 rows

The flag also caused real pagination where the default response had no next page.

### Conclusion

Any operation intended to build the most complete local provider cache should
use `show_unavailable=true`.

A user-facing “currently purchasable/available” view can be derived separately
from provider availability fields. Availability must not define membership.

## 9. Request-budget implications

Observed public-API design after these live tests:

### Find a series for one selected art

If art detail is absent locally:

1. `GET /arts/{id}`

Cost: **1 request**.

Search/facets cannot currently eliminate this detail request because they expose
empty series fields.

### Load one selected series

For direct membership only:

1. `GET /series/{id}/arts?show_unavailable=true&limit=100`
2. follow `pagination.next_page` only as needed
3. keep only rows whose own `series[]` contains the requested ID

Cost: **1 + actual pagination**, then zero requests from cache.

### Load hierarchy

Only when hierarchy is needed:

1. `GET /series/{id}`
2. cache `parent_id` and `nested_series`

Do **not** recursively fetch every nested series automatically.

### Warm path

Once art detail, direct membership and series composition are cached:

- **0 LitRes requests**

## 10. Local SQLite changes implied by the probe

### `arts`

Keep raw provider art data and a tri-state understanding of series discovery:

- series unknown because only facets/search data is cached;
- detail fetched and series empty;
- detail fetched and series claims present.

A simple empty JSON array is not enough to distinguish the first two states.
The existing `detail_cached` flag is therefore important.

### `series`

Store:

- series ID
- name
- raw detail JSON
- parent ID
- nested-series JSON / hierarchy refresh timestamp
- composition refresh timestamp

### direct membership table

The current `series_arts` table should contain **only verified direct
memberships**.

Suggested fields:

- `series_id`
- `art_id`
- `position` nullable
- `claim_json`
- `seen_at`

### expanded results

If we want to retain every row returned by `/series/{id}/arts`, use a separate
relation/cache, for example:

- `series_expanded_arts(series_id, art_id, raw_context_json)`

Do not treat it as direct membership.

## 11. Parser changes required before this branch is merge-ready

The live probe exposed one correctness issue in the current implementation:

- `LitResCatalog.replace_series_arts()` currently stores every
  `/series/{id}/arts` row in the direct membership table.

That behavior is incompatible with the live API evidence above and must be
changed so direct membership requires a matching provider series claim.

The request-efficient architecture itself remains valid:

- facets for bulk art indexing;
- one detail request for series discovery on a selected art;
- one selected-series fetch plus pagination;
- local SQLite for warm reads;
- no automatic nested-series fan-out.

## 12. Current factual conclusions

As observed on **2026-09-22**:

- `/search`: series key exists but was empty in all 10 tested results.
- `/arts/facets`: series key exists but was empty in all 50 tested RU text rows.
- detail comparisons proved those same facet arts can have real series claims.
- `/arts/{id}`: populated provider series claims were observed.
- series claim position field observed: `art_order`.
- `/series/{id}/arts`: returns expanded rows, not only direct members.
- `show_unavailable=true`: materially increases returned composition and can
  introduce pagination.
- `/series/{id}`: exposes parent/nested hierarchy.
- genre facets use server-defined pagination offsets; one live sample returned
  next offset 8 for limit 10.
- all 19 requests in the expanded run returned HTTP 200.
- the entire expanded probe stayed below the 30-request hard budget.

Raw responses remain available in the CI artifact for regression-fixture
extraction.
