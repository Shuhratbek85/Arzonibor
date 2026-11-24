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

QUERY_SYNONYMS = {
    "ayfon": "iphone", "айфон": "iphone", "ifone": "iphone",
    "samsung": "samsung", "самсунг": "samsung",
    "redmi": "redmi", "редми": "redmi", "xiaomi": "xiaomi", "сяоми": "xiaomi",
    "televizor": "телевизор", "tevizor": "телевизор", "tv": "телевизор",
    "noutbuk": "ноутбук", "laptop": "ноутбук", "makbuk": "macbook", "макбук": "macbook",
    "konditsioner": "кондиционер", "kanditsioner": "кондиционер",
    "muzlatgich": "холодильник", "xolodilnik": "холодильник",
    "kir yuvish": "стиральная машина", "avtomat": "стиральная машина",
    "changyutgich": "пылесос", "pilesos": "пылесос",
    "quloqchin": "наушники", "naushnik": "наушники", "airpods": "airpods",
    "soat": "смарт часы", "playstation": "playstation", "ps5": "playstation 5",
}

POPULAR_SUGGESTIONS = [
    "iPhone 16 Pro", "iPhone 16 Pro Max", "iPhone 16", "iPhone 15 Pro", "iPhone 15", "iPhone 14", "iPhone 13",
    "Samsung Galaxy S24 Ultra", "Samsung Galaxy S24", "Samsung Galaxy A55", "Samsung Galaxy A35", "Samsung Galaxy A15",
    "Redmi Note 13", "Redmi Note 13 Pro", "Redmi Note 14", "Redmi 13C", "Redmi 14C", "Xiaomi 14", "Poco X6 Pro",
    "Honor X9b", "Honor 200", "Infinix Note 40", "Tecno Camon 30",
    "MacBook Air M2", "MacBook Air M3", "MacBook Pro M3", "Asus VivoBook", "Lenovo IdeaPad", "HP Pavilion", "Acer Aspire",
    "AirPods Pro 2", "AirPods 4", "Apple Watch Series 9", "Apple Watch Ultra 2", "Samsung Galaxy Watch 6",
    "PlayStation 5", "PlayStation 5 Slim", "Xbox Series S", "Nintendo Switch",
    "Artel 43 televizor", "Samsung 55 televizor", "LG 50 televizor", "Xiaomi TV A Pro 43",
    "Artel konditsioner 12", "Gree konditsioner 12", "Midea konditsioner 12",
    "LG kir yuvish mashinasi", "Artel muzlatgich", "Samsung muzlatgich", "Dyson changyutgich"
]

def normalize_query(q):
    low = re.sub(r"\s+", " ", (q or "").strip().lower())
    for k, v in QUERY_SYNONYMS.items():
        if k in low:
            low = re.sub(rf"\b{re.escape(k)}\b", v, low)
    return low.strip()

