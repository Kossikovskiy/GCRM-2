import sys
import os

# --- НАСТРОЙКА ЛОГГИРОВАНИЯ ОШИБОК ---
# Этот блок кода перенаправляет все ошибки запуска в файл, который мы можем прочитать.
PROJECT_DIR = '/var/www/u3425316/data/www/crm.покос-ропша.рф'
LOG_FILE = os.path.join(PROJECT_DIR, 'passenger_error.log')

# Перенаправляем stderr в наш лог-файл
# 'a' означает, что файл будет дополняться, а не перезаписываться
sys.stderr = open(LOG_FILE, 'a')
# --- КОНЕЦ БЛОКА ЛОГГИРОВАНИЯ ---

try:
    # --- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
    os.environ['AUTH0_DOMAIN'] = 'dev-80umollds5sbkqku.us.auth0.com' # <-- ЗАМЕНИТЕ НА ВАШИ РЕАЛЬНЫЕ ДАННЫЕ
    os.environ['AUTH0_AUDIENCE'] = 'https://grass-crm/api' # <-- ЗАМЕНИТЕ
    os.environ['SESSION_SECRET'] = 'a_very_strong_and_long_secret_string_32_chars' # <-- ЗАМЕНИТЕ
    os.environ['APP_BASE_URL'] = 'https://www.crm.покос-ропша.рф'

    # --- ЗАПУСК ПРИЛОЖЕНИЯ ---
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

except Exception as e:
    # Если на любом этапе выше произойдет ошибка, она запишется в наш лог-файл
    import traceback
    traceback.print_exc(file=sys.stderr)
    # Принудительно выходим, чтобы Passenger понял, что запуск провалился
    sys.exit(1)
