import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from suggest import HEADERS, normalize_query, clean_title

ACCESSORY_WORDS = (
    "chexol", "чехол", "case", "g'ilof", "gilof", "oyna", "стекло", "glass",
    "plenka", "пленка", "himoya", "защит", "kabel", "кабель", "shnur", "шнур",
    "zaryad", "заряд", "adapter", "адаптер", "remeshok", "ремешок", "kovrik",
    "ryukzak", "рюкзак", "sumka", "сумка", "backpack", "bag", "ushlagich",
    "держатель", "holder", "stiker", "стикер", "stativ", "штатив", "linza",
    "линза", "batareya", "батарея", "akkumulyator", "аккумулятор"
)

CACHE = {}
CACHE_TTL = 2 * 3600  # 2 soat
MAX_CACHE_ITEMS = 300
debug_lock = threading.Lock()
cache_lock = threading.Lock()


def set_debug(debug, store, key, val):
    if debug is None:
        return
    with debug_lock:
        if store not in debug:
            debug[store] = {}
        debug[store][key] = val


def parse_monthly_price(raw_val, full_price):
    if isinstance(raw_val, (int, float)) and 1000 <= raw_val < full_price:
        return int(raw_val)
    text = str(raw_val or "").strip()
    m = re.search(r"(\d{1,3}(?:[\s\u00a0.,]\d{3})+|\d{4,8})", text)
    if m:
        val = int(re.sub(r"\D", "", m.group(1)))
        if 1000 <= val < full_price:
            return val
    return round(full_price / 12)


def to_int_price(val):
    if isinstance(val, (int, float)):
        return int(val)
    digits = re.sub(r"\D", "", str(val or ""))
    return int(digits) if digits else 0


def is_relevant_product(query, title):
    q_low = normalize_query(query)
    t_low = (title or "").lower().strip()
    if not t_low:
        return False
    if not any(w in q_low for w in ACCESSORY_WORDS):
        if any(w in t_low for w in ACCESSORY_WORDS):
            return False
    q_tokens = [w for w in re.findall(r"[a-zA-Zа-яА-Я0-9]+", q_low) if len(w) > 1 or w.isdigit()]
    if not q_tokens:
        return True
    matched = 0
    for tok in q_tokens:
        if tok.isdigit():
            if not re.search(rf"(?<![0-9.,]){re.escape(tok)}(?![0-9.,])", t_low):
                return False
            matched += 1
        else:
            if tok in t_low or (len(tok) >= 4 and tok[:4] in t_low):
                matched += 1
    if q_tokens[0] not in t_low:
        return False
    return matched == len(q_tokens)


# 1. Texnomart
def fetch_texnomart(query, debug=None):
    try:
        url = f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(query)}"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        set_debug(debug, "Texnomart", "status", r.status_code)
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        set_debug(debug, "Texnomart", "raw_count", len(products))
        best = None
        for p in products:
            if str(p.get("availability", "")).lower() in ("outofstock", "none", "0"):
                continue
            title = p.get("name", "")
            price = to_int_price(p.get("sale_price") or p.get("price"))
            if price < 10000 or not is_relevant_product(query, title):
                continue
            pid = p.get("id", "")
            img = p.get("image") or ""
            monthly = parse_monthly_price(p.get("axiom_monthly_price"), price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Texnomart", "price": price, "monthly": monthly,
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title),
                    "url": f"https://texnomart.uz/uz/product/detail/{pid}" if pid else f"https://texnomart.uz/uz/search/?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "Texnomart", "error", str(e)[:100])
        return None


