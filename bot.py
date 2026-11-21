import os
import re
import time
import html
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
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
ADMIN_USERNAME = "mr_texno_ai"
CHANNELS = [
    {"name": "🤖 Mr Texno AI", "username": "@mrtexnoai", "url": "https://t.me/mrtexnoai"},
    {"name": "🛍 Arzoni Bor", "username": "@arzoniboragent", "url": "https://t.me/arzoniboragent"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "uz-UZ,uz;q=0.9,ru;q=0.8,en;q=0.7",
}

ACCESSORY_WORDS = (
    "chexol", "чехол", "case", "g'ilof", "gilof", "oyna", "стекло", "glass",
    "plenka", "пленка", "himoya", "защит", "kabel", "кабель", "shnur", "шнур",
    "zaryad", "заряд", "adapter", "адаптер", "remeshok", "ремешок", "kovrik"
)

# --- RENDER UCHUN WEB SERVER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Arzoni Bor ishlayapti!".encode("utf-8"))

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# --- MA'LUMOTLAR BAZASI (THREAD-SAFE) ---
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
    CREATE TABLE IF NOT EXISTS notify_requests (
        user_id INTEGER,
        category TEXT,
        UNIQUE(user_id, category)
    )
    """)
    conn.commit()

# --- 6 SOATLIK KESH ---
CACHE = {}
CACHE_TTL = 6 * 3600
MAX_CACHE_ITEMS = 300

STORES = {
    "Uzum Market": {"domain": "uzum.uz", "search_url": "https://uzum.uz/uz/search?query={q}"},
    "Olcha": {"domain": "olcha.uz", "search_url": "https://olcha.uz/uz/search?q={q}"},
    "Texnomart": {"domain": "texnomart.uz", "search_url": "https://texnomart.uz/uz/search/?q={q}"},
    "Asaxiy": {"domain": "asaxiy.uz", "search_url": "https://asaxiy.uz/product?key={q}"},
    "Mediapark": {"domain": "mediapark.uz", "search_url": "https://mediapark.uz/search?query={q}"},
    "Idea": {"domain": "idea.uz", "search_url": "https://idea.uz/search?q={q}"}
}

# --- YORDAMCHI FUNKSIYALAR ---
def safe_callback(prefix, text):
    max_bytes = 60 - len(prefix.encode("utf-8"))
    encoded = text.encode("utf-8")[:max_bytes]
    return prefix + encoded.decode("utf-8", errors="ignore")

def format_price(price):
    return f"{int(price):,}".replace(",", " ") + " so'm"

def is_relevant_product(query, title):
    q_low = query.lower()
    t_low = (title or "").lower()
    if not t_low:
        return False

    if not any(w in q_low for w in ACCESSORY_WORDS):
        if any(w in t_low for w in ACCESSORY_WORDS):
            return False

    words = [w for w in re.findall(r"[a-zA-Zа-яА-Я0-9]+", q_low) if len(w) > 1]
    if not words:
        return True
    matched = sum(1 for w in words if w in t_low)
    return matched >= max(1, (len(words) + 1) // 2)

# --- MAJBURIY OBUNA ---
def check_subscription(user_id):
    not_joined = []
    for ch in CHANNELS:
        try:
            member = bot.get_chat_member(ch["username"], user_id)
            if member.status not in ["member", "administrator", "creator"]:
                not_joined.append(ch)
        except Exception as e:
            print(f"[SUB] {ch['username']} tekshirishda xato: {e}")
    return not_joined

def send_sub_message(chat_id, not_joined):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in not_joined:
        markup.add(types.InlineKeyboardButton(f"➕ {ch['name']}", url=ch["url"]))
    markup.add(types.InlineKeyboardButton("✅ Obuna bo'ldim (Tekshirish)", callback_data="check_sub"))

    text = (
        "👋 <b>Botdan to'liq va bepul foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n"
        "Obuna bo'lgach, pastdagi <b>«✅ Obuna bo'ldim»</b> tugmasini bosing 👇"
    )
    bot.send_message(chat_id, text, reply_markup=markup)

def require_sub(message):
    not_joined = check_subscription(message.from_user.id)
    if not_joined:
        send_sub_message(message.chat.id, not_joined)
        return False
    return True

# --- DOLLAR KURSI ---
def get_usd_rates():
    lines = ["💵 <b>Bugungi Dollar ($) kursi (O'zbekiston)</b>\n"]
    try:
        r = requests.get("https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/", timeout=8)
        if r.status_code == 200:
            data = r.json()[0]
            rate = float(data["Rate"])
            diff = data["Diff"]
            date = data["Date"]
            sign = "📈" if not str(diff).startswith("-") else "📉"
            lines.append(f"🏛 <b>Markaziy bank ({date}):</b>")
            lines.append(f"• 1 USD = <b>{rate:,.2f} so'm</b> ({sign} {diff})\n".replace(",", " "))
    except Exception as e:
        print(f"[CBU] xato: {e}")

    lines.append("🏦 <b>Tijorat banklaridagi eng foydali kurslar:</b>")
    lines.append("Barcha banklarning jonli sotish va sotib olish kurslarini quyidagi tugmadan ko'ring 👇")

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🏦 Eng foydali banklar (Bank.uz)", url="https://bank.uz/uz/currency"),
        types.InlineKeyboardButton("📊 Dollar kurslari (Dollaruz)", url="https://bank.uz/uz/currency/dollar-ssha")
    )
    return "\n".join(lines), markup

# --- DO'KONLARDAN TO'G'RIDAN-TO'G'RI QIDIRISH ---
def fetch_olcha(query):
    try:
        url = f"https://api.olcha.uz/api/v2/products?search={quote_plus(query)}&per_page=10"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        if r.status_code != 200:
            print(f"[Olcha] status={r.status_code}")
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
                    "title": title,
                    "url": f"https://olcha.uz/uz/product/view/{alias}" if alias else STORES["Olcha"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception as e:
        print(f"[Olcha] xato: {e}")
        return None

def fetch_texnomart(query):
    try:
        url = f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(query)}"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=7)
        if r.status_code != 200:
            print(f"[Texnomart] status={r.status_code}")
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
                    "title": title,
                    "url": f"https://texnomart.uz/uz/product/detail/{pid}" if pid else STORES["Texnomart"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception as e:
        print(f"[Texnomart] xato: {e}")
        return None

def fetch_asaxiy(query):
    try:
        url = f"https://asaxiy.uz/product?key={quote_plus(query)}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            print(f"[Asaxiy] status={r.status_code}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".product__item")
        print(f"[Asaxiy] topilgan kartalar={len(cards)}")
        best = None
        for card in cards[:12]:
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
                best = {
                    "store": "Asaxiy",
                    "price": price,
                    "title": title,
                    "url": full_url
                }
        return best
    except Exception as e:
        print(f"[Asaxiy] xato: {e}")
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
                    "pagination": {"offset": 0, "limit": 12}
                }
            },
            "query": """
            query getMakeSearch($queryInput: MakeSearchQueryInput!) {
              makeSearch(query: $queryInput) {
                items {
                  catalogCard {
                    productId
                    title
                    minSellPrice
                  }
                }
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
            print(f"[Uzum] status={r.status_code}")
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
                    "title": title,
                    "url": f"https://uzum.uz/uz/product/{pid}" if pid else STORES["Uzum Market"]["search_url"].format(q=quote_plus(query))
                }
        return best
    except Exception as e:
        print(f"[Uzum] xato: {e}")
        return None

# --- ZAXIRA (DDGS) ---
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
                if not domain.endswith(".uz"):
                    continue
                store = next((n for n, i in STORES.items() if i["domain"] in domain), domain.split(".")[0].capitalize() + " (.uz)")
                if store in seen:
                    continue
                title = item.get("title", "")
                body = item.get("body", "")
                if not is_relevant_product(query, title):
                    continue
                price = extract_price_fallback(f"{title} {body}")
                if price:
                    seen.add(store)
                    results.append({"store": store, "price": price, "title": title, "url": url})
    except Exception as e:
        print(f"[DDGS fallback] xato: {e}")
    return results

# --- ASOSIY QIDIRUV ---
def search_prices(query):
    key = query.lower().strip()
    now = time.time()
    if key in CACHE and (now - CACHE[key]["time"] < CACHE_TTL):
        return CACHE[key]["data"]

    fetchers = [fetch_olcha, fetch_texnomart, fetch_asaxiy, fetch_uzum]
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fn, query) for fn in fetchers]
        for f in futures:
            res = f.result()
            if res:
                results.append(res)

    print(f"[SEARCH] '{query}' -> to'g'ridan-to'g'ri {len(results)} ta do'kon topildi")

    if len(results) < 3:
        existing = {r["store"] for r in results}
        for item in search_ddg_fallback(query):
            if item["store"] not in existing:
                results.append(item)
                existing.add(item["store"])

    results.sort(key=lambda x: x["price"])
    results = results[:8]

    if results:
        if len(CACHE) >= MAX_CACHE_ITEMS:
            CACHE.clear()
        CACHE[key] = {"time": now, "data": results}

    return results

