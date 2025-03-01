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

from google_calendar import get_upcoming_events

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
        "Я буду отправлять уведомления о предстоящих созвонах в Google Meet."
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
    await message.answer("Проверяю ваши встречи на неделю...")
    
    try:
        # Получаем события на ближайшие 7 дней
        events = await get_upcoming_events(
            time_min=datetime.now(),
            time_max=datetime.now() + timedelta(days=7),
            limit=20
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
            # Получаем события на ближайшие 7 дней
            events = await get_upcoming_events(
                time_min=datetime.now(), 
                time_max=datetime.now() + timedelta(days=7)
            )
            
            logging.info(f"Получено {len(events)} событий из календаря.")
            
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

# Команда /check для принудительной проверки новых встреч
@dp.message(Command("check"))
async def force_check_meetings(message: Message):
    await message.answer("Принудительная проверка новых встреч...")
    
    try:
        # Получаем события
        events = await get_upcoming_events(
            time_min=datetime.now(), 
            time_max=datetime.now() + timedelta(days=7)
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

# Запуск бота
async def main():
    # Запускаем фоновую задачу для проверки встреч
    asyncio.create_task(scheduled_meetings_check())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 
