
import pandas as pd

# Определяем только те колонки, которые нам нужны
columns = [
    'Контакт Имя',
    'Контакт Телефон',
    'Контакт Источник',
    'Сделка Название',
    'Сделка Сумма',
    'Сделка Дата',
    'Сделка Адрес',
    'Сделка Повторная' # Да/Нет
]

# Создаем пустой DataFrame с этими колонками
df = pd.DataFrame(columns=columns)

# Сохраняем в новый, чистый Excel-файл
file_path = 'migration_template.xlsx'
df.to_excel(file_path, index=False, engine='openpyxl')

print(f"Шаблон для миграции '{file_path}' успешно создан.")
