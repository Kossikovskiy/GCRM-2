#!/bin/bash
# Скрипт для создания резервных копий базы данных PostgreSQL

# Останавливать выполнение при ошибках
set -e

# --- НАСТРОЙКИ ---
# Абсолютный путь к папке с бэкапами
BACKUP_DIR="/var/www/crm/GCRM-2/backups"
# Количество дней, в течение которых нужно хранить бэкапы (2 = сегодня и вчера)
RETENTION_DAYS=2
# Путь к файлу окружения
ENV_FILE="/var/www/crm/GCRM-2/.env"

# --- ЛОГИКА СКРИПТА ---

echo "---"
echo "Запуск процесса резервного копирования: $(date)"

# Проверяем, существует ли .env файл и загружаем его
if [ -f "$ENV_FILE" ]; then
    echo "... Загружаю переменные из $ENV_FILE ..."
    export $(cat "$ENV_FILE" | grep -v '#' | xargs)
else 
    echo "⚠️  Предупреждение: Файл .env не найден в $ENV_FILE" >&2
fi

# Проверяем, установлена ли переменная DATABASE_URL после попытки загрузки
if [ -z "$DATABASE_URL" ]; then
    echo "❌ Ошибка: Переменная окружения DATABASE_URL не установлена. Выход." >&2
    exit 1
fi

# Создаем имя файла с датой и временем
DATE_STAMP=$(date +"%Y-%m-%d_%H-%M")
FILE_NAME="backup-$DATE_STAMP.sql.gz"
FULL_PATH="$BACKUP_DIR/$FILE_NAME"

echo "📄 Имя файла: $FILE_NAME"

# Создаем бэкап с помощью pg_dump и сжимаем его с помощью gzip
echo "⏳ Создаю дамп базы данных..."
pg_dump --dbname="$DATABASE_URL" -Fc | gzip > "$FULL_PATH"

echo "✅ Резервная копия успешно создана: $FULL_PATH"

# Удаляем старые бэкапы (старше $RETENTION_DAYS-1 дней)
echo "🧹 Удаляю старые бэкапы (старше $RETENTION_DAYS дней)..."
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +$(($RETENTION_DAYS - 1)) -print -delete

echo "✅ Очистка завершена."
echo "---"
