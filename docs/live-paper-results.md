# LitRes paper-book live probe

Branch: `live-foundation-probe`

Purpose: verify whether `paper_book` results are separate art rows that also
appear in `/series/{id}/arts`, or whether the apparent series over-count is
explained by text/audio alternatives instead.

Target works:

- Сергей Лукьяненко — Ночной Дозор
- Сергей Лукьяненко — Лабиринт отражений
- Роман Злотников — Обреченный на бой
- Роман Злотников — Арвендейл

The probe compares `/search?types=paper_book` with the corresponding live series
composition and records provider IDs and relation fields.

## Results

Pending CI run.
