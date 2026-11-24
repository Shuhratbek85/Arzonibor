import os
import re
import hmac
import html
import json
import time
import hashlib
import sqlite3
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlparse, parse_qs, parse_qsl
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
MAX_POST_BYTES = 16384  # 16 KB
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

# --- RATE LIMITER (IP bo'yicha) ---
rate_lock = threading.Lock()
rate_buckets = defaultdict(list)

def is_rate_limited(ip, limit=20, window=60):
    now = time.time()
    with rate_lock:
        recent = [t for t in rate_buckets[ip] if now - t < window]
        if len(recent) >= limit:
            rate_buckets[ip] = recent
            return True
        recent.append(now)
        rate_buckets[ip] = recent
        return False

# --- TELEGRAM MINI APP INITDATA TEKSHIRUVI (HMAC-SHA256) ---
def verify_telegram_init_data(init_data):
    if not init_data or len(init_data) > 4096:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        auth_date = int(parsed.get("auth_date", 0))
        if abs(time.time() - auth_date) > 86400:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return None
        user_json = parsed.get("user")
        if not user_json:
            return None
        user_obj = json.loads(user_json)
        uid = int(user_obj.get("id", 0))
        return uid if uid > 0 else None
    except Exception:
        return None

# --- MA'LUMOTLAR BAZASI ---
db_lock = threading.Lock()
conn = sqlite3.connect("arzonibor.db", check_same_thread=False)
with db_lock:
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
        image TEXT,
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
    try:
        conn.execute("ALTER TABLE price_alerts ADD COLUMN image TEXT")
    except Exception:
        pass
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

def format_price(price):
    return f"{int(price):,}".replace(",", " ") + " so'm"

def clean_title(title):
    t = re.sub(r"\s+", " ", (title or "").strip())
    return t[:110] + ("..." if len(t) > 110 else "")

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

# --- MAJBURIY OBUNA ---
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

# --- VALYUTA VA BITCOIN KURSLARI ---
def fetch_all_rates():
    now = time.time()
    if RATES_CACHE["data"] and (now - RATES_CACHE["time"] < RATES_TTL):
        return RATES_CACHE["data"]

    result = {}
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

    btc_price = None
    for url, parser in [
        ("https://api.coinbase.com/v2/prices/BTC-USD/spot", lambda j: float(j["data"]["amount"])),
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", lambda j: float(j["bitcoin"]["usd"])),
        ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda j: float(j["price"])),
    ]:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                btc_price = parser(r.json())
                break
        except Exception:
            continue

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
        lines.append("Kurslarni yuklab bo'lmadi, birozdan so'ng qayta urinib ko'ring.")
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
            img = p.get("main_image") or ""
            monthly = parse_monthly_price(p.get("monthly_repayment"), price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Olcha",
                    "price": price,
                    "monthly": monthly,
                    "delivery": "O'zbekiston bo'ylab yetkazish",
                    "image": img,
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
            img = p.get("image") or ""
            monthly = parse_monthly_price(p.get("axiom_monthly_price"), price)
            if best is None or price < best["price"]:
                best = {
                    "store": "Texnomart",
                    "price": price,
                    "monthly": monthly,
                    "delivery": "Kuryer / Do'kondan olib ketish",
                    "image": img,
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
            img_el = card.select_one("img")
            if not title_el or not price_el:
                continue
            title = title_el.get_text(strip=True)
            digits = re.sub(r"\D", "", price_el.get_text())
            if not digits or not is_relevant_product(query, title):
                continue
            price = int(digits)
            if price < 10000:
                continue
            img = ""
            if img_el:
                img = img_el.get("data-src") or img_el.get("src") or ""
            href = link_el["href"] if link_el and link_el.get("href") else ""
            full_url = href if href.startswith("http") else f"https://asaxiy.uz{href}"
            if best is None or price < best["price"]:
                best = {
                    "store": "Asaxiy",
                    "price": price,
                    "monthly": round(price / 12),
                    "delivery": "O'zbekiston bo'ylab yetkazish",
                    "image": img,
                    "title": clean_title(title),
                    "url": full_url
                }
        return best
    except Exception:
        return None

def fetch_uzum(query):
    gql_url = "https://graphql.uzum.uz/"
    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Origin": "https://uzum.uz",
        "Referer": "https://uzum.uz/",
        "apollographql-client-name": "web-customers",
        "apollographql-client-version": "1.0.0",
    }
    queries = [
        """
        query getMakeSearch($queryInput: MakeSearchQueryInput!) {
          makeSearch(query: $queryInput) {
            items { catalogCard { productId title minSellPrice photos { key } } }
          }
        }
        """,
        """
        query getMakeSearch($queryInput: MakeSearchQueryInput!) {
          makeSearch(query: $queryInput) {
            items { catalogCard { productId title minSellPrice } }
          }
        }
        """
    ]
    for gql in queries:
        try:
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
                "query": gql
            }
            r = requests.post(gql_url, json=payload, headers=headers, timeout=7)
            if r.status_code != 200:
                continue
            items = r.json().get("data", {}).get("makeSearch", {}).get("items", [])
            if not items:
                continue
            best = None
            for item in items:
                card = item.get("catalogCard") or {}
                title = card.get("title", "")
                price = card.get("minSellPrice") or 0
                pid = card.get("productId", "")
                if not price or not is_relevant_product(query, title):
                    continue
                price = int(price)
                photos = card.get("photos") or []
                img = f"https://images.uzum.uz/{photos[0]['key']}/t_product_540_high.jpg" if photos and photos[0].get("key") else ""
                if best is None or price < best["price"]:
                    best = {
                        "store": "Uzum Market",
                        "price": price,
                        "monthly": round(price / 12),
                        "delivery": "1 kunda bepul (punktga)",
                        "image": img,
                        "title": clean_title(title),
                        "url": f"https://uzum.uz/uz/product/{pid}" if pid else STORES["Uzum Market"]["search_url"].format(q=quote_plus(query))
                    }
            if best:
                return best
        except Exception:
            continue
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
                    results.append({
                        "store": store,
                        "price": price,
                        "monthly": round(price / 12),
                        "delivery": "Do'kon shartlari bo'yicha",
                        "image": "",
                        "title": title,
                        "url": url
                    })
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

    main_img = next((r["image"] for r in results if r.get("image")), "")
    if results and not main_img:
        main_img = fetch_fallback_image(query)
    for r in results:
        if not r.get("image"):
            r["image"] = main_img

    if results:
        save_price_history(query, results[0]["price"])
        if len(CACHE) >= MAX_CACHE_ITEMS:
            CACHE.clear()
        CACHE[key] = {"time": now, "data": results}

    return results

