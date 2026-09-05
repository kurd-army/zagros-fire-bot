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

PROVINCES = [
    "آذربایجان غربی", "کردستان", "کرمانشاه", "ایلام", 
    "لرستان", "خوزستان", "چهارمحال و بختیاری", 
    "کهگیلویه و بویراحمد", "فارس", "همدان", "همه استان‌ها"
]

POPULAR_CITIES = ["ارومیه", "سنندج", "کرمانشاه", "ایلام", "خرم‌آباد", "شهرکرد", "یاسوج", "شیراز"]

# راه‌اندازی سرور وب برای رندر
app = Flask(__name__)

@app.route('/')
def home():
    return "Zagros Fire Alert Bot is Running Live with Interactive Inline Menus!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ----------------------------------------------------
# پایگاه داده
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            province TEXT DEFAULT 'همه استان‌ها'
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
            weather_info TEXT,
            UNIQUE(latitude, longitude, acq_date, acq_time)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def add_user(chat_id):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id, province) VALUES (?, 'همه استان‌ها')", (chat_id,))
    conn.commit()
    conn.close()

def update_user_province(chat_id, province):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET province = ? WHERE chat_id = ?", (province, chat_id))
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
            if parts: return " - ".join(parts), state or "نامشخص"
        return "منطقه نامشخص (داخل جنگل‌های زاگرس)", "نامشخص"
    except Exception:
        return "منطقه نامشخص (خطا در اتصال به سرویس نام‌گذاری)", "نامشخص"

