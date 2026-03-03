import sys
import os

# --- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
# Поскольку панель ISPmanager не позволяет задать переменные,
# мы пропишем их здесь. Замените значения на ваши реальные данные.
# ВНИМАНИЕ: Хранить секреты в коде — небезопасно. Это вынужденная мера.

os.environ['AUTH0_DOMAIN'] = 'dev-80umollds5sbkqku.us.auth0.com'  # <-- ЗАМЕНИТЕ НА ВАШ ДОМЕН AUTH0
os.environ['AUTH0_AUDIENCE'] = 'https://grass-crm/api' # <-- ЗАМЕНИТЕ НА ВАШ AUDIENCE (IDENTIFIER)
os.environ['SESSION_SECRET'] = 'a_very_strong_and_long_secret_string_32_chars'  # <-- ЗАМЕНИТЕ НА ВАШ СЕКРЕТ
os.environ['APP_BASE_URL'] = 'https://www.crm.покос-ропша.рф'

# --- КОНЕЦ НАСТРОЙКИ ПЕРЕМЕННЫХ ---

# Реальный путь к проекту на хостинге
PROJECT_DIR = '/var/www/u3425316/data/www/crm.покос-ропша.рф'

# Путь к Python в venv (виртуальном окружении)
INTERP = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python3')

# Перезапуск скрипта с правильным интерпретатором из venv
if sys.executable != INTERP:
    os.execl(INTERP, INTERP, *sys.argv)

# Добавление папки проекта в пути Python для корректных импортов
sys.path.insert(0, PROJECT_DIR)

# Импорт и запуск вашего FastAPI приложения
from api.main import app
application = app

# Настройка логгирования для отладки
import logging
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
