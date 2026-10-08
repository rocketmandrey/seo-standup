# SEO Standup

**SEO — это не магия про ключевые слова. Это проектирование продукта
под уже существующий спрос.**

Агентский скилл + 15-минутный стендап об одном и том же пайплайне:

```
Спрос → Кластеры → Сеть страниц → Лучший ответ → Цикл улучшения
  ↑                                                      │
  └──────────────── Search Console ←─────────────────────┘
```

Работает **без единого платного API** — Wordstat, поисковые подсказки,
сама выдача и Search Console закрывают каждый этап. Платные источники
(Topvisor, XMLRiver, Semrush) подключаются опционально, если у вас уже
есть свои ключи.

## Что внутри

| Файл | Что это |
|---|---|
| [`SKILL.md`](SKILL.md) | Скилл для AI-агента: 5 этапов пайплайна с критериями «done when», шаблоном отчёта и чек-листом |
| [`talk/seo-bez-magii.md`](talk/seo-bez-magii.md) | Сценарий стендапа «SEO без магии» на ~15 минут: рынок → сеть → замкнутый цикл |

## Установка скилла

**Claude Code:**

```bash
mkdir -p ~/.claude/skills/seo-standup
curl -fsSL https://raw.githubusercontent.com/rocketmandrey/seo-standup/main/SKILL.md \
  -o ~/.claude/skills/seo-standup/SKILL.md
```

**Hermes Agent:**

```bash
mkdir -p ~/.hermes/skills/research/seo-standup
curl -fsSL https://raw.githubusercontent.com/rocketmandrey/seo-standup/main/SKILL.md \
  -o ~/.hermes/skills/research/seo-standup/SKILL.md
```

Дальше просто попросите агента: *«проверь спрос вокруг X и спроектируй
структуру сайта»* — скилл подхватится сам.

## Пайплайн в пяти строках

1. **Собрать спрос** — реальные запросы людей, с датой, регионом и match type.
2. **Кластеризовать** — LLM раскладывает тысячи запросов по интентам.
3. **Спроектировать сеть** — каждой странице свой кластер; без дыр и каннибализации.
4. **Контент по ТЗ из выдачи** — не «напиши SEO-статью», а лучший ответ, чем у топа.
5. **Замкнуть цикл** — Search Console показывает реальные запросы → правим → повторяем.

## Лицензия

[MIT](LICENSE)

## Быстрый прогон с XMLRiver (`tools/standup.py`)

Если есть ключ [XMLRiver](https://xmlriver.com) — один скрипт делает весь стендап: спрос → кластеры по интентам → живая выдача Яндекса → `REPORT.md`. Только stdlib Python, ничего ставить не надо. ~25 ₽ за 1000 запросов: прогон на 3 seed + 15 SERP ≈ 0,5 ₽.

```bash
cp .env.example .env            # впиши XMLRIVER_USER и XMLRIVER_KEY
python3 tools/standup.py --balance
python3 tools/standup.py "зерносушилка" "сушилка для зерна" --serp 15
python3 tools/standup.py "лепнина" --regions 213 --lr 213   # Москва
```

Результат в `runs/<seed>-<дата>/`: `REPORT.md` (интенты, кластеры → страницы, кто в выдаче), `semantic-map.csv`, `serp-top10.csv`, `associations.csv`, `raw/`.

Агенту достаточно сказать: «прогони standup по запросам X, Y и сделай выводы» — он запустит скрипт и дочитает `REPORT.md`/CSV.
