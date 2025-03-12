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

# Директория для хранения токенов и данных
DATA_DIR = os.getenv("DATA_DIR", ".")

# Убедимся, что директории существуют
os.makedirs(DATA_DIR, exist_ok=True)

# Инициализация базы данных
db_path = os.path.join(DATA_DIR, 'calendar_bot.db')
# Убедимся, что директория существует и доступна для записи
os.makedirs(os.path.dirname(db_path), exist_ok=True)
try:
    # Проверяем права на запись
    with open(db_path, 'a'):
        pass
except PermissionError:
    logging.error(f"Нет прав на запись в файл базы данных: {db_path}")
    # Пробуем изменить права
    try:
        os.chmod(db_path, 0o666)
    except Exception as e:
        logging.error(f"Не удалось изменить права доступа к базе данных: {e}")

db = Database(db_path)

# Команда /start
@dp.message(Command("start"))
async def command_start(message: Message):
    user_id = message.from_user.id
    
    await message.answer(
        f"Привет, {message.from_user.full_name}!\n"
        "Я буду отправлять уведомления о предстоящих созвонах в Google Meet.\n\n"
        "Для начала работы вам нужно авторизоваться в Google Calendar.\n"
        "Выберите подходящий способ авторизации:\n\n"
        "1. Через браузер с кодом авторизации: /serverauth\n"
        "2. Локальная авторизация (только если бот запущен на вашем компьютере): /localauth\n"
        "3. Ручной ввод токена (для продвинутых пользователей): /manualtoken"
    )
    
    logging.info(f"Команда /start от пользователя ID: {user_id}, имя: {message.from_user.full_name}")

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
            week_start = today + timedelta(days=(6 - current_weekday))
        else:
            week_start = today - timedelta(days=current_weekday)
            
        events = await get_upcoming_events(
            time_min=week_start,
            time_max=week_start + timedelta(days=6),
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

async def get_upcoming_meetings(user_id):
    """Получает предстоящие встречи для конкретного пользователя."""
    meetings = set()
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        events = await get_upcoming_events(
            time_min=today,
            time_max=today + timedelta(days=6),
            user_id=user_id,
            db=db
        )
        
        for event in events:
            if 'hangoutLink' in event:
                meetings.add((event['id'], event['summary'], event['hangoutLink'], event['start'].get('dateTime', event['start'].get('date'))))
    except Exception as e:
        logging.error(f"Ошибка при получении встреч для пользователя {user_id}: {e}")
    
    return meetings

async def notify_about_meeting(meeting, user_id):
    """Отправляет уведомление о новой встрече конкретному пользователю."""
    event_id, summary, hangout_link, start_time = meeting
    try:
        # Проверяем, было ли уже отправлено уведомление
        if not db.is_notification_sent(event_id, user_id):
            start_dt = safe_parse_datetime(start_time)
            meeting_info = (
                f"📅 {hbold('Найдена новая онлайн-встреча:')}\n\n"
                f"📌 {hbold(summary)}\n"
                f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                f"🔗 {hangout_link}\n"
            )
            await bot.send_message(
                user_id,
                meeting_info,
                parse_mode="HTML"
            )
            # Помечаем встречу как известную и уведомление как отправленное
            db.add_known_event(event_id, summary, start_time, None, user_id, notification_sent=True)
            logging.info(f"Отправлено уведомление пользователю {user_id} о встрече {summary}")
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")

async def scheduled_meetings_check():
    """Периодическая проверка новых встреч для всех пользователей"""
    user_first_run = {}  # Словарь для отслеживания первого запуска для каждого пользователя
    
    while True:
        try:
            users = db.get_all_users()
            logging.info(f"Проверка встреч для {len(users)} пользователей")
            
            for user_id in users:
                # Инициализируем first_run для нового пользователя
                if user_id not in user_first_run:
                    user_first_run[user_id] = True
                    logging.info(f"Первый запуск для пользователя {user_id}")
                
                # Получаем текущие встречи для пользователя
                current_meetings = await get_upcoming_meetings(user_id)
                
                if not user_first_run[user_id]:
                    # Проверяем каждую встречу отдельно
                    for meeting in current_meetings:
                        event_id, summary, hangout_link, start_time = meeting
                        
                        # Проверяем, было ли уже отправлено уведомление
                        if not db.is_notification_sent(event_id, user_id):
                            await notify_about_meeting(meeting, user_id)
                        
                else:
                    # При первом запуске добавляем все текущие встречи как известные
                    for meeting in current_meetings:
                        event_id, summary, _, _ = meeting
                        db.add_known_event(event_id, summary, None, None, user_id, notification_sent=True)
                    user_first_run[user_id] = False
                    logging.info(f"Первый запуск завершен для пользователя {user_id}, добавлено {len(current_meetings)} встреч")
            
            # Очистка словаря first_run от пользователей, которых больше нет в базе
            for user_id in list(user_first_run.keys()):
                if user_id not in users:
                    del user_first_run[user_id]
            
            await asyncio.sleep(int(os.getenv('CHECK_INTERVAL', 300)))
        except Exception as e:
            logging.error(f"Ошибка при проверке встреч: {e}")
            await asyncio.sleep(int(os.getenv('CHECK_INTERVAL', 300)))

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
        changed_events_count = 0
        
        # Проверяем новые и измененные встречи
        for event in events:
            # Пропускаем события без ссылки на подключение
            if 'hangoutLink' not in event:
                continue
                
            event_id = event['id']
            current_event_ids.add(event_id)
            
            # Получаем время начала и окончания встречи
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            end_time = event['end'].get('dateTime', event['end'].get('date'))
            start_dt = safe_parse_datetime(start_time)
            
            # Проверяем, было ли уже отправлено уведомление
            notification_sent = db.is_notification_sent(event_id, user_id)
            logging.info(f"Проверка встречи {event['summary']} (ID: {event_id}): notification_sent = {notification_sent}")
            
            # Если встреча новая или о ней не было уведомления
            if not db.is_event_known(event_id, user_id) or not notification_sent:
                new_events_count += 1
                
                meeting_info = (
                    f"📅 {hbold('Найдена новая онлайн-встреча:')}\n\n"
                    f"📌 {hbold(event['summary'])}\n"
                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                    f"🔗 {event['hangoutLink']}\n"
                )
                
                await message.answer(meeting_info, parse_mode="HTML")
                
                # Сохраняем в базу данных как обработанное и помечаем уведомление как отправленное
                db.add_known_event(
                    event_id=event_id,
                    summary=event['summary'],
                    start_time=start_time,
                    end_time=end_time,
                    user_id=user_id,
                    notification_sent=True  # Важно: помечаем как отправленное
                )
                
                # Проверяем, что флаг действительно установлен
                if not db.is_notification_sent(event_id, user_id):
                    logging.error(f"Ошибка: флаг notification_sent не был установлен для встречи {event_id}")
                else:
                    logging.info(f"Отправлено уведомление через /check пользователю {user_id} о встрече {event['summary']}")
            else:
                # Если встреча уже известна, проверяем изменения
                known_event = next((e for e in known_events if e['event_id'] == event_id), None)
                if known_event and (known_event['start_time'] != start_time or known_event['end_time'] != end_time):
                    changed_events_count += 1
                    change_info = (
                        f"🔄 {hbold('Изменение в онлайн-встрече:')}\n\n"
                        f"📌 {hbold(event['summary'])}\n"
                        f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                        f"🔗 {event['hangoutLink']}\n"
                    )
                    await message.answer(change_info, parse_mode="HTML")
                
                # Обновляем данные встречи
                db.add_known_event(
                    event_id=event_id,
                    summary=event['summary'],
                    start_time=start_time,
                    end_time=end_time,
                    user_id=user_id,
                    notification_sent=True  # Обновляем флаг на всякий случай
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
        
        if new_events_count == 0 and deleted_events_count == 0 and changed_events_count == 0:
            await message.answer("Изменений в расписании онлайн-встреч не найдено.")
            
    except Exception as e:
        logging.error(f"Ошибка при проверке встреч: {e}")
        await message.answer(f"❌ Произошла ошибка: {str(e)}")

# Команда /localauth для авторизации через локальный сервер
@dp.message(Command("localauth"))
async def local_auth_command(message: Message):
    user_id = message.from_user.id
    
    await message.answer("🔄 Запускаю локальный сервер авторизации...")
    
    try:
        # Запускаем локальный сервер авторизации
        creds = await get_credentials_with_local_server()
        
        if creds:
            # Сохраняем токен в базу данных
            db.save_token(user_id, json.loads(creds.to_json()))
            
            await message.answer(
                "✅ Авторизация успешно завершена!\n\n"
                "Теперь вы можете использовать команды бота:\n"
                "/check - проверить предстоящие встречи\n"
                "/week - показать встречи на неделю"
            )
            
            # Обновляем USER_ID
            global USER_ID
            USER_ID = str(user_id)
        else:
            await message.answer(
                "❌ Не удалось получить учетные данные.\n"
                "Попробуйте другой способ авторизации: /serverauth"
            )
    except Exception as e:
        logging.error(f"Ошибка при локальной авторизации: {e}")
        await message.answer(
            f"❌ Произошла ошибка при авторизации: {str(e)}\n"
            "Попробуйте другой способ авторизации: /serverauth"
        )

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

async def notify_before_meeting(meeting, user_id, minutes_before):
    """Отправляет уведомление за указанное количество минут до начала встречи"""
    event_id, summary, hangout_link, start_time = meeting
    try:
        start_dt = safe_parse_datetime(start_time)
        now = datetime.now(timezone.utc)
        reminder_time = start_dt - timedelta(minutes=minutes_before)
        
        # Проверяем, нужно ли отправлять уведомление
        if now >= reminder_time and not db.is_event_started(event_id, user_id, minutes_before):
            await bot.send_message(
                user_id,
                f"⏰ Напоминание: встреча {summary} начнется через {minutes_before} минут.\n🔗 {hangout_link}",
                parse_mode="HTML"
            )
            # Помечаем уведомление как отправленное
            db.add_started_event(event_id, summary, start_time, None, user_id, minutes_before)
            logging.info(f"Отправлено напоминание пользователю {user_id} о встрече {summary} за {minutes_before} минут")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")

# Запуск бота
async def main():
    # Запускаем фоновую задачу для проверки встреч
    asyncio.create_task(scheduled_meetings_check())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
