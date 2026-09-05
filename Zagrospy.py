import os
import sqlite3
import time
import threading
import requests
import telebot

# تنظیمات اصلی ربات و ادمین
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8918660280:AAF2CMZ1aFG40I821kSK6gL2hCCVJh17diw")
ADMIN_CHAT_ID = 1481775235

# کلید API ناسا (FIRMS) شما
NASA_MAP_KEY = "0MzpvgaGxwaZTsf7t5gHjPDcdm2lGKPnALVOQXa2"

bot = telebot.TeleBot(TOKEN)

# محدوده جغرافیایی کامل زاگرس (عرض: ۲۶.۵ تا ۳۸.۰ | طول: ۴۵.۰ تا ۵۵.۰)
ZAGROS_BBOX = "45.0,26.5,55.0,38.0"

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

# ----------------------------------------------------
# تابع کمکی برای پیدا کردن نام منطقه از روی مختصات
# ----------------------------------------------------
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
            if county:
                parts.append(county)
            if state:
                parts.append(state)
                
            if parts:
                return " - ".join(parts)
        return "منطقه نامشخص (داخل جنگل‌های زاگرس)"
    except Exception:
        return "منطقه نامشخص (خطا در اتصال به سرویس نام‌گذاری)"

# ----------------------------------------------------
# تابع ارسال هشدار فوری به کاربران
# ----------------------------------------------------
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
        chat_id = user[0]
        try:
            bot.send_message(chat_id, alert_text, parse_mode="Markdown")
            time.sleep(0.1)
        except Exception:
            pass

# ----------------------------------------------------
# دستورات ربات (Start, Status, Last Fire, Broadcast)
# ----------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_user(message.chat.id)
    bot.reply_to(
        message, 
        "سلام! سیستم پایش چندماهه و جامع آتش‌سوزی زاگرس فعال است.\n"
        "این ربات از چندین منبع ماهواره‌ای معتبر (VIIRS و MODIS) برای رصد لحظه‌ای استفاده می‌کند.\n\n"
        "دستورات موجود:\n"
        "/status - بررسی وضعیت ربات و آمار\n"
        "/last_fire - نمایش آخرین نقطه حرارتی ثبت‌شده"
    )

@bot.message_handler(commands=['status'])
def send_status(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fires")
    fire_count = cursor.fetchone()[0]
    conn.close()

    status_text = (
        "🟢 **وضعیت سیستم پایش چندمنبعی زاگرس:**\n\n"
        f"• وضعیت ربات: آنلاین و در حال پایش با چندین ماهواره\n"
        f"• تعداد کاربران مشترک: {user_count}\n"
        f"• کل حریق‌های ثبت‌شده: {fire_count}\n"
        f"• محدوده تحت پوشش: از ارومیه تا جنوب زاگرس"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")

@bot.message_handler(commands=['last_fire'])
def send_last_fire(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite FROM fires ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()

    if row:
        lat, lon, date, time_val, conf, loc_name, sat_source = row
        text = (
            "🔥 **آخرین نقطه حرارتی شناسایی‌شده:**\n\n"
            f"📍 **منطقه:** `{loc_name or 'ثبت نشده'}`\n"
            f"📍 عرض جغرافیایی: `{lat}`\n"
            f"📍 طول جغرافیایی: `{lon}`\n"
            f"🛰️ منبع: `{sat_source}`\n"
            f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n"
            f"📊 میزان اطمینان: `{conf}`\n\n"
            f"🔗 [مشاهده روی نقشه گوگل](https://maps.google.com/?q={lat},{lon})"
        )
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.reply_to(message, "هنوز هیچ داده‌ای از آتش‌سوزی در منطقه ثبت نشده است.")

@bot.message_handler(commands=['bc'])
def broadcast_message(message):
    if message.from_user.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ شما دسترسی لازم برای اجرای این دستور را ندارید.")
        return

    text_to_send = message.text.replace("/bc", "").strip()
    if not text_to_send:
        bot.reply_to(message, "لطفاً متن پیام خود را بعد از دستور /bc بنویسید.")
        return

    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    users = cursor.fetchall()
    conn.close()

    success_count = 0
    fail_count = 0

    for user in users:
        chat_id = user[0]
        try:
            bot.send_message(chat_id, f"📢 **پیام همگانی از مدیریت:**\n\n{text_to_send}", parse_mode="Markdown")
            success_count += 1
            time.sleep(0.1)
        except Exception:
            fail_count += 1

    bot.reply_to(message, f"✅ پیام با موفقیت ارسال شد.\nموفق: {success_count}\nناموفق: {fail_count}")

# ----------------------------------------------------
# حلقه جامع پایش چندمنبعی ماهواره‌ای
# ----------------------------------------------------
def fetch_satellite_source(source_name, day_range="1"):
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{NASA_MAP_KEY}/{source_name}/{ZAGROS_BBOX}/{day_range}"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            if len(lines) > 1:
                return lines[1:] # برگرداندن داده‌ها به جز هدر
    except Exception as e:
        print(f"خطا در دریافت از منبع {source_name}: {e}")
    return []

def check_fires_loop():
    # لیست منابع معتبر ماهواره‌ای که به صورت موازی بررسی می‌شوند
    # VIIRS با دقت بالا (۳۷۵ متری) و MODIS با پوشش عمومی
    sources = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"]

    while True:
        print("🔍 در حال بررسی هم‌زمان تمام منابع معتبر ماهواره‌ای برای کل زاگرس...")
        
        for source in sources:
            rows = fetch_satellite_source(source, day_range="1")
            if not rows:
                continue
                
            conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
            cursor = conn.cursor()
            
            for line in rows:
                values = line.split(",")
                if len(values) >= 10:
                    try:
                        lat = float(values[0])
                        lon = float(values[1])
                        acq_date = values[5]
                        acq_time = values[6]
                        confidence = values[8] if len(values) > 8 else "نامشخص"
                        
                        # بررسی تکراری نبودن نقطه حرارتی در دیتابیس
                        cursor.execute(
                            "SELECT id FROM fires WHERE latitude=? AND longitude=? AND acq_date=? AND acq_time=?",
                            (lat, lon, acq_date, acq_time)
                        )
                        exists = cursor.fetchone()
                        
                        if not exists:
                            # استخراج نام منطقه (مانند سردشت، مریوان و...)
                            loc_name = get_location_name(lat, lon)
                            
                            # ذخیره در دیتابیس با ذکر نام منبع ماهواره‌ای
                            cursor.execute(
                                "INSERT INTO fires (latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (lat, lon, acq_date, acq_time, confidence, loc_name, source)
                            )
                            conn.commit()
                            
                            # ارسال هشدار خودکار به همه کاربران
                            fire_data = (lat, lon, acq_date, acq_time, confidence, loc_name, source)
                            broadcast_fire_alert(fire_data)
                            print(f"🔥 حریق جدید از منبع {source} ثبت و هشدار ارسال شد: {loc_name} ({lat}, {lon})")
                            
                    except Exception as inner_e:
                        print(f"خطا در پردازش داده سطر: {inner_e}")
                        
            conn.close()
            
        # استراحت بین هر دوره پایش (هر ۱۰ دقیقه یک‌بار)
        time.sleep(600)

# اجرای ترد پایش در پس‌زمینه
threading.Thread(target=check_fires_loop, daemon=True).start()

# ----------------------------------------------------
# اجرای اصلی ربات
# ----------------------------------------------------
if __name__ == "__main__":
    print("ربات چندمنبعی با موفقیت استارت شد...")
    bot.infinity_polling(skip_pending=True)
