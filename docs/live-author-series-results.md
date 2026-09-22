# LitRes author series live probe

Branch: `live-foundation-probe`

Focused cross-check of real LitRes Foundation series behavior using works/series
associated with Sergey Lukyanenko and Roman Zlotnikov.

The probe uses public Foundation API only and does not apply Abred identity or
canonicalization rules.

## Final CI run

- Commit: `ca5352bfc7a9246b34c845be364791b5a58e7203`
- GitHub Actions:
  https://github.com/ivzaislu/litres-pars/actions/runs/35678924998
- Result: **success**
- HTTP requests: **8**
- HTTP statuses: **8 x 200**
- Response bytes: **339,059**
- Sum of measured HTTP elapsed time: **3,791.36 ms**
- Artifact: `litres-author-series-live-probe`
- Artifact id: `10673634697`
- Artifact SHA-256:
  `aa822f3600021588d1696bc006e449d1d1df168df390c0710e259e86da5ffa0c`

Each series required exactly:

1. `GET /series/{id}`
2. `GET /series/{id}/arts?offset=0&limit=100&show_unavailable=true`

None of the four tested series required a second composition page.

## Tested series

| Author | LitRes series | ID | detail arts_count | unique_arts_count | nested series | returned rows | direct rows | expanded rows |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Сергей Лукьяненко | Дозоры | 728 | 53 | 36 | 1 | 65 | 61 | 4 |
| Сергей Лукьяненко | Диптаун | 1323 | 6 | 3 | 0 | 6 | 6 | 0 |
| Роман Злотников | Грон | 587 | 12 | 6 | 0 | 12 | 12 | 0 |
| Роман Злотников | Арвендейл | 157 | 12 | 8 | 0 | 13 | 13 | 0 |

Direct membership means the returned art's own `series[]` contains the requested
series ID. Expanded means the endpoint returned the art but its own claims did
not contain that ID.

## 1. Сергей Лукьяненко — series 728 «Дозоры»

Series detail:

- `parent_id = null`
- `arts_count = 53`
- `unique_arts_count = 36`
- one nested series:
  - id `482348`
  - name `Школьный Надзор`
  - summary `arts_count = 4`
  - summary `unique_arts_count = 3`

Composition with `show_unavailable=true`:

- 65 rows returned in one page
- 61 rows directly claim `series.id = 728`
- 4 rows do not directly claim series 728
- direct ratio: **93.85%**

This is the key comparison point: the only tested series with a non-empty
`nested_series` array is also the only tested series whose
`/series/{id}/arts` response contains expanded rows.

The direct set includes many authors, not only Lukyanenko. Sergey Lukyanenko was
present on 19 of the 61 direct rows. This is expected provider-side behavior for
the broader `Дозоры` series and means author filtering must not define series
membership.

Only 12 of the 61 direct rows exposed an ordering value in the observed
`art_order` claims. The observed positions were repetitions of values 1 through
6 across multiple formats/works.

### Consequence

For a parent/umbrella series, direct membership must still be filtered by the
matching claim. The remaining rows can be retained as expanded/descendant
results, but not inserted into the direct membership relation.

## 2. Сергей Лукьяненко — series 1323 «Диптаун»

The test was originally described as “Лабиринт отражений”, but the Foundation
provider's actual series name for ID 1323 is **«Диптаун»**. The documentation and
probe now use the provider name.

Series detail:

- `parent_id = null`
- `arts_count = 6`
- `unique_arts_count = 3`
- `nested_series = []`

Composition:

- 6 rows
- 6 direct rows
- 0 expanded rows
- all 6 direct rows include Sergey Lukyanenko as author
- all 6 rows expose an ordering claim
- observed positions: `1, 2, 3`, duplicated across formats

### Consequence

For a simple series without nested series, the endpoint behaved exactly like a
direct composition endpoint in this test.

## 3. Роман Злотников — series 587 «Грон»

Series detail:

- `parent_id = null`
- `arts_count = 12`
- `unique_arts_count = 6`
- `nested_series = []`

Composition:

- 12 rows
- 12 direct rows
- 0 expanded rows
- all 12 rows contain Roman Zlotnikov as author
- all 12 rows expose ordering
- observed positions: `1..6`, duplicated across formats

### Consequence

The direct member count is larger than `unique_arts_count` because provider
formats/versions can appear as separate art rows. The parser must preserve raw
art IDs and must not infer “6 books” from the 12 returned rows without an
application-level format collapsing rule.

## 4. Роман Злотников — series 157 «Арвендейл»

Series detail:

- `parent_id = null`
- `arts_count = 12`
- `unique_arts_count = 8`
- `nested_series = []`

Composition:

- 13 rows
- 13 direct rows
- 0 expanded rows
- all 13 rows contain Roman Zlotnikov as an author
- co-authors observed on direct rows:
  - Юлия Остапенко: 2 rows
  - Антон Корнилов: 1 row
