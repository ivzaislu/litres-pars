# LitRes author series live probe

Branch: `live-foundation-probe`

This document is reserved for focused cross-checks of real LitRes series behavior
using works by:

- Сергей Лукьяненко
- Роман Злотников

The dedicated CI probe uses public Foundation API only, with a hard budget of
36 HTTP requests.

## Target cases

- Сергей Лукьяненко — `Ночной Дозор`
- Сергей Лукьяненко — `Лабиринт отражений`
- Роман Злотников — `Грон`
- Роман Злотников — `Арвендейл`

The probe does not trust remembered series names. It searches the work, verifies
the author in `/arts/{id}`, reads actual `series[]` claims, then selects the
matching provider series and probes:

- `/series/{id}`
- `/series/{id}/arts?show_unavailable=true&limit=100`
- up to three server-driven pages

For every case it records direct membership vs expanded rows and observed
ordering.

## Results

Pending CI run.