# --- BOT TARJIMALARI (UZ / RU) ---
BOT_TEXT = {
    "uz": {
        "btn_open": "📱 Ilovani ochish",
        "btn_rates": "💵 Kurslar (USD/BTC)",
        "btn_alerts": "🔔 Kuzatuvlarim",
        "btn_help": "ℹ️ Yordam / Aloqa",
        "btn_lang": "🌐 Til / Язык",
        "inline_open": "📱 Ilovani ochish (Arzoni Bor)",
        "inline_search": "🔍 «{q}» — ilovada ko'rish",
        "sub_text": "👋 <b>Bot ishlashi uchun quyidagi kanallarga obuna bo'ling:</b>\n\nObuna bo'lgach, <b>«✅ Tekshirish»</b> tugmasini bosing 👇",
        "sub_btn": "➕ {i}-kanalga obuna",
        "sub_btn_one": "➕ Obuna bo'lish",
        "sub_check": "✅ Tekshirish",
        "sub_fail": "❌ Hali barcha kanallarga obuna bo'lmadingiz!",
        "sub_ok": "✅ Obuna tasdiqlandi!",
        "welcome": "👋 <b>Arzoni Bor</b> — O'zbekiston do'konlari narxlarini solishtiring!",
        "open_hint": "📱 Ilovani ochish va narx kuzatuvini yoqish uchun pastdagi tugmani bosing 👇",
        "no_alerts": "🔔 Hozircha faol kuzatuvlaringiz yo'q.\n\nIlovani ochib istalgan mahsulotda <b>«Kuzatish»</b> tugmasini bosing 👇",
        "my_alerts": "🔔 <b>Faol narx kuzatuvlaringiz:</b>",
        "help_text": "ℹ️ <b>Yordam va Aloqa</b>\n\nSavol yoki taklifingiz bo'lsa, quyidagi tugma orqali adminga yozing 👇",
        "contact_btn": "✍️ Adminga yozish",
        "write_msg": "✍️ Xabaringizni yozing (bekor qilish: /cancel):",
        "sent_ok": "✅ Xabaringiz yuborildi!",
        "sent_err": "❌ Xabar yuborilmadi.",
        "short_q": "Mahsulot nomini to'liqroq yozing yoki ilovani oching 👇",
        "search_hint": "📦 <b>{q}</b>\n\nRasmi, narxlari va muddatli to'lovini ko'rish uchun tugmani bosing 👇",
        "alert_set": "🔔 <b>Narx kuzatish yoqildi!</b>\n\n📦 <b>{q}</b>\n🎯 Maqsad narx: <b>{p}</b>\n\nNarx shu summaga tushishi bilan sizga xabar beraman.",
        "price_drop": "🔔 <b>Narx tushdi!</b>\n\n📦 <b>{q}</b>\n💰 Yangi eng arzon narx: <b>{p}</b>\n🛍 Do'kon: <b>{s}</b>",
        "go_shop": "🛍 {s} ga o'tish",
        "rates_title": "💵 <b>Bugungi valyuta va Bitcoin kurslari:</b>\n",
        "bank_btn": "🏦 Banklar kursi (Bank.uz)",
        "sum": "so'm",
    },
    "ru": {
        "btn_open": "📱 Открыть приложение",
        "btn_rates": "💵 Курсы (USD/BTC)",
        "btn_alerts": "🔔 Отслеживания",
        "btn_help": "ℹ️ Помощь / Связь",
        "btn_lang": "🌐 Язык / Til",
        "inline_open": "📱 Открыть приложение (Arzoni Bor)",
        "inline_search": "🔍 «{q}» — открыть в приложении",
        "sub_text": "👋 <b>Для работы бота подпишитесь на каналы ниже:</b>\n\nПосле подписки нажмите <b>«✅ Проверить»</b> 👇",
        "sub_btn": "➕ Подписаться на канал {i}",
        "sub_btn_one": "➕ Подписаться",
        "sub_check": "✅ Проверить",
        "sub_fail": "❌ Вы ещё не подписались на все каналы!",
        "sub_ok": "✅ Подписка подтверждена!",
        "welcome": "👋 <b>Arzoni Bor</b> — сравнивайте цены магазинов Узбекистана!",
        "open_hint": "📱 Нажмите кнопку ниже, чтобы открыть приложение и отслеживать цены 👇",
        "no_alerts": "🔔 У вас пока нет активных отслеживаний.\n\nОткройте приложение и нажмите <b>«Отслеживать»</b> на любом товаре 👇",
        "my_alerts": "🔔 <b>Ваши активные отслеживания цен:</b>",
        "help_text": "ℹ️ <b>Помощь и связь</b>\n\nЕсли есть вопрос или предложение, напишите админу 👇",
        "contact_btn": "✍️ Написать админу",
        "write_msg": "✍️ Напишите ваше сообщение (отмена: /cancel):",
        "sent_ok": "✅ Сообщение отправлено!",
        "sent_err": "❌ Не удалось отправить.",
        "short_q": "Напишите название товара подробнее или откройте приложение 👇",
        "search_hint": "📦 <b>{q}</b>\n\nНажмите кнопку ниже, чтобы увидеть фото, цены и рассрочку 👇",
        "alert_set": "🔔 <b>Отслеживание включено!</b>\n\n📦 <b>{q}</b>\n🎯 Целевая цена: <b>{p}</b>\n\nКак только цена снизится, я сразу сообщу вам.",
        "price_drop": "🔔 <b>Цена снизилась!</b>\n\n📦 <b>{q}</b>\n💰 Новая лучшая цена: <b>{p}</b>\n🛍 Магазин: <b>{s}</b>",
        "go_shop": "🛍 Перейти в {s}",
        "rates_title": "💵 <b>Курсы валют и Bitcoin на сегодня:</b>\n",
        "bank_btn": "🏦 Курсы банков (Bank.uz)",
        "sum": "сум",
    }
}

