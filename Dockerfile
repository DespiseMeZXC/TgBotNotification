# Используем официальный образ Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем зависимости для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .
COPY bot.py .
COPY google_calendar.py .
COPY database.py .

# Создаем директорию для хранения данных
RUN mkdir -p /data
RUN mkdir -p /data/tokens

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1
ENV TOKEN_DIR=/data/tokens
ENV DATA_DIR=/data

# Запускаем приложение
CMD ["python", "bot.py"]
