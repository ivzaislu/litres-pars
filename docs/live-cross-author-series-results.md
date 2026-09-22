# Cross-author text/audio series live probe

Branch: `live-foundation-probe`

Purpose: independently validate the format-multiplication model learned from
Lukyanenko and Zlotnikov on unrelated authors and provider series.

The public LitRes pages for these controls expose both text and audio editions.
The Foundation API probe then verifies the raw provider rows directly.

## Final CI run

- Commit: `9f766e57490b4dbeae0923fcd6aaa18917ec9502`
- GitHub Actions:
  https://github.com/ivzaislu/litres-pars/actions/runs/35681752452
- Result: **success**
- HTTP requests: **14**
- HTTP statuses: **14 x 200**
- Response bytes: **625,932**
- Sum of measured HTTP elapsed time: **4,186.51 ms**
- Artifact: `litres-cross-author-series-probe`
- Artifact id: `10675134095`
- Artifact SHA-256:
  `sha256:6c3648d3af6f675fff5eeea4eb222f315f7ef83e6ed5754fd1b45866ba1d3497`

Each series required exactly two requests in this run:

1. `GET /series/{id}`
2. `GET /series/{id}/arts?offset=0&limit=100&show_unavailable=true`

No control series required a second composition page.

## Controls and results

| Author | Series | ID | text arts | audio arts | ordered positions | positions with both formats | proven reciprocal alt pairs | expanded rows |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Макс Фрай | Лабиринты Ехо | 1324 | 8 | 8 | 8 | 8 | 7 | 0 |
| Вадим Панов | Тайный Город | 2560 | 42 | 35 | 34 | 26 | 26 | 0 |
| Алексей Пехов | Хроники Сиалы | 2827 | 4 | 7 | 3 | 3 | 3 | 0 |
| Сергей Тармашев | Древний | 11402 | 7 | 3 | 7 | 3 | 3 | 12 |
| Дмитрий Емец | Таня Гроттер | 2574 | 15 | 14 | 14 | 14 | 14 | 0 |
| Анджей Сапковский | Ведьмак | 370 | 9 | 9 | 7 | 7 | 2 | 0 |
| Вера Камша | Отблески Этерны. Красный | 924954 | 3 | 3 | 3 | 3 | 3 | 0 |

Here `text arts` means provider `art_type=0`, and `audio arts` means
provider `art_type=1`.

## Direct evidence that extra rows are formats, not extra books

### Макс Фрай — Лабиринты Ехо

The provider returned exactly:

- 8 text arts
- 8 audio arts
- 8 ordered series positions

Every position contained one text row and one audio row.

Examples:

- position 1:
  - audio art `56597898` — `Чужак (сборник)`
  - text art `118965` — `Чужак`
- position 2:
  - audio art `56801156`
  - text art `118966`

Seven of the eight positions also had a reciprocal
`alternative_versions` link between the text and audio art.

Therefore 16 direct provider rows represent 8 logical ordered slots.

## Вадим Панов — Тайный Город

The provider returned:

- 42 text arts
- 35 audio arts
- 34 ordered positions
- 26 positions containing both text and audio

For positions 1-19 and several later positions, text/audio counterparts were
verified at the same `art_order`.

Examples:

- position 1:
  - text `119716` — `Войны начинают неудачники`
  - audio `22477346` — same title
- position 2:
  - text `119717`
  - audio `22570136`

All 26 observed mixed-format positions had a proven reciprocal
`alternative_versions` pair.

Three positions contained one text row and **more than one audio row**.

This is direct evidence that one logical work slot can map to more than two
provider arts.

## Алексей Пехов — Хроники Сиалы

This is the clearest multi-edition positive control.

The provider returned:

- 4 text arts
- 7 audio arts
- 3 ordered positions with multiple formats

For each of positions 1-3 there were:

- 1 text art
- 2 audio arts

Example position 1:

- text `129554` — `Крадущийся в тени`
- audio `23807544` — `Крадущийся в тени`
- audio `71129467` — `Крадущийся в тени. Издание 2-е`

The same 1-text + 2-audio pattern appeared at positions 2 and 3.

At least one reciprocal text/audio `alternative_versions` pair was confirmed
for every one of these positions.

### Consequence

The model cannot be:

`one series position = at most one text + one audio`.

It must be:

`one series position = N provider arts / editions / formats`.

## Сергей Тармашев — Древний

The provider returned:

- 7 direct text arts
- 3 direct audio arts
- 7 direct ordered positions
- 3 positions with both formats
- 12 expanded rows

Mixed positions 1, 2 and 4 each had one text + one audio art, and all three had
reciprocal `alternative_versions` links.

Examples:

- position 1:
  - text `428862` — `Катастрофа`
  - audio `2779425` — `Древний. Катастрофа`
- position 2:
  - text `428872`
  - audio `2779435`

The 12 expanded rows reinforce the separate hierarchy rule: endpoint output and
direct membership are not identical concepts.

## Дмитрий Емец — Таня Гроттер

The provider returned:

- 15 text arts
- 14 audio arts
- 14 ordered positions
- all 14 ordered positions contained text + audio

Every one of those 14 positions had a proven reciprocal cross-format
`alternative_versions` pair.

Examples:

- position 1:
  - text `118927`
  - audio `6717520`
- position 2:
  - text `118928`
  - audio `8495984`

This is the strongest large clean control in the sample.

## Анджей Сапковский — Ведьмак

The provider returned:

- 9 text arts
- 9 audio arts
- 7 ordered positions
- all 7 ordered positions contained both text and audio

Examples:

- position 1:
  - text `19487529`
  - audio `37926337`
  - both titled `Последнее желание`
- position 2:
  - text `122541`
  - audio `39832530`
  - both titled `Меч Предназначения`

However, only **2 of the 7** mixed positions had a reciprocal
`alternative_versions` relation in the returned data.

### Important consequence

`alternative_versions` is strong positive evidence when present, but it is
**not complete enough to be the sole grouping key**.

The Foundation API can expose text and audio arts at the same provider series
position even when it does not give a reciprocal alternative-version link.

## Вера Камша — Отблески Этерны. Красный

The provider returned:

- 3 text arts
- 3 audio arts
- 3 ordered positions
- all 3 positions contained one text + one audio art
- all 3 positions had reciprocal `alternative_versions` links

Examples:

- position 1:
  - audio `73308548`
  - text `121405`
  - `Красное на красном`
- position 2:
  - text `121667`
  - audio `74079967`
  - `От войны до войны`

## Akunin hierarchy counterexample

An earlier candidate control was series `2025`,
`Приключения Эраста Фандорина`.

Its public LitRes page contains text/audio material, but the Foundation API
series structure is different:

- direct rows for series 2025 were 16 audio arts;
- 13 additional rows were expanded results;
- series detail exposed two nested series:
  - `Планета Вода`
  - `Нефритовые четки (Приключения Эраста Фандорина)`

Therefore it was removed from the strict same-series text/audio positive-control
set.

This is useful evidence that the website presentation and Foundation provider
series hierarchy are not guaranteed to map one-to-one.

## What is now confirmed

Across the seven positive controls, every series satisfied all of these facts:

1. Foundation returned direct text arts.
2. Foundation returned direct audio arts.
3. At least one `art_order` position contained multiple format-specific arts.
4. The apparent row count was therefore larger than the count of logical ordered
   series positions.
5. At least one reciprocal cross-format `alternative_versions` relation was
   independently verified.

The pattern is not tied to Lukyanenko, Zlotnikov, one publisher or one genre.

## Refined parser model

The raw parser/local cache should preserve four distinct concepts:

### 1. Provider art

Every LitRes `art_id` is stored separately.

Do not collapse text/audio editions while ingesting provider data.

### 2. Direct series membership

A direct relation exists only when the art's own `series[]` contains the
requested `series_id`.

### 3. Series position

Provider `art_order` / `number` identifies an ordered slot in that series.

Multiple arts can share one position.

Observed examples include:

- 1 text + 1 audio
- 1 text + 2 audio

### 4. Alternative-version relation

`alternative_versions` is stored as provider evidence between arts.

It is useful to confirm format equivalence, but must **not** be required for
grouping because the Witcher sample had text+audio at every ordered position
while only 2/7 positions exposed reciprocal alternative links.

## Safe logical-work inference inside a provider series

For the parser/cache layer, do not create an Abred-style canonical work.

But if a caller needs provider-local grouping, the strongest available key is:

`(series_id, art_order)`

with all underlying provider arts retained.

`alternative_versions` can be attached as corroborating evidence.

This grouping should remain explicitly provider-local because:

- some direct rows have no `art_order`;
- umbrella series can include expanded hierarchy rows;
- the parser must not claim global/canonical work identity.

## Final conclusion

The original suspicion is now confirmed on a substantially broader sample:

> LitRes Foundation series composition counts arts/editions/formats, not just
> logical books.

A logical ordered work can generate multiple direct rows because its text and
audio editions have separate `art_id` values. In at least one tested series,
one ordered work generated one text art plus two different audio arts.

Therefore any local database or consumer that wants a count of logical books in
a cycle must not use `len(/series/{id}/arts)`.

It should preserve all raw arts and derive provider-local ordered slots from
direct membership plus `art_order`, while treating `alternative_versions` as
supporting but incomplete evidence.