- all 13 rows expose ordering
- observed positions range from 1 through 8, with duplicated positions across
  different formats/versions

Notably, the live composition returned 13 direct art rows while series detail
reported `arts_count = 12`.

### Consequence

`arts_count` must be treated as provider metadata, not as a strict invariant
that must equal the number of direct rows returned by
`/series/{id}/arts?show_unavailable=true`.

The crawler/database should record the reported counter and the observed direct
membership independently.

## Format accounting correction

The composition endpoint returns **art rows**, not one row per logical work.

The live sample makes this explicit:

| Series | direct art rows | art_type=0 text | art_type=1 audio | unique ordered positions | rows without position |
| --- | ---: | ---: | ---: | ---: | ---: |
| Дозоры | 61 | 30 | 31 | 6 | 49 |
| Диптаун | 6 | 3 | 3 | 3 | 0 |
| Грон | 12 | 6 | 6 | 6 | 0 |
| Арвендейл | 13 | 8 | 5 | 8 | 0 |

For the simple ordered series this explains the apparent over-count:

- `Диптаун`: 3 logical positions, each represented by text + audio = 6 art rows.
- `Грон`: 6 logical positions, each represented by text + audio = 12 art rows.
- `Арвендейл`: 8 logical positions; positions 1–5 have both text and audio,
  positions 6–8 currently have only the text art in this response = 13 art rows.

Therefore the parser must distinguish:

1. **art rows / editions / formats** — raw LitRes objects, always preserved;
2. **series position** — provider `art_order` / `number`;
3. **logical work slot** — a higher-level grouping derived from position and
   provider relations, never from row count alone.

In these four live `/series/{id}/arts` samples the observed `art_type` values
were only:

- `0` — text
- `1` — audio

LitRes search also supports a `paper_book` type, but these particular series
composition samples did **not** expose a distinct paper art type. We should test
paper editions separately before defining how they map into the same logical
work slot.

The earlier statement that “more rows than books” is therefore not itself an API
inconsistency. The meaningful comparison is between logical positions /
`unique_arts_count` and format-specific art rows.

## Cross-series conclusion

Across these four series, the strongest observed rule is:

- no nested series:
  - Диптаун: 6/6 direct
  - Грон: 12/12 direct
  - Арвендейл: 13/13 direct
- nested series present:
  - Дозоры: 61 direct + 4 expanded

This does not prove that every future Foundation series will behave identically,
but it materially strengthens the earlier Metro finding: expanded rows are
associated with provider series hierarchy rather than being random contamination.

The safe parser rule remains:

> Every row returned by `/series/{id}/arts` is cached as provider output, but
> direct membership is created only when that row's own `series[]` contains the
> requested `series_id`.

## Search-discovery caveat discovered during the probe

The first version of this CI probe tried to find Zlotnikov's `Грон` using:

`GET /search?q=Грон`

That search returned books by author **Ольга Грон**, not Roman Zlotnikov's
series, and therefore failed the author verification step.

This is a useful parser finding:

- a bare title/series-name search is not a reliable way to identify a provider
  series;
- local art IDs / cached series claims are preferable;
- when search is unavoidable, author verification against `persons[]` is
  mandatory.

The final series test therefore probes verified LitRes series IDs directly.

## Request-budget implication

Once a series ID is known, every tested series required only **2 requests**:

1. series detail
2. one composition page

If only direct composition is needed and hierarchy metadata is already cached,
the steady-state cold cost can be reduced to **1 composition request**.

After caching:

- series detail: 0 requests
- composition: 0 requests
- direct membership filtering: local SQLite only

## Local database implications

These tests reinforce keeping separate concepts:

### Series metadata

Store provider counters as metadata, without assuming equality with observed
rows:

- `arts_count`
- `unique_arts_count`
- `parent_id`
- `nested_series`
- raw detail JSON

### Direct membership

Insert only rows with a matching provider claim:

- `series_id`
- `art_id`
- `position`
- raw `claim_json`

### Expanded membership/cache

Rows returned by a series endpoint without the matching claim should be retained
separately if useful for hierarchy discovery, but never treated as direct
membership.

### Formats

Do not collapse duplicate positions or art rows in the parser layer. `Грон`
showed six unique positions across twelve art rows, and `Диптаун` showed three
positions across six rows. Format/version collapsing belongs above the raw
provider parser.

## Current confidence

High confidence for the behavior of these exact four series as observed on
2026-09-22.

Moderate confidence that `nested_series` is the main reason for expanded rows:
this pattern now appears consistently in the tested `Дозоры` and earlier
`Метро` hierarchy samples, while three non-nested author series returned only
direct members. More heterogeneous series should still be sampled before making
it a hard invariant.
