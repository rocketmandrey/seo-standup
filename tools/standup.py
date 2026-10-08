#!/usr/bin/env python3
"""SEO Standup через XMLRiver: спрос (Wordstat) → кластеры по интентам → живая выдача Яндекса → отчёт.

    python3 tools/standup.py "зерносушилка" "сушилка для зерна" --serp 15 --regions 225
    python3 tools/standup.py --balance

Ключи: XMLRIVER_USER / XMLRIVER_KEY в .env в корне репо (см. .env.example).
Только stdlib. Тариф ~25 ₽ / 1000 запросов — перед запуском печатает смету.
"""
import argparse, collections, concurrent.futures as cf, csv, json, os, re, sys, time
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUB_PER_CALL = 0.025

INTENTS = [  # первое совпадение побеждает; порядок важен
    ("купить", r"купить|продам|продаж|авито|\bбу\b|цен[аы]|стоимост|сколько стоит|заказать|заказ|доставк|недорог|дешев|прайс|оптом|магазин|аренд|услуг|под ключ|производител|поставщик|в наличии|интернет"),
    ("сравнить", r"отзыв|лучш|рейтинг|\bтоп\b|сравнен|\bили\b|\bvs\b|какой выбрать|какую выбрать|какое выбрать|обзор|плюсы и минусы"),
    ("узнать", r"^как\b|\bкак\b|^что\b|\bчто такое|почему|зачем|своими руками|инструкц|схем[аы]|чем отлича|когда|можно ли|\bэто\b|сделать|виды|типы|фото|видео|чертеж|принцип|устройств|характеристик|расч[её]т|работа\b|эксплуатац"),
]
STOP = set("в во на для и с со по из от до к у о об при без за под над the a of for to это".split())


def env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() and v and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    try:
        return {"user": os.environ["XMLRIVER_USER"], "key": os.environ["XMLRIVER_KEY"]}
    except KeyError:
        sys.exit("Нет XMLRIVER_USER / XMLRIVER_KEY — скопируй .env.example в .env и впиши ключи.")


def get(url, params, auth, tries=4):
    q = urllib.parse.urlencode({**auth, **params})
    for i in range(tries):  # XMLRiver часто отдаёт 500 «повторите запрос» — ретраим
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=90) as r:
                body = r.read().decode("utf-8", "replace")
            if '"error"' in body[:200] or "<error" in body[:500]:
                raise RuntimeError(body[:200])
            return body
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    print(f"  ! {params.get('query')}: {err}", file=sys.stderr)
    return None


def wordstat(seed, auth, regions):
    p = {"query": seed, **({"regions": regions} if regions else {})}
    body = get("https://xmlriver.com/wordstat/new/json", p, auth)
    if not body:
        return [], []
    d = json.loads(body)
    rows = lambda k: [(x["text"], int(x["value"])) for x in d.get(k, [])]
    return rows("popular"), rows("associations")


def serp(query, auth, lr):
    body = get("https://xmlriver.com/search_yandex/xml", {"query": query, "lr": lr, "groupby": 10}, auth)
    if not body:
        return []
    root = ET.fromstring(body)
    out = []
    for i, doc in enumerate(root.iter("doc"), 1):
        url = doc.findtext("url") or ""
        title = "".join(doc.find("title").itertext()) if doc.find("title") is not None else ""
        out.append({"pos": i, "domain": urllib.parse.urlparse(url).netloc.removeprefix("www."), "url": url, "title": title})
    return out[:10]


def n(x):
    return f"{x:,}".replace(",", " ")


def intent(phrase):
    for name, rx in INTENTS:
        if re.search(rx, phrase):
            return name
    return "общий"


def stem(w):
    # ponytail: обрезка до 5 букв вместо морфологии; pymorphy3, если кластеры поедут
    return w[:5] if len(w) > 5 else w


