"""Офлайн-проверка эвристик прожарки: python3 tools/test_standup.py"""
import standup as s

assert s.lat("духовка") == s.lat("dukhovka") == "duhovka"
assert s.page_type("https://x.ru/") == "главная"
assert s.page_type("https://x.ru/test_ozon/") == "служебная"
assert s.page_type("https://x.ru/press/sravnitelnyj-test-hello/") != "служебная"
assert s.page_type("https://x.ru/blog/kak-vybrat/") == "статья"

html = '<title>Т</title><link href="https://x.ru/a/" rel="canonical"><meta content="noindex, follow" name="robots"><h1>А <b>б</b></h1>'
assert s.attrs(html, "link")[0]["href"] == "https://x.ru/a/"
assert s.text_of(r"<h1[^>]*>(.*?)</h1>", html) == "А б"


def pg(url, h1, title="", typ="статья"):
    p = {"url": url, "h1": h1, "title": title, "type": typ, "indexable": True}
    p["_h1"] = {s.stem(w) for w in h1.lower().split()}
    p["_title"] = {s.stem(w) for w in title.lower().split()}
    p["_slug"] = {s.lat(w) for w in url.rstrip("/").rsplit("/", 1)[-1].split("-")}
    return p


pages = [pg("https://x.ru/", "Аэрогриль", typ="главная"), pg("https://x.ru/blog/luchshij-podarok/", "Лучший подарок на свадьбу"),
         pg("https://x.ru/blog/top-10-luchshih-aerogrilej/", "Топ 10 лучших аэрогрилей")]
assert s.find_landing("лучший", pages, ["лучший аэрогриль", "топ лучших аэрогрилей"])["url"].endswith("top-10-luchshih-aerogrilej/")
assert s.find_landing("(ядро)", pages)["type"] == "главная"
assert s.find_landing("термощуп", pages) is None
# 1: дыра сверяется по слагам несканированных URL sitemap, чужая статья с той же основой не считается
idx = s.slug_index(["https://x.ru/blog/gruzoperevozki-nedorogo-po-rossii-2026/", "https://x.ru/blog/remont-dorog-v-nizhegorodskoj-oblasti/"])
assert s.sitemap_hit("недорого", ["грузоперевозки недорого"], idx).endswith("nedorogo-po-rossii-2026/")
assert s.sitemap_hit("области", ["грузоперевозки по области"], idx) is None
# 5: все записи/чтения файлов — явно в utf-8 (на Windows по умолчанию cp1251)
import pathlib, re
src = pathlib.Path(s.__file__).read_text(encoding="utf-8")
assert all("encoding" in l for l in src.splitlines() if re.search(r"(write_text|read_text|\bopen)\(", l))
# 6: слаги — нормальная транслитерация, не нормализация для матчинга
assert s.slugify("яндекс доставка грузов из китая") == "yandeks-dostavka-gruzov-kitaya"
assert s.slugify("дешёвые грузоперевозки с грузчиками") == "deshevye-gruzoperevozki-gruzchikami"
print("ok")
