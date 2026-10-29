import tkinter as tk
import time
import random
import requests
import threading
import speech_recognition as sr
import subprocess
import os
import re
import json
import atexit
from datetime import datetime, timedelta
from google import genai

# --- CONFIGURATION ---
WEATHER_API_KEY = "f104c4b8251a042efaca44820f294ae8"  # OpenWeatherMap API key
AI_API_KEY = "AQ.Ab8RN6K9ReO1Kc-dgXHHuSEsUUIBA-qOBE4MeF9obMSJUNwEhQ"  # Put your Google AI Studio API key here

# Path to your custom MP3 alarm sound file
CHIME_SOUND_PATH = "/home/mlurye/Sounds/alarm-clock-1.mp3"
REMINDERS_FILE = "reminders.json"

command_list_text = (
    "Доступные голосовые команды:\n"
    "• Алиса, погода / сегодня / завтра / неделя\n"
    "• Алиса, который час\n"
    "• Алиса, будильник на [время]\n"
    "• Алиса, выключи будильник\n"
    "• Алиса, выключи экран / включи экран\n"
    "• Алиса, выключить компьютер\n"
    "• Алиса, список команд\n"
    "• Алиса, обновись\n"
    "• Алиса, прочти\n"
    "• Алиса, напомни [текст] через [X] минут/часов\n"
    "• Алиса, напомни [текст] [дата] в [время]\n"
    "• Алиса, [ваш вопрос для ИИ]"
)

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
}

def get_city_by_ip():
    try:
        response = requests.get("https://ipinfo.io/json", timeout=5)
        if response.status_code == 200:
            data = response.json()
            city = data.get("city")
            if city:
                print(f"Город успешно определен по IP: {city}")
                return city
    except Exception as e:
        print(f"Не удалось определить город по IP: {e}")
    return "Boston"

CITY = get_city_by_ip()
# ---------------------

current_weather_text = "Погода еще не загружена"
today_hourly_forecast = "Почасовой прогноз на сегодня загружается..."
tomorrow_hourly_forecast = "Почасовой прогноз на завтра загружается..."
weekly_forecast_text = "Прогноз на неделю загружается..."

alarm_time = None
alarm_triggered_today = False
alarm_active = False
alarm_stop_event = threading.Event()
active_audio_process = None

display_mode = "clock"
weather_timer = None

def stop_all_audio():
    global alarm_active, active_audio_process
    alarm_active = False
    alarm_stop_event.set()
    if active_audio_process:
        try:
            active_audio_process.terminate()
            active_audio_process.kill()
        except Exception:
            pass
        active_audio_process = None
    os.system("pkill -9 play 2>/dev/null")
    os.system("pkill -9 mpg123 2>/dev/null")
    os.system("pkill -9 aplay 2>/dev/null")
    os.system("pkill -9 paplay 2>/dev/null")

atexit.register(stop_all_audio)

def load_reminders_from_disk():
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения reminders.json: {e}")
        return []

def save_reminders_to_disk(reminders):
    try:
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка записи reminders.json: {e}")

def add_reminder_to_storage(text, target_timestamp, reminder_type="one-time"):
    reminders = load_reminders_from_disk()
    reminders.append({
        "text": text, 
        "target_timestamp": target_timestamp,
        "type": reminder_type
    })
    save_reminders_to_disk(reminders)

def remove_reminder_from_storage(target_timestamp, text):
    reminders = load_reminders_from_disk()
    reminders = [r for r in reminders if not (r["text"] == text and r["target_timestamp"] == target_timestamp)]
    save_reminders_to_disk(reminders)

def update_daily_reminder_in_storage(old_timestamp, new_timestamp, text):
    reminders = load_reminders_from_disk()
    for r in reminders:
        if r["text"] == text and r["target_timestamp"] == old_timestamp:
            r["target_timestamp"] = new_timestamp
            break
    save_reminders_to_disk(reminders)

