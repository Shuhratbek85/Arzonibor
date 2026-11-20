import os
import re
import time
import html
import sqlite3
import threading
import requests
from datetime import datetime
from urllib.parse import quote_plus
from http.server import HTTPServer, BaseHTTPRequestHandler

import telebot
from telebot import types

# ==========================================
# 1. SOZLAMALAR
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()

CACHE_SECONDS = 6 * 60 * 60       # bir xil so'rov natijasi 6 soat keshda saqlanadi
CHANNELS = ["@MrTexnoAI", "@arzoniboragent"]

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arzonibor.db")
COOLDOWN_SECONDS = 2
MAX_QUERY_LEN = 80
STALE_DAYS = 14
MAX_PRODUCTS_SHOWN = 4

STORES = [
    ("Uzum Market", "uzum.uz"),
    ("Olcha", "olcha.uz"),
    ("Texnomart", "texnomart.uz"),
    ("Asaxiy", "asaxiy.uz"),
    ("Mediapark", "mediapark.uz"),
    ("Idea", "idea.uz"),
]

if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_TOKEN topilmadi. Render Environment Variables bo'limiga qo'shing.")

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True, num_threads=4)

BTN_SEARCH = "🔎 Mahsulot qidirish"
BTN_POPULAR = "🔥 Mashhur tovarlar"
BTN_CATS = "📂 Kategoriyalar"
BTN_FAVS = "❤️ Sevimlilar"
BTN_ABOUT = "ℹ️ Bot haqida"

CATEGORIES = {
    "📱 Telefonlar": ["iPhone 16 Pro", "Samsung Galaxy A55", "Xiaomi Redmi Note 13"],
    "💻 Noutbuklar": ["Lenovo IdeaPad", "HP noutbuk", "MacBook Air"],
    "📺 Televizorlar": ["Televizor 55 dyuym", "Samsung televizor", "Xiaomi televizor"],
    "🏠 Maishiy texnika": ["Konditsioner", "Muzlatgich", "Kir yuvish mashinasi"],
}
POPULAR = ["iPhone 16 Pro", "Samsung Galaxy A55", "Redmi Note 13", "Televizor 55 dyuym", "Konditsioner", "Muzlatgich"]

DISCLAIMER = ("⚠️ Narxlar avtomatik yoki admin tomonidan olingan bo'lib, do'konda o'zgargan bo'lishi mumkin. "
              "Xarid qilishdan oldin saytda tekshiring.")

ADMIN_HELP = (
    "🛠 Admin buyruqlari\n\n"
    "Narx qo'shish (bitta qator):\n"
    "/narx iPhone 16 Pro | Uzum Market | 13500000 | https://havola\n\n"
    "Bir nechta do'kon (ko'p qator):\n"
    "/narx iPhone 16 Pro\n"
    "Uzum Market | 13500000 | https://havola\n"
    "Olcha | 13750000\n\n"
    "/ochir iPhone 16 Pro | Olcha  (bitta do'kon)\n"
    "/ochir iPhone 16 Pro  (butun mahsulot)\n"
    "/baza  (kiritilgan mahsulotlar)\n"
    "/top  (eng ko'p qidirilganlar)\n"
    "/xabar Matn...  (hamma foydalanuvchiga xabar yuborish)\n"
    "/holat  (bot statistikasi)"
)

# ==========================================
# 2. BAZA (SQLite)
# ==========================================
def db():
    return sqlite3.connect(DB_PATH, timeout=10)

