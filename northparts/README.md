# NorthParts — Auto Parts Store (Canada)

Полный стек: Flask backend + сайт + админ-панель + Allegro парсер.

## Структура проекта

```
northparts/
├── app.py                  ← Flask сервер (главный файл)
├── requirements.txt        ← зависимости
├── data/
│   └── db.json             ← база данных (создаётся автоматически)
├── templates/
│   ├── base.html
│   ├── store.html          ← витрина магазина
│   ├── admin_login.html
│   ├── admin_base.html
│   ├── admin_dashboard.html
│   ├── admin_products.html
│   ├── admin_orders.html
│   └── admin_settings.html
└── static/
    └── images/             ← фото товаров

allegro_parser.py           ← парсер (рядом с папкой northparts/)
```

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r northparts/requirements.txt

# 2. Запустить сервер
cd northparts
python app.py
```

Открыть:
- **Магазин**: http://localhost:5000
- **Админка**: http://localhost:5000/admin
- **Логин**: `admin` / `admin123`

## API endpoints

| Method | URL | Описание |
|--------|-----|----------|
| GET | `/api/products` | Все активные товары |
| POST | `/api/products` | Добавить товар |
| PUT | `/api/products/<id>` | Обновить товар |
| DELETE | `/api/products/<id>` | Удалить товар |
| POST | `/api/orders` | Создать заказ (из чекаута) |
| PUT | `/api/orders/<id>/status` | Сменить статус заказа |
| PUT | `/api/settings` | Сохранить настройки |
| POST | `/api/parser/run` | Запустить парсер Allegro |

## Как запустить парсер из админки

1. Зайти в Admin → Dashboard
2. Нажать "Run Allegro Parser"
3. Выбрать режим (demo/scrape/api), запрос, страницы
4. Нажать Run — товары добавятся в каталог автоматически

## Как задеплоить на сервер (VPS)

```bash
# Ubuntu/Debian
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# С nginx — настроить reverse proxy на порт 5000
```

## Смена пароля администратора

В `app.py` найди строку `admin_pass_hash` в `get_default_db()` и замени на:
```python
import hashlib
hashlib.sha256("НОВЫЙпароль".encode()).hexdigest()
```

## Переход на реальную БД (PostgreSQL)

Когда товаров станет много, замени `load_db()/save_db()` на SQLAlchemy:
```bash
pip install flask-sqlalchemy psycopg2-binary
```
