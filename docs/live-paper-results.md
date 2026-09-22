# LitRes paper-book live probe

Branch: `live-foundation-probe`

Purpose: verify whether `paper_book` results are separate art rows that also
appear in `/series/{id}/arts`, or whether the apparent series over-count is
explained by text/audio alternatives instead.

## Final observed behavior

CI run:
https://github.com/ivzaislu/litres-pars/actions/runs/35679992143

Result: **success**

- 8 HTTP requests
- 8 x HTTP 200
- 4 series composition requests
- 4 combined search requests
- combined search explicitly requested:
  - `text_book`
  - `audiobook`
  - `paper_book`

The earlier attempt to call `/search` with only `types=paper_book` returned
HTTP 500. Therefore absence of results from that mode is not usable evidence.
The successful probe used the normal combined-type search and inspected both the
outer result `type` and `payload.extra.counters`.

## Results

| Work | series art types | search text | search audio | search paper | paper rows in series |
| --- | --- | ---: | ---: | ---: | ---: |
| Ночной Дозор | 0, 1 | 9 | 5 | **0** | 0 |
| Лабиринт отражений | 0, 1 | 6 | 5 | **0** | 0 |
| Обреченный на бой | 0, 1 | 2 | 1 | **0** | 0 |
| Арвендейл | 0, 1 | 8 | 5 | **0** | 0 |

For all four searches, LitRes returned:

`payload.extra.counters.paper_book = 0`

The returned search rows contained only outer types `text_book` and
`audiobook`.

The corresponding `/series/{id}/arts?show_unavailable=true` responses contained
only:

- `art_type = 0`
- `art_type = 1`

No third art type or paper-specific row was observed.

## Text/audio alternative-version proof

A separate live CI probe verified that duplicate ordered positions are not merely
similar titles.

For direct members, the paired text/audio arts:

1. have the same provider `art_order`;
2. have different `art_type` values (0 vs 1);
3. reference each other's art IDs in `alternative_versions`;
4. the relation is reciprocal.

Observed reciprocal cross-format pairs:

| Series | logical ordered positions | reciprocal text/audio pairs |
| --- | ---: | ---: |
| Дозоры | 6 observed ordered positions | 6 |
| Диптаун | 3 | 3 |
| Грон | 6 | 6 |
| Арвендейл | 8 | 5 |

For `Арвендейл`, positions 1-5 had text/audio pairs, while positions 6-8 had
only text rows in the live response.

## Conclusion

The original hypothesis is **partially confirmed and refined**.

Confirmed:

- the series endpoint returns multiple LitRes art rows for one logical ordered
  work when multiple digital formats exist;
- in these samples, the extra rows are directly proven text/audio alternatives;
- the provider itself links those rows via reciprocal `alternative_versions`.

Not confirmed:

- paper editions do **not** explain the extra rows in these four tested series;
- LitRes reported `paper_book = 0` for all four control searches;
- no paper row appeared in the corresponding series compositions.

Therefore the parser should currently model:

- raw art rows;
- provider series membership;
- series position (`art_order`);
- provider alternative-version links;

and should **not** assume that a second row at the same position is a separate
book.

A separate positive-control paper title is still useful later to document how
Foundation API represents a real `paper_book` when one is available.
