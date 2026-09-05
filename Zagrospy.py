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

# شهرهای پربازدید برای دسترسی سریع دکمه‌ها
POPULAR_CITIES = {
    "تهران": (35.6892, 51.3890),
    "سنندج": (35.3113, 46.9931),
    "کرمانشاه": (34.3142, 47.0650),
    "ارومیه": (37.5527, 45.0758),
    "ایلام": (33.6392, 46.4228),
    "خرم‌آباد": (33.4878, 48.3558),
    "شهرکرد": (32.3256, 50.8644),
    "یاسوج": (30.6684, 51.5876),
    "شیراز": (29.5917, 52.5836),
    "مهاباد": (36.7631, 45.7222),
    "سقز": (36.2465, 46.2730),
    "مریوان": (35.5222, 46.1753)
}

# راه‌اندازی سرور وب برای رندر
app = Flask(__name__)

@app.route('/')
def home():
    return "Zagros Fire Alert Bot is Running Live with Robust Weather API!"

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
    # تلاش اول: استفاده از سرور اصلی Open-Meteo
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
    try:
        response = requests.get(url, timeout=7)
        if response.status_code == 200:
            current = response.json().get("current", {})
            temp = current.get("temperature_2m")
            humidity = current.get("relative_humidity_2m")
            wind_speed = current.get("wind_speed_10m")
            if temp is not None:
                return f"🌡️ دما: `{temp}°C`\n💧 رطوبت: `{humidity if humidity is not None else 'رطوبت نامشخص'}%`\n💨 سرعت باد: `{wind_speed if wind_speed is not None else '0'} km/h`"
    except Exception:
        pass

    # تلاش دوم (پشتیبان): استفاده از آدرس جایگزین (Archive/Backup endpoint)
    backup_url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&current_weather=true"
    try:
        response = requests.get(backup_url, timeout=7)
        if response.status_code == 200:
            current = response.json().get("current_weather", {})
            temp = current.get("temperature")
            wind_speed = current.get("windspeed")
            if temp is not None:
                return f"🌡️ دما: `{temp}°C`\n💨 سرعت باد: `{wind_speed if wind_speed is not None else '0'} km/h`"
    except Exception:
        pass

    return "🌡️ اطلاعات هواشناسی در حال حاضر در دسترس نیست (خطا در ارتباط با سرور آب‌وهوا)"

def get_city_coordinates(city_name):
    city_name = city_name.strip()
    
    # ۱. بررسی اولیه در لیست شهرهای محبوب فارسی
    if city_name in POPULAR_CITIES:
        return POPULAR_CITIES[city_name][0], POPULAR_CITIES[city_name][1], city_name
    
    # ۲. بررسی برای حالت انگلیسی شهرهای محبوب
    city_title_case = city_name.capitalize()
    popular_english = {
        "Tehran": ("تهران", 35.6892, 51.3890),
        "Sanandaj": ("سنندج", 35.3113, 46.9931),
        "Kermanshah": ("کرمانشاه", 34.3142, 47.0650),
        "Urmia": ("ارومیه", 37.5527, 45.0758),
        "Ilam": ("ایلام", 33.6392, 46.4228),
        "Khorramabad": ("خرم‌آباد", 33.4878, 48.3558),
        "Shahrekord": ("شهرکرد", 32.3256, 50.8644),
        "Yasuj": ("یاسوج", 30.6684, 51.5876),
        "Shiraz": ("شیراز", 29.5917, 52.5836),
        "Mahabad": ("مهاباد", 36.7631, 45.7222),
        "Saqqez": ("سقز", 36.2465, 46.2730),
        "Marivan": ("مریوان", 35.5222, 46.1753)
    }
    if city_title_case in popular_english:
        p_name, lat, lon = popular_english[city_title_case]
        return lat, lon, p_name

    # ۳. جستجوی آنلاین سراسری
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=10&language=fa"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            results = response.json().get("results")
            if results:
                for res in results:
                    if res.get("country_code") == "IR":
                        name = res.get("name")
                        admin1 = res.get("admin1", "")
                        full_name = f"{name} ({admin1})" if admin1 else name
                        return res.get("latitude"), res.get("longitude"), full_name
                return results[0].get("latitude"), results[0].get("longitude"), results[0].get("name")
    except Exception:
        pass
        
    return None, None, None

def send_city_weather(chat_id, city_name):
    lat, lon, found_name = get_city_coordinates(city_name)
    if not lat or not lon:
        bot.send_message(
            chat_id, 
            f"❌ شهر یا منطقه «{city_name}» پیدا نشد.\n\n"
            "لطفاً نام شهر را به درستی وارد کنید. مثال:\n"
            "🇮🇷 فارسی: `/weather شیراز`\n"
            "🇬🇧 انگلیسی: `/weather Shiraz`", 
            parse_mode="Markdown"
        )
        return
    
    weather = get_weather_info(lat, lon)
    text = (
        f"🌤️ **وضعیت آب‌وهوای لحظه‌ای:** `{found_name}`\n"
        f"📍 عرض: `{lat}` | طول: `{lon}`\n\n"
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
        f"📊 میزان اطمینان: `{conf}`\n\n"
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
        "سلام! سیستم پایش هوشمند آتش‌سوزی زاگرس فعال است.\n\n"
        "💡 **راهنمای آب‌وهوا:**\n"
        "شما می‌توانید نام هر شهری را به **فارسی** یا **انگلیسی** ارسال کنید:\n"
        "• `/weather Tehran`\n"
        "• `/weather مشهد`", 
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
        buttons = [types.InlineKeyboardButton(city, callback_data=f"wcity_{city}") for city in POPULAR_CITIES.keys()]
        markup.add(*buttons)
        bot.reply_to(
            message, 
            "🌤️ لطفاً نام شهر را به فارسی یا انگلیسی بعد از دستور بنویسید:\n"
            "مثال: `/weather Isfahan` یا `/weather تبریز`", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
        return
    send_city_weather(message.chat.id, args[1].strip())

@bot.message_handler(func=lambda message: not message.text.startswith('/') and len(message.text.strip()) > 1 and len(message.text.strip()) < 30)
def handle_text_city_search(message):
    text = message.text.strip()
    lat, lon, found_name = get_city_coordinates(text)
    if lat and lon:
        weather = get_weather_info(lat, lon)
        reply_text = (
            f"🌤️ **آب‌وهوای منطقه:** `{found_name}`\n"
            f"{weather}\n\n"
            f"🔗 [مشاهده روی نقشه گوگل](https://maps.google.com/?q={lat},{lon})"
        )
        bot.reply_to(message, reply_text, parse_mode="Markdown")

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
            f"✅ استان شما با موفقیت روی **{selected_province}** تنظیم شد.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        bot.send_message(call.message.chat.id, "منوی اصلی:", reply_markup=get_main_menu())

    elif call.data == "open_weather_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(city, callback_data=f"wcity_{city}") for city in POPULAR_CITIES.keys()]
        markup.add(*buttons)
        bot.answer_callback_query(call.id)
        bot.edit_message_text("🌤️ یکی از شهرها را انتخاب کنید یا نام هر شهر دیگری را به فارسی یا انگلیسی تایپ کنید:", call.message.chat.id, call.message.message_id, reply_markup=markup)

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
                f"📅 تاریخ: `{date}` | زمان: `{time_val}`\n\n"
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
            print("Bot is starting polling with robust weather backup system...")
            bot.infinity_polling(skip_pending=True, interval=2, timeout=20)
        except Exception as e:
            print(f"Error encountered: {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)