# 2. Idea.uz
def fetch_idea(query, debug=None):
    try:
        url = f"https://api.idea.uz/api/v2/products?q={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://idea.uz", "Referer": "https://idea.uz/"}, timeout=7)
        set_debug(debug, "Idea", "status", r.status_code)
        if r.status_code != 200:
            return None
        raw = r.json().get("data", [])
        products = raw if isinstance(raw, list) else raw.get("data", [])
        set_debug(debug, "Idea", "raw_count", len(products))
        best = None
        for p in products:
            title = p.get("title_name") or p.get("name") or p.get("title") or ""
            price = to_int_price(p.get("current_price") or p.get("price") or p.get("current_price_formatted"))
            if price < 10000 or not is_relevant_product(query, title):
                continue
            slug = p.get("slug") or p.get("id") or ""
            img = p.get("img") or p.get("image") or ""
            if isinstance(img, dict):
                img = img.get("url") or img.get("src") or ""
            if best is None or price < best["price"]:
                best = {
                    "store": "Idea.uz", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title),
                    "url": f"https://idea.uz/product/{slug}" if slug else f"https://idea.uz/search?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "Idea", "error", str(e)[:100])
        return None


# 3. Olcha
def fetch_olcha(query, debug=None):
    try:
        url = f"https://api.olcha.uz/api/v2/products?search={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://olcha.uz", "Referer": "https://olcha.uz/"}, timeout=7)
        set_debug(debug, "Olcha", "status", r.status_code)
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        set_debug(debug, "Olcha", "raw_count", len(products))
        best = None
        for p in products:
            if p.get("out_of_stock") is True or p.get("in_stock") is False:
                continue
            title = p.get("name_uz") or p.get("name_ru") or p.get("name", "")
            price = to_int_price(p.get("total_price") or p.get("price"))
            if price < 10000 or not is_relevant_product(query, title):
                continue
            alias = p.get("alias", "")
            img = p.get("main_image") or ""
            monthly = parse_monthly_price(p.get("monthly_repayment"), price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Olcha", "price": price, "monthly": monthly,
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title),
                    "url": f"https://olcha.uz/uz/product/view/{alias}" if alias else f"https://olcha.uz/uz/search?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "Olcha", "error", str(e)[:100])
        return None


# 4. Asaxiy
def fetch_asaxiy(query, debug=None):
    try:
        url = f"https://asaxiy.uz/product?key={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        set_debug(debug, "Asaxiy", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product__item")[:15]
        set_debug(debug, "Asaxiy", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one(".product__item__info-title")
            price_el = card.select_one(".product__item-price")
            link_el = card.select_one("a[href*='/product/']")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            card_text = card.get_text(" ", strip=True).lower()
            if "sotuvda yo'q" in card_text or "нет в наличии" in card_text:
                continue
            title = title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://asaxiy.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Asaxiy", "price": price, "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "Asaxiy", "error", str(e)[:100])
        return None


# 5. Uzum Market
def fetch_uzum(query, debug=None):
    gql_url = "https://graphql.uzum.uz/"
    headers = {
        **HEADERS, "Content-Type": "application/json", "Origin": "https://uzum.uz", "Referer": "https://uzum.uz/",
        "apollographql-client-name": "web-customers", "apollographql-client-version": "1.0.0",
    }
    gql = "query getMakeSearch($queryInput: MakeSearchQueryInput!) { makeSearch(query: $queryInput) { items { catalogCard { productId title minSellPrice photos { key } } } } }"
    try:
        payload = {
            "operationName": "getMakeSearch",
            "variables": {"queryInput": {"text": query, "showAdultContent": "NONE", "filters": [], "sort": "BY_RELEVANCE_DESC", "pagination": {"offset": 0, "limit": 15}}},
            "query": gql
        }
        r = requests.post(gql_url, json=payload, headers=headers, timeout=7)
        set_debug(debug, "Uzum", "status", r.status_code)
        if r.status_code != 200:
            return None
        items = r.json().get("data", {}).get("makeSearch", {}).get("items", [])
        set_debug(debug, "Uzum", "raw_count", len(items))
        best = None
        for item in items:
            card = item.get("catalogCard") or {}
            title = card.get("title", "")
            price = to_int_price(card.get("minSellPrice"))
            if price < 10000 or not is_relevant_product(query, title):
                continue
            pid = card.get("productId", "")
            photos = card.get("photos") or []
            img = f"https://images.uzum.uz/{photos[0]['key']}/t_product_540_high.jpg" if photos and photos[0].get("key") else ""
            if best is None or price < best["price"]:
                best = {
                    "store": "Uzum Market", "price": price, "monthly": round(price / 12),
                    "delivery": "1 kunda bepul (punktga)", "image": img, "title": clean_title(title),
                    "url": f"https://uzum.uz/uz/product/{pid}" if pid else f"https://uzum.uz/uz/search?query={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "Uzum", "error", str(e)[:100])
        return None


# 6. Mediapark
def fetch_mediapark(query, debug=None):
    try:
        url = f"https://api.client.mediapark.uz/v1/products/search?search={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://mediapark.uz", "Referer": "https://mediapark.uz/"}, timeout=7)
        set_debug(debug, "Mediapark", "status", r.status_code)
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("data", []) if isinstance(data.get("data"), list) else data.get("data", {}).get("products", [])
        set_debug(debug, "Mediapark", "raw_count", len(products))
        best = None
        for p in products:
            title = p.get("name") or p.get("title") or ""
            price = to_int_price(p.get("price") or p.get("actual_price"))
            if price < 10000 or not is_relevant_product(query, title):
                continue
            slug = p.get("slug") or p.get("id") or ""
            img = p.get("image") or p.get("main_image") or ""
            if best is None or price < best["price"]:
                best = {
                    "store": "Mediapark", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title),
                    "url": f"https://mediapark.uz/products/view/{slug}" if slug else f"https://mediapark.uz/search?search={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "Mediapark", "error", str(e)[:100])
        return None