def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_seen INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, query TEXT, ts INTEGER)")
        conn.execute("""CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT, pkey TEXT, store TEXT, skey TEXT,
            price INTEGER, url TEXT, updated INTEGER,
            UNIQUE(pkey, skey))""")
        conn.execute("CREATE TABLE IF NOT EXISTS cache (query TEXT PRIMARY KEY, answer TEXT, ts INTEGER)")
        conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER, query TEXT, ts INTEGER,
            UNIQUE(user_id, query))""")

init_db()

def norm(s):
    return re.sub(r"[^0-9a-zа-яёўқғҳ]+", "", (s or "").lower())

def save_user(user):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
                     (user.id, user.username or "", int(time.time())))

def log_search(user_id, query):
    with db() as conn:
        conn.execute("INSERT INTO searches (user_id, query, ts) VALUES (?, ?, ?)", (user_id, query, int(time.time())))

# ==========================================
# 3. ADMIN BAZASI: kiritish, qidirish, formatlash
# ==========================================
def parse_price(text):
    digits = re.sub(r"\D", "", text or "")
    if not digits:
        return None
    value = int(digits)
    return value if 1000 <= value <= 10_000_000_000 else None

def parse_narx(raw):
    parts = (raw or "").split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return None, [], []
    errors = []
    if len(lines) == 1:
        p = [x.strip() for x in lines[0].split("|")]
        if len(p) < 3:
            return None, [], []
        product, entries = p[0], [p[1:]]
    else:
        product = lines[0]
        entries = [[x.strip() for x in l.split("|")] for l in lines[1:]]
    if not product or "|" in product:
        return None, [], []
    rows = []
    for e in entries:
        store = e[0] if e else ""
        price = parse_price(e[1]) if len(e) > 1 else None
        url = e[2] if len(e) > 2 and e[2].lower().startswith("http") else ""
        if not store or price is None:
            errors.append(" | ".join(e))
            continue
        rows.append((store, price, url))
    return product, rows, errors

def upsert_prices(product, rows):
    now = int(time.time())
    with db() as conn:
        for store, price, url in rows:
            conn.execute(
                """INSERT INTO prices (product, pkey, store, skey, price, url, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(pkey, skey) DO UPDATE SET
                     product=excluded.product, store=excluded.store,
                     price=excluded.price, url=excluded.url, updated=excluded.updated""",
                (product, norm(product), store, norm(store), price, url, now))

def find_products(query):
    qk = norm(query)
    if len(qk) < 3:
        return []
    with db() as conn:
        rows = conn.execute(
            "SELECT product, pkey, store, price, url, updated FROM prices WHERE pkey LIKE ? ORDER BY price ASC",
            ("%" + qk + "%",)).fetchall()
    groups = {}
    for product, pkey, store, price, url, updated in rows:
        g = groups.setdefault(pkey, {"product": product, "rows": []})
        g["rows"].append((store, price, url, updated))
    ordered = sorted(groups.values(), key=lambda g: g["product"].lower())
    return ordered[:MAX_PRODUCTS_SHOWN]

def fmt_price(value):
    return f"{int(value):,}".replace(",", " ")

def format_matches(matches):
    esc = html.escape
    now = time.time()
    blocks = []
    for g in matches:
        lines = [f"🛒 <b>{esc(g['product'], quote=False)}</b>"]
        many = len(g["rows"]) > 1
        for i, (store, price, url, ts) in enumerate(g["rows"]):
            marker = "🥇" if (many and i == 0) else "▫️"
            name = f'<a href="{esc(url, quote=True)}">{esc(store, quote=False)}</a>' if url else esc(store, quote=False)
            stale = " ⚠️eskirgan" if now - ts > STALE_DAYS * 86400 else ""
            date = datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
            lines.append(f"{marker} {name}: <b>{fmt_price(price)}</b> so'm <i>({date}{stale})</i>")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

# ==========================================
# 3b. JONLI QIDIRUV (Uzum Market, Olcha, Texnomart)
# ==========================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def cache_get(query):
    with db() as conn:
        row = conn.execute("SELECT answer, ts FROM cache WHERE query = ?", (norm(query),)).fetchone()
    if row and time.time() - row[1] < CACHE_SECONDS:
        return row[0]
    return None

def cache_set(query, answer):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO cache (query, answer, ts) VALUES (?, ?, ?)",
                     (norm(query), answer, int(time.time())))

def search_uzum(query, limit=3):
    url = "https://graphql.uzum.uz/"
    payload = {
        "operationName": "getMakeSearch",
        "variables": {
            "queryInput": {
                "text": query,
                "showAdultContent": "NONE",
                "filters": [],
                "sort": "BY_RELEVANCE_DESC",
                "pagination": {"offset": 0, "limit": limit},
                "correct": True,
                "getFastCategories": False,
                "getPromotionItems": False,
                "fastFacets": False,
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
        """,
    }
    headers = {**HEADERS, "Content-Type": "application/json", "Origin": "https://uzum.uz", "Referer": "https://uzum.uz/"}
    results = []
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=6)
        r.raise_for_status()
        items = r.json().get("data", {}).get("makeSearch", {}).get("items", [])
        for item in items:
            card = item.get("catalogCard") or {}
            title, price, pid = card.get("title"), card.get("minSellPrice"), card.get("productId")
            if title and price and pid:
                results.append({
                    "store": "Uzum Market",
                    "title": title,
                    "price": int(price),
                    "url": f"https://uzum.uz/uz/product/{pid}",
                })
    except Exception as e:
        print(f"[Uzum] xato: {str(e)[:100]}")
    return results

