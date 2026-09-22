# Cross-author text/audio series live probe

Branch: `live-foundation-probe`

Purpose: independently validate the model learned from Lukyanenko and Zlotnikov.

Control series are publicly known on LitRes to have both text and audio editions:

- Макс Фрай — Лабиринты Ехо (series 1324)
- Вадим Панов — Тайный Город (series 2560)
- Алексей Пехов — Хроники Сиалы (series 2827)
- Сергей Тармашев — Древний (series 11402)
- Борис Акунин — Приключения Эраста Фандорина (series 2025)

For each provider series the CI probe requires:

1. direct rows with text art type;
2. direct rows with audio art type;
3. at least one `art_order` position containing multiple formats;
4. at least one reciprocal `alternative_versions` relation across different
   art types at the same series position.

This is stronger evidence than title matching or row-count arithmetic.

## Results

Pending CI run.
