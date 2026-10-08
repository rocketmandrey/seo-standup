#!/usr/bin/env python3
"""SEO Standup через XMLRiver: спрос (Wordstat) → кластеры по интентам → живая выдача Яндекса → отчёт.

    python3 tools/standup.py "зерносушилка" "сушилка для зерна" --serp 15 --regions 225
    python3 tools/standup.py "аэрогриль" "аэрогриль купить" --serp 15 --site example.ru
    python3 tools/standup.py --balance

--site включает «прожарку»: скан sitemap → url-inventory.csv, кластеры ↔ посадочные, позиция сайта
в снятой выдаче, SITE-ARCHITECTURE.md и content-plan.csv. Скан сайта бесплатный (прямые HTTP-запросы).

Ключи: XMLRIVER_USER / XMLRIVER_KEY в .env в корне репо (см. .env.example).
Только stdlib. Тариф ~25 ₽ / 1000 запросов — перед запуском печатает смету.
"""
import argparse, collections, concurrent.futures as cf, csv, gzip, html, itertools, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET
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
    AI_BLOCK[query] = root.find(".//ai") is not None
    out = []
    for i, doc in enumerate(root.iter("doc"), 1):
        url = doc.findtext("url") or ""
        title = "".join(doc.find("title").itertext()) if doc.find("title") is not None else ""
        out.append({"pos": i, "domain": urllib.parse.urlparse(url).netloc.removeprefix("www."), "url": url, "title": title})
    return out[:10]


AI_BLOCK = {}  # query → был ли AI-ответ Яндекса над выдачей


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


# ---------- сайт: sitemap → инвентарь → посадочные ----------

UA = {"User-Agent": "Mozilla/5.0 (compatible; seo-standup/1.0; +https://github.com/rocketmandrey/seo-standup)"}
JUNK = r"\btest(?:[_/]|$)|тестов|привет-мир|hello-world|sample-page|\bsuccess|\bfail|checkout|\bcart\b|korzin|корзин|my-account|wishlist|thank|spasibo|oplat|payment|tbank|lorem|/feed\b|cabinet|notification|login|/lk/|\?"
MARKET = r"ozon|wildberries|wb\.ru|market\.yandex|avito|megamarket|dns-shop|mvideo|citilink|eldorado|lamoda|aliexpress|leroymerlin|petrovich|vseinstrumenti|onlinetrade|holodilnik"
UGC = r"reddit|irecommend|otzovik|youtube|dzen|vk\.com|pikabu|habr|vc\.ru|ixbt|wikipedia|rutube|t\.me|livejournal|ok\.ru|pinterest|tiktok|mail\.ru|4pda|drive2|forum|yandex\.ru|\bya\.ru|dtf\.ru|caseguru|hlebopechka|kp\.ru|rbc\.ru|tinkoff\.ru/journal|sravni"
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "a b v g d e e zh z i i k l m n o p r s t u f h c ch sh sh - i - e u a".split()))
W_INTENT = {"купить": 3, "сравнить": 2, "общий": 1, "узнать": 1}  # вес кластера внутри своей группы


def lat(w):
    # ponytail: грубая транслитерация + нормализация вариантов (kh/h, ya/a, j/y/i) с обеих сторон
    w = "".join(TR.get(c, c) for c in w.lower()).replace("-", "")
    for a, b in (("shch", "sh"), ("sch", "sh"), ("kh", "h"), ("ts", "c"), ("yu", "u"), ("ya", "a"), ("yo", "e"), ("j", "i"), ("y", "i"), ("w", "v"), ("x", "ks")):
        w = w.replace(a, b)
    return w


def slugify(phrase):
    return "-".join(lat(w) for w in re.findall(r"[a-zа-яё0-9]+", phrase.lower()) if w not in STOP)[:60]


def fetch(url, timeout=20):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            body = r.read(3_000_000)
            if url.endswith(".gz"):
                body = gzip.decompress(body)
            return r.status, r.url, r.headers, body.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, e.headers, ""
    except Exception as e:
        return 0, url, {}, f"{type(e).__name__}: {e}"