def search_olcha(query, limit=3):
    url = "https://olcha.uz/api/v2/products"
    results = []
    try:
        r = requests.get(url, params={"search": query, "per_page": limit}, headers=HEADERS, timeout=6)
        r.raise_for_status()
        products = r.json().get("data", {}).get("products", [])
        for p in products:
            title = p.get("name_uz") or p.get("name_ru") or p.get("name")
            price = p.get("total_price") or p.get("price")
            alias = p.get("alias")
            if title and price:
                link = f"https://olcha.uz/uz/product/view/{alias}" if alias else "https://olcha.uz"
                results.append({
                    "store": "Olcha",
                    "title": title,
                    "price": int(price),
                    "url": link,
                })
    except Exception as e:
        print(f"[Olcha] xato: {str(e)[:100]}")
    return results

def search_texnomart(query, limit=3):
    url = "https://gateway.texnomart.uz/api/common/v1/search/result"
    results = []
    try:
        r = requests.get(url, params={"q": query, "page": 1}, headers={**HEADERS, "Accept-Language": "uz"}, timeout=6)
        r.raise_for_status()
        products = r.json().get("data", {}).get("products", [])[:limit]
        for p in products:
            title = p.get("name")
            price = p.get("sale_price") or p.get("price")
            pid = p.get("id")
            if title and price and pid:
                results.append({
                    "store": "Texnomart",
                    "title": title,
                    "price": int(price),
                    "url": f"https://texnomart.uz/uz/product/detail/{pid}/",
                })
    except Exception as e:
        print(f"[Texnomart] xato: {str(e)[:100]}")
    return results

def search_live_stores(query):
    items = search_uzum(query, 3) + search_olcha(query, 3) + search_texnomart(query, 3)
    if not items:
        return None
    items.sort(key=lambda x: x["price"])
    esc = html.escape
    lines = [f"🛒 <b>{esc(query, quote=False)}</b> — eng arzon takliflar:\n"]
    for i, item in enumerate(items[:7]):
        marker = "🥇" if i == 0 else "▫️"
        title_short = item["title"][:48] + ("..." if len(item["title"]) > 48 else "")
        link = f'<a href="{esc(item["url"], quote=True)}">{esc(item["store"], quote=False)}</a>'
        lines.append(f"{marker} {link}: <b>{fmt_price(item['price'])}</b> so'm\n    <i>{esc(title_short, quote=False)}</i>")
    return "\n".join(lines)

# ==========================================
# 4. OBUNA TEKSHIRUVI VA ADMIN
# ==========================================
def is_subscribed(user_id):
    for ch in CHANNELS:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception as e:
            print(f"[Obuna] {ch} tekshirib bo'lmadi: {e}")
            return False
    return True

def is_admin(user_id):
    try:
        member = bot.get_chat_member(CHANNELS[0], user_id)
        return member.status in ("creator", "administrator")
    except Exception as e:
        print(f"[Admin] tekshirib bo'lmadi: {e}")
        return False

def ask_to_subscribe(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in CHANNELS:
        markup.add(types.InlineKeyboardButton(f"📢 {ch} kanaliga obuna bo'lish", url=f"https://t.me/{ch.lstrip('@')}"))
    markup.add(types.InlineKeyboardButton("✅ Obuna bo'ldim, tekshirish", callback_data="check_sub"))
    bot.send_message(
        chat_id,
        "Botdan foydalanish uchun avval kanallarimizga obuna bo'ling, so'ng \"Tekshirish\" tugmasini bosing 👇",
        reply_markup=markup,
    )

def gate(chat_id, user_id):
    if is_subscribed(user_id):
        return True
    ask_to_subscribe(chat_id)
    return False

# ==========================================
# 5. MENYULAR
# ==========================================
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton(BTN_SEARCH), types.KeyboardButton(BTN_POPULAR))
    markup.add(types.KeyboardButton(BTN_CATS), types.KeyboardButton(BTN_FAVS))
    markup.add(types.KeyboardButton(BTN_ABOUT))
    return markup

