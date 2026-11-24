import re
from urllib.parse import quote_plus
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "uz-UZ,uz;q=0.9,ru;q=0.8,en;q=0.7",
}

QUERY_SYNONYMS = {
    "ayfon": "iphone", "айфон": "iphone", "ifone": "iphone", "ayphon": "iphone",
    "samsung": "samsung", "самсунг": "samsung", "galaksi": "galaxy",
    "redmi": "redmi", "редми": "redmi", "xiaomi": "xiaomi", "сяоми": "xiaomi", "mi": "xiaomi",
    "poco": "poco", "поко": "poco", "honor": "honor", "хонор": "honor",
    "televizor": "телевизор", "tevizor": "телевизор", "tv": "телевизор", "telik": "телевизор",
    "noutbuk": "ноутбук", "laptop": "ноутбук", "kompyuter": "компьютер",
    "makbuk": "macbook", "макбук": "macbook", "aypad": "ipad", "айпад": "ipad",
    "planshet": "планшет", "tablet": "планшет",
    "konditsioner": "кондиционер", "kanditsioner": "кондиционер", "kondik": "кондиционер",
    "muzlatgich": "холодильник", "xolodilnik": "холодильник", "haladilnik": "холодильник",
    "kir yuvish": "стиральная машина", "avtomat": "стиральная машина", "stiralka": "стиральная машина",
    "changyutgich": "пылесос", "pilesos": "пылесос", "robot pilesos": "робот пылесос",
    "quloqchin": "наушники", "naushnik": "наушники", "airpods": "airpods", "eyrpods": "airpods",
    "soat": "смарт часы", "smart soat": "смарт часы", "apple watch": "apple watch",
    "playstation": "playstation", "ps5": "playstation 5", "ps4": "playstation 4",
    "mikroto'lqinli": "микроволновая печь", "mikrovolnovka": "микроволновая печь",
    "gaz plita": "газовая плита", "duxovka": "духовой шкаф", "dazmol": "утюг",
    "fen": "фен", "blender": "блендер", "kofemashina": "кофемашина", "termopot": "термопот",
    "monitor": "монитор", "printer": "принтер", "klaviatura": "клавиатура", "sichqoncha": "мышь",
}

