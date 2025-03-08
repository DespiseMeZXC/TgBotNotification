import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица для хранения токенов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tokens (
                    user_id TEXT PRIMARY KEY,
                    token_data TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')
            
            # Таблица для хранения состояний авторизации
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auth_states (
                    user_id TEXT PRIMARY KEY,
                    flow_state TEXT,
                    redirect_uri TEXT,
                    created_at TEXT
                )
            ''')
            
            # Таблица для хранения обработанных событий
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    summary TEXT,
                    start_time TEXT,
                    notified_at TEXT,
                    user_id TEXT
                )
            ''')
            
            # Таблица для хранения начатых событий
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS started_events (
                    event_id TEXT PRIMARY KEY,
                    summary TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    notified_at TEXT,
                    user_id TEXT
                )
            ''')
            
            # Таблица для хранения известных событий
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS known_events (
                    event_id TEXT PRIMARY KEY,
                    summary TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    discovered_at TEXT,
                    user_id TEXT
                )
            ''')
            
            conn.commit()

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def add_started_event(self, event_id, summary, start_time, end_time, user_id):
        """Добавление начатого события"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO started_events (event_id, summary, start_time, end_time, notified_at, user_id) VALUES (?, ?, ?, ?, ?, ?)',
                (event_id, summary, start_time, end_time, datetime.now().isoformat(), user_id)
            )
            conn.commit()

    def add_known_event(self, event_id, summary, start_time, end_time, user_id):
        """Добавление известного события"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO known_events (event_id, summary, start_time, end_time, discovered_at, user_id) VALUES (?, ?, ?, ?, ?, ?)',
                (event_id, summary, start_time, end_time, datetime.now().isoformat(), user_id)
            )
            conn.commit()

    def is_event_processed(self, event_id):
        """Проверка, было ли событие обработано"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM processed_events WHERE event_id = ?', (event_id,))
            return cursor.fetchone() is not None

    def is_event_started(self, event_id, user_id):
        """Проверка, было ли отправлено уведомление о начале события"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM started_events WHERE event_id = ? AND user_id = ?', (event_id, str(user_id)))
            return cursor.fetchone() is not None

    def is_event_known(self, event_id, user_id):
        """Проверка, известно ли событие"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM known_events WHERE event_id = ? AND user_id = ?', (event_id, str(user_id)))
            return cursor.fetchone() is not None

    def clean_old_events(self, before_date):
        """Очистка старых событий"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM processed_events WHERE start_time < ?', (before_date.isoformat(),))
            cursor.execute('DELETE FROM started_events WHERE end_time < ?', (before_date.isoformat(),))
            cursor.execute('DELETE FROM known_events WHERE end_time < ?', (before_date.isoformat(),))
            conn.commit()

    def save_auth_state(self, user_id, flow_state, redirect_uri):
        """Сохранение состояния авторизации"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO auth_states (user_id, flow_state, redirect_uri, created_at) VALUES (?, ?, ?, ?)',
                (str(user_id), json.dumps(flow_state), redirect_uri, datetime.now().isoformat())
            )
            conn.commit()

    def get_auth_state(self, user_id):
        """Получение состояния авторизации"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT flow_state, redirect_uri FROM auth_states WHERE user_id = ?', (str(user_id),))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0]), result[1]
            return None, None

    def delete_auth_state(self, user_id):
        """Удаление состояния авторизации"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM auth_states WHERE user_id = ?', (str(user_id),))
            conn.commit()

    def clean_old_auth_states(self, hours=24):
        """Очистка старых состояний авторизации"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            expiry_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            cursor.execute('DELETE FROM auth_states WHERE created_at < ?', (expiry_time,))
            conn.commit()

    def reset_all(self):
        """Сброс всех данных"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM auth_states')
            cursor.execute('DELETE FROM processed_events')
            cursor.execute('DELETE FROM started_events')
            cursor.execute('DELETE FROM known_events')
            cursor.execute('DELETE FROM tokens')
            conn.commit()

    def save_token(self, user_id, token_data):
        """Сохранение токена пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute(
                '''
                INSERT OR REPLACE INTO tokens 
                (user_id, token_data, created_at, updated_at) 
                VALUES (?, ?, COALESCE((SELECT created_at FROM tokens WHERE user_id = ?), ?), ?)
                ''',
                (str(user_id), json.dumps(token_data), str(user_id), now, now)
            )
            conn.commit()

    def get_token(self, user_id):
        """Получение токена пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT token_data FROM tokens WHERE user_id = ?', (str(user_id),))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None

    def delete_token(self, user_id):
        """Удаление токена пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM tokens WHERE user_id = ?', (str(user_id),))
            conn.commit()

    def save_processed_event(self, event_id, summary, start_time, user_id):
        """Сохранение обработанного события"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT OR REPLACE INTO processed_events 
                (event_id, summary, start_time, notified_at, user_id) 
                VALUES (?, ?, ?, ?, ?)
                ''',
                (event_id, summary, start_time, datetime.now().isoformat(), str(user_id))
            )
            conn.commit()

    def get_processed_events(self, user_id):
        """Получение всех обработанных событий пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT event_id, summary, start_time, notified_at 
                FROM processed_events 
                WHERE user_id = ?
                ''', 
                (str(user_id),)
            )
            rows = cursor.fetchall()
            return [
                {
                    'event_id': row[0],
                    'summary': row[1],
                    'start_time': row[2],
                    'notified_at': row[3]
                }
                for row in rows
            ]

    def is_event_processed(self, event_id, user_id):
        """Проверка, было ли событие обработано для конкретного пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT 1 FROM processed_events WHERE event_id = ? AND user_id = ?',
                (event_id, str(user_id))
            )
            return cursor.fetchone() is not None

    def get_known_events(self, user_id):
        """Получение всех известных событий пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT event_id, summary, start_time, end_time 
                FROM known_events 
                WHERE user_id = ?
                ''', 
                (str(user_id),)
            )
            rows = cursor.fetchall()
            return [
                {
                    'event_id': row[0],
                    'summary': row[1],
                    'start_time': row[2],
                    'end_time': row[3]
                }
                for row in rows
            ]

    def delete_known_event(self, event_id, user_id):
        """Удаление известного события"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM known_events WHERE event_id = ? AND user_id = ?',
                (event_id, str(user_id))
            )
            conn.commit() 