def query_buttons(items):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(q, callback_data=f"s:{q[:50]}") for q in items])
    return markup

def categories_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"c:{i}") for i, name in enumerate(CATEGORIES)])
    return markup

def google_url(text):
    return "https://www.google.com/search?q=" + quote_plus(text)

def store_links_markup(query):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(*[
        types.InlineKeyboardButton(f"🛍 {name}", url=google_url(f"{query} site:{domain}"))
        for name, domain in STORES
    ])
    markup.add(types.InlineKeyboardButton("🌐 Hamma do'konlar (Google)", url=google_url(f"{query} narxi O'zbekiston")))
    markup.add(types.InlineKeyboardButton("❤️ Sevimlilarga saqlash", callback_data=f"fav:{query[:50]}"))
    return markup

def send_html(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id, text[:4000], parse_mode="HTML", disable_web_page_preview=True, **kwargs)
    except Exception as e:
        print(f"[HTML] xato: {e}")
        plain = re.sub(r"<[^>]+>", "", html.unescape(text))[:4000]
        bot.send_message(chat_id, plain, disable_web_page_preview=True, **kwargs)

# ==========================================
# 6. QIDIRUV
# ==========================================
_last_request = {}

def too_fast(user_id):
    now = time.time()
    if now - _last_request.get(user_id, 0) < COOLDOWN_SECONDS:
        return True
    _last_request[user_id] = now
    return False

def do_search(chat_id, user, query):
    query = re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_LEN]
    if len(query) < 2:
        bot.send_message(chat_id, "Mahsulot nomini yozing, masalan: Samsung Galaxy A55", reply_markup=main_menu())
        return
    if too_fast(user.id):
        bot.send_message(chat_id, "⏳ Biroz kuting va qayta yuboring.")
        return
    log_search(user.id, query)

    links = store_links_markup(query)

    # 1) Admin kiritgan narxlar
    matches = find_products(query)
    if matches:
        text = format_matches(matches) + "\n\n" + html.escape(DISCLAIMER, quote=False)
        send_html(chat_id, text, reply_markup=links)
        return

    # 2) Kesh yoki jonli do'konlar (Uzum, Olcha, Texnomart)
    cached = cache_get(query)
    if cached:
        send_html(chat_id, cached + "\n\n" + html.escape(DISCLAIMER, quote=False), reply_markup=links)
        return

    wait_msg = bot.send_message(chat_id, "🔎 Do'konlardan narxlarni qidiryapman...")
    answer = search_live_stores(query)
    try:
        bot.delete_message(chat_id, wait_msg.message_id)
    except Exception:
        pass

    if answer:
        cache_set(query, answer)
        send_html(chat_id, answer + "\n\n" + html.escape(DISCLAIMER, quote=False), reply_markup=links)
        return

    # 3) Zaxira: do'kon tugmalari
    text = (f"🔎 <b>{html.escape(query, quote=False)}</b>\n\n"
            "Avtomatik narx topilmadi. Quyidagi do'konlarda to'g'ridan-to'g'ri ko'ring 👇")
    send_html(chat_id, text, reply_markup=links)

# ==========================================
# 7. HANDLERLAR
# ==========================================
@bot.message_handler(commands=["start"])
def start_handler(message):
    save_user(message.from_user)
    if not gate(message.chat.id, message.from_user.id):
        return
    bot.send_message(
        message.chat.id,
        "Assalomu alaykum! 🛒 Arzoni Bor botiga xush kelibsiz!\n\n"
        "Mahsulot nomini yozing — Uzum, Olcha va Texnomart narxlarini solishtirib beraman.",
        reply_markup=main_menu(),
    )

# --- Admin buyruqlari ---
@bot.message_handler(commands=["admin"])
def admin_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    bot.send_message(message.chat.id, ADMIN_HELP, disable_web_page_preview=True)

