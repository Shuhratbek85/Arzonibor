import os
import re
import json
import time
import html
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import requests
from bs4 import BeautifulSoup
import telebot
from telebot import types
from ddgs import DDGS

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN topilmadi! Render Environment Variables ni tekshiring.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- SOZLAMALAR ---
ADMIN_ID = 565834132
ENV_SUPPORT_GROUP_ID = os.environ.get("SUPPORT_GROUP_ID")
WEBAPP_URL = "https://arzonibor-ywla.onrender.com"
CHANNELS = [
    {"username": "@mrtexnoai", "url": "https://t.me/mrtexnoai"},
    {"username": "@arzoniboragent", "url": "https://t.me/arzoniboragent"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "uz-UZ,uz;q=0.9,ru;q=0.8,en;q=0.7",
}

ACCESSORY_WORDS = (
    "chexol", "чехол", "case", "g'ilof", "gilof", "oyna", "стекло", "glass",
    "plenka", "пленка", "himoya", "защит", "kabel", "кабель", "shnur", "шнур",
    "zaryad", "заряд", "adapter", "адаптер", "remeshok", "ремешок", "kovrik",
    "ryukzak", "рюкзак", "sumka", "сумка", "backpack", "bag", "ushlagich",
    "держатель", "holder", "stiker", "стикер", "stativ", "штатив", "linza",
    "линза", "batareya", "батарея", "akkumulyator", "аккумулятор"
)

BRAND_SUGGESTIONS = {
    "iphone": ["iPhone 16 Pro", "iPhone 16 Pro Max", "iPhone 15 Pro", "iPhone 15", "iPhone 14", "iPhone 13"],
    "ayfon": ["iPhone 16 Pro", "iPhone 16 Pro Max", "iPhone 15 Pro", "iPhone 15", "iPhone 13"],
    "redmi": ["Redmi Note 13", "Redmi Note 13 Pro", "Redmi Note 14", "Redmi 14C", "Redmi 13C", "Redmi A3"],
    "redni": ["Redmi Note 13", "Redmi Note 13 Pro", "Redmi Note 14", "Redmi 14C", "Redmi 13C"],
    "samsung": ["Samsung A55", "Samsung A35", "Samsung A25", "Samsung S24 Ultra", "Samsung S24", "Samsung A15"],
    "poco": ["Poco X6 Pro", "Poco X6", "Poco M6 Pro", "Poco F6"],
    "honor": ["Honor X9b", "Honor X8b", "Honor X7b", "Honor 200"],
    "macbook": ["MacBook Air M2", "MacBook Air M3", "MacBook Pro M3"],
    "airpods": ["AirPods Pro 2", "AirPods 3", "AirPods 4"],
    "playstation": ["PlayStation 5", "PlayStation 5 Slim"],
    "ps5": ["PlayStation 5", "PlayStation 5 Slim"],
}

# --- MA'LUMOTLAR BAZASI ---
db_lock = threading.Lock()
conn = sqlite3.connect("arzonibor.db", check_same_thread=False)
with db_lock:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT,
        UNIQUE(user_id, query)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS support_messages (
        admin_msg_id INTEGER PRIMARY KEY,
        user_id INTEGER
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS price_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT,
        target_price INTEGER,
        current_price INTEGER,
        created_at INTEGER,
        UNIQUE(user_id, query)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        price INTEGER,
        ts INTEGER
    )
    """)
    conn.commit()

def get_support_chat_id():
    if ENV_SUPPORT_GROUP_ID:
        try:
            return int(ENV_SUPPORT_GROUP_ID)
        except ValueError:
            pass
    with db_lock:
        row = conn.execute("SELECT value FROM settings WHERE key = 'support_group_id'").fetchone()
    return int(row[0]) if row else ADMIN_ID

def set_support_chat_id(chat_id):
    with db_lock:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('support_group_id', ?)", (str(chat_id),))
        conn.commit()

def save_price_history(query, price):
    if not query or not price:
        return
    key = query.lower().strip()
    now = int(time.time())
    with db_lock:
        last = conn.execute(
            "SELECT price, ts FROM price_history WHERE query = ? ORDER BY ts DESC LIMIT 1",
            (key,)
        ).fetchone()
        if not last or (now - last[1] > 3600 * 6) or last[0] != int(price):
            conn.execute("INSERT INTO price_history (query, price, ts) VALUES (?, ?, ?)", (key, int(price), now))
            conn.commit()

def get_price_history(query):
    key = query.lower().strip()
    with db_lock:
        rows = conn.execute(
            "SELECT price, ts FROM price_history WHERE query = ? ORDER BY ts ASC LIMIT 30",
            (key,)
        ).fetchall()
    return [{"price": r[0], "ts": r[1]} for r in rows]

waiting_admin_msg = set()
waiting_lock = threading.Lock()

def set_waiting_admin(user_id, state=True):
    with waiting_lock:
        if state:
            waiting_admin_msg.add(user_id)
        else:
            waiting_admin_msg.discard(user_id)

def is_waiting_admin(user_id):
    with waiting_lock:
        return user_id in waiting_admin_msg

# --- KESHLAR ---
CACHE = {}
CACHE_TTL = 6 * 3600
MAX_CACHE_ITEMS = 300

RATES_CACHE = {"time": 0, "data": {}}
RATES_TTL = 300

STORES = {
    "Uzum Market": {"domain": "uzum.uz", "search_url": "https://uzum.uz/uz/search?query={q}"},
    "Olcha": {"domain": "olcha.uz", "search_url": "https://olcha.uz/uz/search?q={q}"},
    "Texnomart": {"domain": "texnomart.uz", "search_url": "https://texnomart.uz/uz/search/?q={q}"},
    "Asaxiy": {"domain": "asaxiy.uz", "search_url": "https://asaxiy.uz/product?key={q}"},
    "Mediapark": {"domain": "mediapark.uz", "search_url": "https://mediapark.uz/search?query={q}"},
    "Idea": {"domain": "idea.uz", "search_url": "https://idea.uz/search?q={q}"}
}

def safe_callback(prefix, text):
    max_bytes = 60 - len(prefix.encode("utf-8"))
    encoded = text.encode("utf-8")[:max_bytes]
    return prefix + encoded.decode("utf-8", errors="ignore")

def format_price(price):
    return f"{int(price):,}".replace(",", " ") + " so'm"

def clean_title(title):
    t = re.sub(r"\s+", " ", (title or "").strip())
    return t[:95] + ("..." if len(t) > 95 else "")

def is_relevant_product(query, title):
    q_low = (query or "").lower().strip()
    t_low = (title or "").lower().strip()
    if not t_low:
        return False
    if not any(w in q_low for w in ACCESSORY_WORDS):
        if any(w in t_low for w in ACCESSORY_WORDS):
            return False
    q_tokens = [w for w in re.findall(r"[a-zA-Zа-яА-Я0-9]+", q_low) if len(w) > 1 or w.isdigit()]
    if not q_tokens:
        return True
    t_tokens = set(re.findall(r"[a-zA-Zа-яА-Я0-9]+", t_low))
    for tok in q_tokens:
        if tok.isdigit():
            if not re.search(rf"(?<![0-9.,]){re.escape(tok)}(?![0-9.,])", t_low):
                return False
        else:
            if tok not in t_tokens and tok not in t_low:
                return False
    return True

# --- MAJBURIY OBUNA (KANAL NOMISIZ) ---
def check_subscription(user_id):
    not_joined = []
    for ch in CHANNELS:
        try:
            member = bot.get_chat_member(ch["username"], user_id)
            if member.status not in ["member", "administrator", "creator"]:
                not_joined.append(ch)
        except Exception as e:
            print(f"[SUB] {ch['username']} xato: {e}")
    return not_joined

def send_sub_message(chat_id, not_joined):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, ch in enumerate(not_joined, 1):
        btn_label = f"➕ {idx}-kanalga obuna" if len(not_joined) > 1 else "➕ Obuna bo'lish"
        markup.add(types.InlineKeyboardButton(btn_label, url=ch["url"]))
    markup.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub"))
    text = (
        "👋 <b>Bot ishlashi uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
        "Obuna bo'lgach, <b>«✅ Tekshirish»</b> tugmasini bosing 👇"
    )
    bot.send_message(chat_id, text, reply_markup=markup)

def require_sub(message):
    not_joined = check_subscription(message.from_user.id)
    if not_joined:
        send_sub_message(message.chat.id, not_joined)
        return False
    return True

# --- VALYUTA VA BITCOIN KURSLARI (ZAXIRA MANBALAR BILAN) ---
def fetch_all_rates():
    now = time.time()
    if RATES_CACHE["data"] and (now - RATES_CACHE["time"] < RATES_TTL):
        return RATES_CACHE["data"]

    result = {}
    # 1. USD, EUR, RUB (Markaziy bank)
    try:
        r = requests.get("https://cbu.uz/uz/arkhiv-kursov-valyut/json/", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                ccy = item.get("Ccy")
                if ccy in ("USD", "EUR", "RUB"):
                    result[ccy.lower()] = {
                        "rate": float(item["Rate"]),
                        "diff": item["Diff"],
                        "date": item["Date"],
                    }
    except Exception as e:
        print(f"[CBU] xato: {e}")

    # 2. Bitcoin (Coinbase -> CoinGecko -> Binance)
    btc_price = None
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=6)
        if r.status_code == 200:
            btc_price = float(r.json()["data"]["amount"])
    except Exception:
        pass

    if not btc_price:
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=6)
            if r.status_code == 200:
                btc_price = float(r.json()["bitcoin"]["usd"])
        except Exception:
            pass

    if not btc_price:
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
            if r.status_code == 200:
                btc_price = float(r.json()["price"])
        except Exception:
            pass

    if btc_price:
        result["btc"] = {"usd": btc_price}

    if result:
        RATES_CACHE["time"] = now
        RATES_CACHE["data"] = result
    return result

def get_rates_text():
    d = fetch_all_rates()
    lines = ["💵 <b>Bugungi valyuta va Bitcoin kurslari:</b>\n"]
    if "usd" in d:
        lines.append(f"🇺🇸 <b>1 USD:</b> {d['usd']['rate']:,.2f} so'm ({d['usd']['diff']})".replace(",", " "))
    if "eur" in d:
        lines.append(f"🇪🇺 <b>1 EUR:</b> {d['eur']['rate']:,.2f} so'm ({d['eur']['diff']})".replace(",", " "))
    if "rub" in d:
        lines.append(f"🇷🇺 <b>1 RUB:</b> {d['rub']['rate']:,.2f} so'm ({d['rub']['diff']})".replace(",", " "))
    if "btc" in d:
        lines.append(f"₿ <b>Bitcoin (BTC):</b> ${d['btc']['usd']:,.0f}".replace(",", " "))
    if len(lines) == 1:
        lines.append("Hozircha kurslarni yuklab bo'lmadi, birozdan so'ng qayta urinib ko'ring.")
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🏦 Banklar kursi (Bank.uz)", url="https://bank.uz/uz/currency"))
    return "\n".join(lines), markup

# --- DO'KONLARDAN QIDIRISH ---
def fetch_olcha(query):
    try:
        url = f"https://api.olcha.uz/api/v2/products?search={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        best = None
        for p in products:
            title = p.get("name_uz") or p.get("name_ru") or p.get("name", "")
            price = p.get("total_price") or p.get("price") or 0
            alias = p.get("alias", "")
            if not price or not is_relevant_product(query, title):
                continue
            price = int(price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Olcha",
                    "price": price,
                    "title": clean_title(title),
                    "url": f"https://olcha.uz/uz/product/view/{alias}" if alias else STORES["Olcha"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception:
        return None

def fetch_texnomart(query):
    try:
        url = f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(query)}"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        best = None
        for p in products:
            title = p.get("name", "")
            price = p.get("sale_price") or p.get("price") or 0
            pid = p.get("id", "")
            if not price or not is_relevant_product(query, title):
                continue
            price = int(price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Texnomart",
                    "price": price,
                    "title": clean_title(title),
                    "url": f"https://texnomart.uz/uz/product/detail/{pid}" if pid else STORES["Texnomart"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception:
        return None

def fetch_asaxiy(query):
    try:
        url = f"https://asaxiy.uz/product?key={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        best = None
        for card in soup.select(".product__item")[:15]:
            title_el = card.select_one(".product__item__info-title")
            price_el = card.select_one(".product__item-price")
            link_el = card.select_one("a[href*='/product/']")
            if not title_el or not price_el:
                continue
            title = title_el.get_text(strip=True)
            digits = re.sub(r"\D", "", price_el.get_text())
            if not digits or not is_relevant_product(query, title):
                continue
            price = int(digits)
            if price < 10000:
                continue
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://asaxiy.uz{href}"
            if best is None or price < best["price"]:
                best = {"store": "Asaxiy", "price": price, "title": clean_title(title), "url": full_url}
        return best
    except Exception:
        return None

def fetch_uzum(query):
    try:
        gql_url = "https://graphql.uzum.uz/"
        payload = {
            "operationName": "getMakeSearch",
            "variables": {
                "queryInput": {
                    "text": query,
                    "showAdultContent": "NONE",
                    "filters": [],
                    "sort": "BY_RELEVANCE_DESC",
                    "pagination": {"offset": 0, "limit": 15}
                }
            },
            "query": """
            query getMakeSearch($queryInput: MakeSearchQueryInput!) {
              makeSearch(query: $queryInput) {
                items { catalogCard { productId title minSellPrice } }
              }
            }
            """
        }
        headers = {
            **HEADERS,
            "Content-Type": "application/json",
            "Origin": "https://uzum.uz",
            "Referer": "https://uzum.uz/",
            "apollographql-client-name": "web-customers",
            "apollographql-client-version": "1.0.0",
        }
        r = requests.post(gql_url, json=payload, headers=headers, timeout=7)
        if r.status_code != 200:
            return None
        items = r.json().get("data", {}).get("makeSearch", {}).get("items", [])
        best = None
        for item in items:
            card = item.get("catalogCard") or {}
            title = card.get("title", "")
            price = card.get("minSellPrice") or 0
            pid = card.get("productId", "")
            if not price or not is_relevant_product(query, title):
                continue
            price = int(price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Uzum Market",
                    "price": price,
                    "title": clean_title(title),
                    "url": f"https://uzum.uz/uz/product/{pid}" if pid else STORES["Uzum Market"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception:
        return None

def extract_price_fallback(text):
    if not text:
        return None
    cleaned = re.sub(
        r'(?:oyiga|в\s*месяц)?\s*(\d{1,3}(?:[\s\u00a0.,]\d{3})+)\s*(?:so[\'‘’`]?m|сум|uzs|сўм)?\s*(?:/|dan)?\s*(?:oyiga|oy|в\s*месяц|мес)',
        '',
        text,
        flags=re.IGNORECASE
    )
    matches = re.findall(r'(\d{1,3}(?:[\s\u00a0.,]\d{3})+)\s*(?:so[\'‘’`]?m|сум|uzs|сўм)', cleaned, re.IGNORECASE)
    found = [int(re.sub(r'\D', '', m)) for m in matches if 100000 <= int(re.sub(r'\D', '', m)) <= 200000000]
    return min(found) if found else None

def search_ddg_fallback(query):
    results = []
    seen = set()
    try:
        with DDGS() as ddgs:
            res = list(ddgs.text(f"{query} narxi so'm uz", region="wt-wt", max_results=10))
            for item in res:
                url = item.get("href", "")
                domain = urlparse(url).netloc.lower().replace("www.", "")
                if not domain.endswith(".uz") or "olx.uz" in domain:
                    continue
                store = next((n for n, i in STORES.items() if i["domain"] in domain), domain.split(".")[0].capitalize() + " (.uz)")
                if store in seen:
                    continue
                title = clean_title(item.get("title", ""))
                body = item.get("body", "")
                if not is_relevant_product(query, title):
                    continue
                price = extract_price_fallback(f"{title} {body}")
                if price:
                    seen.add(store)
                    results.append({"store": store, "price": price, "title": title, "url": url})
    except Exception:
        pass
    return results

def search_prices(query, bypass_cache=False):
    key = query.lower().strip()
    now = time.time()
    if not bypass_cache and key in CACHE and (now - CACHE[key]["time"] < CACHE_TTL):
        return CACHE[key]["data"]

    fetchers = [fetch_olcha, fetch_texnomart, fetch_asaxiy, fetch_uzum]
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for f in [executor.submit(fn, query) for fn in fetchers]:
            res = f.result()
            if res:
                results.append(res)

    if len(results) < 2:
        existing = {r["store"] for r in results}
        for item in search_ddg_fallback(query):
            if item["store"] not in existing:
                results.append(item)
                existing.add(item["store"])

    results.sort(key=lambda x: x["price"])
    results = results[:8]

    if results:
        save_price_history(query, results[0]["price"])
        if len(CACHE) >= MAX_CACHE_ITEMS:
            CACHE.clear()
        CACHE[key] = {"time": now, "data": results}

    return results

# --- NARX KUZATISH (ORQA FONDA TEKSHIRUV) ---
def price_alert_worker():
    while True:
        time.sleep(3 * 3600)  # Har 3 soatda tekshiradi
        try:
            with db_lock:
                alerts = conn.execute("SELECT id, user_id, query, target_price FROM price_alerts").fetchall()
            for alert_id, user_id, query, target_price in alerts:
                results = search_prices(query, bypass_cache=True)
                if not results:
                    continue
                best = results[0]
                if best["price"] <= target_price:
                    text = (
                        f"🔔 <b>Narx tushdi!</b>\n\n"
                        f"📦 <b>{html.escape(query)}</b>\n"
                        f"💰 Yangi eng arzon narx: <b>{format_price(best['price'])}</b>\n"
                        f"🛍 Do'kon: <b>{html.escape(best['store'])}</b>"
                    )
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton(f"🛍 {best['store']} ga o'tish", url=best["url"]))
                    try:
                        bot.send_message(user_id, text, reply_markup=markup)
                        with db_lock:
                            conn.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
                            conn.commit()
                    except Exception:
                        pass
                time.sleep(2)
        except Exception as e:
            print(f"[ALERT WORKER] xato: {e}")

# --- WEB SERVER (MINI APP + API) ---
class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/search":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            if len(q) < 2:
                self._send_json({"query": q, "results": [], "stores": []})
                return
            results = search_prices(q)
            q_enc = quote_plus(q)
            stores = [{"name": n, "url": i["search_url"].format(q=q_enc)} for n, i in STORES.items()]
            self._send_json({"query": q, "results": results, "stores": stores})
            return

        if path == "/api/rates":
            self._send_json(fetch_all_rates())
            return

        if path == "/api/history":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            self._send_json({"query": q, "history": get_price_history(q)})
            return

        if path in ("/", "/index.html"):
            try:
                with open("index.html", "r", encoding="utf-8") as f:
                    content = f.read().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Arzoni Bor ishlayapti!".encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/alert":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                data = json.loads(raw)
                user_id = int(data.get("user_id") or 0)
                query = (data.get("query") or "").strip()[:80]
                target_price = int(data.get("target_price") or 0)
                current_price = int(data.get("current_price") or 0)
                if not user_id or not query or target_price <= 0:
                    self._send_json({"ok": False, "error": "Ma'lumot to'liq emas"}, 400)
                    return
                with db_lock:
                    conn.execute(
                        "INSERT OR REPLACE INTO price_alerts (user_id, query, target_price, current_price, created_at) VALUES (?, ?, ?, ?, ?)",
                        (user_id, query, target_price, current_price, int(time.time()))
                    )
                    conn.commit()
                try:
                    bot.send_message(
                        user_id,
                        f"🔔 <b>Narx kuzatish yoqildi!</b>\n\n"
                        f"📦 <b>{html.escape(query)}</b>\n"
                        f"🎯 Maqsad narx: <b>{format_price(target_price)}</b>\n\n"
                        f"Narx shu summaga tushishi bilan sizga xabar beraman."
                    )
                except Exception:
                    pass
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    ThreadingHTTPServer(("0.0.0.0", port), AppHandler).serve_forever()

# --- TUGMALAR (KATEGORIYASIZ, TOZA MENYU) ---
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("📱 Ilovani ochish (Arzoni Bor)", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    markup.row("🔍 Mahsulot qidirish", "🔥 Mashhur tovarlar")
    markup.row("💵 Kurslar (USD/BTC)", "🔔 Kuzatuvlarim")
    markup.row("❤️ Sevimlilar", "ℹ️ Yordam / Aloqa")
    return markup

def result_buttons(query, results):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for r in results:
        btn_text = f"🛍 {r['store']} — {format_price(r['price'])}"
        markup.add(types.InlineKeyboardButton(btn_text, url=r["url"]))
    best_price = results[0]["price"]
    markup.add(
        types.InlineKeyboardButton("🔔 Narx tushsa xabar ber", callback_data=safe_callback(f"alertmenu:{best_price}:", query)),
        types.InlineKeyboardButton("❤️ Sevimlilarga saqlash", callback_data=safe_callback("fav:", query))
    )
    return markup

# --- GURUHNI ULASH (/setgroup) ---
@bot.message_handler(commands=["setgroup"])
def set_group_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "Bu buyruqni murojaatlar guruhi ichida yozing.")
        return
    set_support_chat_id(message.chat.id)
    bot.reply_to(
        message,
        f"✅ Murojaatlar guruhi ulandi!\n\n"
        f"Render → Environment Variables ga qo'shing:\n"
        f"<code>SUPPORT_GROUP_ID</code> = <code>{message.chat.id}</code>"
    )

# --- ADMIN JAVOBI ---
@bot.message_handler(func=lambda m: m.from_user and m.from_user.id == ADMIN_ID and m.reply_to_message)
def admin_reply_handler(message):
    with db_lock:
        row = conn.execute("SELECT user_id FROM support_messages WHERE admin_msg_id = ?", (message.reply_to_message.message_id,)).fetchone()
    if not row:
        return
    reply_text = (message.text or "").strip()
    if not reply_text:
        return
    try:
        bot.send_message(row[0], f"📩 <b>Javob:</b>\n{html.escape(reply_text)}")
        bot.reply_to(message, "✅ Javob foydalanuvchiga yuborildi.")
    except Exception as e:
        bot.reply_to(message, f"❌ Xato: {html.escape(str(e))}")

# --- HANDLERLAR ---
@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    text = (
        "🤖 <b>Arzoni Bor</b> — O'zbekiston do'konlarida eng arzon narxlarni topish boti.\n\n"
        "📱 Pastdagi <b>«Ilovani ochish»</b> tugmasini bosing yoki shunchaki mahsulot nomini yozing!"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    bot.send_message(message.chat.id, "Asosiy menyu:", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Mahsulot qidirish" in m.text)
def ask_product(message):
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    text = (
        "🔍 <b>Mahsulot nomini aniqroq yozing:</b>\n\n"
        "Masalan:\n"
        "• <code>iPhone 16 Pro</code>\n"
        "• <code>Redmi Note 13</code>\n"
        "• <code>Samsung A55</code>\n"
        "• <code>Artel 43</code>"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Mashhur tovarlar" in m.text)
def popular_products(message):
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    items = ["iPhone 16 Pro", "Redmi Note 13", "Samsung A55", "AirPods Pro 2", "PlayStation 5", "MacBook Air M2"]
    markup.add(*[types.InlineKeyboardButton(i, callback_data=safe_callback("search:", i)) for i in items])
    bot.send_message(message.chat.id, "🔥 Eng ko'p qidirilayotgan mahsulotlar:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Kurslar" in m.text or "Dollar kursi" in m.text))
def dollar_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    text, markup = get_rates_text()
    bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Kuzatuvlarim" in m.text)
def show_alerts(message):
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    with db_lock:
        rows = conn.execute("SELECT id, query, target_price FROM price_alerts WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "🔔 Hozircha narx kuzatuvlaringiz yo'q.\n\nBiror mahsulotni qidirib, <b>«🔔 Narx tushsa xabar ber»</b> tugmasini bosing.")
        return
    markup = types.InlineKeyboardMarkup()
    for row_id, q, target in rows:
        markup.row(
            types.InlineKeyboardButton(f"🔔 {q} (≤ {format_price(target)})", callback_data=safe_callback("search:", q)),
            types.InlineKeyboardButton("❌", callback_data=f"delalert:{row_id}")
        )
    bot.send_message(message.chat.id, "🔔 <b>Faol narx kuzatuvlaringiz:</b>", reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Sevimlilar" in m.text)
def show_favorites(message):
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    with db_lock:
        rows = conn.execute("SELECT id, query FROM favorites WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "Sevimlilar ro'yxatingiz hozircha bo'sh.")
        return
    markup = types.InlineKeyboardMarkup()
    for row_id, q in rows:
        markup.row(types.InlineKeyboardButton(f"🔍 {q}", callback_data=safe_callback("search:", q)), types.InlineKeyboardButton("❌", callback_data=f"delfav:{row_id}"))
    bot.send_message(message.chat.id, "❤️ Saqlangan mahsulotlaringiz:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Yordam / Aloqa" in m.text or "Bot haqida" in m.text))
def help_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    text = "ℹ️ <b>Yordam va Aloqa</b>\n\nSavol yoki taklifingiz bo'lsa, quyidagi tugma orqali adminga yozing 👇"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✍️ Adminga yozish", callback_data="contact_admin"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

def process_search(chat_id, query):
    # Agar faqat brend nomi yozilgan bo'lsa — aniq modellarni tavsiya berish
    q_low = query.lower().strip()
    if q_low in BRAND_SUGGESTIONS:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(*[types.InlineKeyboardButton(m, callback_data=safe_callback("search:", m)) for m in BRAND_SUGGESTIONS[q_low]])
        bot.send_message(chat_id, f"🔍 <b>{html.escape(query)}</b> bo'yicha aniq modelni tanlang:", reply_markup=markup)
        return

    safe_q = html.escape(query)
    wait_msg = bot.send_message(chat_id, f"🔍 <b>{safe_q}</b> narxlari qidirilmoqda...")
    results = search_prices(query)

    try:
        bot.delete_message(chat_id, wait_msg.message_id)
    except Exception:
        pass

    if results:
        text = f"🔎 <b>{safe_q}</b> bo'yicha topilgan narxlar:\n\n<i>Do'konga o'tish uchun tugmani bosing 👇</i>"
        bot.send_message(chat_id, text, reply_markup=result_buttons(query, results), disable_web_page_preview=True)
    else:
        # Xato yozilgan bo'lsa brend bo'yicha tavsiya berish
        sug = None
        for brand, models in BRAND_SUGGESTIONS.items():
            if brand in q_low:
                sug = models
                break
        if sug:
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(*[types.InlineKeyboardButton(m, callback_data=safe_callback("search:", m)) for m in sug])
            bot.send_message(chat_id, f"🔎 <b>{safe_q}</b> bo'yicha aniq narx topilmadi.\nBalki quyidagilardan birini qidiryapsiz?", reply_markup=markup)
        else:
            bot.send_message(chat_id, f"🔎 <b>{safe_q}</b>\n\nAfsuski, do'konlardan aniq narx topilmadi. Model nomini to'g'ri yozib ko'ring (masalan: <code>Redmi Note 13</code>).")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data == "check_sub":
        not_joined = check_subscription(call.from_user.id)
        if not_joined:
            bot.answer_callback_query(call.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "✅ Rahmat!")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, "Xush kelibsiz! Mahsulot nomini yozing 👇", reply_markup=main_menu())
    elif call.data == "contact_admin":
        bot.answer_callback_query(call.id)
        set_waiting_admin(call.from_user.id, True)
        bot.send_message(call.message.chat.id, "✍️ Xabaringizni yozing (bekor qilish: /cancel):")
    elif call.data.startswith("search:"):
        bot.answer_callback_query(call.id)
        process_search(call.message.chat.id, call.data.split("search:", 1)[1])
    elif call.data.startswith("alertmenu:"):
        bot.answer_callback_query(call.id)
        parts = call.data.split(":", 2)
        cur_price = int(parts[1])
        q = parts[2]
        p5 = int(cur_price * 0.95)
        p10 = int(cur_price * 0.90)
        p15 = int(cur_price * 0.85)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"📉 -5% ({format_price(p5)})", callback_data=safe_callback(f"setalert:{p5}:{cur_price}:", q)),
            types.InlineKeyboardButton(f"📉 -10% ({format_price(p10)})", callback_data=safe_callback(f"setalert:{p10}:{cur_price}:", q)),
            types.InlineKeyboardButton(f"📉 -15% ({format_price(p15)})", callback_data=safe_callback(f"setalert:{p15}:{cur_price}:", q)),
        )
        bot.send_message(
            call.message.chat.id,
            f"🔔 <b>{html.escape(q)}</b>\nHozirgi narx: <b>{format_price(cur_price)}</b>\n\nQaysi narxga tushganda xabar beray?",
            reply_markup=markup
        )
    elif call.data.startswith("setalert:"):
        parts = call.data.split(":", 3)
        target_p, cur_p, q = int(parts[1]), int(parts[2]), parts[3]
        with db_lock:
            conn.execute(
                "INSERT OR REPLACE INTO price_alerts (user_id, query, target_price, current_price, created_at) VALUES (?, ?, ?, ?, ?)",
                (call.from_user.id, q, target_p, cur_p, int(time.time()))
            )
            conn.commit()
        bot.answer_callback_query(call.id, "✅ Kuzatuv yoqildi!")
        bot.edit_message_text(
            f"✅ <b>Narx kuzatish yoqildi!</b>\n\n📦 {html.escape(q)}\n🎯 Maqsad narx: <b>{format_price(target_p)}</b>\n\nNarx tushishi bilan xabar beraman.",
            call.message.chat.id,
            call.message.message_id
        )
    elif call.data.startswith("delalert:"):
        with db_lock:
            conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (int(call.data.split("delalert:", 1)[1]), call.from_user.id))
            conn.commit()
        bot.answer_callback_query(call.id, "🗑 O'chirildi")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
    elif call.data.startswith("fav:"):
        q = call.data.split("fav:", 1)[1]
        with db_lock:
            conn.execute("INSERT OR IGNORE INTO favorites (user_id, query) VALUES (?, ?)", (call.from_user.id, q))
            conn.commit()
        bot.answer_callback_query(call.id, "✅ Sevimlilarga saqlandi!")
    elif call.data.startswith("delfav:"):
        with db_lock:
            conn.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (int(call.data.split("delfav:", 1)[1]), call.from_user.id))
            conn.commit()
        bot.answer_callback_query(call.id, "🗑 O'chirildi")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.chat.type == "private")
def handle_text(message):
    user = message.from_user
    text = (message.text or "").strip()
    if is_waiting_admin(user.id):
        set_waiting_admin(user.id, False)
        if not text:
            return
        admin_text = f"👤 <b>{html.escape(user.full_name or 'Noma’lum')}</b> | <code>{user.id}</code>\n💬 {html.escape(text)}"
        try:
            target_chat = get_support_chat_id()
            sent = bot.send_message(target_chat, admin_text)
            with db_lock:
                conn.execute("INSERT OR REPLACE INTO support_messages (admin_msg_id, user_id) VALUES (?, ?)", (sent.message_id, user.id))
                conn.commit()
            bot.send_message(message.chat.id, "✅ Xabaringiz yuborildi!", reply_markup=main_menu())
        except Exception as e:
            print(f"[SUPPORT] xato: {e}")
            bot.send_message(message.chat.id, "❌ Xabar yuborilmadi.", reply_markup=main_menu())
        return

    if not require_sub(message):
        return
    if len(text) < 2:
        bot.send_message(message.chat.id, "Iltimos, mahsulot nomini to'liqroq yozing.")
        return
    process_search(message.chat.id, text)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=price_alert_worker, daemon=True).start()
    print("Arzoni Bor ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
