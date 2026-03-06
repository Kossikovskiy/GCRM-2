#!/bin/bash

# 1. Переходим в директорию проекта
cd /var/www/crm/GCRM-2

# 2. Получаем последние изменения из Git
git pull origin main

# 3. Устанавливаем права (!!!)
echo "Setting permissions..."
chmod +x start.sh
chmod +x backup.sh # На всякий случай и для других скриптов

# 4. Устанавливаем/обновляем зависимости
source /var/www/crm/venv/bin/activate
pip install -r requirements.txt

# 5. Перезапускаем сервис, чтобы применить изменения
echo "Restarting service..."
systemctl restart crm

echo "Deployment finished!"