@bot.message_handler(commands=["narx"])
def narx_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    product, rows, errors = parse_narx(message.text)
    if not product or (not rows and not errors):
        return bot.send_message(message.chat.id, "Format noto'g'ri. /admin ni bosib namunani ko'ring.")
    if rows:
        upsert_prices(product, rows)
    reply = f"✅ Saqlandi: {product}, {len(rows)} ta do'kon."
    if errors:
        reply += "\n\n⚠️ Xato qatorlar:\n" + "\n".join(errors)
    bot.send_message(message.chat.id, reply, disable_web_page_preview=True)

@bot.message_handler(commands=["ochir"])
def ochir_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    parts = (message.text or "").split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    if not body:
        return bot.send_message(message.chat.id, "Masalan: /ochir iPhone 16 Pro | Olcha")
    p = [x.strip() for x in body.split("|")]
    with db() as conn:
        if len(p) >= 2 and p[1]:
            cur = conn.execute("DELETE FROM prices WHERE pkey = ? AND skey = ?", (norm(p[0]), norm(p[1])))
        else:
            cur = conn.execute("DELETE FROM prices WHERE pkey = ?", (norm(p[0]),))
        count = cur.rowcount
    bot.send_message(message.chat.id, f"🗑 O'chirildi: {count} ta yozuv.")

@bot.message_handler(commands=["baza"])
def baza_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    with db() as conn:
        rows = conn.execute(
            "SELECT product, COUNT(*), MIN(price) FROM prices GROUP BY pkey ORDER BY product COLLATE NOCASE").fetchall()
    if not rows:
        return bot.send_message(message.chat.id, "Baza hali bo'sh.")
    lines = [f"• {p}: {n} ta do'kon, eng arzon {fmt_price(m)} so'm" for p, n, m in rows]
    bot.send_message(message.chat.id, ("📦 Bazadagi mahsulotlar:\n" + "\n".join(lines))[:4000])

@bot.message_handler(commands=["top"])
def top_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    with db() as conn:
        rows = conn.execute(
            "SELECT MIN(query), COUNT(*) AS c FROM searches GROUP BY lower(query) ORDER BY c DESC LIMIT 15").fetchall()
    if not rows:
        return bot.send_message(message.chat.id, "Hali qidiruvlar yo'q.")
    lines = [f"{c}x  {q}" for q, c in rows]
    bot.send_message(message.chat.id, "🔥 Eng ko'p qidirilganlar:\n" + "\n".join(lines))

@bot.message_handler(commands=["xabar"])
def broadcast_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return bot.send_message(message.chat.id, "Foydalanish: /xabar Assalomu alaykum, yangi chegirmalar!")
    text = parts[1].strip()
    with db() as conn:
        users = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
    sent, failed = 0, 0
    bot.send_message(message.chat.id, f"📤 {len(users)} ta foydalanuvchiga yuborilmoqda...")
    for uid in users:
        try:
            bot.send_message(uid, text)
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    bot.send_message(message.chat.id, f"✅ Yuborildi: {sent}\n❌ Yetib bormadi: {failed}")

@bot.message_handler(commands=["holat"])
def holat_handler(message):
    if not is_admin(message.from_user.id):
        return bot.send_message(message.chat.id, "Bu buyruq faqat kanal adminlari uchun.")
    with db() as conn:
        n_prices = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        n_searches = conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    bot.send_message(
        message.chat.id,
        f"📊 Bot holati\n"
        f"Jonli do'konlar: Uzum, Olcha, Texnomart ✅\n"
        f"Bazadagi narxlar: {n_prices}\n"
        f"Foydalanuvchilar: {n_users}\n"
        f"Jami qidiruvlar: {n_searches}"
    )