def get_weather_info(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,wind_direction_10m"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            current = response.json().get("current", {})
            temp = current.get("temperature_2m", "نامشخص")
            wind_speed = current.get("wind_speed_10m", "نامشخص")
            return f"🌡️ دما: `{temp}°C` | 💨 سرعت باد: `{wind_speed} km/h`"
    except Exception:
        pass
    return "🌡️ اطلاعات هواشناسی در دسترس نیست"

def get_city_coordinates(city_name):
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=fa"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            results = response.json().get("results")
            if results:
                lat = results[0].get("latitude")
                lon = results[0].get("longitude")
                name = results[0].get("name")
                return lat, lon, name
    except Exception:
        pass
    return None, None, None

def send_city_weather(chat_id, city_name):
    lat, lon, found_name = get_city_coordinates(city_name)
    if not lat or not lon:
        bot.send_message(chat_id, f"❌ شهر «{city_name}» پیدا نشد. لطفاً نام شهر را به درستی وارد کنید.", parse_mode="Markdown")
        return
    
    weather = get_weather_info(lat, lon)
    text = (
        f"🌤️ **وضعیت آب‌وهوای لحظه‌ای:** `{found_name}`\n"
        f"📍 عرض: `{lat}` | طول: `{lon}`\n"
        f"{weather}\n\n"
        f"🔗 [مشاهده روی نقشه گوگل](https://maps.google.com/?q={lat},{lon})"
    )
    bot.send_message(chat_id, text, parse_mode="Markdown")

def broadcast_fire_alert(fire_details, province_target):
    lat, lon, date, time_val, conf, loc_name, sat_source, weather = fire_details
    alert_text = (
        "🚨 **هشدار فوری: شناسایی کانون حرارتی / حریق!** 🚨\n\n"
        f"📍 **منطقه:** `{loc_name}`\n"
        f"📍 عرض جغرافیایی: `{lat}`\n"
        f"📍 طول جغرافیایی: `{lon}`\n"
        f"🛰️ **منبع ماهواره‌ای:** `{sat_source}`\n"
        f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n"
        f"📊 میزان اطمینان: `{conf}`\n"
        f"{weather}\n\n"
        f"🔗 [مشاهده دقیق نقطه روی نقشه گوگل](https://maps.google.com/?q={lat},{lon})"
    )

    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users WHERE province = 'همه استان‌ها' OR ? LIKE '%' || province || '%'", (province_target,))
    users = cursor.fetchall()
    conn.close()

    for user in users:
        try:
            bot.send_message(user[0], alert_text, parse_mode="Markdown")
            time.sleep(0.1)
        except Exception:
            pass

# تابع ساخت کیبورد اصلی شیشه‌ای
def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_prov = types.InlineKeyboardButton("📍 انتخاب استان من", callback_data="open_settings")
    btn_weather = types.InlineKeyboardButton("🌤️ استعلام آب‌وهوا", callback_data="open_weather_menu")
    btn_status = types.InlineKeyboardButton("📊 وضعیت ربات", callback_data="check_status")
    btn_last = types.InlineKeyboardButton("🔥 آخرین حریق ثبت‌شده", callback_data="check_last_fire")
    markup.add(btn_prov, btn_weather, btn_status, btn_last)
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_user(message.chat.id)
    bot.reply_to(
        message, 
        "سلام! سیستم پایش هوشمند آتش‌سوزی زاگرس فعال است.\n"
        "از منوی زیر برای دسترسی به امکانات ربات استفاده کنید:", 
        reply_markup=get_main_menu()
    )

@bot.message_handler(commands=['settings'])
def settings_command(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(p, callback_data=f"prov_{p}") for p in PROVINCES]
    markup.add(*buttons)
    bot.reply_to(message, "📍 لطفاً استان مورد نظر خود را برای دریافت هشدارهای آتش‌سوزی انتخاب کنید:", reply_markup=markup)

@bot.message_handler(commands=['weather', 'hava'])
def weather_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(city, callback_data=f"wcity_{city}") for city in POPULAR_CITIES]
        markup.add(*buttons)
        bot.reply_to(
            message, 
            "🌤️ لطفاً یکی از شهرهای زیر را انتخاب کنید یا نام شهر را به این شکل بنویسید:\n`/weather سنندج`", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
        return
    send_city_weather(message.chat.id, args[1].strip())

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data == "open_settings":
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(p, callback_data=f"prov_{p}") for p in PROVINCES]
        markup.add(*buttons)
        bot.answer_callback_query(call.id)
        bot.edit_message_text("📍 لطفاً استان مورد نظر خود را انتخاب کنید:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("prov_"):
        selected_province = call.data.replace("prov_", "")
        update_user_province(call.message.chat.id, selected_province)
        bot.answer_callback_query(call.id, f"استان روی «{selected_province}» تنظیم شد.")
        bot.edit_message_text(
            f"✅ استان شما با موفقیت روی **{selected_province}** تنظیم شد.\nاز این پس هشدارهای این منطقه را دریافت خواهید کرد.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        # بازگرداندن منوی اصلی پس از تنظیم
        bot.send_message(call.message.chat.id, "منوی اصلی:", reply_markup=get_main_menu())

    elif call.data == "open_weather_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(city, callback_data=f"wcity_{city}") for city in POPULAR_CITIES]
        markup.add(*buttons)
        bot.answer_callback_query(call.id)
        bot.edit_message_text("🌤️ لطفاً شهر مورد نظر خود را برای استعلام آب‌وهوا انتخاب کنید:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif call.data.startswith("wcity_"):
        city_name = call.data.replace("wcity_", "")
        bot.answer_callback_query(call.id, f"در حال دریافت آب‌وهوای {city_name}...")
        send_city_weather(call.message.chat.id, city_name)

    elif call.data == "check_status":
        conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        u_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fires")
        f_count = cursor.fetchone()[0]
        conn.close()
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"🟢 ربات آنلاین و فعال\n• کل کاربران: {u_count}\n• کل حریق‌های ثبت‌شده: {f_count}", parse_mode="Markdown")

    elif call.data == "check_last_fire":
        conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite, weather_info FROM fires ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        bot.answer_callback_query(call.id)
        if row:
            lat, lon, date, time_val, conf, loc_name, sat_source, weather = row
            text = (
                f"🔥 **آخرین نقطه حرارتی ثبت‌شده:**\n\n"
                f"📍 منطقه: `{loc_name}`\n"
                f"🛰️ منبع: `{sat_source}`\n"
                f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n"
                f"{weather}\n\n"
                f"🔗 [مشاهده روی نقشه](https://maps.google.com/?q={lat},{lon})"
            )
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, "هنوز حریقی ثبت نشده است.")

@bot.message_handler(commands=['status'])
def send_status(message):
    conn = sqlite3.connect("zagros_bot.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    u_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fires")
    f_count = cursor.fetchone()[0]
    conn.close()
    bot.reply_to(message, f"🟢 ربات آنلاین و فعال\n• کل کاربران: {u_count}\n• حریق‌های ثبت‌شده: {f_count}", parse_mode="Markdown")

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
    bot.reply_to(message, "✅ پیام همگانی ارسال شد.")

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
                            loc_name, state_name = get_location_name(lat, lon)
                            weather = get_weather_info(lat, lon)
                            cursor.execute("INSERT INTO fires (latitude, longitude, acq_date, acq_time, confidence, location_name, source_satellite, weather_info) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (lat, lon, date, time_v, conf, loc_name, source, weather))
                            conn.commit()
                            broadcast_fire_alert((lat, lon, date, time_v, conf, loc_name, source, weather), state_name)
                    except:
                        pass
            conn.close()
        time.sleep(600)

# اجرای تردها
threading.Thread(target=run_web, daemon=True).start()
threading.Thread(target=check_fires_loop, daemon=True).start()

if __name__ == "__main__":
    while True:
        try:
            bot.remove_webhook()
            print("Bot is starting polling with fully interactive inline menus...")
            bot.infinity_polling(skip_pending=True, interval=2, timeout=20)
        except Exception as e:
            print(f"Error encountered: {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)