# --- NARX KUZATISH (ORQA FONDA) ---
def price_alert_worker():
    while True:
        time.sleep(3 * 3600)
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

# --- WEB SERVER (MINI APP + XAVFSIZ API) ---
class AppHandler(BaseHTTPRequestHandler):
    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _auth_user_id(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        return verify_telegram_init_data(init_data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json({"status": "ok"})
            return

        if path == "/api/search":
            if is_rate_limited(self._client_ip(), limit=20, window=60):
                self._send_json({"error": "Juda ko'p so'rov. Biroz kuting.", "results": []}, 429)
                return
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            if len(q) < 2:
                self._send_json({"query": q, "results": []})
                return
            results = search_prices(q)
            self._send_json({"query": q, "results": results})
            return

        if path == "/api/rates":
            self._send_json(fetch_all_rates())
            return

        if path == "/api/history":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            self._send_json({"query": q, "history": get_price_history(q)})
            return

        if path == "/api/alerts":
            uid = self._auth_user_id()
            if not uid:
                self._send_json({"error": "Telegram autentifikatsiyasi talab qilinadi", "alerts": []}, 401)
                return
            with db_lock:
                rows = conn.execute(
                    "SELECT id, query, target_price, current_price, image FROM price_alerts WHERE user_id = ? ORDER BY id DESC",
                    (uid,)
                ).fetchall()
            items = [{"id": r[0], "query": r[1], "target_price": r[2], "current_price": r[3], "image": r[4] or ""} for r in rows]
            self._send_json({"alerts": items})
            return

        if path in ("/", "/index.html"):
            try:
                with open("index.html", "r", encoding="utf-8") as f:
                    content = f.read().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
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
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json({"ok": False, "error": "Noto'g'ri so'rov"}, 400)
            return
        if length <= 0 or length > MAX_POST_BYTES:
            self._send_json({"ok": False, "error": "So'rov hajmi ruxsat etilmagan"}, 413)
            return

        try:
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("JSON object kutilgan")
        except Exception:
            self._send_json({"ok": False, "error": "Noto'g'ri JSON"}, 400)
            return

        uid = self._auth_user_id()
        if not uid:
            self._send_json({"ok": False, "error": "Telegram autentifikatsiyasi talab qilinadi"}, 401)
            return

        if parsed.path == "/api/alert":
            try:
                query = str(data.get("query") or "").strip()[:80]
                target_price = int(data.get("target_price") or 0)
                current_price = int(data.get("current_price") or 0)
                image = str(data.get("image") or "").strip()[:300]
                if image and not image.startswith(("http://", "https://")):
                    image = ""
                if len(query) < 2 or not (1000 <= target_price <= 500000000):
                    self._send_json({"ok": False, "error": "Narx yoki nom noto'g'ri"}, 400)
                    return
                with db_lock:
                    conn.execute(
                        "INSERT OR REPLACE INTO price_alerts (user_id, query, target_price, current_price, image, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, query, target_price, max(0, current_price), image, int(time.time()))
                    )
                    conn.commit()
                try:
                    bot.send_message(
                        uid,
                        f"🔔 <b>Narx kuzatish yoqildi!</b>\n\n"
                        f"📦 <b>{html.escape(query)}</b>\n"
                        f"🎯 Maqsad narx: <b>{format_price(target_price)}</b>\n\n"
                        f"Narx shu summaga tushishi bilan sizga xabar beraman."
                    )
                except Exception:
                    pass
                self._send_json({"ok": True})
            except Exception:
                self._send_json({"ok": False, "error": "Server xatosi"}, 500)
            return

        if parsed.path == "/api/alert/delete":
            try:
                alert_id = int(data.get("id") or 0)
                if alert_id <= 0:
                    self._send_json({"ok": False, "error": "ID noto'g'ri"}, 400)
                    return
                with db_lock:
                    conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (alert_id, uid))
                    conn.commit()
                self._send_json({"ok": True})
            except Exception:
                self._send_json({"ok": False, "error": "Server xatosi"}, 500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    ThreadingHTTPServer(("0.0.0.0", port), AppHandler).serve_forever()

# --- IXCHAM BOT MENYUSI ---
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("📱 Ilovani ochish (Arzoni Bor)", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    markup.row("💵 Kurslar (USD/BTC)", "🔔 Kuzatuvlarim")
    markup.row("ℹ️ Yordam / Aloqa")
    return markup

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
        bot.reply_to(message, "✅ Javob yuborildi.")
    except Exception as e:
        bot.reply_to(message, f"❌ Xato: {html.escape(str(e))}")

@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    if not require_sub(message):
        return
    text = (
        "👋 <b>Arzoni Bor</b> ilovasiga xush kelibsiz!\n\n"
        "O'zbekiston do'konlaridagi mahsulotlarning <b>rasmi, narxi, muddatli to'lovi va yetkazib berish shartlarini</b> bir joyda solishtiring.\n\n"
        "📱 Boshlash uchun pastdagi <b>«Ilovani ochish»</b> tugmasini bosing 👇"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    bot.send_message(message.chat.id, "Asosiy menyu:", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Kurslar" in m.text)
def rates_cmd(message):
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
        bot.send_message(message.chat.id, "🔔 Hozircha faol kuzatuvlaringiz yo'q.\n\nIlovani ochib istalgan mahsulotda <b>«Kuzatish»</b> tugmasini bosing.")
        return
    markup = types.InlineKeyboardMarkup()
    for row_id, q, target in rows:
        app_link = f"{WEBAPP_URL}/?q={quote_plus(q)}"
        markup.row(
            types.InlineKeyboardButton(f"🔔 {q} (≤ {format_price(target)})", web_app=types.WebAppInfo(url=app_link)),
            types.InlineKeyboardButton("❌", callback_data=f"delalert:{row_id}")
        )
    bot.send_message(message.chat.id, "🔔 <b>Faol narx kuzatuvlaringiz:</b>", reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and "Yordam / Aloqa" in m.text)
def help_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    text = "ℹ️ <b>Yordam va Aloqa</b>\n\nSavol yoki taklifingiz bo'lsa, quyidagi tugma orqali adminga yozing 👇"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✍️ Adminga yozish", callback_data="contact_admin"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

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
            bot.send_message(call.message.chat.id, "Xush kelibsiz! Pastdagi tugma orqali ilovani oching 👇", reply_markup=main_menu())
    elif call.data == "contact_admin":
        bot.answer_callback_query(call.id)
        set_waiting_admin(call.from_user.id, True)
        bot.send_message(call.message.chat.id, "✍️ Xabaringizni yozing (bekor qilish: /cancel):")
    elif call.data.startswith("delalert:"):
        with db_lock:
            conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (int(call.data.split("delalert:", 1)[1]), call.from_user.id))
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
        bot.send_message(message.chat.id, "Mahsulot nomini to'liqroq yozing yoki ilovani oching 👇", reply_markup=main_menu())
        return

    app_link = f"{WEBAPP_URL}/?q={quote_plus(text)}"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(f"🔍 «{text[:25]}» — ilovada ko'rish", web_app=types.WebAppInfo(url=app_link)))
    bot.send_message(
        message.chat.id,
        f"📦 <b>{html.escape(text)}</b>\n\nRasmi, narxlari va muddatli to'lovini ko'rish uchun tugmani bosing 👇",
        reply_markup=markup
    )

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=price_alert_worker, daemon=True).start()
    print("Arzoni Bor ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