def schedule_reminder_thread(delay, text, target_timestamp, reminder_type):
    def reminder_job():
        time.sleep(max(0, delay))
        speak_russian(f"Напоминание: {text}")
        
        if reminder_type == "daily":
            next_timestamp = target_timestamp + 86400
            update_daily_reminder_in_storage(target_timestamp, next_timestamp, text)
            schedule_reminder_thread(86400, text, next_timestamp, "daily")
        else:
            remove_reminder_from_storage(target_timestamp, text)

    threading.Thread(target=reminder_job, daemon=True).start()

def set_reminder(reminder_text, delay_seconds, reminder_type="one-time"):
    target_timestamp = time.time() + delay_seconds
    add_reminder_to_storage(reminder_text, target_timestamp, reminder_type)
    if reminder_type == "one-time":
        speak_russian("Напоминание установлено.")
    schedule_reminder_thread(delay_seconds, reminder_text, target_timestamp, reminder_type)

def schedule_advanced_reminder(reminder_text, event_datetime):
    """
    Schedules:
    1. Day before at 21:10
    2. Day of event at 9:10
    3. Exact event time
    """
    now = datetime.now()
    event_timestamp = event_datetime.timestamp()
    
    # 1. Day before at 21:10
    day_before = event_datetime - timedelta(days=1)
    day_before_dt = day_before.replace(hour=21, minute=10, second=0, microsecond=0)
    if day_before_dt > now:
        delay_1 = (day_before_dt - now).total_seconds()
        text_1 = f"Накануне: {reminder_text}"
        add_reminder_to_storage(text_1, day_before_dt.timestamp(), "advanced-pre")
        schedule_reminder_thread(delay_1, text_1, day_before_dt.timestamp(), "one-time")

    # 2. Day of event at 9:10
    day_of_dt = event_datetime.replace(hour=9, minute=10, second=0, microsecond=0)
    if day_of_dt > now:
        delay_2 = (day_of_dt - now).total_seconds()
        text_2 = f"Сегодня: {reminder_text}"
        add_reminder_to_storage(text_2, day_of_dt.timestamp(), "advanced-pre")
        schedule_reminder_thread(delay_2, text_2, day_of_dt.timestamp(), "one-time")

    # 3. Exact event time
    delay_event = (event_datetime - now).total_seconds()
    if delay_event > 0:
        add_reminder_to_storage(reminder_text, event_timestamp, "advanced-event")
        schedule_reminder_thread(delay_event, reminder_text, event_timestamp, "one-time")
        
    speak_russian("Расширенное напоминание установлено.")

def parse_date_time_reminder(query):
    """
    Parses queries like: 'напомни визит к врачу 12 ноября в 10:15'
    """
    # Extract time
    time_match = re.search(r'(?:в\s+)?(\d{1,2})[:\s](\d{2})', query)
    hour, minute = 10, 0
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))

    # Extract day and month
    now = datetime.now()
    target_day = now.day
    target_month = now.month
    
    day_match = re.search(r'\b(\d{1,2})\b', query)
    if day_match:
        potential_day = int(day_match.group(1))
        if 1 <= potential_day <= 31:
            target_day = potential_day

    for month_name, m_num in MONTHS_RU.items():
        if month_name in query:
            target_month = m_num
            break

    try:
        target_dt = datetime(now.year, target_month, target_day, hour, minute, 0)
        if target_dt < now:
            # If date has passed this year, assume next year
            target_dt = datetime(now.year + 1, target_month, target_day, hour, minute, 0)
        return target_dt
    except Exception as e:
        print(f"Date parse error: {e}")
        return None

def setup_default_daily_reminders():
    reminders = load_reminders_from_disk()
    pill_text = "Пожалуйста, возьмите ваши таблетки"
    target_times = [("09", "10"), ("21", "10")]
    now = datetime.now()
    
    for hour_str, min_str in target_times:
        hour = int(hour_str)
        minute = int(min_str)
        
        exists = any(r.get("type") == "daily" and r["text"] == pill_text and 
                     datetime.fromtimestamp(r["target_timestamp"]).strftime('%H:%M') == f"{hour_str}:{min_str}" 
                     for r in reminders)
        
        if not exists:
            target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target_dt <= now:
                target_dt += timedelta(days=1)
            
            target_timestamp = target_dt.timestamp()
            add_reminder_to_storage(pill_text, target_timestamp, "daily")