def bt(lang, key, **kwargs):
    d = BOT_TEXT.get(lang or "uz", BOT_TEXT["uz"])
    val = d.get(key, BOT_TEXT["uz"].get(key, key))
    return val.format(**kwargs) if kwargs else val

# --- RATE LIMITER ---
rate_lock = threading.Lock()
rate_buckets = defaultdict(list)

def is_rate_limited(ip, limit=30, window=60):
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
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS support_messages (admin_msg_id INTEGER PRIMARY KEY, user_id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
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

def get_user_lang(user_id):
    with db_lock:
        row = conn.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] in ("uz", "ru") else None

def set_user_lang(user_id, lang):
    if lang not in ("uz", "ru"):
        lang = "uz"
    with db_lock:
        conn.execute("INSERT OR REPLACE INTO users (user_id, lang) VALUES (?, ?)", (user_id, lang))
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
        last = conn.execute("SELECT price, ts FROM price_history WHERE query = ? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        if not last or (now - last[1] > 3600 * 6) or last[0] != int(price):
            conn.execute("INSERT INTO price_history (query, price, ts) VALUES (?, ?, ?)", (key, int(price), now))
            conn.commit()

def get_price_history(query):
    key = query.lower().strip()
    with db_lock:
        rows = conn.execute("SELECT price, ts FROM price_history WHERE query = ? ORDER BY ts ASC LIMIT 30", (key,)).fetchall()
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

def format_price(price, lang="uz"):
    return f"{int(price):,}".replace(",", " ") + " " + bt(lang, "sum")

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
    return matched >= max(1, len(q_tokens) - (1 if len(q_tokens) >= 3 else 0))

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

def send_sub_message(chat_id, not_joined, lang="uz"):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, ch in enumerate(not_joined, 1):
        btn_label = bt(lang, "sub_btn", i=idx) if len(not_joined) > 1 else bt(lang, "sub_btn_one")
        markup.add(types.InlineKeyboardButton(btn_label, url=ch["url"]))
    markup.add(types.InlineKeyboardButton(bt(lang, "sub_check"), callback_data="check_sub"))
    bot.send_message(chat_id, bt(lang, "sub_text"), reply_markup=markup)

def require_sub(message, lang="uz"):
    not_joined = check_subscription(message.from_user.id)
    if not_joined:
        send_sub_message(message.chat.id, not_joined, lang)
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
                    result[ccy.lower()] = {"rate": float(item["Rate"]), "diff": item["Diff"], "date": item["Date"]}
    except Exception:
        pass
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

def get_rates_text(lang="uz"):
    d = fetch_all_rates()
    s = bt(lang, "sum")
    lines = [bt(lang, "rates_title")]
    if "usd" in d:
        lines.append(f"🇺🇸 <b>1 USD:</b> {d['usd']['rate']:,.2f} {s} ({d['usd']['diff']})".replace(",", " "))
    if "eur" in d:
        lines.append(f"🇪🇺 <b>1 EUR:</b> {d['eur']['rate']:,.2f} {s} ({d['eur']['diff']})".replace(",", " "))
    if "rub" in d:
        lines.append(f"🇷🇺 <b>1 RUB:</b> {d['rub']['rate']:,.2f} {s} ({d['rub']['diff']})".replace(",", " "))
    if "btc" in d:
        lines.append(f"₿ <b>Bitcoin (BTC):</b> ${d['btc']['usd']:,.0f}".replace(",", " "))
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(bt(lang, "bank_btn"), url="https://bank.uz/uz/currency"))
    return "\n".join(lines), markup

