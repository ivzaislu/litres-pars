# LitRes Foundation live probe results

Branch: `live-foundation-probe`

This document records observed LitRes Foundation API behavior used to design the
standalone parser and local SQLite cache. It intentionally records provider facts
only; Abred identity/canonicalization rules do not belong here.

## Probe constraints

- Public Foundation API only; no partner credentials.
- Hard network budget: 30 HTTP requests per CI run.
- Requests are throttled.
- Temporary failures are retried by the parser and retries count against the budget.
- Raw JSON responses and `report.json` are preserved as a CI artifact.
- The probe is not a bulk crawler.

## Questions under test

1. Does `/search` expose series claims?
2. Does `/arts/{id}` expose the complete provider-side series claim list?
3. Does `/arts/facets` expose series claims strongly enough to build `art -> series`
   locally without one detail request per art?
4. Does `/series/{id}/arts` return full series composition and provider ordering?
5. Do member rows repeat the requested series claim and its position?
6. Is `/series/{id}` unnecessary for composition, so it can remain optional metadata?
7. Does server pagination provide authoritative next offsets?
8. Do `/genres` and `/genres/{id}/arts/facets` support the segmented local-catalog crawl?

## Results

Pending first CI live run.

After each meaningful live run, record here:

- CI run URL and commit SHA.
- Total HTTP requests and response bytes.
- Observed status codes / retries.
- Search item keys and series claim presence.
- Art detail series claim keys.
- Facets item keys and series claim presence.
- Facets-vs-detail comparison results.
- Series composition page sizes, pagination and ordering fields.
- Genre-tree / genre-facets observations.
- Consequences for the local SQLite schema and request budget.

## Target request budgets

- Fully cached art and series: 0 HTTP requests.
- Cached art with known series claim, uncached selected series: only
  `/series/{id}/arts` pagination.
- Unknown series for a selected art: one `/arts/{id}` request.
- Cold art -> selected series: one `/arts/{id}` plus the actual
  `/series/{id}/arts` pagination.
- Multiple series claims must not cause automatic request fan-out.