POPULAR_SUGGESTIONS = [
    # Smartfonlar — Apple
    "iPhone 16 Pro Max", "iPhone 16 Pro", "iPhone 16 Plus", "iPhone 16",
    "iPhone 15 Pro Max", "iPhone 15 Pro", "iPhone 15", "iPhone 14 Pro", "iPhone 14", "iPhone 13",
    # Smartfonlar — Samsung
    "Samsung Galaxy S25 Ultra", "Samsung Galaxy S24 Ultra", "Samsung Galaxy S24", "Samsung Galaxy S23 FE",
    "Samsung Galaxy A55", "Samsung Galaxy A54", "Samsung Galaxy A35", "Samsung Galaxy A25", "Samsung Galaxy A15", "Samsung Galaxy A05",
    "Samsung Galaxy Z Fold 6", "Samsung Galaxy Z Flip 6",
    # Smartfonlar — Xiaomi / Redmi / Poco
    "Redmi Note 14 Pro", "Redmi Note 14", "Redmi Note 13 Pro+", "Redmi Note 13 Pro", "Redmi Note 13",
    "Redmi 14C", "Redmi 13C", "Redmi 13", "Redmi A3",
    "Xiaomi 14 Ultra", "Xiaomi 14", "Xiaomi 14T Pro", "Poco X6 Pro", "Poco F6", "Poco M6 Pro",
    # Boshqa brendlar
    "Honor X9b", "Honor X8b", "Honor 200 Pro", "Honor Magic 6 Pro",
    "Infinix Note 40 Pro", "Infinix Hot 40", "Tecno Camon 30", "Tecno Spark 20",
    "Nothing Phone 2a", "Google Pixel 8 Pro", "Google Pixel 9",
    # Noutbuklar va Planshetlar
    "MacBook Air M3", "MacBook Air M2", "MacBook Air M1", "MacBook Pro M3",
    "iPad Air M2", "iPad 10", "iPad Pro M4", "Samsung Galaxy Tab S9", "Xiaomi Pad 6",
    "Asus VivoBook 15", "Asus TUF Gaming F15", "Asus ROG Strix",
    "Lenovo IdeaPad 3", "Lenovo Legion 5", "Lenovo ThinkPad",
    "HP Pavilion 15", "HP Victus", "Acer Aspire 5", "Acer Nitro 5", "MSI Thin GF63",
    # Quloqchin va Soatlar
    "AirPods Pro 2", "AirPods 4", "AirPods 3", "AirPods Max",
    "Samsung Galaxy Buds 3 Pro", "Samsung Galaxy Buds 2 Pro", "Xiaomi Redmi Buds 5", "JBL Tune 520BT",
    "Apple Watch Ultra 2", "Apple Watch Series 10", "Apple Watch Series 9", "Apple Watch SE",
    "Samsung Galaxy Watch 7", "Samsung Galaxy Watch 6", "Xiaomi Watch S3", "Mi Band 9",
    # O'yin va Gadjetlar
    "PlayStation 5 Slim", "PlayStation 5 Pro", "PlayStation 5", "Xbox Series X", "Xbox Series S",
    "Nintendo Switch OLED", "Steam Deck OLED", "DualSense kontroller",
    "Yandex Stansiya Mini", "Yandex Stansiya Maks", "GoPro Hero 12", "DJI Mini 4 Pro",
    # Televizorlar
    "Artel 32 televizor", "Artel 43 televizor", "Artel 55 televizor",
    "Samsung 43 televizor", "Samsung 55 televizor", "Samsung 65 QLED",
    "LG 43 televizor", "LG 55 OLED", "Xiaomi TV A Pro 43", "Xiaomi TV A Pro 55", "TCL 50 televizor",
    # Maishiy texnika
    "Artel konditsioner 12", "Artel konditsioner 18", "Gree konditsioner 12", "Midea konditsioner 12", "Aux konditsioner 12",
    "LG kir yuvish mashinasi 7 kg", "Samsung kir yuvish mashinasi", "Artel kir yuvish mashinasi", "Shivaki kir yuvish mashinasi",
    "Artel muzlatgich", "Samsung muzlatgich", "LG muzlatgich", "Hoffmann muzlatgich",
    "Dyson V15 changyutgich", "Dyson Airwrap fen", "Xiaomi robot changyutgich", "Artel changyutgich",
    "Artel mikroto'lqinli pech", "Samsung mikroto'lqinli pech", "Artel gaz plita", "Philips dazmol",
]


def normalize_query(q):
    low = re.sub(r"\s+", " ", (q or "").strip().lower())
    for k, v in QUERY_SYNONYMS.items():
        if k in low:
            low = re.sub(rf"\b{re.escape(k)}\b", v, low)
    return low.strip()


def clean_title(title):
    t = re.sub(r"\s+", " ", (title or "").strip())
    return t[:110] + ("..." if len(t) > 110 else "")


def get_suggestions(q):
    q_low = (q or "").strip().lower()
    if not q_low:
        return []
    norm = normalize_query(q_low)
    matches = []

    # 1. Avval boshidan mos kelganlar, keyin ichida borlari
    for item in POPULAR_SUGGESTIONS:
        il = item.lower()
        if il.startswith(q_low) or il.startswith(norm):
            matches.append(item)
    for item in POPULAR_SUGGESTIONS:
        il = item.lower()
        if (q_low in il or norm in il) and item not in matches:
            matches.append(item)

    # 2. Yetarli bo'lmasa, Texnomart API dan jonli takliflar qo'shish
    if len(matches) < 6 and len(q_low) >= 2:
        try:
            r = requests.get(
                f"https://gw.texnomart.uz/api/common/v1/search/result?q={quote_plus(norm)}",
                headers=HEADERS,
                timeout=2.5,
            )
            if r.status_code == 200:
                for p in r.json().get("data", {}).get("products", [])[:6]:
                    name = clean_title(p.get("name", ""))
                    if name and name not in matches:
                        matches.append(name)
        except Exception:
            pass

    return matches[:8]
