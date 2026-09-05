import sqlite3
import threading
import time
import requests

# ۱. تنظیمات اختصاصی
TELEGRAM_BOT_TOKEN = "8918660280:AAF2CMZ1aFG40I821kSK6gL2hCCVJh17diw"
MAP_KEY = "0MzpvgaGxwaZTsf7t5gHjPDcdm2lGKPnALVOQXa2"

# اتصال مستقیم (بدون پروکسی برای محیط Render)
PROXIES = None

# لیست منابع داده ماهواره‌ای جهت بررسی خودکار
SOURCES = [
    {
        "name": "NASA FIRMS (VIIRS SNPP)",
        "url": f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_SNPP_NRT/45.0,28.0,53.5,37.0/1",
        "type": "csv"
    },
    {
        "name": "NASA FIRMS (MODIS NRT)",
        "url": f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/MODIS_NRT/45.0,28.0,53.5,37.0/1",
        "type": "csv"
    },
    {
        "name": "Copernicus EFFIS (Europe)",
        "url": "https://effis.jrc.ec.europa.eu/geoserver/effis/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=effis:modis.hs&outputFormat=application/json",
        "type": "geojson"
    },
    {
        "name": "EU GWIS / GDIS Active Fires",
        "url": "https://gwis.jrc.ec.europa.eu/geoserver/gwis/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=gwis:viirs.hs&outputFormat=application/json",
        "type": "geojson"
    },
    {
        "name": "EUMETSAT Thermal Anomaly Service",
        "url": "https://forest-fire.emergency.copernicus.eu/geoserver/wfs?service=WFS&version=1.0.0&request=GetFeature&typeName=effis:viirs.hs&outputFormat=application/json",
        "type": "geojson"
    }
]

# ۲. مدیریت پایگاه داده (SQLite)
def init_db():
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fires (
            fire_id TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            date_str TEXT,
            source_name TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_user(chat_id):
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def save_fire(fire_id, lat, lon, date_str, source_name):
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO fires (fire_id, lat, lon, date_str, source_name) VALUES (?, ?, ?, ?, ?)",
                   (fire_id, lat, lon, date_str, source_name))
    conn.commit()
    conn.close()

def is_fire_sent(fire_id):
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM fires WHERE fire_id = ?", (fire_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def get_last_fire():
    conn = sqlite3.connect("zagros_bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT lat, lon, date_str, source_name, timestamp FROM fires ORDER BY rowid DESC LIMIT 1")
    last = cursor.fetchone()
    conn.close()
    return last

# ۳. ارسال پیام به کاربران
def send_alert_to_all(message, lat, lon):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    users = get_all_users()

    for chat_id in users:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[{"text": "🗺 مشاهده روی نقشه گوگل", "url": f"https://maps.google.com/?q={lat},{lon}"}]]
            }
        }
        try:
            requests.post(url, json=payload, proxies=PROXIES, timeout=10)
        except Exception as e:
            print(f"خطا در ارسال به {chat_id}: {e}")

# ۴. توابع دریافت و پردازش انواع فرمت‌ها
def fetch_from_csv_source(source):
    res = requests.get(source["url"], proxies=PROXIES, timeout=15)
    if res.status_code == 200:
        lines = res.text.strip().split('\n')
        if len(lines) > 1:
            header = lines[0].split(',')
            for line in lines[1:]:
                data = dict(zip(header, line.split(',')))
                lat, lon = float(data['latitude']), float(data['longitude'])
                if abs(lon - (45.0 + (37.0 - lat) * 0.85)) <= 2.5:
                    fire_id = f"{lat}_{lon}_{data.get('acq_date')}_{data.get('acq_time')}"
                    if not is_fire_sent(fire_id) and data.get('confidence') in ['h', 'n', '100', '80']:
                        save_fire(fire_id, lat, lon, data.get('acq_date'), source["name"])
                        msg = (
                            f"🔥 **هشدار آتش‌سوزی جدید در زاگرس!**\n\n"
                            f"📍 **عرض جغرافیایی:** `{lat}`\n"
                            f"📍 **طول جغرافیایی:** `{lon}`\n"
                            f"📅 **تاریخ ثبت:** {data.get('acq_date')}\n"
                            f"🛰 **منبع:** {source['name']}"
                        )
                        send_alert_to_all(msg, lat, lon)
        return True
    return False

