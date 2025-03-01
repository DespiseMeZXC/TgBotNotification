import os
import asyncio
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv
import json
import uuid
import logging

# Загрузка переменных окружения
load_dotenv()

# Если изменить эти области, удалите файл token.json
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Словарь для хранения состояний авторизации пользователей
auth_states = {}

async def get_credentials(user_id=None):
    """Получение и обновление учетных данных Google."""
    creds = None
    token_file = 'token.json'
    
    # Если указан user_id, используем персональный файл токена
    if user_id:
        token_file = f'token_{user_id}.json'
    
    # Файл token.json хранит токены доступа и обновления пользователя
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_info(
            json.loads(open(token_file).read()), SCOPES)
    
    # Если нет действительных учетных данных, возвращаем None
    # Авторизация будет запущена из бота
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Сохраняем обновленные учетные данные
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        else:
            return None
    
    return creds

def create_auth_url(user_id):
    """Создает URL для авторизации и сохраняет состояние."""
    flow = InstalledAppFlow.from_client_secrets_file(
        'credentials.json', 
        SCOPES, 
        # Используем стандартный редирект для OOB (Out-of-Band) авторизации
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )
    
    # Добавляем дополнительные параметры для корректного запроса
    auth_url, state = flow.authorization_url(
        # Запрашиваем оффлайн доступ для получения refresh_token
        access_type='offline',
        # Включаем prompt для гарантированного получения refresh_token
        prompt='consent',
        # Включаем ранее предоставленные разрешения
        include_granted_scopes='true'
    )
    
    # Сохраняем flow для последующего использования
    auth_states[user_id] = {
        'flow': flow,
        'state': state
    }
    
    return auth_url

async def process_auth_code(user_id, code):
    """Обрабатывает код авторизации и сохраняет токен."""
    if user_id not in auth_states:
        return False, "Сессия авторизации истекла. Пожалуйста, начните заново с команды /auth."
    
    flow = auth_states[user_id]['flow']
    
    try:
        # Обмениваем код на токены
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        # Сохраняем учетные данные
        token_file = f'token_{user_id}.json'
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
        
        # Удаляем состояние авторизации
        del auth_states[user_id]
        
        return True, "Авторизация успешно завершена! Теперь вы можете использовать команды бота."
    except Exception as e:
        return False, f"Ошибка при обработке кода авторизации: {str(e)}"

async def get_upcoming_events(limit=10, time_min=None, time_max=None, user_id=None):
    """Получение предстоящих событий из Google Calendar."""
    loop = asyncio.get_event_loop()
    
    # Получаем учетные данные
    creds = await get_credentials(user_id)
    
    # Если нет учетных данных, возвращаем пустой список
    if not creds:
        return []
    
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

async def get_credentials_with_local_server(user_id):
    """Получение учетных данных через локальный сервер."""
    token_file = f'token_{user_id}.json'
    
    try:
        # Создаем flow с локальным сервером
        flow = InstalledAppFlow.from_client_secrets_file(
            'credentials.json', SCOPES)
        
        # Запускаем локальный сервер для авторизации в отдельном потоке
        loop = asyncio.get_event_loop()
        creds = await loop.run_in_executor(None, lambda: flow.run_local_server(port=0))
        
        # Сохраняем учетные данные
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
        
        return creds
    except Exception as e:
        logging.error(f"Ошибка при авторизации через локальный сервер: {e}")
        return None 