def sitemap_urls(site, limit):
    base = site.rstrip("/")
    _, _, _, robots = fetch(base + "/robots.txt")
    todo = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots) or [base + x for x in ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml")]
    seen, found, groups = set(), [], []
    while todo and len(seen) < 200:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        st, _, _, body = fetch(sm)
        if st != 200 or "<loc" not in body:
            continue
        found.append(sm)
        locs = [html.unescape(x) for x in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", body, re.S)]
        if "<sitemapindex" in body:
            todo += locs
        else:
            groups.append(locs)
    # вперемешку по под-sitemap'ам, чтобы при лимите попали и страницы, и товары, и статьи
    urls = list(dict.fromkeys([base + "/"] + [u for row in itertools.zip_longest(*groups) for u in row if u]))
    return urls[:limit], sum(map(len, groups)), found


def attrs(body, tag):
    return [{k.lower(): html.unescape(v) for k, _, v in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2', m, re.S)}
            for m in re.findall(rf"<{tag}\b([^>]*)>", body, re.I)]


def text_of(rx, body):
    m = re.search(rx, body, re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""


def norm_url(u):
    u = urllib.parse.unquote(u or "").split("#")[0]
    return u.rstrip("/").replace("://www.", "://").lower()


def page_type(url):
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).lower()
    q = urllib.parse.urlparse(url).query
    for t, rx in (("служебная", JUNK), ("главная", r"^/?$"), ("товар", r"/(product|tovar|item|goods)/"),
                  ("категория", r"/(shop|catalog|katalog|category|product-category|collection|uslugi|services?)\b"),
                  ("статья", r"/(blog|news|stat|article|post|journal|novost|recept|recipes|wiki|faq)"),
                  ("архив", r"/(tag|author|page/\d)")):
        if re.search(rx, path + ("?" if q else "")):
            return t
    return "страница"


def scan_page(url):
    st, final, hdr, body = fetch(url)
    canon = next((a.get("href", "") for a in attrs(body, "link") if "canonical" in a.get("rel", "").lower()), "")
    robots = ", ".join(filter(None, [a.get("content", "") for a in attrs(body, "meta") if a.get("name", "").lower() in ("robots", "yandex")]
                              + [hdr.get("X-Robots-Tag", "") if hdr else ""]))
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", body, flags=re.S | re.I)
    r = {"url": url, "status": st, "final_url": final, "type": page_type(url),
         "title": text_of(r"<title[^>]*>(.*?)</title>", body), "h1": text_of(r"<h1[^>]*>(.*?)</h1>", body),
         "h1_count": len(re.findall(r"<h1\b", body, re.I)), "canonical": canon, "robots": robots,
         "words": len(re.findall(r"\w{2,}", text)) if st == 200 else 0}
    problems = []
    if st != 200:
        problems.append(f"HTTP {st}")
    if norm_url(final) != norm_url(url):
        problems.append("редирект")
    if "noindex" in robots.lower():
        problems.append("noindex")
    if canon and norm_url(urllib.parse.urljoin(final, canon)) != norm_url(final):
        problems.append("canonical на другой URL")
    junk = r["type"] == "служебная"
    if junk and "noindex" not in robots.lower():
        problems.append("служебная без noindex")
    if not junk and st == 200:  # у служебных контентные проблемы не интересны
        if not r["h1"]:
            problems.append("нет H1")
        if r["h1_count"] > 1:
            problems.append(f"H1×{r['h1_count']}")
        if r["words"] < 150:
            problems.append("тонкая (<150 слов)")
    r["indexable"] = st == 200 and not ({"редирект", "noindex", "canonical на другой URL"} & set(problems))
    r["problems"] = "; ".join(problems)
    return r


def scan_site(site, limit):
    urls, total, sitemaps = sitemap_urls(site, limit)
    with cf.ThreadPoolExecutor(16) as ex:
        pages = list(ex.map(scan_page, urls))
    titles = [p["title"] for p in pages if p["title"]]
    dup = {t for t, c in collections.Counter(titles).items() if c > 1}
    for p in pages:
        if p["title"] in dup and p["type"] != "служебная":
            p["problems"] = "; ".join(filter(None, [p["problems"], "дубль title"]))
    # общий хвост title («— Бренд — слоган») встречается почти везде и топит матчинг — выкидываем частые слова
    tw = collections.Counter(w for t in titles for w in set(stem(x) for x in re.findall(r"[a-zа-яё0-9]+", t.lower())))
    common = {w for w, c in tw.items() if len(titles) > 4 and c > len(titles) * 0.5}
    for p in pages:
        words = re.findall(r"[a-zа-яё0-9]+", f"{p['title']} {p['h1']}".lower())
        slug = re.findall(r"[a-zа-яё0-9]+", urllib.parse.unquote(urllib.parse.urlparse(p["url"]).path).lower())
        p["_h1"] = {stem(w) for w in re.findall(r"[a-zа-яё0-9]+", p["h1"].lower())}
        p["_title"] = {stem(w) for w in words} - common
        p["_slug"] = {lat(w) for w in slug}
    return pages, total, sitemaps


def find_landing(cluster, pages, phrases=()):
    """Лучшая существующая страница под кластер: совпадение основы в H1 > title > slug. None = дыра."""
    cands = [p for p in pages if p["indexable"] and p["type"] not in ("служебная", "архив")]
    if cluster == "(ядро)":
        return next((p for p in cands if p["type"] == "главная"), None)
    s = stem(cluster)
    ls = lat(s)
    ctx = {stem(w) for ph in phrases[:10] for w in re.findall(r"[a-zа-яё0-9]+", ph) if w not in STOP} - {s}

    def score(p):  # основа кластера обязательна; при равенстве — сколько ещё слов кластера на странице
        return (3 * (s in p["_h1"]) + 2 * (s in p["_title"]) + 2 * any(w.startswith(ls) for w in p["_slug"] if len(ls) >= 4),
                len(ctx & (p["_h1"] | p["_title"])), p["type"] in ("категория", "товар", "главная"), -len(p["url"]))
    best = max(cands, key=score, default=None)
    return best if best and score(best)[0] else None


def domain_kind(d):
    return "маркетплейс" if re.search(MARKET, d) else "медиа/UGC" if re.search(UGC, d) else "нишевый"



BRIEF = {
    "купить": "Коммерческая посадочная: цена, наличие/сроки, доставка, характеристики, FAQ, schema Product/Offer; сначала ответ «сколько и как купить».",
    "сравнить": "Страница выбора: таблица сравнения, критерии, честные минусы, дата обновления; CTA на коммерческую страницу.",
    "узнать": "Гайд: прямой ответ на первом экране, пошагово/таблицей; ссылки на коммерческие страницы, не «SEO-статья».",
    "общий": "Хаб категории: что это, варианты/типы, цены, переходы в подкатегории и выбор.",
}
LAYER = {"купить": "Коммерческий слой", "общий": "Коммерческий слой", "сравнить": "Выбор и сравнения", "узнать": "Инфо / блог"}
PREFIX = {"купить": "/", "общий": "/", "сравнить": "/vybor/", "узнать": "/blog/"}


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds", nargs="*")
    ap.add_argument("--serp", type=int, default=10, help="сколько запросов прогнать по живой выдаче (0 = не надо)")
    ap.add_argument("--regions", default="", help="регион Wordstat: 225 Россия, 213 Москва, 2 СПб (пусто = все)")
    ap.add_argument("--lr", default="225", help="регион выдачи Яндекса")
    ap.add_argument("--min-freq", type=int, default=10)
    ap.add_argument("--site", help="URL сайта для прожарки: скан sitemap + посадочные + позиции")
    ap.add_argument("--max-urls", type=int, default=300, help="лимит URL при скане сайта")
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
    site_host = urllib.parse.urlparse(a.site if a.site and "://" in a.site else f"https://{a.site}").netloc.removeprefix("www.") if a.site else ""
    if a.site and "://" not in a.site:
        a.site = "https://" + a.site
    slug = re.sub(r"[^a-zа-яё0-9]+", "-", (site_host or a.seeds[0]).lower()).strip("-")
    out = Path(a.out or ROOT / "runs" / f"{slug}-{datetime.now():%Y%m%d-%H%M}")
    (out / "raw").mkdir(parents=True, exist_ok=True)

    # 0. Сайт (параллельно со всем остальным — он бесплатный)
    ex_site = cf.ThreadPoolExecutor(1)
    site_job = ex_site.submit(scan_site, a.site, a.max_urls) if a.site else None

    # 1. Спрос
    with cf.ThreadPoolExecutor(8) as ex:
        res = dict(zip(a.seeds, ex.map(lambda s: wordstat(s, auth, a.regions), a.seeds)))
    (out / "raw" / "wordstat.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    ws_ok = sum(1 for v in res.values() if v[0])
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
    write_csv(out / "semantic-map.csv", rows, ["phrase", "freq", "intent", "cluster"])
    with open(out / "associations.csv", "w", newline="") as f:
        csv.writer(f).writerows([("phrase", "freq"), *sorted(assoc.items(), key=lambda x: -x[1])])

    total = sum(r["freq"] for r in rows) or 1
    by_int = collections.defaultdict(lambda: [0, 0])
    by_cl = collections.defaultdict(lambda: {"n": 0, "freq": 0, "intents": collections.Counter(), "phrases": []})
    for r in rows:
        by_int[r["intent"]][0] += 1; by_int[r["intent"]][1] += r["freq"]
        c = by_cl[r["cluster"]]
        c["n"] += 1; c["freq"] += r["freq"]; c["intents"][r["intent"]] += r["freq"]; c["phrases"].append(r["phrase"])
    for c in by_cl.values():
        c["intent"] = c["intents"].most_common(1)[0][0]
        c["score"] = c["freq"] * W_INTENT[c["intent"]]
        c["top"] = [f"{p} ({phrases[p]})" for p in c["phrases"][:4]]
    ranked = sorted(((k, c) for k, c in by_cl.items() if k != "(прочее)"), key=lambda x: -x[1]["score"])
    # инфо-спрос по объёму давит деньги, поэтому очередь — по кругу: купить → сравнить → остальное
    grp = lambda c: {"купить": 0, "сравнить": 1}.get(c["intent"], 2)
    lanes = [[x for x in ranked if x[0] != "(ядро)" and grp(x[1]) == g] for g in range(3)]
    # ponytail: хвост <1% спроса группы (города по 300 показов) в план не идёт — его видно в semantic-map.csv
    lanes = [[x for x in lane if x[1]["freq"] * 100 >= sum(c["freq"] for _, c in lane)] for lane in lanes]
    ranked = [x for x in ranked if x[0] == "(ядро)"] + [x for row in itertools.zip_longest(*lanes) for x in row if x]

    # 3. Выдача: главная фраза каждого кластера по весу, добиваем топом по частоте
    top = list(dict.fromkeys([c["phrases"][0] for _, c in ranked] + [r["phrase"] for r in rows]))[: a.serp]
    with cf.ThreadPoolExecutor(8) as ex:
        serps = dict(zip(top, ex.map(lambda q: serp(q, auth, a.lr), top)))
    (out / "raw" / "serp.json").write_text(json.dumps(serps, ensure_ascii=False, indent=1))
    (out / "raw" / "serp-ai.json").write_text(json.dumps(AI_BLOCK, ensure_ascii=False, indent=1))
    write_csv(out / "serp-top10.csv", [{"query": q, **i} for q, items in serps.items() for i in items], ["query", "pos", "domain", "url", "title"])
    ok_serp = sum(1 for v in serps.values() if v)
    dom = collections.Counter(i["domain"] for items in serps.values() for i in items)
    site_pos = {q: next((i["pos"] for i in items if site_host and (i["domain"] == site_host or i["domain"].endswith("." + site_host))), None)
                for q, items in serps.items()}
    comp = {}
    for q, items in serps.items():
        for i in items:
            c = comp.setdefault(i["domain"], {"domain": i["domain"], "kind": domain_kind(i["domain"]), "appearances": 0, "best_pos": 99, "queries": []})
            c["appearances"] += 1; c["best_pos"] = min(c["best_pos"], i["pos"]); c["queries"].append(q)
    comp_rows = sorted(comp.values(), key=lambda c: (-c["appearances"], c["best_pos"]))
    for c in comp_rows:
        c["queries"] = " | ".join(dict.fromkeys(c["queries"]))
    write_csv(out / "competitor-domains.csv", comp_rows, ["domain", "kind", "appearances", "best_pos", "queries"])
    write_csv(out / "serp-features.csv", [{"query": q, "ok": bool(items), "ai_present": AI_BLOCK.get(q, ""), "site_pos": site_pos[q] or "",
                                           "top3": " | ".join(i["domain"] for i in items[:3])} for q, items in serps.items()],
              ["query", "ok", "ai_present", "site_pos", "top3"])
    kinds = collections.Counter(c["kind"] for c in comp.values() for _ in range(c["appearances"]))
    n_docs = sum(kinds.values()) or 1

    intents_tbl = ["| Интент | Фраз | Показов | Доля |", "|---|---:|---:|---:|"] + [
        f"| {k} | {cnt} | {n(fr)} | {fr * 100 // total}% |" for k, (cnt, fr) in sorted(by_int.items(), key=lambda x: -x[1][1])]
    clusters_tbl = ["| Кластер | Фраз | Показов | Главный интент | Топ-фразы |", "|---|---:|---:|---|---|"] + [
        f"| {k} | {c['n']} | {n(c['freq'])} | {c['intent']} | {'; '.join(c['top'])} |"
        for k, c in sorted(by_cl.items(), key=lambda x: -x[1]["freq"])[:40]]
    dom_tbl = ["| Домен | Тип | Появлений | Лучшая позиция |", "|---|---|---:|---:|"] + [
        f"| {c['domain']} | {c['kind']} | {c['appearances']} | {c['best_pos']} |" for c in comp_rows[:20]]
    cost = (len(a.seeds) + len(top)) * RUB_PER_CALL
    files = ["", "## Файлы", "", "- `semantic-map.csv` — фраза, частота, интент, кластер",
             "- `associations.csv` — ассоциации Wordstat (расширение семантики)",
             "- `serp-top10.csv`, `competitor-domains.csv`, `serp-features.csv` — живая выдача Яндекса, домены, AI-блок, позиция сайта",
             "- `raw/` — сырые ответы XMLRiver"]

    if not site_job:
        L = [f"# SEO Standup: {', '.join(a.seeds)}", "",
             f"_{datetime.now():%Y-%m-%d %H:%M} · Wordstat регион: {a.regions or 'все'} · выдача lr={a.lr} · "
             f"запросов: {len(a.seeds)} Wordstat + {ok_serp}/{len(top)} SERP ≈ {cost:.2f} ₽_", "",
             f"**{len(rows)} фраз** (частота ≥ {a.min_freq}), суммарно **{n(total)}** показов/мес. "
             f"Ассоциаций на ручной разбор: {len(assoc)}. «общий» = голый запрос без модификатора: интент размытый, "
             "обычно это главная/хаб категории.", "", "## Интенты", "", *intents_tbl,
             "", "## Кластеры → страницы", "", "Каждый кластер — кандидат в отдельную посадочную. Интент подсказывает тип страницы: "
             "купить → категория/услуга, сравнить → обзор/сравнение, узнать → статья/гайд.", "", *clusters_tbl]
        if dom:
            L += ["", f"## Кто в выдаче (топ-10 по {ok_serp} запросам)", "", *dom_tbl, "",
                  "Маркетплейсы/агрегаторы в топе = коммерческий запрос, туда нужна сильная категория, а не статья. "
                  "Повторяющиеся нишевые домены = прямые конкуренты, их структуру разбирать первой."]
        (out / "REPORT.md").write_text("\n".join(L + files) + "\n")
        print(f"Готово → {out}/REPORT.md")
        return

    # 4. Прожарка сайта
    pages, sm_total, sitemaps = site_job.result()
    ex_site.shutdown()
    write_csv(out / "url-inventory.csv", pages, ["url", "status", "final_url", "type", "indexable", "title", "h1", "canonical", "robots", "words", "problems"])
    plan = []
    for k, c in ranked:
        if re.search(r"озон|ozon|вайлдбер|wildber|\bвб\b|авито|avito|маркет", k):
            continue  # спрос на площадку — своей страницей его не забрать
        kind = c["intent"] if c["intent"] != "общий" or k == "(ядро)" else "узнать"  # «общий» вне ядра — это инфо (рецепты и т.п.)
        land = find_landing(k, pages, c["phrases"])
        qs = [q for q in serps if q in c["phrases"] and serps[q]]
        pos = [site_pos[q] for q in qs if site_pos[q]]
        rivals = list(dict.fromkeys(i["domain"] for q in qs for i in serps[q][:5] if i["domain"] != site_host))[:4]
        if not land:
            status, why = "Новая", "посадочной нет"
        elif c["intent"] == "купить" and land["type"] == "статья":
            status, why = "Усилить", "коммерческий спрос ловит статья — нужна коммерческая посадочная"
        elif qs and not pos:
            status, why = "Усилить", f"нет в топ-10 по «{qs[0]}»"
        elif land["problems"]:
            status, why = "Усилить", land["problems"]
        else:
            status, why = ("Держать", f"в топ-10 (#{min(pos)})") if pos else ("Есть", "посадочная есть, выдача не снималась")
        path = urllib.parse.unquote(urllib.parse.urlparse(land["url"]).path) if land else f"{PREFIX[kind]}{slugify(c['phrases'][0])}/"
        plan.append({"cluster": k, "intent": c["intent"], "layer": LAYER[kind], "freq": c["freq"], "status": status, "target_url": path,
                     "current_type": land["type"] if land else "", "site_pos": min(pos) if pos else ("—" if qs else "не снималось"),
                     "keyword_focus": "; ".join(c["phrases"][:4]), "why": why,
                     "brief": f"{BRIEF[kind]} Сейчас: {why}." + (f" В топе: {', '.join(rivals)}." if rivals else "")})
    todo = [p for p in plan if p["status"] in ("Новая", "Усилить")]
    for i, p in enumerate(todo):
        p["priority"] = "P0" if i < 6 else "P1" if i < 16 else "P2"
    write_csv(out / "content-plan.csv", todo[:40], ["priority", "target_url", "status", "cluster", "intent", "freq", "site_pos", "keyword_focus", "brief"])

    money = [p for p in plan if p["intent"] in ("купить", "сравнить")]
    money_freq = sum(p["freq"] for p in money) or 1
    big = [p for p in money if p["freq"] >= money_freq / 100]
    money_hole = sum(p["freq"] for p in money if p["status"] == "Новая")
    seen_pos = {q: p for q, p in site_pos.items() if p}
    types = collections.Counter(p["type"] for p in pages)
    bad = [p for p in pages if p["problems"]]
    pc = collections.Counter(x for p in pages for x in p["problems"].split("; ") if x)
    junk = [p for p in pages if p["type"] == "служебная"]
    commercial = types["товар"] + types["категория"]
    holes = [p for p in plan if p["status"] == "Новая"]
    verdict = [f"- **Видимость:** сайт в топ-10 Яндекса в **{len(seen_pos)} из {ok_serp}** снятых запросов"
               + (f" ({', '.join(f'«{q}» #{p}' for q, p in seen_pos.items())})." if seen_pos else "."),
               f"- **Деньги без посадочных:** {sum(1 for p in big if p['status'] == 'Новая')} из {len(big)} крупных денежных кластеров "
               f"(купить/сравнить, ≥1% денежного спроса) без своей страницы — это **{money_hole * 100 // money_freq}%** денежного спроса.",
               f"- **Структура:** в sitemap {sm_total} URL, просканировано {len(pages)}: "
               + ", ".join(f"{t} {c}" for t, c in types.most_common()) + "."
               + (f" Коммерческих посадочных (товар+категория) всего {commercial} — коммерческий спрос держит главная/пара карточек, "
                  "нишевым интентам некуда приземляться." if commercial < max(3, len(pages) // 10) else ""),
               f"- **Выдача:** маркетплейсы — {kinds['маркетплейс'] * 100 // n_docs}% мест в топ-10, медиа/UGC — "
               f"{kinds['медиа/UGC'] * 100 // n_docs}%, нишевые сайты — {kinds['нишевый'] * 100 // n_docs}%. "
               f"AI-блок над выдачей в {sum(1 for q in serps if AI_BLOCK.get(q))}/{ok_serp}.",
               f"- **Гигиена:** {len(junk)} служебных URL в sitemap, " + ", ".join(f"{k}: {v}" for k, v in pc.most_common(6)) + "."]
    L = [f"# Прожарка {site_host}", "",
         f"**Дата:** {datetime.now():%Y-%m-%d}  ", f"**Рынок:** Яндекс, Wordstat регион {a.regions or 'все'}, выдача lr={a.lr}  ",
         f"**Seed:** {', '.join(a.seeds)}", "",
         "## Что снято", "",
         f"- XMLRiver Wordstat: {ws_ok}/{len(a.seeds)} seed → **{len(rows)}** фраз (частота ≥ {a.min_freq}), {n(total)} показов/мес.; ассоциаций на разбор: {len(assoc)}.",
         f"- XMLRiver выдача Яндекса: **{ok_serp}/{len(top)}** запросов (главная фраза каждого кластера по весу), {sum(map(len, serps.values()))} органических результатов.",
         f"- Сайт: {len(sitemaps)} sitemap ({sitemaps[0] if sitemaps else 'не найден'}) → {sm_total} URL, просканировано **{len(pages)}** (лимит {a.max_urls}), "
         f"200: {sum(1 for p in pages if p['status'] == 200)}, индексируемых: {sum(1 for p in pages if p['indexable'])}.",
         f"- Стоимость: ≈ {cost:.2f} ₽ ({len(a.seeds) + len(top)} запросов XMLRiver; скан сайта бесплатный).", "",
         "## Вердикт", "", *verdict, "",
         "## Структурные дыры", "", "Кластеры без своей посадочной, по очереди приоритета (купить → сравнить → остальное, внутри — по спросу).", "",
         "| Кластер | Интент | Показов | Предлагаемый URL | Кто в топе |", "|---|---|---:|---|---|"]
    L += [f"| {p['cluster']} | {p['intent']} | {n(p['freq'])} | `{p['target_url']}` | {p['brief'].split('В топе: ')[-1].rstrip('.') if 'В топе' in p['brief'] else '—'} |" for p in holes[:15]]
    L += ["", "## Денежные кластеры", "", "| Кластер | Интент | Показов | Посадочная | Позиция сайта | Статус |", "|---|---|---:|---|---:|---|"]
    L += [f"| {p['cluster']} | {p['intent']} | {n(p['freq'])} | {'`' + p['target_url'] + '`' if p['status'] != 'Новая' else '**дыра**'} | {p['site_pos']} | {p['status']}: {p['why']} |"
          for p in big[:20]]
    L += ["", "Топ-фразы (частоты Wordstat не складывать в трафик):", "", "| Фраза | Wordstat | Интент | Кластер |", "|---|---:|---|---|"]
    L += [f"| {r['phrase']} | {n(r['freq'])} | {r['intent']} | {r['cluster']} |" for r in rows if r["intent"] in ("купить", "сравнить")][:15]
    L += ["", f"## Выдача (топ-10 по {ok_serp} запросам)", "", "| Запрос | Сайт | AI | Топ-3 |", "|---|---:|---|---|"]
    L += [f"| {q} | {site_pos[q] or '—'} | {'да' if AI_BLOCK.get(q) else ''} | {', '.join(i['domain'] for i in items[:3])} |" for q, items in serps.items() if items]
    L += ["", *dom_tbl, "", "Маркетплейсы в топе = коммерческий запрос: нужна сильная категория/карточка, не статья. "
          "Нишевые домены, которые повторяются, — прямые конкуренты: их структуру разбирать первой. Медиа/UGC = спрос на честное сравнение."]
    L += ["", "## Технический аудит", "", f"- Типы страниц: " + ", ".join(f"{t} {c}" for t, c in types.most_common()) + ".",
          *[f"- {k}: **{v}**" for k, v in pc.most_common()], ""]
    if bad:
        L += ["| URL | Тип | Проблемы |", "|---|---|---|"] + [f"| {urllib.parse.unquote(p['url'])} | {p['type']} | {p['problems']} |"
                                                         for p in sorted(bad, key=lambda p: (p['type'] != 'служебная', p['status'] == 200))[:25]]
    L += ["", "## P0 / P1", ""]
    for pr in ("P0", "P1"):
        L += [f"### {pr}", ""] + [f"- **{p['status']}** `{p['target_url']}` — {p['keyword_focus']}. {p['why'].capitalize()}." for p in todo if p.get("priority") == pr] + [""]
    L += ["Полный план — `content-plan.csv`, целевая структура — `SITE-ARCHITECTURE.md`, инвентарь — `url-inventory.csv`.", "",
          "## Ограничения данных", "",
          f"- Wordstat: только топ-фразы XMLRiver по {len(a.seeds)} seed; это сигнал спроса, не трафик.",
          f"- Выдача: снимок {datetime.now():%Y-%m-%d}, {ok_serp} запросов, lr={a.lr}, desktop; позиции не мониторинг. "
          + (f"{len(top) - ok_serp} запросов не отдались (ошибка провайдера)." if ok_serp < len(top) else ""),
          f"- Сайт: только URL из sitemap (лимит {a.max_urls}), без рендера JS; страницы вне sitemap не видны.",
          "- Кластеры и сопоставление кластер → посадочная — эвристика по основам слов в H1/title/slug: дыры и посадочные проверять глазами.",
          "- Нет Search Console / Вебмастера / Метрики: индексация и реальные запросы сайта не проверялись.", "",
          "## Интенты", "", *intents_tbl, "", "## Все кластеры", "", *clusters_tbl, *files,
          "- `url-inventory.csv` — скан сайта: статус, title, H1, canonical, robots, тип, проблемы",
          "- `content-plan.csv`, `SITE-ARCHITECTURE.md` — что делать"]
    (out / "REPORT.md").write_text("\n".join(L) + "\n")

    A = [f"# {site_host} — целевая структура под спрос", "", "Статусы: **Новая** — страницы нет, **Усилить** — есть, но не ловит кластер, "
         "**Держать** — в топ-10, **Есть** — страница есть, выдача не снималась. Слой определяется интентом кластера.", ""]
    for layer in dict.fromkeys(LAYER.values()):
        items = [p for p in plan if p["layer"] == layer]
        if items:
            A += [f"## {layer}", ""] + [f"- `{p['target_url']}` — {p['status']}{' ' + p['priority'] if p.get('priority') else ''} · {p['cluster']} "
                                        f"({n(p['freq'])}) · {p['keyword_focus']}" for p in items[:25]] + [""]
    out_idx = [p for p in pages if p["type"] == "служебная" or (p["status"] != 200)]
    if out_idx:
        A += ["## Убрать из sitemap / закрыть noindex", ""] + [f"- {urllib.parse.unquote(p['url'])} — {p['problems'] or p['type']}" for p in out_idx[:30]] + [""]
    A += ["## Перелинковка", "", "- Инфо/блог → страница выбора → коммерческая посадочная: каждая статья ведёт в деньги.",
          "- Сравнения → чеклист выбора → категория/карточка.", "- Одна страница = один кластер; при пересечении — склеить и 301, а не держать двух слабых."]
    (out / "SITE-ARCHITECTURE.md").write_text("\n".join(A) + "\n")
    print(f"Готово → {out}/REPORT.md")


if __name__ == "__main__":
    main()