# --- 7 TA REAL DO'KONDAN QIDIRISH ---
def fetch_olcha(query, debug=None):
    try:
        url = f"https://api.olcha.uz/api/v2/products?search={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://olcha.uz", "Referer": "https://olcha.uz/"}, timeout=7)
        if debug is not None:
            debug["Olcha"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        if debug is not None:
            debug["Olcha"]["raw_count"] = len(products)
        best = None
        for p in products:
            if p.get("out_of_stock") is True or p.get("in_stock") is False:
                continue
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
                    "store": "Olcha", "price": price, "monthly": monthly,
                    "delivery": "O'zbekiston bo'ylab yetkazish", "image": img, "title": clean_title(title),
                    "url": f"https://olcha.uz/uz/product/view/{alias}" if alias else f"https://olcha.uz/uz/search?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        if debug is not None:
            debug["Olcha"] = {"error": str(e)[:120]}
        return None

def fetch_texnomart(query, debug=None):
    try:
        url = f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(query)}"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        if debug is not None:
            debug["Texnomart"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        products = r.json().get("data", {}).get("products", [])
        if debug is not None:
            debug["Texnomart"]["raw_count"] = len(products)
        best = None
        for p in products:
            if str(p.get("availability", "")).lower() in ("outofstock", "none", "0"):
                continue
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
                    "store": "Texnomart", "price": price, "monthly": monthly,
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title),
                    "url": f"https://texnomart.uz/uz/product/detail/{pid}" if pid else f"https://texnomart.uz/uz/search/?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        if debug is not None:
            debug["Texnomart"] = {"error": str(e)[:120]}
        return None

