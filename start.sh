#!/bin/bash
# Этот скрипт выполняется из рабочей директории /var/www/crm/GCRM-2, указанной в crm.service

# Активируем виртуальное окружение
source /var/www/crm/venv/bin/activate

# Запускаем приложение. 
# Python-скрипт main.py сам найдет и загрузит .env файл из текущей директории.
exec /var/www/crm/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