# --- Inline qidiruv (@bot_nomi mahsulot) ---
@bot.inline_handler(lambda query: len(query.query.strip()) >= 2)
def inline_search_handler(inline_query):
    q = inline_query.query.strip()[:MAX_QUERY_LEN]
    answer = cache_get(q) or search_live_stores(q)
    if answer:
        cache_set(q, answer)
        result = types.InlineQueryResultArticle(
            id="1",
            title=f"🛒 {q} — narxlarni ko'rish",
            description="Uzum, Olcha va Texnomart narxlari",
            input_message_content=types.InputTextMessageContent(
                answer + "\n\n" + html.escape(DISCLAIMER, quote=False),
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
        )
        bot.answer_inline_query(inline_query.id, [result], cache_time=60)
    else:
        bot.answer_inline_query(inline_query.id, [], cache_time=10)

# --- Oddiy foydalanuvchi xabarlari ---
@bot.message_handler(func=lambda m: True, content_types=["text"])
def text_handler(message):
    save_user(message.from_user)
    if not gate(message.chat.id, message.from_user.id):
        return
    text = (message.text or "").strip()

    if text in (BTN_SEARCH, "⬅️ Asosiy menyu"):
        bot.send_message(message.chat.id, "Mahsulot nomini yozing, masalan: Samsung Galaxy A55", reply_markup=main_menu())
    elif text == BTN_POPULAR:
        bot.send_message(message.chat.id, "🔥 Mashhur so'rovlar. Birini tanlang:", reply_markup=query_buttons(POPULAR))
    elif text == BTN_CATS:
        bot.send_message(message.chat.id, "📂 Kategoriyani tanlang:", reply_markup=categories_markup())
    elif text == BTN_FAVS:
        with db() as conn:
            favs = [r[0] for r in conn.execute(
                "SELECT query FROM favorites WHERE user_id = ? ORDER BY ts DESC LIMIT 10",
                (message.from_user.id,)).fetchall()]
        if not favs:
            bot.send_message(message.chat.id, "❤️ Sevimlilar ro'yxati bo'sh. Mahsulot qidirib, ostidagi ❤️ tugmasini bosing.")
        else:
            bot.send_message(message.chat.id, "❤️ Saqlangan mahsulotlaringiz (qayta tekshirish uchun bosing):",
                             reply_markup=query_buttons(favs))
    elif text == BTN_ABOUT:
        bot.send_message(
            message.chat.id,
            "🤖 Arzoni Bor: O'zbekiston onlayn do'konlarida mahsulot narxlarini solishtirish boti.\n\n"
            "💡 Maslahat: istalgan chatda bot nomini yozib ham narx qidirishingiz mumkin!\n\n" + DISCLAIMER,
            reply_markup=main_menu(),
        )
    else:
        do_search(message.chat.id, message.from_user, text)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data or ""
    chat_id = call.message.chat.id

    if data == "check_sub":
        if is_subscribed(call.from_user.id):
            bot.answer_callback_query(call.id, "✅ Rahmat! Endi foydalanishingiz mumkin.")
            bot.send_message(chat_id, "Mahsulot nomini yozing 👇", reply_markup=main_menu())
        else:
            bot.answer_callback_query(call.id, "❌ Hali obuna bo'lmagansiz.", show_alert=True)
        return

    if not is_subscribed(call.from_user.id):
        bot.answer_callback_query(call.id)
        ask_to_subscribe(chat_id)
        return

    if data.startswith("c:"):
        try:
            name = list(CATEGORIES)[int(data[2:])]
        except (ValueError, IndexError):
            return bot.answer_callback_query(call.id)
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, f"{name}: birini tanlang:", reply_markup=query_buttons(CATEGORIES[name]))
    elif data.startswith("s:"):
        bot.answer_callback_query(call.id)
        do_search(chat_id, call.from_user, data[2:])
    elif data.startswith("fav:"):
        q = data[4:]
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO favorites (user_id, query, ts) VALUES (?, ?, ?)",
                         (call.from_user.id, q, int(time.time())))
        bot.answer_callback_query(call.id, f"❤️ «{q}» sevimlilarga saqlandi!")
    else:
        bot.answer_callback_query(call.id)

# ==========================================
# 8. RENDER UCHUN HTTP SERVER VA ISHGA TUSHIRISH
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Arzoni Bor ishlayapti!")

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    bot.remove_webhook()
    for ch in CHANNELS:
        try:
            me = bot.get_me()
            status = bot.get_chat_member(ch, me.id).status
            if status not in ("administrator", "creator"):
                print(f"⚠️ Bot {ch} kanalida admin emas!")
        except Exception as e:
            print(f"⚠️ {ch} kanalini tekshirib bo'lmadi: {e}")
    print("✅ Arzoni Bor (Uzum + Olcha + Texnomart) ishga tushdi!")
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            print(f"Ulanishda xato: {e}")
            time.sleep(3)