def fetch_asaxiy(query, debug=None):
    try:
        url = f"https://asaxiy.uz/product?key={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if debug is not None:
            debug["Asaxiy"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product__item")[:15]
        if debug is not None:
            debug["Asaxiy"]["raw_count"] = len(cards)
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
            digits = re.sub(r"\D", "", price_el.get_text())
            if not digits or not is_relevant_product(query, title):
                continue
            price = int(digits)
            if price < 10000:
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
        if debug is not None:
            debug["Asaxiy"] = {"error": str(e)[:120]}
        return None

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
        if debug is not None:
            debug["Uzum"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        items = r.json().get("data", {}).get("makeSearch", {}).get("items", [])
        if debug is not None:
            debug["Uzum"]["raw_count"] = len(items)
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
                    "store": "Uzum Market", "price": price, "monthly": round(price / 12),
                    "delivery": "1 kunda bepul (punktga)", "image": img, "title": clean_title(title),
                    "url": f"https://uzum.uz/uz/product/{pid}" if pid else f"https://uzum.uz/uz/search?query={quote_plus(query)}"
                }
        return best
    except Exception as e:
        if debug is not None:
            debug["Uzum"] = {"error": str(e)[:120]}
        return None

def fetch_mediapark(query, debug=None):
    try:
        url = f"https://api.client.mediapark.uz/v1/products/search?search={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://mediapark.uz", "Referer": "https://mediapark.uz/"}, timeout=7)
        if debug is not None:
            debug["Mediapark"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("data", []) if isinstance(data.get("data"), list) else data.get("data", {}).get("products", [])
        if debug is not None:
            debug["Mediapark"]["raw_count"] = len(products)
        best = None
        for p in products:
            title = p.get("name") or p.get("title") or ""
            price = p.get("price") or p.get("actual_price") or 0
            if not price or not is_relevant_product(query, title):
                continue
            price = int(price)
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
        if debug is not None:
            debug["Mediapark"] = {"error": str(e)[:120]}
        return None

def fetch_idea(query, debug=None):
    try:
        url = f"https://api.idea.uz/api/v2/products?q={quote_plus(query)}&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://idea.uz", "Referer": "https://idea.uz/"}, timeout=7)
        if debug is not None:
            debug["Idea"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        products = r.json().get("data", [])
        if debug is not None:
            debug["Idea"]["raw_count"] = len(products)
        best = None
        for p in products:
            title = p.get("title_name") or p.get("name") or ""
            price = p.get("current_price") or p.get("price") or 0
            if not price or not is_relevant_product(query, title):
                continue
            price = int(price)
            slug = p.get("slug") or p.get("id") or ""
            img = p.get("img") or p.get("image") or ""
            if best is None or price < best["price"]:
                best = {
                    "store": "Idea.uz", "price": price, "monthly": round(price / 12),
                    "delivery": "Kuryer / Do'kondan olib ketish", "image": img, "title": clean_title(title),
                    "url": f"https://idea.uz/product/{slug}" if slug else f"https://idea.uz/search?q={quote_plus(query)}"
                }
        return best
    except Exception as e:
        if debug is not None:
            debug["Idea"] = {"error": str(e)[:120]}
        return None

def fetch_alifshop(query, debug=None):
    try:
        url = f"https://gw.alifshop.uz/web/client/search/full-text?query={quote_plus(query)}&page=1&per_page=15"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json", "Origin": "https://alifshop.uz", "Referer": "https://alifshop.uz/"}, timeout=7)
        if debug is not None:
            debug["AlifShop"] = {"status": r.status_code}
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("offers") or data.get("data") or []
        if debug is not None:
            debug["AlifShop"]["raw_count"] = len(products)
        best = None
        for p in products:
            title = p.get("name") or p.get("title") or ""
            raw_price = p.get("price") or 0
            price = int(raw_price / 100) if raw_price > 1000000000 else int(raw_price)
            if not price or not is_relevant_product(query, title):
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
        if debug is not None:
            debug["AlifShop"] = {"error": str(e)[:120]}
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

def search_prices(query, bypass_cache=False, debug_info=None):
    norm_q = normalize_query(query)
    key = norm_q.lower().strip()
    now = time.time()
    if not bypass_cache and debug_info is None and key in CACHE and (now - CACHE[key]["time"] < CACHE_TTL):
        return CACHE[key]["data"]

    fetchers = [fetch_olcha, fetch_texnomart, fetch_asaxiy, fetch_uzum, fetch_mediapark, fetch_idea, fetch_alifshop]
    results = []
    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = [executor.submit(fn, norm_q, debug_info) for fn in fetchers]
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
        save_price_history(query, results[0]["price"])
        if len(CACHE) >= MAX_CACHE_ITEMS:
            CACHE.clear()
        CACHE[key] = {"time": now, "data": results}
    return results

def get_suggestions(q):
    q_low = (q or "").strip().lower()
    if not q_low:
        return []
    norm = normalize_query(q_low)
    matches = []
    for item in POPULAR_SUGGESTIONS:
        il = item.lower()
        if q_low in il or norm in il:
            matches.append(item)
    if len(matches) < 6:
        try:
            r = requests.get(f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(norm)}", headers=HEADERS, timeout=2.5)
            if r.status_code == 200:
                for p in r.json().get("data", {}).get("products", [])[:6]:
                    name = clean_title(p.get("name", ""))
                    if name and name not in matches:
                        matches.append(name)
        except Exception:
            pass
    return matches[:8]

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
                    lang = get_user_lang(user_id) or "uz"
                    text = bt(lang, "price_drop", q=html.escape(query), p=format_price(best["price"], lang), s=html.escape(best["store"]))
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton(bt(lang, "go_shop", s=best["store"]), url=best["url"]))
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

