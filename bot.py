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
from urllib.parse import quote_plus, urlparse, parse_qs, parse_qsl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

from suggest import HEADERS, normalize_query, get_suggestions
from stores import search_prices

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN topilmadi! Render Environment Variables ni tekshiring.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- SOZLAMALAR ---
ADMIN_ID = 565834132
ENV_SUPPORT_GROUP_ID = os.environ.get("SUPPORT_GROUP_ID")
WEBAPP_URL = "https://arzonibor-ywla.onrender.com"
MAX_POST_BYTES = 16384
DEBUG_ENDPOINT_ENABLED = os.environ.get("ENABLE_DEBUG_ENDPOINT", "").lower() in ("1", "true", "yes")
CHANNELS = [
    {"username": "@mrtexnoai", "url": "https://t.me/mrtexnoai"},
    {"username": "@arzoniboragent", "url": "https://t.me/arzoniboragent"},
]

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

def format_price(price, lang="uz"):
    return f"{int(price):,}".replace(",", " ") + " " + bt(lang, "sum")

# --- RATE LIMITER & INITDATA ---
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

def verify_telegram_init_data(init_data):
    if not init_data or len(init_data) > 4096:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash or abs(time.time() - int(parsed.get("auth_date", 0))) > 86400:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return None
        uid = int(json.loads(parsed.get("user", "{}")).get("id", 0))
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
        user_id INTEGER, query TEXT, target_price INTEGER, current_price INTEGER,
        image TEXT, created_at INTEGER, UNIQUE(user_id, query)
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, price INTEGER, ts INTEGER
    )""")
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
    with db_lock:
        conn.execute("INSERT OR REPLACE INTO users (user_id, lang) VALUES (?, ?)", (user_id, lang if lang in ("uz", "ru") else "uz"))
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
    with db_lock:
        rows = conn.execute("SELECT price, ts FROM price_history WHERE query = ? ORDER BY ts ASC LIMIT 30", (query.lower().strip(),)).fetchall()
    return [{"price": r[0], "ts": r[1]} for r in rows]

waiting_admin_msg = set()
waiting_lock = threading.Lock()
def set_waiting_admin(uid, state=True):
    with waiting_lock:
        waiting_admin_msg.add(uid) if state else waiting_admin_msg.discard(uid)
def is_waiting_admin(uid):
    with waiting_lock:
        return uid in waiting_admin_msg

# --- OBUNA VA KURSLAR ---
def check_subscription(user_id):
    not_joined = []
    for ch in CHANNELS:
        try:
            if bot.get_chat_member(ch["username"], user_id).status not in ("member", "administrator", "creator"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined

def send_sub_message(chat_id, not_joined, lang="uz"):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for idx, ch in enumerate(not_joined, 1):
        lbl = bt(lang, "sub_btn", i=idx) if len(not_joined) > 1 else bt(lang, "sub_btn_one")
        markup.add(types.InlineKeyboardButton(lbl, url=ch["url"]))
    markup.add(types.InlineKeyboardButton(bt(lang, "sub_check"), callback_data="check_sub"))
    bot.send_message(chat_id, bt(lang, "sub_text"), reply_markup=markup)

def require_sub(message, lang="uz"):
    not_joined = check_subscription(message.from_user.id)
    if not_joined:
        send_sub_message(message.chat.id, not_joined, lang)
        return False
    return True

RATES_CACHE = {"time": 0, "data": {}}
def fetch_all_rates():
    now = time.time()
    if RATES_CACHE["data"] and (now - RATES_CACHE["time"] < 300):
        return RATES_CACHE["data"]
    result = {}
    try:
        r = requests.get("https://cbu.uz/uz/arkhiv-kursov-valyut/json/", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            for item in r.json():
                if item.get("Ccy") in ("USD", "EUR", "RUB"):
                    result[item["Ccy"].lower()] = {"rate": float(item["Rate"]), "diff": item["Diff"], "date": item["Date"]}
    except Exception:
        pass
    for url, parser in [
        ("https://api.coinbase.com/v2/prices/BTC-USD/spot", lambda j: float(j["data"]["amount"])),
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", lambda j: float(j["bitcoin"]["usd"])),
        ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda j: float(j["price"])),
    ]:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                result["btc"] = {"usd": parser(r.json())}
                break
        except Exception:
            continue
    if result:
        RATES_CACHE["time"], RATES_CACHE["data"] = now, result
    return result

def get_rates_text(lang="uz"):
    d, s = fetch_all_rates(), bt(lang, "sum")
    lines = [bt(lang, "rates_title")]
    for flag, code in [("🇺🇸", "usd"), ("🇪🇺", "eur"), ("🇷🇺", "rub")]:
        if code in d:
            lines.append(f"{flag} <b>1 {code.upper()}:</b> {d[code]['rate']:,.2f} {s} ({d[code]['diff']})".replace(",", " "))
    if "btc" in d:
        lines.append(f"₿ <b>Bitcoin (BTC):</b> ${d['btc']['usd']:,.0f}".replace(",", " "))
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(bt(lang, "bank_btn"), url="https://bank.uz/uz/currency"))
    return "\n".join(lines), markup

BANK_RATES_CACHE = {"time": 0, "data": {}}
def fetch_bank_rates():
    now = time.time()
    if BANK_RATES_CACHE["data"] and (now - BANK_RATES_CACHE["time"] < 900):
        return BANK_RATES_CACHE["data"]
    data = {c: {"buy": [], "sell": []} for c in ("usd", "eur", "rub")}
    cbu = fetch_all_rates()
    cbu_map = {c: cbu[c]["rate"] for c in ("usd", "eur", "rub") if c in cbu}
    if not cbu_map:
        cbu_map = {"usd": 12000.0, "eur": 13100.0, "rub": 145.0}

    try:
        r = requests.get("https://bank.uz/uz/currency", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
            cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
            cleaned = html.unescape(cleaned)
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines()]
            lines = [ln for ln in lines if ln]

            mode = None
            seen = {c: {"buy": set(), "sell": set()} for c in ("usd", "eur", "rub")}
            price_re = re.compile(r"^(\d[\d\s]{1,9})\s*so['’`]?m$", re.I)

            for i, ln in enumerate(lines):
                low = ln.lower()
                if low in ("sotib olish", "покупка"):
                    mode = "buy"
                    continue
                if low in ("sotish", "продажа"):
                    mode = "sell"
                    continue
                if not mode:
                    continue

                m = price_re.match(ln)
                if m and i > 0:
                    bank = lines[i - 1]
                    if len(bank) < 2 or len(bank) > 50 or price_re.match(bank) or bank.lower() in ("sotib olish", "sotish", "kreditlar", "omonatlar"):
                        continue
                    rate = int(re.sub(r"\D", "", m.group(1)))
                    # Qaysi valyutaga tegishliligini MB kursiga qarab aniqlaymiz
                    best_ccy, best_diff = None, 999.0
                    for ccy, ref in cbu_map.items():
                        rel = abs(rate - ref) / ref
                        limit = 0.35 if ccy == "rub" else 0.12
                        if rel < limit and rel < best_diff:
                            best_ccy, best_diff = ccy, rel
                    if best_ccy and bank.lower() not in seen[best_ccy][mode]:
                        seen[best_ccy][mode].add(bank.lower())
                        data[best_ccy][mode].append({"bank": bank, "rate": rate})

            for ccy in ("usd", "eur", "rub"):
                data[ccy]["sell"].sort(key=lambda x: x["rate"])
                data[ccy]["buy"].sort(key=lambda x: x["rate"], reverse=True)

            if any(data[c]["buy"] or data[c]["sell"] for c in data):
                BANK_RATES_CACHE["time"], BANK_RATES_CACHE["data"] = now, data
            else:
                print(f"[BANK RATES] Matndan topilmadi, qatorlar: {len(lines)}")
    except Exception as e:
        print(f"[BANK RATES] xato: {e}")
    return data
# --- NARX KUZATISH WORKER ---
def price_alert_worker():
    while True:
        time.sleep(3 * 3600)
        try:
            with db_lock:
                alerts = conn.execute("SELECT id, user_id, query, target_price FROM price_alerts").fetchall()
            for alert_id, user_id, query, target_price in alerts:
                results = search_prices(query, bypass_cache=True)
                if results:
                    best = results[0]
                    with db_lock:
                        conn.execute("UPDATE price_alerts SET current_price = ? WHERE id = ?", (int(best["price"]), alert_id))
                        conn.commit()
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
            return self._send_json({"status": "ok"})
        if path == "/api/debug":
            if not DEBUG_ENDPOINT_ENABLED:
                return self._send_json({"error": "Not found"}, 404)
            if self._auth_user_id() != ADMIN_ID:
                return self._send_json({"error": "Auth required"}, 403)
            q = (parse_qs(parsed.query).get("q", ["iphone 16"])[0] or "iphone 16").strip()[:60]
            dbg = {}
            res = search_prices(q, bypass_cache=True, debug_info=dbg)
            return self._send_json({"query": q, "normalized": normalize_query(q), "found_stores": [r["store"] for r in res], "debug": dbg})
        if path == "/api/suggest":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:60]
            return self._send_json({"suggestions": get_suggestions(q)})
        if path == "/api/search":
            if is_rate_limited(self._client_ip()):
                return self._send_json({"error": "Too many requests", "results": []}, 429)
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            if len(q) < 2:
                return self._send_json({"query": q, "results": []})
            return self._send_json({"query": q, "results": search_prices(q, on_best_price=save_price_history)})
        if path == "/api/rates":
            return self._send_json(fetch_all_rates())
        if path == "/api/bank-rates":
            return self._send_json(fetch_bank_rates())
        if path == "/api/history":
            q = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()[:80]
            return self._send_json({"query": q, "history": get_price_history(q)})
        if path == "/api/alerts":
            uid = self._auth_user_id()
            if not uid:
                return self._send_json({"error": "Auth required", "alerts": []}, 401)
            with db_lock:
                rows = conn.execute("SELECT id, query, target_price, current_price, image FROM price_alerts WHERE user_id = ? ORDER BY id DESC", (uid,)).fetchall()
            return self._send_json({"alerts": [{"id": r[0], "query": r[1], "target_price": r[2], "current_price": r[3], "image": r[4] or ""} for r in rows]})
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
                self.end_headers()
                self.wfile.write(b"Arzoni Bor ishlayapti!")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not (0 < length <= MAX_POST_BYTES):
                return self._send_json({"ok": False, "error": "Payload too large"}, 413)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            return self._send_json({"ok": False, "error": "Invalid JSON"}, 400)

        uid = self._auth_user_id()
        if not uid:
            return self._send_json({"ok": False, "error": "Auth required"}, 401)

        if parsed.path == "/api/lang":
            new_lang = str(data.get("lang") or "").strip().lower()
            if new_lang not in ("uz", "ru"):
                return self._send_json({"ok": False, "error": "Invalid lang"}, 400)
            set_user_lang(uid, new_lang)
            return self._send_json({"ok": True, "lang": new_lang})

        if parsed.path == "/api/alert":
            try:
                query = str(data.get("query") or "").strip()[:80]
                target_price = int(data.get("target_price") or 0)
                current_price = int(data.get("current_price") or 0)
                image = str(data.get("image") or "").strip()[:300]
                if image and not image.startswith(("http://", "https://")):
                    image = ""
                if len(query) < 2 or not (1000 <= target_price <= 500000000):
                    return self._send_json({"ok": False, "error": "Invalid data"}, 400)
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
                return self._send_json({"ok": True})
            except Exception:
                return self._send_json({"ok": False, "error": "Server error"}, 500)

        if parsed.path == "/api/alert/delete":
            try:
                alert_id = int(data.get("id") or 0)
                with db_lock:
                    conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (alert_id, uid))
                    conn.commit()
                return self._send_json({"ok": True})
            except Exception:
                return self._send_json({"ok": False, "error": "Server error"}, 500)

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), AppHandler).serve_forever()

# --- BOT MENYULARI VA HANDLERLAR ---
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
    markup.add(types.InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="setlang:uz"), types.InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang:ru"))
    bot.send_message(chat_id, "🌐 <b>Tilni tanlang / Выберите язык:</b>", reply_markup=markup)

@bot.message_handler(commands=["setgroup"])
def set_group_cmd(message):
    if message.from_user.id == ADMIN_ID and message.chat.type in ("group", "supergroup"):
        set_support_chat_id(message.chat.id)
        bot.reply_to(message, f"✅ Murojaatlar guruhi ulandi!\n<code>SUPPORT_GROUP_ID</code> = <code>{message.chat.id}</code>")

@bot.message_handler(func=lambda m: m.from_user and m.from_user.id == ADMIN_ID and m.reply_to_message)
def admin_reply_handler(message):
    with db_lock:
        row = conn.execute("SELECT user_id FROM support_messages WHERE admin_msg_id = ?", (message.reply_to_message.message_id,)).fetchone()
    if row and (message.text or "").strip():
        try:
            bot.send_message(row[0], f"📩 <b>Javob:</b>\n{html.escape(message.text.strip())}")
            bot.reply_to(message, "✅ Javob yuborildi.")
        except Exception as e:
            bot.reply_to(message, f"❌ Xato: {html.escape(str(e))}")

@bot.message_handler(commands=["lang", "language"])
def lang_cmd(message):
    if message.chat.type == "private":
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
        return send_lang_picker(message.chat.id)
    if require_sub(message, lang):
        bot.send_message(message.chat.id, bt(lang, "welcome"), reply_markup=main_menu(lang))
        bot.send_message(message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))

@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.chat.type == "private":
        set_waiting_admin(message.from_user.id, False)
        lang = get_user_lang(message.from_user.id) or "uz"
        bot.send_message(message.chat.id, bt(lang, "welcome"), reply_markup=main_menu(lang))

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Ilovani ochish" in m.text or "Открыть приложение" in m.text))
def open_app_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    if require_sub(message, lang):
        bot.send_message(message.chat.id, bt(lang, "open_hint"), reply_markup=open_app_inline(lang=lang))

@bot.message_handler(func=lambda m: m.chat.type == "private" and m.text and ("Kurslar" in m.text or "Курсы" in m.text))
def rates_cmd(message):
    set_waiting_admin(message.from_user.id, False)
    lang = get_user_lang(message.from_user.id) or "uz"
    if require_sub(message, lang):
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
        return bot.send_message(message.chat.id, bt(lang, "no_alerts"), reply_markup=open_app_inline(lang=lang))
    markup = types.InlineKeyboardMarkup()
    for row_id, q, target in rows:
        markup.row(
            types.InlineKeyboardButton(f"🔔 {q} (≤ {format_price(target, lang)})", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?q={quote_plus(q)}&lang={lang}")),
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
        if check_subscription(call.from_user.id):
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
    user, lang, text = message.from_user, get_user_lang(message.from_user.id) or "uz", (message.text or "").strip()
    if is_waiting_admin(user.id):
        set_waiting_admin(user.id, False)
        if not text:
            return
        try:
            sent = bot.send_message(get_support_chat_id(), f"👤 <b>{html.escape(user.full_name or 'Noma’lum')}</b> | <code>{user.id}</code>\n💬 {html.escape(text)}")
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
        return bot.send_message(message.chat.id, bt(lang, "short_q"), reply_markup=open_app_inline(lang=lang))
    bot.send_message(message.chat.id, bt(lang, "search_hint", q=html.escape(text)), reply_markup=open_app_inline(text, lang=lang))

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=price_alert_worker, daemon=True).start()
    print("Arzoni Bor ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