def restore_pending_reminders():
    setup_default_daily_reminders()
    reminders = load_reminders_from_disk()
    now = time.time()
    remaining_reminders = []
    
    for r in reminders:
        text = r["text"]
        target_timestamp = r["target_timestamp"]
        rem_type = r.get("type", "one-time")
        delay = target_timestamp - now
        
        if delay > 0:
            remaining_reminders.append(r)
            schedule_reminder_thread(delay, text, target_timestamp, rem_type)
        else:
            if rem_type == "daily":
                while target_timestamp <= now:
                    target_timestamp += 86400
                r["target_timestamp"] = target_timestamp
                remaining_reminders.append(r)
                new_delay = target_timestamp - now
                schedule_reminder_thread(new_delay, text, target_timestamp, "daily")
            else:
                # Skip expired one-time/advanced items or report them
                pass
                
    save_reminders_to_disk(remaining_reminders)

def update_display():
    global alarm_time, alarm_triggered_today, display_mode
    
    if display_mode == "clock":
        days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        months_ru = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
        
        t = time.localtime()
        day_name = days_ru[t.tm_wday]
        day_num = t.tm_mday
        month_name = months_ru[t.tm_mon]
        
        date_str = f"{day_name}, {day_num} {month_name}"
        time_str = time.strftime('%H:%M:%S')
        current_hm = time.strftime('%H:%M')
        
        date_label.config(text=date_str)
        time_label.config(text=time_str, font=('Helvetica', 110, 'bold'))
        
        formatted_weather = f"Погода в {CITY}: {current_weather_text}"
        weather_label.config(text=formatted_weather, font=('Helvetica', 28, 'bold'), fg='lightseagreen')
        
        if alarm_time and current_hm == alarm_time and not alarm_triggered_today:
            alarm_triggered_today = True
            threading.Thread(target=trigger_alarm, daemon=True).start()
        elif current_hm != alarm_time:
            alarm_triggered_today = False
            
    root.after(100, update_display)

def shift_position():
    if display_mode == "clock":
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        max_x = max(50, screen_width - 650)
        max_y = max(50, screen_height - 400)
        
        new_x = random.randint(40, max_x)
        new_y = random.randint(40, max_y)
        
        frame.place(x=new_x, y=new_y)
    root.after(30000, shift_position)

def show_weather_screen_only(text_content, announcement_text):
    global display_mode, weather_timer
    display_mode = "weather"
    
    frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)
    
    if weather_timer and weather_timer.is_alive():
        weather_timer.cancel()
        
    date_label.config(text=f"Прогноз погоды ({CITY})")
    time_label.config(text="", font=('Helvetica', 10))
    weather_label.config(text=text_content, font=('Helvetica', 22, 'bold'), fg='white')
    
    speak_russian(announcement_text)
    
    weather_timer = threading.Timer(60.0, restore_clock_mode)
    weather_timer.start()

def show_command_list_screen(text_content, announcement_text):
    global display_mode, weather_timer
    display_mode = "weather"
    
    frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)
    
    if weather_timer and weather_timer.is_alive():
        weather_timer.cancel()
        
    date_label.config(text="Справочник команд")
    time_label.config(text="", font=('Helvetica', 10))
    weather_label.config(text=text_content, font=('Helvetica', 20, 'bold'), fg='white')
    
    speak_russian(announcement_text)
    
    weather_timer = threading.Timer(60.0, restore_clock_mode)
    weather_timer.start()

def restore_clock_mode():
    global display_mode
    display_mode = "clock"

