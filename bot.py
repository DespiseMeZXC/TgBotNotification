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
    
    # Проверяем, авторизован ли пользователь
    token_file = f'token_{user_id}.json'
    if not os.path.exists(token_file):
        await message.answer(
            "Вы не авторизованы в Google Calendar.\n"
            "Используйте команду /auth для авторизации."
        )
        return
    
    await message.answer("Проверяю ваши встречи на неделю...")
    
    try:
        # Получаем события на ближайшие 7 дней
        events = await get_upcoming_events(
            time_min=datetime.now(),
            time_max=datetime.now() + timedelta(days=7),
            limit=20,
            user_id=user_id
        )
        
        if not events:
            await message.answer("У вас нет предстоящих встреч на неделю.")
            return
        
        # Группируем встречи по дням
        meetings_by_day = {}
        for event in events:
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            start_dt = safe_parse_datetime(start_time)
            day_key = start_dt.strftime('%d.%m.%Y')
            
            if day_key not in meetings_by_day:
                meetings_by_day[day_key] = []
            
            meetings_by_day[day_key].append(event)
        
        # Отправляем встречи по дням
        for day, day_events in sorted(meetings_by_day.items()):
            day_message = f"📆 {hbold(f'Встречи на {day}:')}\n\n"
            
            for event in day_events:
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                start_dt = safe_parse_datetime(start_time)
                
                # Добавляем только встречи с ссылкой на Google Meet
                if 'hangoutLink' in event:
                    day_message += (
                        f"🕒 {start_dt.strftime('%H:%M')} - {hbold(event['summary'])}\n"
                        f"🔗 [Ссылка на встречу]({event['hangoutLink']})\n\n"
                    )
            
            # Отправляем сообщение только если есть встречи с ссылками
            if "🔗" in day_message:
                await message.answer(day_message, parse_mode="HTML")
            else:
                await message.answer(f"📆 {hbold(f'На {day} нет онлайн-встреч')}", parse_mode="HTML")
    
    except Exception as e:
        logging.error(f"Ошибка при получении встреч на неделю: {e}")
        await message.answer("Произошла ошибка при получении данных о встречах.")

