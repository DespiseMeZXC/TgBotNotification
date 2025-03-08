import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
import json
import os.path

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.markdown import hbold
from dotenv import load_dotenv

from google_calendar import get_upcoming_events, create_auth_url, process_auth_code, get_credentials_with_local_server
from database import Database

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Инициализация бота и диспетчера
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

# ID пользователя, которому отправлять уведомления
USER_ID = os.getenv("USER_ID")

# Директория для хранения токенов и данных
TOKEN_DIR = os.getenv("TOKEN_DIR", ".")
DATA_DIR = os.getenv("DATA_DIR", ".")

# Убедимся, что директории существуют
os.makedirs(TOKEN_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Инициализация базы данных
db = Database(os.path.join(DATA_DIR, 'calendar_bot.db'))

# Команда /start
@dp.message(Command("start"))
async def command_start(message: Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n"
        "Я буду отправлять уведомления о предстоящих созвонах в Google Meet.\n\n"
        "Для начала работы вам нужно авторизоваться в Google Calendar.\n"
        "Выберите подходящий способ авторизации:\n\n"
        "1. Через браузер с кодом авторизации: /serverauth\n"
        "2. Локальная авторизация (только если бот запущен на вашем компьютере): /localauth\n"
        "3. Ручной ввод токена (для продвинутых пользователей): /manualtoken"
    )
    user_id = message.from_user.id
    logging.info(f"Команда /start от пользователя ID: {user_id}, имя: {message.from_user.full_name}")
    
    # Сохраняем ID пользователя в .env файл
    if USER_ID or USER_ID == str(user_id):
        await message.answer(f"Ваш ID {user_id} сохранен для отправки уведомлений.")
        logging.info(f"ID пользователя {user_id} сохранен в .env файл")

# Команда /week для просмотра встреч на неделю
@dp.message(Command("week"))
async def check_week_meetings(message: Message):
    user_id = message.from_user.id
    
    # Проверяем наличие токена в базе данных
    if not db.get_token(user_id):
        await message.answer(
            "Вы не авторизованы в Google Calendar.\n"
            "Используйте команду /serverauth для авторизации."
        )
        return
    
    await message.answer("Проверяю ваши онлайн-встречи на неделю...")
    
    try:
        # Получаем события на ближайшие 7 дней
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        now = datetime.now(timezone.utc)
        
        # Определяем начало недели
        current_weekday = today.weekday()
        if current_weekday >= 5:  # Суббота (5) или воскресенье (6)
            week_start = today + timedelta(days=(7 - current_weekday))
        else:
            week_start = today - timedelta(days=current_weekday)
            
        events = await get_upcoming_events(
            time_min=week_start,
            time_max=week_start + timedelta(days=7),
            limit=20,
            user_id=user_id,
            db=db
        )
        
        # Фильтруем события
        active_events = []
        for event in events:
            # Пропускаем события без ссылки на подключение
            if 'hangoutLink' not in event:
                continue
                
            end_time = event['end'].get('dateTime', event['end'].get('date'))
            end_dt = safe_parse_datetime(end_time)
            if end_dt > now:
                active_events.append(event)
        
        if not active_events:
            await message.answer("У вас нет предстоящих онлайн-встреч на неделю.")
            return
        
        # Группируем встречи по дням
        meetings_by_day = {}
        for event in active_events:
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            start_dt = safe_parse_datetime(start_time)
            day_key = start_dt.strftime('%d.%m.%Y')
            
            if day_key not in meetings_by_day:
                meetings_by_day[day_key] = []
            
            meetings_by_day[day_key].append(event)
        
        # Отправляем встречи по дням
        for day, day_events in sorted(meetings_by_day.items()):
            day_message = f"📆 {hbold(f'Онлайн-встречи на {day}:')}\n\n"
            has_meetings = False
            
            for event in day_events:
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                start_dt = safe_parse_datetime(start_time)
                
                day_message += f"🕒 {start_dt.strftime('%H:%M')} - {hbold(event['summary'])}\n"
                day_message += f"🔗 {event['hangoutLink']}\n\n"
                has_meetings = True
            
            # Отправляем сообщение если есть встречи
            if has_meetings:
                await message.answer(day_message, parse_mode="HTML")
    
    except Exception as e:
        logging.error(f"Ошибка при получении встреч на неделю: {e}")
        await message.answer("Произошла ошибка при получении данных о встречах.")

# Команда /reset для сброса кэша обработанных встреч
@dp.message(Command("reset"))
async def reset_processed_events(message: Message):
    try:
        # Сбрасываем все данные в базе
        db.reset_all()
        # Удаляем токен пользователя
        db.delete_token(message.from_user.id)
        await message.answer("✅ Все данные успешно сброшены. Теперь вы получите уведомления о всех текущих встречах как о новых.")
    except Exception as e:
        logging.error(f"Ошибка при сбросе данных: {e}")
        await message.answer("❌ Произошла ошибка при сбросе данных.")

# Функция для безопасного парсинга даты
def safe_parse_datetime(date_str):
    try:
        if date_str.endswith('Z'):
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        elif '+' in date_str or '-' in date_str and 'T' in date_str:
            return datetime.fromisoformat(date_str)
        else:
            # Если дата без часового пояса, добавляем UTC
            return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    except Exception as e:
        logging.error(f"Ошибка при парсинге даты {date_str}: {e}")
        return datetime.now(timezone.utc)

# Функция для периодической проверки и отправки уведомлений
async def scheduled_meetings_check():
    """Фоновая задача для проверки предстоящих встреч."""
    while True:
        try:
            # Получаем всех пользователей с токенами из базы данных
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT user_id FROM tokens')
                users = cursor.fetchall()
            
            for (user_id,) in users:
                try:
                    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    events = await get_upcoming_events(
                        time_min=today,
                        time_max=today + timedelta(days=7, hours=23, minutes=59, seconds=59),
                        user_id=user_id,
                        db=db
                    )
                    
                    now = datetime.now(timezone.utc)
                    
                    # Получаем все известные события
                    known_events = db.get_known_events(user_id)
                    current_event_ids = set()
                    
                    for event in events:
                        # Пропускаем события без ссылки на подключение
                        if 'hangoutLink' not in event:
                            continue
                            
                        event_id = event['id']
                        current_event_ids.add(event_id)
                        
                        # Проверка новых встреч
                        if not db.is_event_known(event_id, user_id):
                            db.add_known_event(event_id, event['summary'], event['start'].get('dateTime', event['start'].get('date')), event['end'].get('dateTime', event['end'].get('date')), user_id)
                            
                            if safe_parse_datetime(event['start'].get('dateTime', event['start'].get('date'))) > now:
                                new_meeting_info = (
                                    f"📅 {hbold('Новая онлайн-встреча добавлена в календарь:')}\n\n"
                                    f"📌 {hbold(event['summary'])}\n"
                                    f"🕒 {safe_parse_datetime(event['start'].get('dateTime', event['start'].get('date'))).strftime('%d.%m.%Y %H:%M')} - {safe_parse_datetime(event['end'].get('dateTime', event['end'].get('date'))).strftime('%H:%M')}\n\n"
                                    f"🔗 {event['hangoutLink']}\n"
                                )
                                
                                if USER_ID:
                                    await bot.send_message(USER_ID, new_meeting_info, parse_mode="HTML")
                        
                        # Проверка предстоящих встреч
                        if not db.is_event_processed(event_id, user_id):
                            start_time = event['start'].get('dateTime', event['start'].get('date'))
                            start_dt = safe_parse_datetime(start_time)
                            time_until_start = start_dt - now
                            
                            if timedelta(0) <= time_until_start <= timedelta(minutes=15):
                                meeting_info = (
                                    f"🔔 {hbold('Скоро начнется онлайн-встреча:')}\n\n"
                                    f"📅 {hbold(event['summary'])}\n"
                                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n\n"
                                    f"🔗 {event['hangoutLink']}\n"
                                )
                                
                                if USER_ID:
                                    await bot.send_message(USER_ID, meeting_info, parse_mode="HTML")
                                
                                db.save_processed_event(event_id, event['summary'], start_time, user_id)
                        
                        # Проверка начавшихся встреч
                        if not db.is_event_started(event_id, user_id):
                            start_time = event['start'].get('dateTime', event['start'].get('date'))
                            end_time = event['end'].get('dateTime', event['end'].get('date'))
                            start_dt = safe_parse_datetime(start_time)
                            end_dt = safe_parse_datetime(end_time)
                            
                            if start_dt <= now < end_dt:
                                meeting_started_info = (
                                    f"🚀 {hbold('Онлайн-встреча началась!')}\n\n"
                                    f"📅 {hbold(event['summary'])}\n"
                                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')} - {end_dt.strftime('%H:%M')}\n\n"
                                    f"🔗 {event['hangoutLink']}\n"
                                )
                                
                                if USER_ID:
                                    await bot.send_message(USER_ID, meeting_started_info, parse_mode="HTML")
                                
                                db.add_started_event(event_id, event['summary'], start_time, end_time, user_id)
                    
                    # Очистка старых событий
                    db.clean_old_events(now - timedelta(days=1))
                    
                    # Проверяем удаленные события
                    for known_event in known_events:
                        if known_event['event_id'] not in current_event_ids:
                            # Событие было удалено
                            deleted_meeting_info = (
                                f"❌ {hbold('Онлайн-встреча отменена:')}\n\n"
                                f"📌 {hbold(known_event['summary'])}\n"
                                f"🕒 {safe_parse_datetime(known_event['start_time']).strftime('%d.%m.%Y %H:%M')}\n"
                            )
                            
                            if USER_ID:
                                await bot.send_message(USER_ID, deleted_meeting_info, parse_mode="HTML")
                            
                            # Удаляем событие из базы
                            db.delete_known_event(known_event['event_id'], user_id)
                    
                except Exception as e:
                    logging.error(f"Ошибка при проверке встреч для пользователя {user_id}: {e}")
            
        except Exception as e:
            logging.error(f"Ошибка при проверке предстоящих встреч: {e}")
        
        await asyncio.sleep(int(os.getenv('CHECK_INTERVAL', 300)))

# Команда /debug для проверки настроек
@dp.message(Command("debug"))
async def debug_info(message: Message):
    debug_message = (
        f"🔍 Отладочная информация:\n"
        f"- Ваш ID: {message.from_user.id}\n"
        f"- Сохраненный USER_ID: {USER_ID}\n"
    )
    
    # Проверяем файл processed_events.json
    if os.path.exists('processed_events.json'):
        try:
            with open('processed_events.json', 'r') as f:
                processed = json.load(f)
                debug_message += f"- Обработанных встреч: {len(processed)}\n"
        except Exception as e:
            debug_message += f"- Ошибка чтения processed_events.json: {e}\n"
    else:
        debug_message += "- Файл processed_events.json не существует\n"
    
    # Проверяем последние события
    try:
        events = await get_upcoming_events(limit=3)
        debug_message += f"- Ближайших событий: {len(events)}\n"
        
        if events:
            debug_message += "\nПоследние события:\n"
            for event in events[:3]:
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                start_dt = safe_parse_datetime(start_time)
                debug_message += f"  • {event['summary']} ({start_dt.strftime('%d.%m.%Y %H:%M')})\n"
                debug_message += f"    ID: {event['id']}\n"
                debug_message += f"    Meet: {'Да' if 'hangoutLink' in event else 'Нет'}\n"
    except Exception as e:
        debug_message += f"- Ошибка получения событий: {e}\n"
    
    await message.answer(debug_message)

# Команда /auth для авторизации в Google Calendar
@dp.message(Command("auth"))
async def auth_command(message: Message):
    user_id = message.from_user.id
    
    # Создаем URL для авторизации
    auth_url = create_auth_url(user_id, db)
    
    await message.answer(
        f"Для авторизации в Google Calendar, пожалуйста, перейдите по ссылке:\n\n"
        f"{auth_url}\n\n"
        f"После авторизации вы получите код. Отправьте его мне в формате:\n"
        f"/code ПОЛУЧЕННЫЙ_КОД"
    )

# Команда /code для обработки кода авторизации
@dp.message(Command("code"))
async def process_auth_code_command(message: Message):
    user_id = message.from_user.id
    
    # Проверяем наличие кода
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Пожалуйста, укажите код после команды /code")
        return
    
    # Получаем код
    code = message.text.split(maxsplit=1)[1].strip()
    
    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer("🔄 Обрабатываю код авторизации...")
    
    try:
        # Обрабатываем код авторизации
        success, result = await process_auth_code(user_id, code, db)
        
        # Обновляем сообщение с результатом
        await processing_msg.edit_text(result)
        
        if success:
            # Если авторизация успешна, обновляем USER_ID
            global USER_ID
            USER_ID = str(user_id)
            
            # Сохраняем USER_ID в .env файл
            env_path = '.env'
            env_lines = []
            
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    env_lines = f.readlines()
            
            # Обновляем или добавляем USER_ID
            user_id_found = False
            for i, line in enumerate(env_lines):
                if line.startswith('USER_ID='):
                    env_lines[i] = f'USER_ID={user_id}\n'
                    user_id_found = True
                    break
            
            if not user_id_found:
                env_lines.append(f'USER_ID={user_id}\n')
            
            # Записываем обновленный .env файл
            with open(env_path, 'w') as f:
                f.writelines(env_lines)
    except Exception as e:
        logging.error(f"Ошибка при обработке кода авторизации: {e}")
        await processing_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")

# Команда /check для принудительной проверки новых встреч
@dp.message(Command("check"))
async def check_command(message: Message):
    user_id = message.from_user.id
    
    # Проверяем авторизацию
    if not db.get_token(user_id):
        await message.answer(
            "Вы не авторизованы в Google Calendar.\n"
            "Используйте команду /serverauth для авторизации."
        )
        return
    
    try:
        # Получаем события
        events = await get_upcoming_events(
            time_min=datetime.now(), 
            time_max=datetime.now() + timedelta(days=7),
            limit=10,
            user_id=user_id,
            db=db
        )
        
        # Получаем все известные события
        known_events = db.get_known_events(user_id)
        current_event_ids = set()
        new_events_count = 0
        deleted_events_count = 0
        
        # Проверяем новые встречи
        for event in events:
            # Пропускаем события без ссылки на подключение
            if 'hangoutLink' not in event:
                continue
                
            event_id = event['id']
            current_event_ids.add(event_id)
            
            # Если встреча новая
            if not db.is_event_known(event_id, user_id):
                new_events_count += 1
                
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                end_time = event['end'].get('dateTime', event['end'].get('date'))
                start_dt = safe_parse_datetime(start_time)
                
                meeting_info = (
                    f"📅 {hbold('Найдена новая онлайн-встреча:')}\n\n"
                    f"📌 {hbold(event['summary'])}\n"
                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                    f"🔗 {event['hangoutLink']}\n"
                )
                
                await message.answer(meeting_info, parse_mode="HTML")
                
                # Сохраняем в базу данных как обработанное
                db.add_known_event(
                    event_id=event_id,
                    summary=event['summary'],
                    start_time=start_time,
                    end_time=end_time,
                    user_id=user_id
                )
        
        # Проверяем удаленные события
        for known_event in known_events:
            if known_event['event_id'] not in current_event_ids:
                deleted_events_count += 1
                
                deleted_meeting_info = (
                    f"❌ {hbold('Онлайн-встреча отменена:')}\n\n"
                    f"📌 {hbold(known_event['summary'])}\n"
                    f"🕒 {safe_parse_datetime(known_event['start_time']).strftime('%d.%m.%Y %H:%M')}\n"
                )
                
                await message.answer(deleted_meeting_info, parse_mode="HTML")
                
                # Удаляем событие из базы
                db.delete_known_event(known_event['event_id'], user_id)
        
        if new_events_count == 0 and deleted_events_count == 0:
            await message.answer("Изменений в расписании онлайн-встреч не найдено.")
            
    except Exception as e:
        logging.error(f"Ошибка при проверке встреч: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")

# Команда /authstatus для проверки статуса авторизации
@dp.message(Command("authstatus"))
async def auth_status_command(message: Message):
    user_id = message.from_user.id
    token_file = os.path.join(TOKEN_DIR, f'token_{user_id}.json')
    
    if os.path.exists(token_file):
        try:
            with open(token_file, 'r') as f:
                creds_data = json.load(f)
                
            # Проверяем наличие основных полей в токене
            if 'token' in creds_data and 'refresh_token' in creds_data:
                await message.answer(
                    "✅ Вы успешно авторизованы в Google Calendar.\n"
                    "Можете использовать команды /week и /check для работы с календарем."
                )
            else:
                await message.answer(
                    "⚠️ Ваш токен авторизации неполный. Рекомендуется повторить авторизацию.\n"
                    "Используйте команду /auth для повторной авторизации."
                )
        except Exception as e:
            await message.answer(
                f"⚠️ Ошибка при проверке токена авторизации: {str(e)}\n"
                "Рекомендуется повторить авторизацию с помощью команды /auth."
            )
    else:
        await message.answer(
            "❌ Вы не авторизованы в Google Calendar.\n"
            "Используйте команду /auth для авторизации."
        )

# Команда /localauth для авторизации через локальный сервер
@dp.message(Command("localauth"))
async def local_auth_command(message: Message):
    user_id = message.from_user.id
    
    await message.answer(
        "Запускаю локальный сервер для авторизации в Google Calendar...\n"
        "Сейчас в вашем браузере должно открыться окно авторизации.\n"
        "Пожалуйста, следуйте инструкциям в браузере."
    )
    
    # Запускаем процесс авторизации в отдельной задаче
    asyncio.create_task(run_local_auth(message, user_id))

async def run_local_auth(message, user_id):
    try:
        # Запускаем локальный сервер для авторизации
        creds = await get_credentials_with_local_server(user_id)
        
        if creds:
            # Если авторизация успешна, обновляем USER_ID
            global USER_ID
            USER_ID = str(user_id)
            
            # Сохраняем USER_ID в .env файл
            env_path = '.env'
            env_lines = []
            
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    env_lines = f.readlines()
            
            # Обновляем или добавляем USER_ID
            user_id_found = False
            for i, line in enumerate(env_lines):
                if line.startswith('USER_ID='):
                    env_lines[i] = f'USER_ID={user_id}\n'
                    user_id_found = True
                    break
            
            if not user_id_found:
                env_lines.append(f'USER_ID={user_id}\n')
            
            # Записываем обновленный .env файл
            with open(env_path, 'w') as f:
                f.writelines(env_lines)
            
            await message.answer(
                "✅ Авторизация успешно завершена!\n"
                "Теперь вы можете использовать команды /week и /check для работы с календарем."
            )
        else:
            await message.answer(
                "❌ Не удалось выполнить авторизацию.\n"
                "Пожалуйста, попробуйте еще раз или обратитесь к разработчику."
            )
    except Exception as e:
        logging.error(f"Ошибка при локальной авторизации: {e}")
        await message.answer(f"❌ Произошла ошибка при авторизации: {str(e)}")

# Команда /manualtoken для ручного создания токена
@dp.message(Command("manualtoken"))
async def manual_token_command(message: Message):
    await message.answer(
        "Для ручного создания токена авторизации, пожалуйста, отправьте JSON-данные токена в формате:\n\n"
        "/settoken {\"token\": \"ваш_токен\", \"refresh_token\": \"ваш_рефреш_токен\", ...}\n\n"
        "Эти данные можно получить, выполнив авторизацию на другом устройстве или через API Console."
    )

# Команда /settoken для установки токена вручную
@dp.message(Command("settoken"))
async def set_token_command(message: Message):
    user_id = message.from_user.id
    
    # Извлекаем JSON из сообщения
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Пожалуйста, укажите JSON-данные токена после команды /settoken")
        return
    
    token_json = parts[1].strip()
    
    try:
        # Проверяем, что это валидный JSON
        token_data = json.loads(token_json)
        
        # Проверяем наличие необходимых полей
        if 'token' not in token_data or 'refresh_token' not in token_data:
            await message.answer("❌ JSON-данные токена должны содержать поля 'token' и 'refresh_token'")
            return
        
        # Сохраняем токен в файл
        token_file = os.path.join(TOKEN_DIR, f'token_{user_id}.json')
        with open(token_file, 'w') as f:
            f.write(token_json)
        
        # Обновляем USER_ID
        global USER_ID
        USER_ID = str(user_id)
        
        # Сохраняем USER_ID в .env файл
        env_path = '.env'
        env_lines = []
        
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                env_lines = f.readlines()
        
        # Обновляем или добавляем USER_ID
        user_id_found = False
        for i, line in enumerate(env_lines):
            if line.startswith('USER_ID='):
                env_lines[i] = f'USER_ID={user_id}\n'
                user_id_found = True
                break
        
        if not user_id_found:
            env_lines.append(f'USER_ID={user_id}\n')
        
        # Записываем обновленный .env файл
        with open(env_path, 'w') as f:
            f.writelines(env_lines)
        
        await message.answer("✅ Токен успешно сохранен! Теперь вы можете использовать команды /week и /check.")
    except json.JSONDecodeError:
        await message.answer("❌ Неверный формат JSON. Пожалуйста, проверьте данные и попробуйте снова.")
    except Exception as e:
        logging.error(f"Ошибка при установке токена вручную: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")

# Команда /serverauth для авторизации на сервере
@dp.message(Command("serverauth"))
async def server_auth_command(message: Message):
    user_id = message.from_user.id
    
    # Создаем URL для авторизации с правильными параметрами
    auth_url = create_auth_url(user_id, db)
    
    await message.answer(
        "📱 <b>Инструкция по авторизации на сервере:</b>\n\n"
        "1️⃣ Перейдите по ссылке ниже в браузере:\n"
        f"{auth_url}\n\n"
        "2️⃣ Войдите в аккаунт Google и разрешите доступ к календарю\n\n"
        "3️⃣ Вы получите код авторизации. Скопируйте его\n\n"
        "4️⃣ Отправьте боту команду:\n"
        "/code ПОЛУЧЕННЫЙ_КОД\n\n"
        "❗ Если возникает ошибка при авторизации, попробуйте использовать команду /manualtoken",
        parse_mode="HTML"
    )

# Команда /authinfo для получения информации об авторизации
@dp.message(Command("authinfo"))
async def auth_info_command(message: Message):
    user_id = message.from_user.id
    
    # Проверяем наличие credentials.json
    if not os.path.exists('credentials.json'):
        await message.answer("❌ Файл credentials.json не найден. Необходимо создать OAuth-клиент в Google Cloud Console.")
        return
    
    # Читаем данные клиента
    try:
        with open('credentials.json', 'r') as f:
            client_data = json.load(f)
        
        client_info = client_data.get('installed', client_data.get('web', {}))
        
        auth_info = (
            "📋 <b>Информация об OAuth-клиенте:</b>\n\n"
            f"🔹 Тип клиента: {'Web' if 'web' in client_data else 'Desktop'}\n"
            f"🔹 Client ID: {client_info.get('client_id', 'Не найден')[:15]}...\n"
            f"🔹 Redirect URIs: {', '.join(client_info.get('redirect_uris', ['Не найдены']))}\n\n"
            "Для корректной работы авторизации убедитесь, что:\n"
            "1. В Google Cloud Console включен Calendar API\n"
            "2. Настроен OAuth consent screen\n"
            "3. Добавлен ваш email в список тестовых пользователей\n"
            "4. В redirect URIs добавлен urn:ietf:wg:oauth:2.0:oob"
        )
        
        await message.answer(auth_info, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка при чтении данных клиента: {str(e)}")

# Команда /notifications для настройки уведомлений
@dp.message(Command("notifications"))
async def notifications_settings(message: Message):
    user_id = message.from_user.id
    
    await message.answer(
        "⚙️ <b>Настройки уведомлений:</b>\n\n"
        "Сейчас вы получаете следующие уведомления:\n"
        "✅ При добавлении новой встречи\n"
        "✅ За 15 минут до начала встречи\n"
        "✅ В момент начала встречи\n\n"
        "Для сброса всех уведомлений используйте команду /reset\n"
        "Это позволит получить уведомления о всех текущих встречах заново.",
        parse_mode="HTML"
    )

# Запуск бота
async def main():
    # Запускаем фоновую задачу для проверки встреч
    asyncio.create_task(scheduled_meetings_check())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