# --- WEB SERVER ---
class AppHandler(BaseHTTPRequestHandler):
    def _client_ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _auth_user_id(self):
        return verify_telegram_init_data(self.headers.get("X-Telegram-Init-Data", ""))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json({"status": "ok"})
            return

        if path == "/api/debug":
            q = (parse_qs(parsed.query).get("q", ["iphone 16"])[0] or "iphone 16").strip()[:60]
            dbg = {}
            res = search_prices(q, bypass_cache=True, debug_info=dbg)
            self._send_json({"query": q, "normalized": normalize_query(q), "found_stores": [r["store"] for r in res], "debug": dbg})
            return

        if path == "/api/suggest":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:60]
            self._send_json({"suggestions": get_suggestions(q)})
            return

        if path == "/api/search":
            if is_rate_limited(self._client_ip(), limit=30, window=60):
                self._send_json({"error": "Too many requests", "results": []}, 429)
                return
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            if len(q) < 2:
                self._send_json({"query": q, "results": []})
                return
            self._send_json({"query": q, "results": search_prices(q)})
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
                self._send_json({"error": "Auth required", "alerts": []}, 401)
                return
            with db_lock:
                rows = conn.execute("SELECT id, query, target_price, current_price, image FROM price_alerts WHERE user_id = ? ORDER BY id DESC", (uid,)).fetchall()
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
            self._send_json({"ok": False, "error": "Bad request"}, 400)
            return
        if length <= 0 or length > MAX_POST_BYTES:
            self._send_json({"ok": False, "error": "Payload too large"}, 413)
            return

        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
        except Exception:
            self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
            return

        uid = self._auth_user_id()
        if not uid:
            self._send_json({"ok": False, "error": "Auth required"}, 401)
            return

        if parsed.path == "/api/lang":
            new_lang = str(data.get("lang") or "").strip().lower()
            if new_lang not in ("uz", "ru"):
                self._send_json({"ok": False, "error": "Invalid lang"}, 400)
                return
            set_user_lang(uid, new_lang)
            self._send_json({"ok": True, "lang": new_lang})
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
                    self._send_json({"ok": False, "error": "Invalid data"}, 400)
                    return
                with db_lock:
                    conn.execute(
                        "INSERT OR REPLACE INTO price_alerts (user_id, query, target_price, current_price, image, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, query, target_price, max(0, current_price), image, int(time.time()))
                    )
                    conn.commit()
                lang = get_user_lang(uid) or "uz"
                try:
                    bot.send_message(uid, bt(lang, "alert_set", q=html.escape(query), p=format_price(target_price, lang)))
                except Exception:
                    pass
                self._send_json({"ok": True})
            except Exception:
                self._send_json({"ok": False, "error": "Server error"}, 500)
            return

        if parsed.path == "/api/alert/delete":
            try:
                alert_id = int(data.get("id") or 0)
                if alert_id <= 0:
                    self._send_json({"ok": False, "error": "Invalid ID"}, 400)
                    return
                with db_lock:
                    conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (alert_id, uid))
                    conn.commit()
                self._send_json({"ok": True})
            except Exception:
                self._send_json({"ok": False, "error": "Server error"}, 500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    ThreadingHTTPServer(("0.0.0.0", port), AppHandler).serve_forever()

# --- BOT MENYULARI ---
def main_menu(lang="uz"):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(bt(lang, "btn_open"))
    markup.row(bt(lang, "btn_rates"), bt(lang, "btn_alerts"))
    markup.row(bt(lang, "btn_help"), bt(lang, "btn_lang"))
    return markup

def open_app_inline(query=None, lang="uz"):
    url = f"{WEBAPP_URL}/?q={quote_plus(query)}&lang={lang}" if query else f"{WEBAPP_URL}/?lang={lang}"
    label = bt(lang, "inline_search", q=query[:25]) if query else bt(lang, "inline_open")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(label, web_app=types.WebAppInfo(url=url)))
    return markup

def send_lang_picker(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="setlang:uz"),
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang:ru")
    )
    bot.send_message(chat_id, "🌐 <b>Tilni tanlang / Выберите язык:</b>", reply_markup=markup)

@bot.message_handler(commands=["setgroup"])
def set_group_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "Bu buyruqni murojaatlar guruhi ichida yozing.")
        return
    set_support_chat_id(message.chat.id)
    bot.reply_to(message, f"✅ Murojaatlar guruhi ulandi!\n\n<code>SUPPORT_GROUP_ID</code> = <code>{message.chat.id}</code>")

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

@bot.message_handler(commands=["lang", "language"])
def lang_cmd(message):
    if message.chat.type != "private":
        return
    send_lang_picker(message.chat.id)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Til / Язык" in m.text or "Язык / Til" in m.text))