# --- TUGMALAR ---
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔍 Mahsulot qidirish", "🔥 Mashhur tovarlar")
    markup.row("📁 Kategoriyalar", "💵 Dollar kursi")
    markup.row("❤️ Sevimlilar", "ℹ️ Yordam / Aloqa")
    return markup

def store_buttons(query):
    q = quote_plus(query)
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [
        types.InlineKeyboardButton(f"🛍 {name}", url=info["search_url"].format(q=q))
        for name, info in STORES.items()
    ]
    markup.add(*btns)
    markup.add(types.InlineKeyboardButton(
        "🌐 Hamma do'konlar (Google)",
        url=f"https://www.google.com/search?q={q}+narxi+uzbekiston"
    ))
    markup.add(types.InlineKeyboardButton(
        "❤️ Sevimlilarga saqlash",
        callback_data=safe_callback("fav:", query)
    ))
    return markup

# --- HANDLERLAR ---
@bot.message_handler(commands=["start"])
def start_cmd(message):
    if not require_sub(message):
        return

    text = (
        "🤖 <b>Arzoni Bor</b>: O'zbekiston onlayn do'konlarida mahsulot narxlarini solishtirish boti.\n\n"
        "🔍 Shunchaki mahsulot nomini yozing (masalan: <i>Redmi Note 13</i> yoki <i>iPhone 16</i>).\n\n"
        "⚠️ <i>Narxlar do'konlardan jonli olinadi. Xarid qilishdan oldin saytda tekshiring.</i>"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text and "Mahsulot qidirish" in m.text)