# Команда /reset для сброса кэша обработанных встреч
@dp.message(Command("reset"))
async def reset_processed_events(message: Message):
    if os.path.exists('processed_events.json'):
        os.remove('processed_events.json')
        await message.answer("Кэш обработанных встреч сброшен. Теперь вы получите уведомления о всех текущих встречах как о новых.")
    else:
        await message.answer("Кэш обработанных встреч пуст.")

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
    # Словарь для хранения уже обработанных встреч
    processed_events = {}
    
    # Загружаем ранее обработанные события из файла, если он существует
    if os.path.exists('processed_events.json'):
        try:
            with open('processed_events.json', 'r') as f:
                processed_events = json.load(f)
        except Exception as e:
            logging.error(f"Ошибка при загрузке обработанных событий: {e}")
    
    # Словарь для отслеживания уведомлений о скором начале встреч
    notified_events = {}
    
    logging.info(f"Запуск проверки встреч. Загружено {len(processed_events)} обработанных событий.")
    
    while True:
        try:
            # Проверяем для каждого пользователя с сохраненным токеном
            token_files = [f for f in os.listdir() if f.startswith('token_') and f.endswith('.json')]
            
            for token_file in token_files:
                try:
                    # Извлекаем user_id из имени файла
                    user_id = token_file.replace('token_', '').replace('.json', '')
                    
                    # Получаем события на ближайшие 7 дней
                    events = await get_upcoming_events(
                        time_min=datetime.now(), 
                        time_max=datetime.now() + timedelta(days=7),
                        user_id=user_id
                    )
                    
                    logging.info(f"Получено {len(events)} событий из календаря для пользователя {user_id}.")
                    
                    # Проверяем новые встречи
                    for event in events:
                        event_id = event['id']
                        
                        # Если встреча новая (не обрабатывалась ранее)
                        if event_id not in processed_events:
                            # Добавляем в словарь обработанных
                            processed_events[event_id] = {
                                'summary': event['summary'],
                                'processed_at': datetime.now().isoformat()
                            }
                            
                            # Сохраняем обновленный список обработанных событий
                            try:
                                with open('processed_events.json', 'w') as f:
                                    json.dump(processed_events, f)
                            except Exception as e:
                                logging.error(f"Ошибка при сохранении обработанных событий: {e}")
                            
                            # Отправляем уведомление о новой встрече
                            start_time = event['start'].get('dateTime', event['start'].get('date'))
                            start_dt = safe_parse_datetime(start_time)
                            
                            # Форматирование сообщения
                            meeting_info = (
                                f"🆕 {hbold('Новая встреча назначена!')}\n"
                                f"📅 {hbold(event['summary'])}\n"
                                f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                            )
                            
                            # Добавляем ссылку на Google Meet, если она есть
                            if 'hangoutLink' in event and USER_ID:
                                meeting_info += f"🔗 [Присоединиться к встрече]({event['hangoutLink']})"
                                await bot.send_message(USER_ID, meeting_info, parse_mode="HTML")
                                logging.info(f"Обнаружена новая встреча: {event['summary']} (ID: {event_id})")
                    
                    # Проверяем встречи, которые скоро начнутся
                    current_time = datetime.now(timezone.utc)
                    for event in events:
                        event_id = event['id']
                        start_time = event['start'].get('dateTime', event['start'].get('date'))
                        start_dt = safe_parse_datetime(start_time)
                        
                        # Проверяем, начинается ли встреча в течение следующих 15 минут
                        time_until_meeting = (start_dt - current_time).total_seconds() / 60
                        
                        if 0 <= time_until_meeting <= 15:
                            # Проверяем, отправляли ли мы уже уведомление для этой встречи
                            # или прошло ли 5 минут с момента последнего уведомления
                            if event_id not in notified_events:
                                # Первое уведомление
                                notified_events[event_id] = current_time.isoformat()
                                send_notification = True
                            else:
                                # Проверяем, прошло ли 5 минут с момента последнего уведомления
                                last_notification_time = notified_events[event_id]
                                if isinstance(last_notification_time, str):
                                    # Если время хранится как строка, преобразуем его
                                    last_notification_time = safe_parse_datetime(last_notification_time)
                                    notified_events[event_id] = last_notification_time
                                
                                time_since_last = (current_time - last_notification_time).total_seconds()
                                send_notification = time_since_last >= 300  # 5 минут
                                
                                if send_notification:
                                    notified_events[event_id] = current_time.isoformat()
                            
                            if send_notification:
                                # Форматирование сообщения
                                meeting_info = (
                                    f"⚠️ {hbold('Скоро начнется встреча!')}\n"
                                    f"📅 {hbold(event['summary'])}\n"
                                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                                    f"⏱️ Осталось примерно {int(time_until_meeting)} минут\n"
                                )
                                
                                # Добавляем ссылку на Google Meet, если она есть
                                if 'hangoutLink' in event:
                                    meeting_info += f"🔗 [Присоединиться к встрече]({event['hangoutLink']})"
                                
                                if USER_ID:
                                    await bot.send_message(USER_ID, meeting_info, parse_mode="HTML")
                                    logging.info(f"Отправлено уведомление о скором начале встречи: {event['summary']} (через {int(time_until_meeting)} минут)")
                    
                    # Очищаем словарь от старых событий (прошедших)
                    current_time = datetime.now(timezone.utc)
                    new_processed_events = {}

                    for event_id, event_data in processed_events.items():
                        # Проверяем, есть ли это событие в текущих событиях
                        event_still_exists = False
                        for event in events:
                            if event['id'] == event_id:
                                event_still_exists = True
                                break
                        
                        # Если событие всё ещё существует, сохраняем его
                        if event_still_exists:
                            new_processed_events[event_id] = event_data

                    processed_events = new_processed_events

                    # Сохраняем обновленный список обработанных событий
                    try:
                        with open('processed_events.json', 'w') as f:
                            json.dump(processed_events, f)
                    except Exception as e:
                        logging.error(f"Ошибка при сохранении обработанных событий: {e}")
                    
                    logging.info(f"Очистка завершена. Осталось {len(processed_events)} обработанных событий.")
                
                except Exception as e:
                    logging.error(f"Ошибка при проверке встреч для пользователя {user_id}: {e}")
            
        except Exception as e:
            logging.error(f"Ошибка при проверке встреч: {e}")
        
        # Проверяем каждые 5 минут
        await asyncio.sleep(int(os.getenv("CHECK_INTERVAL", 300)))

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
    auth_url = create_auth_url(user_id)
    
    await message.answer(
        f"Для авторизации в Google Calendar, пожалуйста, перейдите по ссылке:\n\n"
        f"{auth_url}\n\n"
        f"После авторизации вы получите код. Отправьте его мне в формате:\n"
        f"/code ПОЛУЧЕННЫЙ_КОД"
    )