# 7. AlifShop
def fetch_alifshop(query, debug=None):
    try:
        url = f"https://gw.alifshop.uz/web/client/search/full-text?query={quote_plus(query)}&page=1&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://alifshop.uz", "Referer": "https://alifshop.uz/"}, timeout=7)
        set_debug(debug, "AlifShop", "status", r.status_code)
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("offers") or data.get("data") or []
        set_debug(debug, "AlifShop", "raw_count", len(products))
        best = None
        for p in products:
            title = p.get("name") or p.get("title") or ""
            raw_price = to_int_price(p.get("price"))
            price = int(raw_price / 100) if raw_price > 1000000000 else raw_price
            if price < 10000 or not is_relevant_product(query, title):
                continue
            slug = p.get("slug") or ""
            img = p.get("image_url") or p.get("image") or ""
            if best is None or price < best["price"]:
                best = {
                    "store": "AlifShop", "price": price, "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title),
                    "url": f"https://alifshop.uz/uz/moderated-offer/{slug}" if slug else f"https://alifshop.uz/uz/search?query={quote_plus(query)}"
                }
        return best
    except Exception as e:
        set_debug(debug, "AlifShop", "error", str(e)[:100])
        return None


# 8. Openshop.uz
def fetch_openshop(query, debug=None):
    try:
        url = f"https://openshop.uz/search?search={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "Openshop", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-card, .product-item, .product")[:15]
        set_debug(debug, "Openshop", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one("a[title], .product-title, .title, h3, h4")
            price_el = card.select_one(".price, .product-price, .new-price")
            link_el = card.select_one("a[href]")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            if img.startswith("/"):
                img = f"https://openshop.uz{img}"
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://openshop.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Openshop", "price": price, "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "Openshop", "error", str(e)[:100])
        return None


# 9. Elmakon
def fetch_elmakon(query, debug=None):
    try:
        url = f"https://elmakon.uz/?subcats=Y&pcode_from_q=Y&pshort=Y&pfull=Y&pname=Y&pkeywords=Y&search_performed=Y&q={quote_plus(query)}&dispatch=products.search"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "Elmakon", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".ty-grid-list__item")[:15]
        set_debug(debug, "Elmakon", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one(".product-title")
            price_el = card.select_one(".ty-price-num")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            href = title_el.get("href") or "https://elmakon.uz/"
            if best is None or price < best["price"]:
                best = {
                    "store": "Elmakon", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title), "url": href
                }
        return best
    except Exception as e:
        set_debug(debug, "Elmakon", "error", str(e)[:100])
        return None


