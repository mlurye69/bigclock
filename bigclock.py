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
import sys
import atexit
from datetime import datetime, timedelta

# --- PATH SETUP ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "big_clock.json")
REMINDERS_FILE = os.path.join(SCRIPT_DIR, "reminders.json")
CHIME_SOUND_PATH = os.path.join(SCRIPT_DIR, "Sounds", "alarm-clock-1.mp3")

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12
}

COMMAND_LIST_TEXT = (
    "Доступные голосовые команды:\n"
    "• Алиса, погода / сегодня / завтра / неделя\n"
    "• Алиса, который час\n"
    "• Алиса, будильник на [время]\n"
    "• Алиса, выключи будильник\n"
    "• Алиса, выключи экран / включи экран\n"
    "• Алиса, выключить компьютер\n"
    "• Алиса, включи свет / выключи свет\n"
    "• Алиса, список команд\n"
    "• Алиса, список напоминаний\n"
    "• Алиса, обновись\n"
    "• Алиса, прочти\n"
    "• Алиса, переведи [фраза] / как сказать [фраза]\n"
    "• Алиса, напомни [текст] через [X] минут/часов\n"
    "• Алиса, напомни [текст] [дата] в [время]\n"
    "• Алиса, [ваш вопрос для ИИ]"
)


class BigClockApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Clock")
        self.root.attributes('-fullscreen', True)
        self.root.configure(background='black')
        self.root.config(cursor="none")

        self.load_config()
        
        # State variables
        self.current_weather_text = "Погода еще не загружена"
        self.today_hourly_forecast = "Почасовой прогноз на сегодня загружается..."
        self.tomorrow_hourly_forecast = "Почасовой прогноз на завтра загружается..."
        self.weekly_forecast_text = "Прогноз на неделю загружается..."

        self.alarm_time = None
        self.alarm_triggered_today = False
        self.alarm_active = False
        self.alarm_stop_event = threading.Event()
        self.active_audio_process = None

        self.display_mode = "clock"
        self.light_timer = None
        self.weather_timer = None
        self.countdown_seconds = 0

        self.reminders = []
        self.load_reminders()

        self.setup_ui()
        
        # Register exit handler
        atexit.register(self.stop_all_audio)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.bind('<Escape>', lambda e: self.on_closing())

        # Start background tasks
        self.update_display()
        self.fetch_weather()
        self.shift_position()
        self.restore_pending_reminders()

        threading.Thread(target=self.voice_listener_loop, daemon=True).start()
        self.root.after(5000, lambda: self.check_for_updates(manual=False))

    def load_config(self):
        default_config = {
            "GROQ_API_KEY": "gsk_YOUR_GROQ_API_KEY_HERE",
            "WEATHER_API_KEY": "YOUR_WEATHER_API_KEY",
            "CITY": self.get_city_by_ip(),
            "VOSK_MODEL_PATH": os.path.join(SCRIPT_DIR, "vosk-model-small-ru-0.22"),
            "GITHUB_RAW_URL": "https://raw.githubusercontent.com/mlurye69/bigclock/main/bigclock.py"
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    default_config.update(loaded)
            except Exception as e:
                print(f"Ошибка чтения big_clock.json: {e}")
        
        self.groq_api_key = default_config["GROQ_API_KEY"]
        self.weather_api_key = default_config["WEATHER_API_KEY"]
        self.city = default_config["CITY"]
        self.model_path = default_config["VOSK_MODEL_PATH"]
        self.github_raw_url = default_config["GITHUB_RAW_URL"]

    def load_reminders(self):
        if os.path.exists(REMINDERS_FILE):
            try:
                with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                    self.reminders = json.load(f)
            except Exception as e:
                print(f"Ошибка загрузки reminders.json: {e}")
                self.reminders = []

    def save_reminders(self):
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.reminders, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Ошибка сохранения reminders.json: {e}")

    def restore_pending_reminders(self):
        now = datetime.now().timestamp()
        valid_reminders = []
        for rem in self.reminders:
            target_time = rem.get("timestamp", 0)
            if target_time > now:
                valid_reminders.append(rem)
                delay = target_time - now
                threading.Timer(delay, self.trigger_reminder, args=[rem["text"]]).start()
        self.reminders = valid_reminders
        self.save_reminders()

    def add_reminder(self, text, delay_seconds):
        target_time = datetime.now().timestamp() + delay_seconds
        reminder = {"text": text, "timestamp": target_time}
        self.reminders.append(reminder)
        self.save_reminders()
        
        threading.Timer(delay_seconds, self.trigger_reminder, args=[text]).start()
        self.speak_russian(f"Напоминание установлено.")

    def trigger_reminder(self, text):
        self.speak_russian(f"Напоминание: {text}")
        self.reminders = [r for r in self.reminders if r["text"] != text]
        self.save_reminders()

    def get_city_by_ip(self):
        try:
            response = requests.get("https://ipinfo.io/json", timeout=5)
            if response.status_code == 200:
                city = response.json().get("city")
                if city:
                    return city
        except Exception as e:
            print(f"Не удалось определить город по IP: {e}")
        return "Boston"

    def setup_ui(self):
        self.frame = tk.Frame(self.root, bg='black')
        self.frame.place(relx=0.05, rely=0.05, relwidth=0.9, relheight=0.9)

        self.weather_label = tk.Label(self.frame, font=('Helvetica', 22, 'bold'), fg='lightseagreen', bg='black', justify='left', anchor='w')
        self.weather_label.pack(anchor='w', pady=(0, 10))

        self.date_label = tk.Label(self.frame, font=('Helvetica', 32, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
        self.date_label.pack(anchor='w', pady=(0, 15))

        self.time_label = tk.Label(self.frame, font=('Helvetica', 110, 'bold'), fg='mediumseagreen', bg='black', anchor='w')
        self.time_label.pack(anchor='w', pady=(0, 0))

    def stop_all_audio(self):
        self.alarm_active = False
        self.alarm_stop_event.set()
        if self.active_audio_process:
            try:
                self.active_audio_process.terminate()
                self.active_audio_process.wait(timeout=0.5)
            except Exception:
                try:
                    self.active_audio_process.kill()
                except Exception:
                    pass
            self.active_audio_process = None

    def speak_russian(self, text, lang='ru'):
        if self.active_audio_process:
            try:
                self.active_audio_process.terminate()
                self.active_audio_process.kill()
                self.active_audio_process.wait(timeout=0.2)
            except Exception:
                pass
            self.active_audio_process = None

        os.system("pkill -9 mpg123 2>/dev/null")
        os.system("pkill -9 play 2>/dev/null")
        os.system("pkill -9 aplay 2>/dev/null")
        os.system("pkill -9 paplay 2>/dev/null")

        def speech_worker():
            try:
                from gtts import gTTS
                audio_file = os.path.join(SCRIPT_DIR, "response.mp3")
                
                tts = gTTS(text=text, lang=lang, slow=False)
                tts.save(audio_file)
                
                self.active_audio_process = subprocess.Popen(
                    ["mpg123", "-q", audio_file],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.active_audio_process.wait()
                
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                self.active_audio_process = None
            except Exception as e:
                print(f"Ошибка речи: {e}")

        time.sleep(0.1)
        threading.Thread(target=speech_worker, daemon=True).start()

    def update_display(self):
        if self.display_mode == "clock":
            days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            months_ru = ["", "Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
            
            t = time.localtime()
            date_str = f"{days_ru[t.tm_wday]}, {t.tm_mday} {months_ru[t.tm_mon]}"
            current_hm = time.strftime('%H:%M')
            
            self.date_label.config(text=date_str)
            self.time_label.config(text=time.strftime('%H:%M:%S'), font=('Helvetica', 110, 'bold'))
            self.weather_label.config(text=f"Погода в {self.city}: {self.current_weather_text}", font=('Helvetica', 28, 'bold'), fg='lightseagreen')
            
            if self.alarm_time and current_hm == self.alarm_time and not self.alarm_triggered_today:
                self.alarm_triggered_today = True
                threading.Thread(target=self.trigger_alarm, daemon=True).start()
            elif current_hm != self.alarm_time:
                self.alarm_triggered_today = False
                
        # Optimized from 100ms to 1000ms
        self.root.after(1000, self.update_display)

    def shift_position(self):
        if self.display_mode == "clock":
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            self.frame.place(x=random.randint(40, max(50, screen_width - 650)),
                             y=random.randint(40, max(50, screen_height - 400)),
                             relwidth=0.9, relheight=0.9, anchor='nw')
        self.root.after(30000, self.shift_position)

    def trigger_alarm(self):
        self.alarm_active = True
        self.alarm_stop_event.clear()
        
        for _ in range(2):
            if not self.alarm_active or self.alarm_stop_event.is_set():
                break
            try:
                if os.path.exists(CHIME_SOUND_PATH):
                    self.active_audio_process = subprocess.Popen(["mpg123", "-q", CHIME_SOUND_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.active_audio_process = subprocess.Popen(["play", "-q", "-n", "synth", "0.3", "bell", "523"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active_audio_process.wait()
            except Exception:
                pass
            
            for _ in range(50):
                if not self.alarm_active or self.alarm_stop_event.is_set():
                    break
                time.sleep(0.1)
                
        self.alarm_active = False
        self.alarm_time = None

    def fetch_weather(self):
        if self.weather_api_key != "YOUR_WEATHER_API_KEY":
            try:
                url_current = f"https://api.openweathermap.org/data/2.5/weather?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp_curr = requests.get(url_current, timeout=10)
                if resp_curr.status_code == 200:
                    data = resp_curr.json()
                    self.current_weather_text = f"{data['weather'][0]['description'].capitalize()}, {round(data['main']['temp'])}°C"

                url_forecast = f"https://api.openweathermap.org/data/2.5/forecast?q={self.city}&appid={self.weather_api_key}&units=metric&lang=ru"
                resp_fore = requests.get(url_forecast, timeout=10)
                if resp_fore.status_code == 200:
                    fdata = resp_fore.json()
                    today_date = datetime.now().strftime('%Y-%m-%d')
                    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                    
                    today_lines, tomorrow_lines, weekly_lines, seen_dates = [], [], [], set()
                    days_full_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    
                    for item in fdata.get("list", []):
                        item_date, item_time = item["dt_txt"].split()
                        line = f"{item_time[:5]}: {item['weather'][0]['description']}, {round(item['main']['temp'])}°C"
                        if item_date == today_date:
                            today_lines.append(line)
                        elif item_date == tomorrow_date:
                            tomorrow_lines.append(line)
                        if item_date not in seen_dates and "12:00" in item_time:
                            seen_dates.add(item_date)
                            dt_obj = datetime.strptime(item_date, '%Y-%m-%d')
                            weekly_lines.append(f"{days_full_ru[dt_obj.weekday()]} : {item['weather'][0]['description']}, {round(item['main']['temp'])}°C")
                    
                    self.today_hourly_forecast = "\n".join(today_lines) if today_lines else "Недоступно."
                    self.tomorrow_hourly_forecast = "\n".join(tomorrow_lines) if tomorrow_lines else "Недоступно."
                    self.weekly_forecast_text = "\n".join(weekly_lines) if weekly_lines else "Недоступно."
            except Exception as e:
                print(f"Weather fetch error: {e}")
        else:
            self.current_weather_text = "API ключ погоды не настроен"
        
        self.root.after(600000, self.fetch_weather)

    def ask_ai(self, prompt):
        if self.groq_api_key == "gsk_YOUR_GROQ_API_KEY_HERE":
            return "Пожалуйста, настройте API ключ Groq в коде."
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.groq_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "qwen/qwen3.8-27b",
                    "messages": [
                        {"role": "system", "content": "Ты голосовой ассистент на умных часах. Отвечай кратко, по делу, исключительно на русском языке, максимум в 1-2 предложениях."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 150
                },
                timeout=10
            )
            if response.status_code == 200:
                answer = response.json()["choices"][0]["message"]["content"]
                if answer: return answer.strip()
        except Exception as e:
            print(f"Groq connection error: {e}")
        return "Не удалось связаться с искусственным интеллектом."

    def check_for_updates(self, manual=False):
        def update_worker():
            if manual:
                self.speak_russian("Проверяю обновления.")
                time.sleep(3.5)
            try:
                response = requests.get(self.github_raw_url, timeout=10)
                if response.status_code == 200:
                    remote_code = response.text
                    script_path = os.path.abspath(__file__)
                    with open(script_path, "r", encoding="utf-8") as f:
                        local_code = f.read()
                    if remote_code.strip() != local_code.strip():
                        with open(script_path, "w", encoding="utf-8") as f:
                            f.write(remote_code)
                        self.speak_russian("Обновление загружено. Перезапускаюсь.")
                        time.sleep(3.0)
                        os.execv(sys.executable, ['python'] + sys.argv)
                    elif manual:
                        self.speak_russian("Обновлений не найдено.")
                elif manual:
                    self.speak_russian("Не удалось получить ответ от сервера.")
            except Exception:
                if manual:
                    self.speak_russian("Ошибка соединения с сетью.")
        threading.Thread(target=update_worker, daemon=True).start()

    def voice_listener_loop(self):
        time.sleep(5)
        import pyaudio
        from vosk import Model, KaldiRecognizer
        import audioop

        if not os.path.exists(self.model_path):
            print(f"Модель Vosk не найдена по пути: {self.model_path}")
            return

        try:
            vosk_model = Model(self.model_path)
        except Exception as e:
            print(f"Ошибка загрузки модели Vosk: {e}")
            return

        RATE, CHUNK, CHANNELS = 16000, 4000, 1
        p = pyaudio.PyAudio()
        r = sr.Recognizer()

        print("Локальный сторож Vosk запущен...")

        while True:
            stream = None
            try:
                rec = KaldiRecognizer(vosk_model, RATE)
                rec.SetWords(False)
                stream = p.open(format=pyaudio.paInt16, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

                while True:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if data and rec.AcceptWaveform(data):
                        recognized_text = json.loads(rec.Result()).get("text", "").lower()
                        if "алиса" in recognized_text or "alisa" in recognized_text:
                            self.speak_russian("Слушаю.")
                            break

                frames, silent_chunks = [], 0
                silence_threshold_chunks = int((RATE / CHUNK) * 3)

                while True:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    if not data: break
                    frames.append(data)
                    if audioop.rms(data, 2) < 300:
                        silent_chunks += 1
                    else:
                        silent_chunks = 0
                    if silent_chunks > silence_threshold_chunks:
                        break

                stream.stop_stream()
                stream.close()
                stream = None

                if frames:
                    query = r.recognize_google(sr.AudioData(b''.join(frames), RATE, 2), language="ru-RU").lower()
                    print(f"Распознано: {query}")
                    
                    if "напомни" in query:
                        match_delay = re.search(r"напомни\s+(.*?)\s+через\s+(\d+)\s*(минут|минуту|минуты|час|часа|часов)", query)
                        if match_delay:
                            text_reminder = match_delay.group(1)
                            amount = int(match_delay.group(2))
                            unit = match_delay.group(3)
                            delay_seconds = amount * 60 if "мин" in unit else amount * 3600
                            self.add_reminder(text_reminder, delay_seconds)
                        else:
                            self.speak_russian("Не поняла время напоминания.")
                    elif "список напоминаний" in query:
                        if self.reminders:
                            texts = ", ".join([rem["text"] for rem in self.reminders])
                            self.speak_russian(f"У вас активны напоминания: {texts}")
                        else:
                            self.speak_russian("Активных напоминаний нет.")
            except Exception as e:
                print(f"Ошибка аудиопотока Vosk: {e}")
                if stream:
                    try: stream.stop_stream(); stream.close()
                    except: pass
                time.sleep(1)

    def on_closing(self):
        self.stop_all_audio()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = BigClockApp(root)
    root.mainloop()
