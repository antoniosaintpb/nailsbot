# Nailsbot — запись на маникюр (Telegram)

Бот на **Python 3.11+**, **aiogram 3**, **SQLAlchemy 2 (async)** и **SQLite** (файл `data/nailsbot.db` по умолчанию).

## Возможности

- **Клиент:** календарь месяца (дни со свободными слотами с префиксом 🟢), выбор времени и услуги, ввод имени и телефона, подтверждение записи; «Мои записи» и перенос на другое время с учётом лимита «не позднее чем за N часов до начала» (N задаёт мастер).
- **Мастер** (Telegram ID в `MASTER_TELEGRAM_IDS`): календарь для ручного добавления слотов (шаг 30 минут), удаление свободных слотов, CRUD услуг и цен, настройка N часов для переноса, уведомления о новых записях и переносах, статистика за 7 дней и с начала месяца.

## Запуск

1. Скопируйте `.env.example` в `.env` и укажите `BOT_TOKEN` и свой числовой Telegram ID в `MASTER_TELEGRAM_IDS` (через запятую, если несколько).

2. Установите зависимости и запустите бота из корня репозитория:

```text
python -m pip install -r requirements.txt
python -m bot.main
```

При первом запуске создаётся БД и добавляются настройки мастера и пример услуги, если таблицы пустые.

## Проверка перед деплоем

```text
python -m compileall -q bot db services scripts config.py
python scripts/smoke.py
```

`scripts/smoke.py` создаёт временную SQLite-БД `data/smoke.db`, проверяет создание слотов, запись клиента, перенос и календарную отметку свободного дня.

## Деплой на Bothost

Рекомендуемый вариант для Bothost — Docker-деплой из `Dockerfile`.
Подробный чеклист вынесен в `BOTHOST.md`.

1. Загрузите проект в GitHub/GitLab или в Bothost напрямую.
2. В панели Bothost выберите Docker-запуск.
3. Укажите переменные окружения:

```text
BOT_TOKEN=токен_от_BotFather
MASTER_TELEGRAM_IDS=123456789
TIMEZONE=Europe/Moscow
DATABASE_URL=sqlite+aiosqlite:///./data/nailsbot.db
```

4. Стартовая команда уже указана в `Dockerfile`:

```text
python -m bot.main
```

5. Для SQLite нужно постоянное хранилище/volume на папку `/app/data`, иначе записи могут пропасть при пересоздании контейнера.

Если Bothost даёт PostgreSQL, используйте PostgreSQL вместо SQLite и задайте `DATABASE_URL` в формате SQLAlchemy async-драйвера. Для этого потребуется добавить подходящий драйвер в `requirements.txt`, например `asyncpg`.

## Бэкап SQLite

Если используется SQLite, периодически копируйте `data/nailsbot.db`. Локально или в контейнере можно запустить:

```text
python scripts/backup_sqlite.py
```

Скрипт создаст копию в `data/backups/`. На Bothost лучше настроить регулярный запуск этой команды или скачать файл БД вручную перед обновлениями.

## Подсветка дней

В обычных inline-кнопках Telegram нельзя задать цвет фона; дни со свободными окнами помечаются символом 🟢 в тексте кнопки.

## Структура

- `bot/main.py` — точка входа и роутеры.
- `bot/handlers/` — сценарии клиента, мастера и `/start`.
- `bot/keyboards/calendar.py` — календарь и callback-префиксы.
- `db/` — модели и сессия БД.
- `services/booking.py` — запись, перенос, слоты, статистика.
- `services/notify.py` — уведомления мастерам.
- `scripts/smoke.py` — быстрая проверка основных сценариев без Telegram.
- `scripts/backup_sqlite.py` — резервная копия SQLite-БД.