def trigger_alarm():
    global alarm_active, active_audio_process, alarm_time
    alarm_active = True
    alarm_stop_event.clear()
    
    for repetition in range(2):
        if not alarm_active or alarm_stop_event.is_set():
            break
            
        try:
            if os.path.exists(CHIME_SOUND_PATH):
                active_audio_process = subprocess.Popen(
                    ["mpg123", "-q", CHIME_SOUND_PATH],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                active_audio_process = subprocess.Popen(
                    ["play", "-q", "-n", "synth", "0.3", "bell", "523"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            active_audio_process.wait()
        except Exception:
            pass
            
        for _ in range(50):
            if not alarm_active or alarm_stop_event.is_set():
                break
            time.sleep(0.1)
            
    alarm_active = False
    alarm_time = None

GITHUB_RAW_URL = "https://raw.githubusercontent.com/mlurye69/bigclock/main/bigclock.py"

def check_for_updates():
    try:
        response = requests.get(GITHUB_RAW_URL, timeout=10)
        if response.status_code == 200:
            remote_code = response.text
            script_path = os.path.abspath(__file__)
            
            with open(script_path, "r", encoding="utf-8") as f:
                local_code = f.read()
                
            if remote_code.strip() != local_code.strip():
                print("Обнаружено обновление на GitHub. Применяю...")
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(remote_code)
                speak_russian("Обновление загружено. Перезапускаю часы.")
                os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        print(f"Ошибка проверки обновлений: {e}")

def fetch_weather():
    global current_weather_text, tomorrow_hourly_forecast, weekly_forecast_text, today_hourly_forecast
    if WEATHER_API_KEY != "YOUR_WEATHER_API_KEY":
        try:
            url_current = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
            resp_curr = requests.get(url_current, timeout=10)
            if resp_curr.status_code == 200:
                data = resp_curr.json()
                temp = round(data["main"]["temp"])
                desc = data["weather"][0]["description"].capitalize()
                current_weather_text = f"{desc}, {temp}°C"

            url_forecast = f"https://api.openweathermap.org/data/2.5/forecast?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
            resp_fore = requests.get(url_forecast, timeout=10)
            if resp_fore.status_code == 200:
                fdata = resp_fore.json()
                today_date = datetime.now().strftime('%Y-%m-%d')
                tomorrow_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                
                today_lines = []
                tomorrow_lines = []
                weekly_lines = []
                seen_dates = set()
                
                days_full_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                
                for item in fdata.get("list", []):
                    dt_txt = item["dt_txt"]
                    item_date, item_time = dt_txt.split()
                    hour_str = item_time[:5]
                    temp_val = round(item["main"]["temp"])
                    desc_val = item["weather"][0]["description"]
                    
                    line = f"{hour_str}: {desc_val}, {temp_val}°C"
                    
                    if item_date == today_date:
                        today_lines.append(line)
                    elif item_date == tomorrow_date:
                        tomorrow_lines.append(line)
                        
                    if item_date not in seen_dates and "12:00" in item_time:
                        seen_dates.add(item_date)
                        dt_obj = datetime.strptime(item_date, '%Y-%m-%d')
                        day_of_week = days_full_ru[dt_obj.weekday()]
                        weekly_lines.append(f"{day_of_week}: {desc_val}, {temp_val}°C")
                
                if today_lines:
                    today_hourly_forecast = "\n".join(today_lines)
                else:
                    today_hourly_forecast = "Почасовой прогноз на сегодня недоступен."
                    
                if tomorrow_lines:
                    tomorrow_hourly_forecast = "\n".join(tomorrow_lines)
                else:
                    tomorrow_hourly_forecast = "Почасовой прогноз на завтра недоступен."
                    
                if weekly_lines:
                    weekly_forecast_text = "\n".join(weekly_lines)
                else:
                    weekly_forecast_text = "Прогноз на неделю недоступен."
                
        except Exception as e:
            print(f"Weather fetch error: {e}")
    else:
        current_weather_text = "API ключ погоды не настроен"
    
    root.after(600000, fetch_weather)

def speak_russian(text):
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang='ru', slow=False)
        tts.save("response.mp3")
        subprocess.run(["mpg123", "-q", "response.mp3"])
        if os.path.exists("response.mp3"):
            os.remove("response.mp3")
    except Exception as e:
        print(f"Ошибка речи: {e}")

def ask_ai(prompt):
    if AI_API_KEY == "YOUR_GEMINI_API_KEY":
        return "Пожалуйста, настройте API ключ искусственного интеллекта в коде."
    
    client = genai.Client(api_key=AI_API_KEY)
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=(
                    "Ты голосовой ассистент на умных часах. Отвечай кратко, по делу, "
                    f"исключительно на русском языке, максимум в 1-2 предложениях. Вопрос: {prompt}"
                )
            )
            if response.text:
                return response.text
            else:
                return "Не удалось получить ответ от сервера искусственного интеллекта."
        except Exception as e:
            print(f"AI error (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                return "Сервер искусственного интеллекта перегружен. Попробуйте позже."

def parse_alarm_time(query):
    time_match = re.search(r'(\d{1,2})[:\s](\d{2})', query)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return f"{hour:02d}:{minute:02d}"
            
    hour_match = re.search(r'(?:на|в)\s+(\d{1,2})\b', query)
    if hour_match:
        hour = int(hour_match.group(1))
        if 0 <= hour < 24:
            return f"{hour:02d}:00"
            
    return None

def parse_reminder_delay(query):
    match = re.search(r'через\s+(\d+)\s*(мин|м|час|ч|сек|с)', query)
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        if unit.startswith('час') or unit.startswith('ч'):
            return val * 3600
        elif unit.startswith('сек') or unit.startswith('с'):
            return val
        else:
            return val * 60
    return None

def process_command(query):
    global alarm_time, alarm_active, display_mode
    print(f"Распознано: {query}")
    
    if alarm_active and any(w in query for w in ["выключ", "отмен", "убер", "сброс"]):
        stop_all_audio()
        alarm_time = None
        speak_russian("Будильник выключен.")
        return

    wake_word = "алиса"
    if wake_word not in query and "alisa" not in query:
        return
        
    command = query.replace("алиса", "").replace("alisa", "").strip(" ,.!?-")
    print(f"Команда: {command}")
    
    if not command:
        speak_russian("Слушаю вас.")
    elif "выключить компьютер" in command or "выключи компьютер" in command:
        speak_russian("Выключаю компьютер.")
        stop_all_audio()
        os.system("sudo shutdown now")
    elif "выключить экран" in command or "выключи экран" in command:
        speak_russian("Выключаю экран.")
        os.system("xrandr --output LVDS-1 --off")
    elif "включить экран" in command or "включи экран" in command:
        speak_russian("Включаю экран.")
        os.system("xrandr --output LVDS-1 --auto")
    elif "выход" in command:
        speak_russian("Выключаю часы.")
        stop_all_audio()
        root.destroy()
    elif "будильник" in command or "разбуди" in command:
        if any(w in command for w in ["выключ", "отмен", "убер", "сброс"]):
            stop_all_audio()
            if alarm_time:
                alarm_time = None
                speak_russian("Будильник выключен.")
            else:
                speak_russian("Будильник остановлен.")
        else:
            parsed = parse_alarm_time(command)
            if parsed:
                alarm_time = parsed
                speak_russian(f"Будильник установлен на {alarm_time}")
            else:
                speak_russian("Укажите время для будильника, например, на 7:30.")
    elif "напомни" in command or "напомнить" in command:
        delay = parse_reminder_delay(command)
        if delay:
            clean_cmd = re.sub(r'напомнить?', '', command).strip()
            text_to_remind = re.split(r'через\s+\d+', clean_cmd)[0].strip(" ,.-")
            if not text_to_remind:
                text_to_remind = "сделать запланированное"
            set_reminder(text_to_remind, delay, "one-time")
        else:
            # Try parsing advanced reminder with specific date/time
            event_dt = parse_date_time_reminder(command)
            if event_dt:
                clean_cmd = re.sub(r'напомнить?', '', command).strip()
                # Remove date/time tokens to isolate text
                for m_name in MONTHS_RU.keys():
                    clean_cmd = clean_cmd.replace(m_name, "")
                clean_cmd = re.sub(r'\d{1,2}[:\s]\d{2}', '', clean_cmd)
                clean_cmd = re.sub(r'\b\d{1,2}\b', '', clean_cmd)
                clean_cmd = clean_cmd.replace("в", "").replace("на", "").strip(" ,.-")
                
                if not clean_cmd:
                    clean_cmd = "Важное событие"
                schedule_advanced_reminder(clean_cmd, event_dt)
            else:
                speak_russian("Укажите время или дату для напоминания.")
    elif any(w in command for w in ["прочти", "читай", "что на экране", "прочитать"]):
        if display_mode == "clock":
            days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            months_ru = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
            t = time.localtime()
            day_name = days_ru[t.tm_wday]
            day_num = t.tm_mday
            month_name = months_ru[t.tm_mon]
            current_time = time.strftime('%H:%M')
            text_to_read = f"Сегодня {day_name}, {day_num} {month_name}. Время {current_time}. Погода в городе {CITY}: {current_weather_text}"
            speak_russian(text_to_read)
        else:
            screen_title = date_label.cget("text")
            screen_body = weather_label.cget("text")
            speak_russian(f"{screen_title}. {screen_body}")
    elif any(w in command for w in ["список команд", "список", "команды", "справка"]):
        show_command_list_screen(command_list_text, "Показываю список доступных команд")
    elif any(w in command for w in ["недел", "неделю"]):
        show_weather_screen_only(weekly_forecast_text, "Показываю погоду на неделю")
    elif "завтра" in command:
        show_weather_screen_only(tomorrow_hourly_forecast, "Показываю погоду на завтра")
    elif "сегодня" in command:
        show_weather_screen_only(today_hourly_forecast, "Почасовой прогноз на сегодня")
    elif any(word in command for word in ["погод", "температур", "градус"]):
        show_weather_screen_only(current_weather_text, "Показываю текущую погоду")
    elif any(word in command for word in ["времен", "час"]):
        current_time = time.strftime('%H:%M')
        speak_russian(f"Текущее время {current_time}")
    else:
        ai_answer = ask_ai(command)
        speak_russian(ai_answer)

def voice_listener_loop():
    import pyaudio
    
    CHUNK = 4096
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    RECORD_SECONDS = 8
    
    p = pyaudio.PyAudio()
    
    print("Слушаю микрофон (непрерывная запись через PyAudio)...")
    
    while True:
        stream = None
        try:
            stream = p.open(format=FORMAT,
                            channels=CHANNELS,
                            rate=RATE,
                            input=True,
                            frames_per_buffer=CHUNK)
            
            frames = []
            total_chunks = int((RATE / CHUNK) * RECORD_SECONDS)
            
            for _ in range(total_chunks):
                data = stream.read(CHUNK)
                if data:
                    frames.append(data)
                
            stream.stop_stream()
            stream.close()
            stream = None
            
            if not frames:
                continue
                
            audio_data = sr.AudioData(b''.join(frames), RATE, 2)
            
            r = sr.Recognizer()
            query = r.recognize_google(audio_data, language="ru-RU").lower()
            process_command(query)
            
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            print(f"Ошибка сервиса распознавания речи: {e}")
            time.sleep(2)
        except Exception as e:
            print(f"Ошибка аудиопотока: {e}")
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except:
                    pass
            time.sleep(1)

# --- GUI SETUP ---
root = tk.Tk()
root.title("Smart Clock")
root.attributes('-fullscreen', True)
root.configure(background='black')

def on_closing():
    stop_all_audio()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)

frame = tk.Frame(root, bg='black')
frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)

weather_label = tk.Label(frame, font=('Helvetica', 22, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w')
weather_label.pack(anchor='w', pady=(0, 10))

date_label = tk.Label(frame, font=('Helvetica', 32, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
date_label.pack(anchor='w', pady=(0, 15))

time_label = tk.Label(frame, font=('Helvetica', 110, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
time_label.pack(anchor='w', pady=(0, 0))

root.bind('<Escape>', lambda e: on_closing())

update_display()
fetch_weather()
shift_position()
restore_pending_reminders()

voice_thread = threading.Thread(target=voice_listener_loop, daemon=True)
voice_thread.start()

# Add this right before root.mainloop()
root.after(5000, check_for_updates)
root.mainloop()
