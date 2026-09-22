# Northflank production validation

Date: 2026-09-22

These checks were executed against the deployed Northflank `litres-api`
service using the real public API endpoint and the private PostgreSQL addon.

The purpose was to validate that the production path:

```text
client -> Northflank FastAPI -> PostgreSQL cache -> LitRes Foundation
```

preserves the provider semantics established by the earlier live Foundation
probes.

## Case 1: Alexey Pekhov — Chronicles of Siala

Resolve input:

```json
{
  "author": "Алексей Пехов",
  "book_title": "Крадущийся в тени",
  "series_name": "Хроники Сиалы"
}
```

Observed result:

```text
series_id: 2827
name: Хроники Сиалы
ordered_work_count: 3
direct_art_count: 11
unpositioned_direct_art_count: 2
expanded_art_count: 0
```

All three ordered positions preserved one text art plus two audio arts:

```text
position 1
  text  129554  Крадущийся в тени
  audio 23807544 Крадущийся в тени
  audio 71129467 Крадущийся в тени. Издание 2-е

position 2
  text  129555  Джанга с тенями
  audio 39463232 Джанга с тенями
  audio 71190040 Джанга с тенями. Издание 2-е

position 3
  text  129556  Вьюга теней
  audio 42280898 Вьюга теней
  audio 71407096 Вьюга теней. Издание 2-е
```

This validates that one logical provider-local series position can contain more
than two LitRes arts and that the API does not collapse distinct audio editions.

The remaining two direct arts have no provider `art_order`. They are retained
as direct membership but are intentionally excluded from ordered `works` and
returned through `unpositioned_arts`.

## Case 2: Sergey Tarmashev — Ancient

Resolve input:

```json
{
  "author": "Сергей Тармашев",
  "book_title": "Катастрофа",
  "series_name": "Древний"
}
```

Observed result:

```text
series_id: 11402
name: Древний
ordered_work_count: 7
direct_art_count: 10
unpositioned_direct_art_count: 0
expanded_art_count: 12
positions: 1,2,3,4,5,6,7
```

The 12 expanded rows were the `Древний. Предыстория` books returned by the
provider series endpoint through hierarchy expansion.

They remained in `expanded_arts` and did not enter the seven ordered direct
works.

This validates the production PostgreSQL separation:

```text
series_arts          = verified direct membership
series_expanded_arts = hierarchy-expanded endpoint output
```

## Production conclusions

The Northflank deployment reproduces the same provider behavior as the isolated
live probes:

- LitRes series composition rows are provider arts/editions, not logical books.
- `art_order` is a provider-local slot and multiple arts may share one slot.
- Direct rows without `art_order` must be retained but not assigned an invented
  logical position.
- Expanded hierarchy rows must not be counted as direct series membership.
- PostgreSQL preserves all raw provider art identities while the API exposes a
  derived `works` view for the application.

No parser rule needs to change based on these production checks.


## Cache validation: Roman Zlotnikov — Gron

The same `POST /v1/series/resolve` request was executed twice consecutively
against the deployed Northflank API.

Both responses returned:

```text
series_id: 587
name: Грон
ordered_work_count: 6
direct_art_count: 12
unpositioned_direct_art_count: 0
expanded_art_count: 0
cached_at: 2026-09-22T05:16:30.581280+00:00
```

The identical `cached_at` value on both responses confirms that the second
request was served from the existing PostgreSQL cache and did not refresh the
series composition from LitRes.

This validates the intended steady-state path:

```text
client -> Northflank FastAPI -> PostgreSQL -> SeriesInfo
                                  |
                                  +-> 0 LitRes requests while cache is fresh
```