def fetch_from_geojson_source(source):
    res = requests.get(source["url"], proxies=PROXIES, timeout=15)
    if res.status_code == 200:
        data = res.json()
        for feature in data.get('features', []):
            coords = feature.get('geometry', {}).get('coordinates', [])
            props = feature.get('properties', {})
            if coords and len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
                if 28.0 <= lat <= 37.0 and 45.0 <= lon <= 53.5:
                    date_str = props.get('initial_date', props.get('acq_date', 'امروز'))
                    fire_id = f"{lat}_{lon}_{date_str}"
                    if not is_fire_sent(fire_id):
                        save_fire(fire_id, lat, lon, date_str, source["name"])
                        msg = (
                            f"🔥 **هشدار آتش‌سوزی جدید در زاگرس!**\n\n"
                            f"📍 **عرض جغرافیایی:** `{lat}`\n"
                            f"📍 **طول جغرافیایی:** `{lon}`\n"
                            f"📅 **تاریخ ثبت:** {date_str}\n"
                            f"🛰 **منبع:** {source['name']}"
                        )
                        send_alert_to_all(msg, lat, lon)
        return True
    return False

def check_fires_loop():
    while True:
        print("🔍 در حال بررسی چرخشی منابع ماهواره‌ای...")
        for source in SOURCES:
            try:
                success = False
                if source["type"] == "csv":
                    success = fetch_from_csv_source(source)
                elif source["type"] == "geojson":
                    success = fetch_from_geojson_source(source)

                if success:
                    print(f"✅ داده‌ها با موفقیت از منبع [{source['name']}] دریافت شدند.")
                    break
            except Exception as e:
                print(f"❌ خطای دریافت از منبع [{source['name']}]: {e}. بررسی منبع بعدی...")

        time.sleep(600)

# ۵. مدیریت ربات تلگرام
def handle_telegram_updates():
    last_update_id = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    print("🤖 ربات پایش ۲۴/۷ زاگرس فعال شد...")
    while True:
        try:
            res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 30}, proxies=PROXIES, timeout=35)
            if res.status_code == 200:
                updates = res.json().get("result", [])
                for update in updates:
                    last_update_id = update["update_id"]
                    if "message" in update and "chat" in update["message"]:
                        chat_id = update["message"]["chat"]["id"]
                        text = update["message"].get("text", "").strip()

                        add_user(chat_id)

                        if text == "/start":
                            welcome = (
                                "سلام! 👋\n"
                                "به سامانه پایش هوشمند و خودکار آتش‌سوزی زاگرس خوش آمدید.\n\n"
                                "راهنمای دستورات:\n"
                                "🔹 /status - وضعیت سیستم و منابع فعال\n"
                                "🔹 /last_fire - دریافت آخرین گزارش ثبت‌شده"
                            )
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                          json={"chat_id": chat_id, "text": welcome}, proxies=PROXIES)

                        elif text == "/status":
                            users_count = len(get_all_users())
                            status_msg = (
                                f"🟢 **وضعیت ربات:** آنلاین و فعال (۲۴/۷)\n"
                                f"👥 **تعداد کاربران:** {users_count} نفر\n"
                                f"🌐 **تعداد منابع تحت پایش:** {len(SOURCES)} منبع معتبر جهانی\n"
                                f"📍 **منطقه پایش:** زاگرس"
                            )
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                          json={"chat_id": chat_id, "text": status_msg, "parse_mode": "Markdown"}, proxies=PROXIES)

                        elif text == "/last_fire":
                            last = get_last_fire()
                            if last:
                                lat, lon, date_str, src_name, ts = last
                                fire_msg = (
                                    f"🔥 **آخرین آتش‌سوزی ثبت‌شده:**\n\n"
                                    f"📍 **عرض جغرافیایی:** `{lat}`\n"
                                    f"📍 **طول جغرافیایی:** `{lon}`\n"
                                    f"📅 **تاریخ:** {date_str}\n"
                                    f"🛰 **منبع داده:** {src_name}\n"
                                    f"⏰ **زمان ثبت:** {ts}"
                                )
                                payload = {
                                    "chat_id": chat_id,
                                    "text": fire_msg,
                                    "parse_mode": "Markdown",
                                    "reply_markup": {
                                        "inline_keyboard": [[{"text": "🗺 مشاهده روی نقشه گوگل", "url": f"https://maps.google.com/?q={lat},{lon}"}]]
                                    }
                                }
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, proxies=PROXIES)
                            else:
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                              json={"chat_id": chat_id, "text": "هنوز هیچ داده‌ای ثبت نشده است."}, proxies=PROXIES)

        except Exception as e:
            time.sleep(5)

# ۶. نقطه شروع برنامه
if __name__ == "__main__":
    init_db()
    threading.Thread(target=check_fires_loop, daemon=True).start()
    handle_telegram_updates()