# Команда /code для обработки кода авторизации
@dp.message(Command("code"))
async def code_command(message: Message):
    user_id = message.from_user.id
    
    # Извлекаем код из сообщения
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Пожалуйста, укажите код после команды /code")
        return
    
    code = parts[1].strip()
    
    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer("Обрабатываю код авторизации...")
    
    try:
        # Обрабатываем код авторизации
        success, result_message = await process_auth_code(user_id, code)
        
        await message.answer(result_message)
        
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
        await message.answer(f"Произошла ошибка при обработке кода авторизации: {str(e)}")

# Команда /check для принудительной проверки новых встреч
@dp.message(Command("check"))
async def force_check_meetings(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, авторизован ли пользователь
    token_file = f'token_{user_id}.json'
    if not os.path.exists(token_file):
        await message.answer(
            "Вы не авторизованы в Google Calendar.\n"
            "Используйте команду /auth для авторизации."
        )
        return
    
    await message.answer("Принудительная проверка новых встреч...")
    
    try:
        # Получаем события
        events = await get_upcoming_events(
            time_min=datetime.now(), 
            time_max=datetime.now() + timedelta(days=7),
            user_id=user_id
        )
        
        # Загружаем обработанные события
        processed_events = {}
        if os.path.exists('processed_events.json'):
            try:
                with open('processed_events.json', 'r') as f:
                    processed_events = json.load(f)
            except Exception as e:
                logging.error(f"Ошибка при загрузке обработанных событий: {e}")
        
        # Проверяем новые встречи
        new_events_count = 0
        for event in events:
            event_id = event['id']
            
            # Если встреча новая
            if event_id not in processed_events:
                new_events_count += 1
                
                # Добавляем в словарь обработанных
                processed_events[event_id] = {
                    'summary': event['summary'],
                    'processed_at': datetime.now().isoformat()
                }
                
                # Отправляем уведомление
                start_time = event['start'].get('dateTime', event['start'].get('date'))
                start_dt = safe_parse_datetime(start_time)
                
                meeting_info = (
                    f"🆕 {hbold('Новая встреча обнаружена!')}\n"
                    f"📅 {hbold(event['summary'])}\n"
                    f"🕒 {start_dt.strftime('%d.%m.%Y %H:%M')}\n"
                )
                
                if 'hangoutLink' in event:
                    meeting_info += f"🔗 [Присоединиться к встрече]({event['hangoutLink']})"
                    await message.answer(meeting_info, parse_mode="HTML")
        
        # Сохраняем обновленный список
        with open('processed_events.json', 'w') as f:
            json.dump(processed_events, f)
        
        if new_events_count == 0:
            await message.answer("Новых встреч не обнаружено.")
        else:
            await message.answer(f"Обнаружено {new_events_count} новых встреч.")
    
    except Exception as e:
        logging.error(f"Ошибка при принудительной проверке: {e}")
        await message.answer(f"Произошла ошибка: {e}")

# Команда /authstatus для проверки статуса авторизации
@dp.message(Command("authstatus"))
async def auth_status_command(message: Message):
    user_id = message.from_user.id
    token_file = f'token_{user_id}.json'
    
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
        token_file = f'token_{user_id}.json'
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
    auth_url = create_auth_url(user_id)
    
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

# Запуск бота
async def main():
    # Запускаем фоновую задачу для проверки встреч
    asyncio.create_task(scheduled_meetings_check())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
