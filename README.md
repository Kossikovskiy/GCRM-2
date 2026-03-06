# GrassCRM

CRM-система для компании по покосу газонов и ландшафтным работам. Бэкенд на FastAPI, фронтенд — одностраничное приложение на чистом JS. Развёрнута на VPS под Ubuntu 24.04, данные хранятся в PostgreSQL (Supabase).

---

## Стек

| Слой | Технология |
|---|---|
| Бэкенд | Python 3.12, FastAPI, SQLAlchemy |
| Веб-сервер | Uvicorn + systemd |
| База данных | PostgreSQL (Supabase, Session Pooler) |
| Аутентификация | Auth0 (вход через Яндекс) |
| Экспорт | openpyxl (Excel), reportlab (PDF) |
| Уведомления | python-telegram-bot |

---

## Возможности

- **Сделки** — канбан-доска с drag & drop, карточки с услугами, скидкой, налогом
- **Контакты** — карточка клиента с историей всех сделок и KPI
- **Задачи** — список с приоритетами, назначением на сотрудника и сроками
- **Расходы** — учёт по категориям с фильтром по году
- **Техника** — учёт оборудования и история ТО
- **Склад** — остатки расходных материалов
- **Прайс-лист** — услуги с единицами измерения и ценами
- **Налог** — расчёт налога по ставке с режимами «включён» / «сверху»
- **Аналитика** — воронка, динамика выручки/расходов, топ услуг (только Admin)
- **Бюджет** — планирование по периодам с процентом исполнения (только Admin)
- **Экспорт** — отчёт в Excel (4 листа) и PDF за выбранный год
- **Роли** — Admin видит всё, User видит только свои сделки и задачи

---

## Структура проекта

```
GCRM-2/
├── main.py          # FastAPI-приложение (v13+)
├── index.html       # Фронтенд (SPA, без фреймворков)
├── bot.py           # Telegram-бот с утренними/вечерними отчётами
├── backup.sh        # Скрипт резервного копирования БД
├── start.sh         # Запуск uvicorn для systemd
├── requirements.txt # Зависимости Python
├── .env             # Секреты (не коммитить!)
└── backups/         # Дампы БД (не коммитить!)
```

---

## Быстрый старт (локально)

```bash
git clone <URL> && cd GCRM-2
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # заполнить переменные
uvicorn main:app --reload --port 8000
```

Swagger UI: `http://127.0.0.1:8000/docs`

### Переменные окружения (`.env`)

```
DATABASE_URL=postgresql://user:password@host:5432/dbname
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_AUDIENCE=...
APP_BASE_URL=http://localhost:8000
SESSION_SECRET=случайная-строка
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## Деплой на сервере

### Параметры

| | |
|---|---|
| Сервер | VPS Timeweb Cloud, Ubuntu 24.04 |
| IP | 77.232.134.112 |
| Путь | `/var/www/crm/GCRM-2` |
| Venv | `/var/www/crm/venv` |
| Порт | 127.0.0.1:8000 (за nginx/proxy) |

### systemd unit (`/etc/systemd/system/crm.service`)

```ini
[Unit]
Description=Grass CRM FastAPI
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/crm/GCRM-2
EnvironmentFile=/var/www/crm/GCRM-2/.env
ExecStart=/var/www/crm/GCRM-2/start.sh
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

После изменения файла: `systemctl daemon-reload`

### Деплой новой версии

```bash
deploy
```

Алиас `deploy` делает: `stop → fuser -k 8000/tcp → git reset --hard origin/main → start`

Добавить в `/etc/profile.d/deploy.sh`:
```bash
alias deploy='systemctl stop crm; fuser -k 8000/tcp 2>/dev/null; sleep 3; git -C /var/www/crm/GCRM-2 fetch --all && git -C /var/www/crm/GCRM-2 reset --hard origin/main && systemctl start crm'
```

---

## Роли пользователей

Роль задаётся вручную в таблице `users` в Supabase (колонка `role`).

| Роль | Сделки | Задачи | Расходы | Аналитика | Бюджет |
|---|---|---|---|---|---|
| `Admin` | Все | Все | Полный доступ | ✅ | ✅ |
| `User` | Только свои | Только свои | Скрыты | ❌ | ❌ |

---

## Telegram-бот

Ежедневные отчёты в Telegram-чат.

- **09:00** (будни) — задачи на сегодня, активные сделки, напоминания о ТО
- **18:00** (ежедневно) — итоги дня, задачи на завтра, мотивирующая цитата

Управление расписанием: `crontab -e`

Ручной запуск:
```bash
source /var/www/crm/venv/bin/activate && python /var/www/crm/GCRM-2/bot.py
```

---

## Резервное копирование

Автоматически в 03:00 через cron. Хранятся 2 последних дампа.

```
/var/www/crm/GCRM-2/backups/backup-YYYY-MM-DD.sql.gz
```

Ручной бэкап:
```bash
/var/www/crm/GCRM-2/backup.sh
```

Восстановление:
```bash
gunzip < backups/backup-2026-03-06.sql.gz | psql -d "$DATABASE_URL"
```

> ⚠️ Восстановление **перезаписывает** текущую БД.