def ask_product(message):
    if not require_sub(message):
        return
    bot.send_message(message.chat.id, "Qaysi mahsulot narxini bilmoqchisiz? Nomini yozing 👇")

@bot.message_handler(func=lambda m: m.text and "Mashhur tovarlar" in m.text)
def popular_products(message):
    if not require_sub(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    items = ["iPhone 16 Pro", "Redmi Note 13", "Samsung A55", "AirPods Pro 2", "PlayStation 5", "MacBook Air M2"]
    markup.add(*[types.InlineKeyboardButton(i, callback_data=safe_callback("search:", i)) for i in items])
    bot.send_message(message.chat.id, "🔥 Eng ko'p qidirilayotgan mahsulotlar:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and "Kategoriyalar" in m.text)
def categories(message):
    if not require_sub(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)

    active_cats = [
        ("📱 Smartfonlar", "Smartfon"),
        ("🏠 Maishiy texnika", "Maishiy texnika"),
        ("💻 Noutbuklar", "Noutbuk"),
        ("📺 Televizorlar", "Televizor"),
        ("🎧 Aksessuarlar", "Aksessuarlar"),
    ]
    markup.add(*[
        types.InlineKeyboardButton(label, callback_data=safe_callback("search:", query))
        for label, query in active_cats
    ])
    markup.add(types.InlineKeyboardButton("💵 Dollar kursi (Jonli)", callback_data="usd_rates"))

    soon_cats = [
        "🏦 Bank va Valyuta",
        "🏢 Uy-joy",
        "🛒 Oziq-ovqat",
        "💊 Dorixona",
        "✈️ Aviachipta",
        "🚗 Avto va Sug'urta",
        "📡 Aloqa va Internet",
        "🧱 Qurilish mollari",
        "🏥 Klinikalar",
    ]
    markup.add(*[
        types.InlineKeyboardButton(f"{c} (⏳)", callback_data=safe_callback("soon:", c))
        for c in soon_cats
    ])

    text = (
        "📁 <b>Kategoriyani tanlang:</b>\n\n"
        "✅ Yuqoridagi bo‘limlar hozir jonli ishlaydi.\n"
        "⏳ Belgili bo‘limlar esa <b>tez kunda</b> ishga tushadi."
    )
    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and "Dollar kursi" in m.text)
def dollar_cmd(message):
    if not require_sub(message):
        return
    text, markup = get_usd_rates()
    bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.text and "Sevimlilar" in m.text)