def cluster_key(phrase, seed_stems, df):
    words = [stem(w) for w in re.findall(r"[a-zа-яё0-9]+", phrase) if w not in STOP]
    rest = [w for w in words if w not in seed_stems]
    if not rest:
        return "(ядро)"
    return max(rest, key=lambda w: df[w])  # самый частый модификатор = тема кластера


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", nargs="*")
    ap.add_argument("--serp", type=int, default=10, help="сколько топ-фраз прогнать по живой выдаче (0 = не надо)")
    ap.add_argument("--regions", default="", help="регион Wordstat: 225 Россия, 213 Москва, 2 СПб (пусто = все)")
    ap.add_argument("--lr", default="225", help="регион выдачи Яндекса")
    ap.add_argument("--min-freq", type=int, default=10)
    ap.add_argument("--out", help="папка результата (по умолчанию runs/<seed>-<дата>)")
    ap.add_argument("--balance", action="store_true")
    a = ap.parse_args()
    auth = env()

    if a.balance:
        print("Баланс XMLRiver:", get("https://xmlriver.com/api/get_balance/", {}, auth), "₽")
        return
    if not a.seeds:
        ap.error("нужен хотя бы один seed")

    calls = len(a.seeds) + a.serp
    print(f"Смета: ≤{calls} запросов ≈ {calls * RUB_PER_CALL:.2f} ₽ (+ретраи)")
    slug = re.sub(r"[^a-zа-яё0-9]+", "-", a.seeds[0].lower()).strip("-")
    out = Path(a.out or ROOT / "runs" / f"{slug}-{datetime.now():%Y%m%d-%H%M}")
    (out / "raw").mkdir(parents=True, exist_ok=True)

    # 1. Спрос
    with cf.ThreadPoolExecutor(8) as ex:
        res = dict(zip(a.seeds, ex.map(lambda s: wordstat(s, auth, a.regions), a.seeds)))
    (out / "raw" / "wordstat.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    phrases, assoc = {}, {}
    for pop, ass in res.values():
        for t, v in pop:
            phrases[t] = max(v, phrases.get(t, 0))
        for t, v in ass:
            assoc[t] = max(v, assoc.get(t, 0))
    phrases = {t: v for t, v in phrases.items() if v >= a.min_freq}

    # 2. Кластеры
    seed_stems = {stem(w) for s in a.seeds for w in re.findall(r"[a-zа-яё0-9]+", s.lower())}
    df = collections.Counter(stem(w) for p in phrases for w in set(re.findall(r"[a-zа-яё0-9]+", p)) if w not in STOP)
    rows = [{"phrase": p, "freq": f, "intent": intent(p), "cluster": cluster_key(p, seed_stems, df)} for p, f in phrases.items()]
    rows.sort(key=lambda r: -r["freq"])
    # мелкие кластеры (1 фраза) сливаем в «прочее», чтобы не было 500 кластеров по одной фразе
    words = collections.Counter(w for p in phrases for w in re.findall(r"[a-zа-яё0-9]+", p))
    label = {}
    for w, _ in words.most_common():
        label.setdefault(stem(w), w)
    for r in rows:
        r["cluster"] = label.get(r["cluster"], r["cluster"])
    size = collections.Counter(r["cluster"] for r in rows)
    for r in rows:
        if size[r["cluster"]] < 2:
            r["cluster"] = "(прочее)"
    with open(out / "semantic-map.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["phrase", "freq", "intent", "cluster"])
        w.writeheader(); w.writerows(rows)
    with open(out / "associations.csv", "w", newline="") as f:
        csv.writer(f).writerows([("phrase", "freq"), *sorted(assoc.items(), key=lambda x: -x[1])])

    # 3. Выдача
    top = [r["phrase"] for r in rows[: a.serp]]
    with cf.ThreadPoolExecutor(8) as ex:
        serps = dict(zip(top, ex.map(lambda q: serp(q, auth, a.lr), top)))
    (out / "raw" / "serp.json").write_text(json.dumps(serps, ensure_ascii=False, indent=1))
    with open(out / "serp-top10.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["query", "pos", "domain", "url", "title"])
        w.writeheader()
        for q, items in serps.items():
            w.writerows({"query": q, **i} for i in items)
    dom = collections.Counter(i["domain"] for items in serps.values() for i in items)

    # 4. Отчёт
    total = sum(r["freq"] for r in rows) or 1
    by_int = collections.defaultdict(lambda: [0, 0])
    by_cl = collections.defaultdict(lambda: {"n": 0, "freq": 0, "intents": collections.Counter(), "top": []})
    for r in rows:
        by_int[r["intent"]][0] += 1; by_int[r["intent"]][1] += r["freq"]
        c = by_cl[r["cluster"]]
        c["n"] += 1; c["freq"] += r["freq"]; c["intents"][r["intent"]] += r["freq"]
        if len(c["top"]) < 4:
            c["top"].append(f"{r['phrase']} ({r['freq']})")
    ok_serp = sum(1 for v in serps.values() if v)
    L = [f"# SEO Standup: {', '.join(a.seeds)}", "",
         f"_{datetime.now():%Y-%m-%d %H:%M} · Wordstat регион: {a.regions or 'все'} · выдача lr={a.lr} · "
         f"запросов: {len(a.seeds)} Wordstat + {ok_serp}/{len(top)} SERP ≈ {(len(a.seeds) + len(top)) * RUB_PER_CALL:.2f} ₽_", "",
         f"**{len(rows)} фраз** (частота ≥ {a.min_freq}), суммарно **{n(total)}** показов/мес. "
         f"Ассоциаций на ручной разбор: {len(assoc)}. «общий» = голый запрос без модификатора: интент размытый, "
         "обычно это главная/хаб категории.", "",
         "## Интенты", "", "| Интент | Фраз | Показов | Доля |", "|---|---:|---:|---:|"]
    for k, (cnt, fr) in sorted(by_int.items(), key=lambda x: -x[1][1]):
        L.append(f"| {k} | {cnt} | {n(fr)} | {fr * 100 // total}% |")
    L += ["", "## Кластеры → страницы", "", "Каждый кластер — кандидат в отдельную посадочную. Интент подсказывает тип страницы: "
          "купить → категория/услуга, сравнить → обзор/сравнение, узнать → статья/гайд.", "",
          "| Кластер | Фраз | Показов | Главный интент | Топ-фразы |", "|---|---:|---:|---|---|"]
    for k, c in sorted(by_cl.items(), key=lambda x: -x[1]["freq"])[:40]:
        L.append(f"| {k} | {c['n']} | {n(c['freq'])} | {c['intents'].most_common(1)[0][0]} | {'; '.join(c['top'])} |")
    if dom:
        L += ["", f"## Кто в выдаче (топ-10 по {ok_serp} запросам)", "", "| Домен | Появлений |", "|---|---:|"]
        L += [f"| {d} | {n} |" for d, n in dom.most_common(20)]
        L += ["", "Маркетплейсы/агрегаторы в топе = коммерческий запрос, туда нужна сильная категория, а не статья. "
              "Повторяющиеся нишевые домены = прямые конкуренты, их структуру разбирать первой."]
    L += ["", "## Файлы", "", "- `semantic-map.csv` — фраза, частота, интент, кластер",
          "- `associations.csv` — ассоциации Wordstat (расширение семантики)",
          "- `serp-top10.csv` — живая выдача Яндекса", "- `raw/` — сырые ответы XMLRiver"]
    (out / "REPORT.md").write_text("\n".join(L) + "\n")
    print(f"Готово → {out}/REPORT.md")


if __name__ == "__main__":
    main()
