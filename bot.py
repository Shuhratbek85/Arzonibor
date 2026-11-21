import os
import re
import time
import html
import sqlite3
import threading
import requests
from urllib.parse import quote_plus, urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
from ddgs import DDGS

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN topilmadi! Render Environment Variables ni tekshiring.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- SOZLAMALAR ---
ADMIN_USERNAME = "@Mr_texnoai"
CHANNELS = [
    {"name": "🤖 Mr Texno AI", "username": "@mrtexnoai", "url": "https://t.me/mrtexnoai"},
    {"name": "🛍 Arzoni Bor", "username": "@arzoniboragent", "url": "https://t.me/arzoniboragent"},
]

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
    conn.commit()

# --- 6 SOATLIK KESH (XOTIRA LIMITI BILAN) ---
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
    """Telegram 64 bayt limitidan oshmasligi uchun xavfsiz qisqartirish"""
    max_bytes = 60 - len(prefix.encode("utf-8"))
    encoded = text.encode("utf-8")[:max_bytes]
    return prefix + encoded.decode("utf-8", errors="ignore")

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

# --- NARXNI MATNDAN SUG'URIB OLISH (RASSROCHKA FILTRI BILAN) ---
def extract_price(text):
    if not text:
        return None

    # Muddatli to'lov (oyiga / в месяц) yozuvlarini olib tashlash
    cleaned = re.sub(
        r'(\d{1,3}(?:[\s\u00a0.,]\d{3})+)\s*(?:so[\'‘’`]?m|сум|uzs|сўм)?\s*(?:/|dan)?\s*(?:oyiga|oy|в\s*месяц|мес)',
        '',
        text,
        flags=re.IGNORECASE
    )

    patterns = [
        r'(\d{1,3}(?:[\s\u00a0.,]\d{3})+)\s*(?:so[\'‘’`]?m|сум|uzs|сўм)',
        r'(?:uzs|сум|so[\'‘’`]?m)\s*(\d{1,3}(?:[\s\u00a0.,]\d{3})+)',
        r'(\d{6,9})\s*(?:so[\'‘’`]?m|сум|uzs|сўм)'
    ]
    found = []
    for pattern in patterns:
        matches = re.findall(pattern, cleaned, re.IGNORECASE)
        for m in matches:
            clean_num = int(re.sub(r'\D', '', m))
            if 50000 <= clean_num <= 200000000:
                found.append(clean_num)

    # Agar bir nechta narx chiqsa, eng kichik oylik to'lov emas, asosiy narxni olish uchun
    return min(found) if found else None

def format_price(price):
    return f"{price:,}".replace(",", " ") + " so'm"

def get_store_name(url):
    domain = urlparse(url).netloc.lower().replace("www.", "")
    for name, info in STORES.items():
        if info["domain"] in domain:
            return name
    if domain.endswith(".uz"):
        return domain.split(".")[0].capitalize() + " (.uz)"
    return None

# --- DDGS ORQALI QIDIRUV ---
def search_prices_ddg(query):
    key = query.lower().strip()
    now = time.time()
    if key in CACHE and (now - CACHE[key]["time"] < CACHE_TTL):
        return CACHE[key]["data"]

    raw_results = []
    queries = [
        f"{query} narxi so'm uz",
        f"{query} цена сум ташкент"
    ]

    for q in queries:
        try:
            with DDGS() as ddgs:
                res = list(ddgs.text(q, region="wt-wt", max_results=12))
                print(f"[DDGS] '{q}' -> {len(res)} ta natija")
                raw_results.extend(res)
        except Exception as e:
            print(f"[DDGS] xato ({q}): {e}")
        time.sleep(0.5)

    results = []
    seen_stores = set()

    for item in raw_results:
        url = item.get("href", "")
        title = item.get("title", "")
        body = item.get("body", "")
        full_text = f"{title} {body}"

        store_name = get_store_name(url)
        if not store_name or store_name in seen_stores:
            continue

        price = extract_price(full_text)
        if price:
            seen_stores.add(store_name)
            results.append({
                "store": store_name,
                "price": price,
                "url": url
            })

    results.sort(key=lambda x: x["price"])
    results = results[:8]

    # Faqat natija topilgandagina keshlaymiz!
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
        "⚠️ <i>Narxlar avtomatik qidiruv orqali olinadi va do'konda o'zgargan bo'lishi mumkin. "
        "Xarid qilishdan oldin saytda tekshiring.</i>"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🔍 Mahsulot qidirish")
def ask_product(message):
    if not require_sub(message):
        return
    bot.send_message(message.chat.id, "Qaysi mahsulot narxini bilmoqchisiz? Nomini yozing 👇")

@bot.message_handler(func=lambda m: m.text == "🔥 Mashhur tovarlar")
def popular_products(message):
    if not require_sub(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    items = ["iPhone 16 Pro", "Redmi Note 13", "Samsung A55", "AirPods Pro 2", "PlayStation 5", "MacBook Air M2"]
    markup.add(*[types.InlineKeyboardButton(i, callback_data=safe_callback("search:", i)) for i in items])
    bot.send_message(message.chat.id, "🔥 Eng ko'p qidirilayotgan mahsulotlar:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📁 Kategoriyalar")
def categories(message):
    if not require_sub(message):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    cats = ["Smartfon", "Noutbuk", "Televizor", "Konditsioner", "Kir yuvish mashinasi", "Muzlatkich"]
    markup.add(*[types.InlineKeyboardButton(c, callback_data=safe_callback("search:", c)) for c in cats])
    markup.add(types.InlineKeyboardButton("💵 Dollar kursi (Banklar)", callback_data="usd_rates"))
    bot.send_message(message.chat.id, "📁 Kategoriyani tanlang:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💵 Dollar kursi")
def dollar_cmd(message):
    if not require_sub(message):
        return
    text, markup = get_usd_rates()
    bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)

@bot.message_handler(func=lambda m: m.text == "❤️ Sevimlilar")
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

@bot.message_handler(func=lambda m: m.text == "ℹ️ Yordam / Aloqa")
def help_cmd(message):
    text = (
        "ℹ️ <b>Yordam va Aloqa</b>\n\n"
        "• <b>Qanday ishlaydi?</b> Istalgan mahsulot nomini yozing — bot O'zbekistonning yirik do'konlaridagi narxlarni qidirib beradi.\n"
        "• <b>Dollar kursi:</b> «💵 Dollar kursi» tugmasi orqali kunlik kurslarni kuzatib boring.\n"
        "• <b>Savol yoki taklif bormi?</b> Quyidagi tugma orqali adminga yozing 👇"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✍️ Adminga yozish", url=f"https://t.me/{ADMIN_USERNAME}"))
    bot.send_message(message.chat.id, text, reply_markup=markup)

def process_search(chat_id, query):
    safe_q = html.escape(query)
    wait_msg = bot.send_message(chat_id, f"🔍 <b>{safe_q}</b> narxlari qidirilmoqda...")
    results = search_prices_ddg(query)

    if results:
        lines = [f"🔎 <b>{safe_q}</b> bo'yicha topilgan narxlar:\n"]
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
    print("Arzoni Bor ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