def show_favorites(message):
    if not require_sub(message):
        return
    with db_lock:
        rows = conn.execute(
            "SELECT id, query FROM favorites WHERE user_id = ? ORDER BY id DESC",
            (message.from_user.id,)
        ).fetchall()

    if not rows:
        bot.send_message(message.chat.id, "Sevimlilar ro'yxatingiz hozircha bo'sh.")
        return

    markup = types.InlineKeyboardMarkup()
    for row_id, q in rows:
        markup.row(
            types.InlineKeyboardButton(f"🔍 {q}", callback_data=safe_callback("search:", q)),
            types.InlineKeyboardButton("❌", callback_data=f"delfav:{row_id}")
        )
    bot.send_message(message.chat.id, "❤️ Saqlangan mahsulotlaringiz:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and ("Yordam / Aloqa" in m.text or "Bot haqida" in m.text))
def help_cmd(message):
    text = (
        "ℹ️ <b>Yordam va Aloqa</b>\n\n"
        "• <b>Qanday ishlaydi?</b> Istalgan mahsulot nomini yozing — bot O'zbekistonning yirik do'konlaridagi jonli narxlarni solishtirib beradi.\n"
        "• <b>Dollar kursi:</b> «💵 Dollar kursi» tugmasi orqali kunlik kurslarni kuzatib boring.\n"
        "• <b>Savol yoki taklif bormi?</b> Quyidagi tugma orqali adminga yozing 👇"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✍️ Adminga yozish", url=f"https://t.me/{ADMIN_USERNAME}"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

def process_search(chat_id, query):
    safe_q = html.escape(query)
    wait_msg = bot.send_message(chat_id, f"🔍 <b>{safe_q}</b> narxlari do'konlardan qidirilmoqda...")
    results = search_prices(query)

    if results:
        lines = [f"🔎 <b>{safe_q}</b> bo'yicha jonli narxlar:\n"]
        for idx, r in enumerate(results, 1):
            medal = "🏆 " if idx == 1 else f"{idx}. "
            safe_store = html.escape(r["store"])
            safe_url = html.escape(r["url"], quote=True)
            lines.append(f"{medal}<b>{safe_store}</b>: <a href='{safe_url}'>{format_price(r['price'])}</a>")
        lines.append("\n👇 <i>Barcha do'konlarda ko'rish uchun tugmalardan foydalaning:</i>")
        text = "\n".join(lines)
    else:
        text = f"🔎 <b>{safe_q}</b>\n\nAvtomatik narx topilmadi. Quyidagi do'konlarda to'g'ridan-to'g'ri ko'ring 👇"

    try:
        bot.delete_message(chat_id, wait_msg.message_id)
    except Exception:
        pass

    bot.send_message(chat_id, text, reply_markup=store_buttons(query), disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data == "check_sub":
        not_joined = check_subscription(call.from_user.id)
        if not_joined:
            bot.answer_callback_query(call.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, "Xush kelibsiz! Mahsulot nomini yozing 👇", reply_markup=main_menu())
    elif call.data == "usd_rates":
        bot.answer_callback_query(call.id)
        text, markup = get_usd_rates()
        bot.send_message(call.message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)
    elif call.data.startswith("search:"):
        query = call.data.split("search:", 1)[1]
        bot.answer_callback_query(call.id)
        process_search(call.message.chat.id, query)
    elif call.data.startswith("fav:"):
        query = call.data.split("fav:", 1)[1]
        with db_lock:
            conn.execute("INSERT OR IGNORE INTO favorites (user_id, query) VALUES (?, ?)", (call.from_user.id, query))
            conn.commit()
        bot.answer_callback_query(call.id, "✅ Sevimlilarga saqlandi!")
    elif call.data.startswith("delfav:"):
        row_id = int(call.data.split("delfav:", 1)[1])
        with db_lock:
            conn.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (row_id, call.from_user.id))
            conn.commit()
        bot.answer_callback_query(call.id, "🗑 O'chirildi")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
    elif call.data.startswith("soon:"):
        cat = call.data.split("soon:", 1)[1]
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "🔔 Ochilganda xabar berish",
            callback_data=safe_callback("notify:", cat)
        ))
        text = (
            f"⏳ <b>{html.escape(cat)}</b>\n\n"
            "Bu bo‘lim ustida ish olib boryapmiz va tez kunda ishga tushadi.\n"
            "Ochilishi bilan birinchilardan bo‘lib bilish uchun pastdagi tugmani bosing 👇"
        )
        bot.send_message(call.message.chat.id, text, reply_markup=markup)
    elif call.data.startswith("notify:"):
        cat = call.data.split("notify:", 1)[1]
        with db_lock:
            conn.execute(
                "INSERT OR IGNORE INTO notify_requests (user_id, category) VALUES (?, ?)",
                (call.from_user.id, cat)
            )
            conn.commit()
        bot.answer_callback_query(
            call.id,
            f"✅ Saqlandi! «{cat}» ochilganda sizga xabar beramiz.",
            show_alert=True
        )

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    if not require_sub(message):
        return

    query = (message.text or "").strip()
    if len(query) < 2:
        bot.send_message(message.chat.id, "Iltimos, mahsulot nomini to'liqroq yozing.")
        return
    process_search(message.chat.id, query)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    print("Arzoni Bor (To'liq versiya) ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
