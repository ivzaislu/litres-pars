# Author fallback resolver validation

Date: 2026-09-22  
Branch: `northflank-postgres`

## Problem observed in production

The deployed APK produced many `POST /v1/series/resolve` responses with
HTTP 404, while some requests for other books/series succeeded.

The previous resolver depended heavily on the source-provided `book_title`:

```text
source book title
    ↓
LitRes /search?q=<book title>
    ↓
author + compatible title filter
    ↓
art detail
    ↓
series[] exact normalized name
```

This is brittle when the same work has a materially different title in the
source and in LitRes.

## New resolution strategy

The original title-first path remains unchanged as the fast path.

If it does not resolve a series, the aggregator now performs a bounded fallback:

```text
/search?q=<author>
    ↓
candidate art details
    ↓
author verification on detail
    ↓
series[] claims
    ↓
normalized series_name match
```

The fallback does not require the LitRes title to match the source title.

Bounds:

```text
author search page size:       50
maximum author search pages:    3
maximum inspected art details: 24
```

The fallback stops immediately when it finds the requested provider series.

## Regression test 1: title mismatch recovered by author fallback

Synthetic setup:

```text
request author:      Нужный Автор
request book_title:  Книга из источника
request series_name: Нужная серия

title search result:
  Совсем другая книга
  -> cannot satisfy title-first resolver

author search result:
  Название в LitRes отличается
  -> art detail author = Нужный Автор
  -> series claim = Нужная серия!
  -> series_id = 900
```

Normalization treats punctuation-only differences in the series name as the
same key.

Expected behavior:

```text
/search?q=Книга из источника
/search?q=Нужный Автор
/arts/20
/series/900
/series/900/arts

result series_id = 900
```

This proves the resolver can recover a valid series even when source and LitRes
book titles do not match.

## Regression test 2: author must be verified from art detail

Author search is fuzzy and cannot be trusted by itself.

Synthetic author-search results deliberately include:

```text
art 30 -> detail author = Другой Автор -> series "Серия"
art 31 -> detail author = Автор        -> series "Серия"
```

Expected result:

```text
art 30 rejected after detail author verification
art 31 accepted
series_id = 902
```

This prevents a same-named series belonging to another author from being
selected merely because it appeared in the author search response.

## Cache and provider semantics

This change affects only discovery of the provider `series_id`.

It does not change:

- PostgreSQL cache TTL;
- direct vs expanded membership;
- `art_order` grouping;
- preservation of multiple text/audio editions;
- unpositioned direct arts;
- the public `SeriesInfo` response shape.

Once a series has been resolved and cached, subsequent fresh requests still
produce zero LitRes calls.

## Expected production effect

The main class of false 404 that should decrease is:

```text
same author
same series
different source/LitRes book title
```

A 404 can still be legitimate when:

- LitRes has no matching series claim;
- author naming differs enough to fail normalized equality;
- the matching art is outside the bounded author-search scan;
- the source series name and LitRes series name are materially different.

Those remaining cases should be investigated from concrete failed inputs before
loosening identity checks further.
