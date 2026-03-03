
import sys
import os

# --- НАСТРОЙКА ЛОГГИРОВАНИЯ ОШИБОК ---
PROJECT_DIR = '/var/www/u3425316/data/www/crm.покос-ропша.рф'
LOG_FILE = os.path.join(PROJECT_DIR, 'passenger_error.log')
sys.stderr = open(LOG_FILE, 'a')
# --- КОНЕЦ БЛОКА ЛОГГИРОВАНИЯ ---

try:
    # --- НАСТРОЙКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
    # ВАЖНО: Замените 'YOUR_CLIENT_SECRET' на ваш реальный ключ Auth0
    os.environ['AUTH0_CLIENT_SECRET'] = '1FRpZiQxnpp8hF-xk7ihUCTof54kYXSw0x3RWzLbVD-sFrvSWQME-r13AYFVxCYL' 
    
    os.environ['AUTH0_DOMAIN'] = 'dev-80umollds5sbkqku.us.auth0.com'
    os.environ['AUTH0_AUDIENCE'] = 'https://grass-crm/api'
    os.environ['SESSION_SECRET'] = 'a_very_strong_and_long_secret_string_32_chars' 
    os.environ['APP_BASE_URL'] = 'https://www.crm.покос-ропша.рф'

    # --- ЗАПУСК ПРИЛОЖЕНИЯ ---
    INTERP = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python3')
    if sys.executable != INTERP:
        os.execl(INTERP, INTERP, *sys.argv)

    sys.path.insert(0, PROJECT_DIR)

    # Исправленный импорт: убираем 'api.'
    from main import app 
    application = app

except Exception as e:
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
