import os
import re
import time
import sqlite3
import threading
from urllib.parse import quote_plus
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
from duckduckgo_search import DDGS

TOKEN = os.environ.get("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- RENDER UCHUN WEB SERVER (UXLAB QOLMASLIGI UCHUN) ---
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

# --- MA'LUMOTLAR BAZASI (SEVIMLILAR UCHUN) ---
conn = sqlite3.connect("arzonibor.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER,
    query TEXT,
    UNIQUE(user_id, query)
)
""")
conn.commit()

# --- 6 SOATLIK KESH ---
CACHE = {}
CACHE_TTL = 6 * 3600

STORES = {
    "Uzum Market": {
        "domain": "uzum.uz",
        "search_url": "https://uzum.uz/uz/search?query={q}"
    },
    "Olcha": {
        "domain": "olcha.uz",
        "search_url": "https://olcha.uz/uz/search?q={q}"
    },
    "Texnomart": {
        "domain": "texnomart.uz",
        "search_url": "https://texnomart.uz/uz/search/?q={q}"
    },
    "Asaxiy": {
        "domain": "asaxiy.uz",
        "search_url": "https://asaxiy.uz/product?key={q}"
    },
    "Mediapark": {
        "domain": "mediapark.uz",
        "search_url": "https://mediapark.uz/search?query={q}"
    },
    "Idea": {
        "domain": "idea.uz",
        "search_url": "https://idea.uz/search?q={q}"
    }
}

# --- NARXNI MATNDAN SUG'URIB OLISH ---
def extract_price(text):
    if not text:
        return None
    matches = re.findall(r'(\d{1,3}(?:[\s\u00a0.,]\d{3})+)\s*(?:so[\'‘’`]?m|сум|uzs|сўм)', text, re.IGNORECASE)
    for m in matches:
        clean_num = int(re.sub(r'\D', '', m))
        if 10000 <= clean_num <= 200000000:
            return clean_num
    return None

def format_price(price):
    return f"{price:,}".replace(",", " ") + " so'm"

# --- DUCKDUCKGO ORQALI BEZLIMIT QIDIRUV ---
def search_prices_ddg(query):
    key = query.lower().strip()
    now = time.time()
    if key in CACHE and (now - CACHE[key]["time"] < CACHE_TTL):
        return CACHE[key]["data"]

    results = []
    domains_query = " OR ".join([f"site:{info['domain']}" for info in STORES.values()])
    search_term = f"{query} narxi ({domains_query})"

    try:
        with DDGS() as ddgs:
            ddg_results = list(ddgs.text(search_term, region="uz-uz", max_results=15))
    except Exception as e:
        print(f"[DDG] xato: {e}")
        ddg_results = []

    found_stores = set()
    for item in ddg_results:
        url = item.get("href", "")
        title = item.get("title", "")
        body = item.get("body", "")
        full_text = f"{title} {body}"

        store_name = None
        for name, info in STORES.items():
            if info["domain"] in url:
                store_name = name
                break

        if not store_name or store_name in found_stores:
            continue

        price = extract_price(full_text)
        if price:
            found_stores.add(store_name)
            results.append({
                "store": store_name,
                "price": price,
                "url": url,
                "title": title[:60]
            })

    results.sort(key=lambda x: x["price"])
    CACHE[key] = {"time": now, "data": results}
    return results

# --- TUGMALAR ---
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔍 Mahsulot qidirish", "🔥 Mashhur tovarlar")
    markup.row("📁 Kategoriyalar", "❤️ Sevimlilar")
    markup.row("ℹ️ Bot haqida")
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
        callback_data=f"fav:{query[:40]}"
    ))
    return markup

# --- HANDLERLAR ---
@bot.message_handler(commands=["start"])
def start_cmd(message):
    text = (
        "🤖 <b>Arzoni Bor</b>: O'zbekiston onlayn do'konlarida mahsulot narxlarini solishtirish boti.\n\n"
        "🔍 Shunchaki mahsulot nomini yozing (masalan: <i>Redmi Note 13</i> yoki <i>iPhone 16</i>).\n\n"
        "⚠️ <i>Narxlar avtomatik qidiruv orqali olinadi va do'konda o'zgargan bo'lishi mumkin. "
        "Xarid qilishdan oldin saytda tekshiring.</i>"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🔍 Mahsulot qidirish")
def ask_product(message):
    bot.send_message(message.chat.id, "Qaysi mahsulot narxini bilmoqchisiz? Nomini yozing 👇")

@bot.message_handler(func=lambda m: m.text == "🔥 Mashhur tovarlar")
def popular_products(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    items = ["iPhone 16 Pro", "Redmi Note 13", "Samsung A55", "AirPods Pro 2", "PlayStation 5", "MacBook Air M2"]
    markup.add(*[types.InlineKeyboardButton(i, callback_data=f"search:{i}") for i in items])
    bot.send_message(message.chat.id, "🔥 Eng ko'p qidirilayotgan mahsulotlar:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📁 Kategoriyalar")
def categories(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    cats = ["Smartfon", "Noutbuk", "Televizor", "Konditsioner", "Kir yuvish mashinasi", "Muzlatkich"]
    markup.add(*[types.InlineKeyboardButton(c, callback_data=f"search:{c}") for c in cats])
    bot.send_message(message.chat.id, "📁 Kategoriyani tanlang:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "❤️ Sevimlilar")
def show_favorites(message):
    cursor.execute("SELECT query FROM favorites WHERE user_id = ?", (message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(message.chat.id, "Sevimlilar ro'yxatingiz hozircha bo'sh.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for (q,) in rows:
        markup.add(types.InlineKeyboardButton(f"🔍 {q}", callback_data=f"search:{q}"))
    bot.send_message(message.chat.id, "❤️ Saqlangan mahsulotlaringiz:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "ℹ️ Bot haqida")
def about_bot(message):
    bot.send_message(
        message.chat.id,
        "🤖 <b>Arzoni Bor</b> — O'zbekistonning yirik onlayn do'konlari (Uzum, Olcha, Texnomart, Asaxiy, Mediapark, Idea) bo'ylab narxlarni solishtirish boti."
    )

def process_search(chat_id, query):
    wait_msg = bot.send_message(chat_id, f"🔍 <b>{query}</b> narxlari qidirilmoqda...")
    results = search_prices_ddg(query)

    if results:
        lines = [f"🔎 <b>{query}</b> bo'yicha topilgan narxlar:\n"]
        for idx, r in enumerate(results, 1):
            medal = "🏆 " if idx == 1 else f"{idx}. "
            lines.append(f"{medal}<b>{r['store']}</b>: <a href='{r['url']}'>{format_price(r['price'])}</a>")
        lines.append("\n👇 <i>Barcha do'konlarda ko'rish uchun tugmalardan foydalaning:</i>")
        text = "\n".join(lines)
    else:
        text = f"🔎 <b>{query}</b>\n\nAvtomatik narx topilmadi. Quyidagi do'konlarda to'g'ridan-to'g'ri ko'ring 👇"

    try:
        bot.delete_message(chat_id, wait_msg.message_id)
    except Exception:
        pass

    bot.send_message(chat_id, text, reply_markup=store_buttons(query), disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data.startswith("search:"):
        query = call.data.split("search:", 1)[1]
        bot.answer_callback_query(call.id)
        process_search(call.message.chat.id, query)
    elif call.data.startswith("fav:"):
        query = call.data.split("fav:", 1)[1]
        cursor.execute("INSERT OR IGNORE INTO favorites (user_id, query) VALUES (?, ?)", (call.from_user.id, query))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Sevimlilarga saqlandi!")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    query = message.text.strip()
    if len(query) < 2:
        bot.send_message(message.chat.id, "Iltimos, mahsulot nomini to'liqroq yozing.")
        return
    process_search(message.chat.id, query)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    print("Arzoni Bor (DuckDuckGo bezlimit) ishga tushdi!")
    bot.infinity_polling(skip_pending=True)
