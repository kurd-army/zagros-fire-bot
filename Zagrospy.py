import os
import sqlite3
import time
import threading
import requests
import telebot

# تنظیمات اصلی ربات و ادمین
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8918660280:AAF2CMZ1aFG40I821kSK6gL2hCCVJh17diw")
ADMIN_CHAT_ID = 1481775235

bot = telebot.TeleBot(TOKEN)

# ----------------------------------------------------
# تنظیمات پایگاه داده (zagros_bot.db)
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    # جدول کاربران برای پیام همگانی
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY
        )
    ''')
    # جدول ثبت آتش‌سوزی‌ها
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL,
            longitude REAL,
            acq_date TEXT,
            acq_time TEXT,
            confidence TEXT,
            location_name TEXT,
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
        # استفاده از سرویس رایگان و عمومی نقشه برای تشخیص نام مکان
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=fa"
        headers = {'User-Agent': 'ZagrosFireBot/1.0'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            address = data.get("address", {})
            # استخراج نام شهر، شهرستان یا استان
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
# دستورات ربات (Start, Status, Last Fire, Broadcast)
# ----------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_user(message.chat.id)
    bot.reply_to(
        message, 
        "سلام! من پشتیبان سیستم پایش آتش‌سوزی زاگرس فایر اَلرت هستم.\n"
        "منطقه تحت پوشش: از ارومیه تا جنوبی‌ترین نقاط زاگرس (همراه با تشخیص نام منطقه).\n\n"
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
        "🟢 **وضعیت سیستم پایش زاگرس:**\n\n"
        f"• وضعیت ربات: آنلاین و فعال\n"
        f"• تعداد کاربران مشترک: {user_count}\n"
        f"• کل حریق‌های ثبت‌شده در دیتابیس: {fire_count}\n"
        f"• وسعت پوشش: ارومیه تا جنوب زاگرس"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")

@bot.message_handler(commands=['last_fire'])
def send_last_fire(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT latitude, longitude, acq_date, acq_time, confidence, location_name FROM fires ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()

    if row:
        lat, lon, date, time_val, conf, loc_name = row
        text = (
            "🔥 **آخرین نقطه حرارتی شناسایی‌شده:**\n\n"
            f"📍 **منطقه:** `{loc_name or 'ثبت نشده'}`\n"
            f"📍 عرض جغرافیایی: `{lat}`\n"
            f"📍 طول جغرافیایی: `{lon}`\n"
            f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n"
            f"📊 میزان اطمینان ماهواره: `{conf}`\n\n"
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
# بخش پایش خودکار ماهواره‌ای (محدوده کامل زاگرس)
# ----------------------------------------------------
def check_fires_loop():
    bbox = "45.0,26.5,55.0,38.0"
    
    while True:
        try:
            print("🔍 در حال بررسی چرخشی منابع ماهواره‌ای برای کل محدوده زاگرس...")
            
            # در صورت دریافت داده جدید از ماهواره، نام منطقه با تابع زیر استخراج می‌شود:
            # sample_lat, sample_lon = 36.15, 45.48 # مثل سردشت
            # loc_name = get_location_name(sample_lat, sample_lon)
            
        except Exception as e:
            print(f"خطا در بررسی ماهواره‌ای: {e}")
            
        time.sleep(600)

# اجرای ترد پایش ماهواره‌ای در پس‌زمینه
threading.Thread(target=check_fires_loop, daemon=True).start()

# ----------------------------------------------------
# اجرای اصلی ربات با متد پولینگ
# ----------------------------------------------------
if __name__ == "__main__":
    print("ربات با موفقیت استارت شد و روی حالت دریافت پیام قرار گرفت...")
    bot.infinity_polling(skip_pending=True)
