#!/bin/bash
# Скрипт для создания резервных копий базы данных PostgreSQL

# Останавливать выполнение при ошибках
set -e

# --- НАСТРОЙКИ ---
# Абсолютный путь к папке с бэкапами
BACKUP_DIR="/var/www/crm/GCRM-2/backups"
# Количество дней, в течение которых нужно хранить бэкапы (2 = сегодня и вчера)
RETENTION_DAYS=2

# --- ЛОГИКА СКРИПТА ---

echo "---"
echo "Запуск процесса резервного копирования: $(date)"

# Проверяем, установлена ли переменная DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    echo "❌ Ошибка: Переменная окружения DATABASE_URL не найдена." >&2
    # Пробуем загрузить ее из файла .env, если он существует в родительской директории
    if [ -f "/var/www/crm/.env" ]; then
        echo "... Попытка загрузить .env файл..."
        export $(cat "/var/www/crm/.env" | xargs)
    else
       exit 1
    fi
fi

# Убедимся, что переменная загрузилась
if [ -z "$DATABASE_URL" ]; then
    echo "❌ Ошибка: DATABASE_URL так и не была установлена. Выход." >&2
    exit 1
fi


# Создаем имя файла с датой и временем
DATE_STAMP=$(date +"%Y-%m-%d_%H-%M")
FILE_NAME="backup-$DATE_STAMP.sql.gz"
FULL_PATH="$BACKUP_DIR/$FILE_NAME"

echo "📄 Имя файла: $FILE_NAME"

# Создаем бэкап с помощью pg_dump и сжимаем его с помощью gzip
# pg_dump использует переменную PGPASSWORD, которая должна быть в DATABASE_URL
echo "⏳ Создаю дамп базы данных..."
pg_dump --dbname="$DATABASE_URL" -Fc | gzip > "$FULL_PATH"

echo "✅ Резервная копия успешно создана: $FULL_PATH"

# Удаляем старые бэкапы
# -mtime +1 означает "файлы, измененные более 24*2=48 часов назад"
echo "🧹 Удаляю старые бэкапы (старше $RETENTION_DAYS дней)..."
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +$(($RETENTION_DAYS - 1)) -print -delete

echo "✅ Очистка завершена."
echo "---"