def lang_btn_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    send_lang_picker(message.chat.id)

@bot.message_handler(commands=["start"])
def start_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id)
    if not lang:
        send_lang_picker(message.chat.id)
        return
    if not require_sub(message, lang):
        return
    bot.send_message(message.chat.id, bt(lang, "welcome"), reply_markup=main_menu(lang))
    bot.send_message(message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))

@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.chat.type != "private":
        return
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    bot.send_message(message.chat.id, bt(lang, "welcome"), reply_markup=main_menu(lang))

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Ilovani ochish" in m.text or "Открыть приложение" in m.text))
def open_app_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    if not require_sub(message, lang):
        return
    bot.send_message(message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Kurslar" in m.text or "Курсы" in m.text))
def rates_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    if not require_sub(message, lang):
        return
    text, markup = get_rates_text(lang)
    bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Kuzatuvlarim" in m.text or "Отслеживания" in m.text))
def show_alerts(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    if not require_sub(message, lang):
        return
    with db_lock:
        rows = conn.execute("SELECT id, query, target_price FROM price_alerts WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, bt(lang, "no_alerts"), reply_markup=open_app_inline(lang=lang))
        return
    markup = types.InlineKeyboardMarkup()
    for row_id, q, target in rows:
        app_link = f"{WEBAPP_URL}/?q={quote_plus(q)}&lang={lang}"
        markup.row(
            types.InlineKeyboardButton(f"🔔 {q} (≤ {format_price(target, lang)})", web_app=types.WebAppInfo(url=app_link)),
            types.InlineKeyboardButton("❌", callback_data=f"delalert:{row_id}")
        )
    bot.send_message(message.chat.id, bt(lang, "my_alerts"), reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Yordam" in m.text or "Помощь" in m.text))
def help_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(bt(lang, "contact_btn"), callback_data="contact_admin"))
    bot.send_message(message.chat.id, bt(lang, "help_text"), reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data.startswith("setlang:"):
        lang = call.data.split(":", 1)[1]
        set_user_lang(call.from_user.id, lang)
        bot.answer_callback_query(call.id, "✅")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        not_joined = check_subscription(call.from_user.id)
        if not_joined:
            send_sub_message(call.message.chat.id, not_joined, lang)
        else:
            bot.send_message(call.message.chat.id, bt(lang, "welcome"), reply_markup=main_menu(lang))
            bot.send_message(call.message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))
        return

    lang = get_user_lang(call.from_user.id) or "uz"
    if call.data == "check_sub":
        not_joined = check_subscription(call.from_user.id)
        if not_joined:
            bot.answer_callback_query(call.id, bt(lang, "sub_fail"), show_alert=True)
        else:
            bot.answer_callback_query(call.id, "✅")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, bt(lang, "sub_ok"), reply_markup=main_menu(lang))
            bot.send_message(call.message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))
    elif call.data == "contact_admin":
        bot.answer_callback_query(call.id)
        set_waiting_admin(call.from_user.id, True)
        bot.send_message(call.message.chat.id, bt(lang, "write_msg"))
    elif call.data.startswith("delalert:"):
        with db_lock:
            conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (int(call.data.split("delalert:", 1)[1]), call.from_user.id))
            conn.commit()
        bot.answer_callback_query(call.id, "🗑")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.chat.type == "private")
def handle_text(message):
    user = message.from_user
    lang = get_user_lang(user.id) or "uz"
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
            bot.send_message(message.chat.id, bt(lang, "sent_ok"), reply_markup=main_menu(lang))
        except Exception:
            bot.send_message(message.chat.id, bt(lang, "sent_err"), reply_markup=main_menu(lang))
        return

    if not require_sub(message, lang):
        return
    if len(text) < 2:
        bot.send_message(message.chat.id, bt(lang, "short_q"), reply_markup=open_app_inline(lang=lang))
        return

    bot.send_message(
        message.chat.id,
        bt(lang, "search_hint", q=html.escape(text)),
        reply_markup=open_app_inline(text, lang=lang)
    )

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=price_alert_worker, daemon=True).start()
    print("Arzoni Bor ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
