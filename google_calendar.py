import os
import asyncio
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv
import json

# Загрузка переменных окружения
load_dotenv()

# Если изменить эти области, удалите файл token.json
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

async def get_credentials():
    """Получение и обновление учетных данных Google."""
    creds = None
    
    # Файл token.json хранит токены доступа и обновления пользователя
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_info(
            json.loads(open('token.json').read()), SCOPES)
    
    # Если нет действительных учетных данных, пользователь входит в систему
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Сохраняем учетные данные для следующего запуска
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return creds

async def get_upcoming_events(limit=10, time_min=None, time_max=None):
    """Получение предстоящих событий из Google Calendar."""
    loop = asyncio.get_event_loop()
    
    # Получаем учетные данные
    creds = await get_credentials()
    
    # Создаем сервис
    service = await loop.run_in_executor(
        None, lambda: build('calendar', 'v3', credentials=creds))
    
    # Устанавливаем временные рамки, если не указаны
    if time_min is None:
        time_min = datetime.utcnow()
    if time_max is None:
        time_max = time_min + timedelta(days=7)
    
    # Форматируем время в формат RFC3339
    time_min_str = time_min.isoformat() + 'Z'
    time_max_str = time_max.isoformat() + 'Z'
    
    # Вызываем API
    events_result = await loop.run_in_executor(
        None,
        lambda: service.events().list(
            calendarId='primary',
            timeMin=time_min_str,
            timeMax=time_max_str,
            maxResults=limit,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
    )
    
    events = events_result.get('items', [])
    
    # Возвращаем все события, а не только с Google Meet
    return events 
