import os
import sqlite3
import time
import threading
import requests
import telebot
from telebot import types
from flask import Flask

# تنظیمات اصلی ربات و ادمین
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8918660280:AAF2CMZ1aFG40I821kSK6gL2hCCVJh17diw")
ADMIN_CHAT_ID = 1481775235
NASA_MAP_KEY = "0MzpvgaGxwaZTsf7t5gHjPDcdm2lGKPnALVOQXa2"

bot = telebot.TeleBot(TOKEN)
ZAGROS_BBOX = "45.0,26.5,55.0,38.0"

# راه‌اندازی یک سرور وب بسیار سبک برای پاسخ به نیاز پورت رندر (رایگان)
app = Flask(__name__)

@app.route('/')
def home():
    return "Zagros Fire Alert Bot is Running Live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ----------------------------------------------------
# تنظیمات پایگاه داده (zagros_bot.db)
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL,
            longitude REAL,
            acq_date TEXT,
            acq_time TEXT,
            confidence TEXT,
            location_name TEXT,
            source_satellite TEXT,
            UNIQUE(latitude, longitude, acq_date, acq_time)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def add_user(chat_id):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_location_name(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=fa"
        headers = {'User-Agent': 'ZagrosFireBot/1.0'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            address = data.get("address", {})
            county = address.get("county") or address.get("city") or address.get("town") or address.get("village")
            state = address.get("state")
            parts = []
            if county: parts.append(county)
            if state: parts.append(state)
            if parts: return " - ".join(parts)
        return "منطقه نامشخص (داخل جنگل‌های زاگرس)"
    except Exception:
        return "منطقه نامشخص (خطا در اتصال به سرویس نام‌گذاری)"

def broadcast_fire_alert(fire_details):
    lat, lon, date, time_val, conf, loc_name, sat_source = fire_details
    alert_text = (
        "🚨 **هشدار فوری: شناسایی کانون حرارتی / حریق!** 🚨\n\n"
        f"📍 **منطقه:** `{loc_name}`\n"
        f"📍 عرض جغرافیایی: `{lat}`\n"
        f"📍 طول جغرافیایی: `{lon}`\n"
        f"🛰️ **منبع ماهواره‌ای:** `{sat_source}`\n"
        f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n"
        f"📊 میزان اطمینان: `{conf}`\n\n"
        f"🔗 [مشاهده دقیق نقطه روی نقشه گوگل](https://maps.google.com/?q={lat},{lon})"
    )

    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    users = cursor.fetchall()
    conn.close()

    for user in users:
        try:
            bot.send_message(user[0], alert_text, parse_mode="Markdown")
            time.sleep(0.1)
        except Exception:
            pass

@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_user(message.chat.id)
    
    # ساخت دکمه شیشه‌ای برای دسترسی سریع به وضعیت
    markup = types.InlineKeyboardMarkup()
    btn_status = types.InlineKeyboardButton("📊 مشاهده وضعیت ربات", callback_data="check_status")
    markup.add(btn_status)
    
    bot.reply_to(
        message, 
        "سلام! سیستم پایش خودکار آتش‌سوزی زاگرس فعال است.\nمنطقه تحت پوشش: ارومیه تا جنوب زاگرس.", 
        reply_markup=markup
    )

@bot.message_handler(commands=['status'])
def send_status(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    u_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fires")
    f_count = cursor.fetchone()[0]
    conn.close()
    bot.reply_to(message, f"🟢 ربات آنلاین و فعال\n• کاربران: {u_count}\n• کل حریق‌های ثبت‌شده: {f_count}", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "check_status")
def callback_status(call):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    u_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fires")
    f_count = cursor.fetchone()[0]
    conn.close()
    
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id, 
        f"🟢 ربات آنلاین و فعال\n• کاربران: {u_count}\n• کل حریق‌های ثبت‌شده: {f_count}", 
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['last_fire'])
def send_last_fire(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite FROM fires ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        lat, lon, date, time_val, conf, loc_name, sat_source = row
        bot.reply_to(message, f"🔥 **آخرین نقطه حرارتی:**\n📍 منطقه: `{loc_name}`\n🛰️ منبع: `{sat_source}`\n🔗 [نقشه](https://maps.google.com/?q={lat},{lon})", parse_mode="Markdown")
    else:
        bot.reply_to(message, "هنوز حریقی ثبت نشده است.")

@bot.message_handler(commands=['bc'])
def broadcast_message(message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    text = message.text.replace("/bc", "").strip()
    if not text:
        return
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    users = cursor.fetchall()
    conn.close()
    for u in users:
        try:
            bot.send_message(u[0], f"📢 {text}")
        except:
            pass
    bot.reply_to(message, "✅ پیام ارسال شد.")

def fetch_satellite_source(source_name):
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{NASA_MAP_KEY}/{source_name}/{ZAGROS_BBOX}/1"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            if len(lines) > 1:
                return lines[1:]
    except:
        pass
    return []

def check_fires_loop():
    sources = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"]
    while True:
        for source in sources:
            rows = fetch_satellite_source(source)
            if not rows: continue
            conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
            cursor = conn.cursor()
            for line in rows:
                values = line.split(",")
                if len(values) >= 10:
                    try:
                        lat, lon = float(values[0]), float(values[1])
                        date, time_v, conf = values[5], values[6], values[8] if len(values) > 8 else "نامشخص"
                        cursor.execute("SELECT id FROM fires WHERE latitude=? AND longitude=? AND acq_date=? AND acq_time=?", (lat, lon, date, time_v))
                        if not cursor.fetchone():
                            loc_name = get_location_name(lat, lon)
                            cursor.execute("INSERT INTO fires (latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite) VALUES (?, ?, ?, ?, ?, ?, ?)", (lat, lon, date, time_v, conf, loc_name, source))
                            conn.commit()
                            broadcast_fire_alert((lat, lon, date, time_v, conf, loc_name, source))
                    except:
                        pass
            conn.close()
        time.sleep(600)

# اجرای سرور وب در یک ترد جداگانه
threading.Thread(target=run_web, daemon=True).start()

# اجرای ترد پایش ماهواره‌ای
threading.Thread(target=check_fires_loop, daemon=True).start()

if __name__ == "__main__":
    while True:
        try:
            bot.remove_webhook()
            print("Bot is starting polling...")
            bot.infinity_polling(skip_pending=True, interval=2, timeout=20)
        except Exception as e:
            print(f"Error encountered: {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)