# 10. Goodzone
def fetch_goodzone(query, debug=None):
    try:
        url = f"https://goodzone.uz/search?q={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "Goodzone", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-card, .card")[:15]
        set_debug(debug, "Goodzone", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one("a[title], .title, h3, h4")
            price_el = card.select_one(".price, .product-price")
            link_el = card.select_one("a[href]")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://goodzone.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Goodzone", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "Goodzone", "error", str(e)[:100])
        return None


# 11. Radius.uz
def fetch_radius(query, debug=None):
    try:
        url = f"https://radius.uz/search?q={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "Radius", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-card, .product-item")[:15]
        set_debug(debug, "Radius", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one("a[title], .product-title, .title")
            price_el = card.select_one(".price, .product-price")
            link_el = card.select_one("a[href]")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://radius.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Radius", "price": price, "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "Radius", "error", str(e)[:100])
        return None


# 12. Credit Asia
def fetch_creditasia(query, debug=None):
    try:
        url = f"https://creditasia.uz/search/?q={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "CreditAsia", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-card, .catalog-item, .item")[:15]
        set_debug(debug, "CreditAsia", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one(".item-title, .product-title, a[title]")
            price_el = card.select_one(".price, .item-price")
            link_el = card.select_one("a[href]")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            if img.startswith("/"):
                img = f"https://creditasia.uz{img}"
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://creditasia.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Credit Asia", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "CreditAsia", "error", str(e)[:100])
        return None


# 13. Ishonch Savdo
def fetch_ishonch(query, debug=None):
    try:
        url = f"https://ishonchsavdo.uz/search?q={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=7)
        set_debug(debug, "Ishonch", "status", r.status_code)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product-card, .product-item")[:15]
        set_debug(debug, "Ishonch", "raw_count", len(cards))
        best = None
        for card in cards:
            title_el = card.select_one(".product-title, .title, a[title]")
            price_el = card.select_one(".price, .product-price")
            link_el = card.select_one("a[href]")
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get("title") or title_el.get_text(strip=True)
            price = to_int_price(price_el.get_text())
            if price < 10000 or not is_relevant_product(query, title):
                continue
            img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://ishonchsavdo.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Ishonch", "price": price, "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title), "url": full_url
                }
        return best
    except Exception as e:
        set_debug(debug, "Ishonch", "error", str(e)[:100])
        return None


def fetch_fallback_image(query):
    try:
        with DDGS() as ddgs:
            imgs = list(ddgs.images(f"{query} official product", max_results=3))
            for im in imgs:
                u = im.get("image") or im.get("thumbnail") or ""
                if u.startswith("http"):
                    return u
    except Exception:
        pass
    return ""


ALL_FETCHERS = [
    fetch_texnomart, fetch_idea, fetch_olcha, fetch_asaxiy, fetch_uzum,
    fetch_mediapark, fetch_alifshop, fetch_openshop, fetch_elmakon,
    fetch_goodzone, fetch_radius, fetch_creditasia, fetch_ishonch
]


def search_prices(query, bypass_cache=False, debug_info=None, on_best_price=None):
    norm_q = normalize_query(query)
    key = norm_q.lower().strip()
    now = time.time()
    if not bypass_cache and debug_info is None:
        with cache_lock:
            cached = CACHE.get(key)
            if cached and (now - cached["time"] < CACHE_TTL):
                return cached["data"]

    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(fn, norm_q, debug_info) for fn in ALL_FETCHERS]
        for f in futures:
            res = f.result()
            if res:
                results.append(res)

    results.sort(key=lambda x: x["price"])
    main_img = next((r["image"] for r in results if r.get("image")), "")
    if results and not main_img:
        main_img = fetch_fallback_image(norm_q)
    for r in results:
        if not r.get("image"):
            r["image"] = main_img

    if results and debug_info is None:
        if on_best_price:
            on_best_price(query, results[0]["price"])
        with cache_lock:
            if len(CACHE) >= MAX_CACHE_ITEMS:
                CACHE.clear()
            CACHE[key] = {"time": now, "data": results}

    return results
