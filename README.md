# Yandex Direct Agent

MCP-сервер на Python для управления Яндекс.Директом, Яндекс.Метрикой,
Яндекс Тег Менеджером и анализа сайтов — через ИИ-агентов (Cursor,
Claude Desktop) или командную строку.

## Возможности

- **Яндекс.Директ** — кампании, отчёты по статистике (показы, клики, CTR, CPC, расход), конверсии, CPA, цели Метрики, атрибуция.
- **Яндекс.Метрика** — счётчики с классификацией (metrica / yandex_business / ga), цели счётчиков.
- **Яндекс Тег Менеджер (YTM)** — теги, триггеры, переменные, аудит контейнера, снимки состояния и diff изменений.
- **Анализ сайтов** — формы, счётчики Метрики/GA/YTM, `dataLayer.push`, inline-обработчики, перехват сетевых запросов через Playwright (работает с SPA).
- **Обогащение данных** — подстановка имён вместо голых ID (кампании, счётчики, цели).

## Инструменты (17)

### Яндекс.Директ
| Инструмент | Что делает |
|---|---|
| `get_campaigns` | Список кампаний с параметрами |
| `check_api_connection` | Проверка подключения к API Директа |
| `get_campaign_stats` | Отчёты: показы, клики, CTR, CPC, конверсии, CPA |

### Яндекс.Метрика
| Инструмент | Что делает |
|---|---|
| `get_metrica_counters` | Счётчики с классификацией |
| `get_metrica_goals` | Цели конкретного счётчика |

### Обогащение (ID → имя)
| Инструмент | Что делает |
|---|---|
| `get_campaign_names` | ID кампаний → названия |
| `get_counter_names` | ID счётчиков → названия |
| `get_goal_names` | ID целей → названия |

### Яндекс Тег Менеджер
| Инструмент | Что делает |
|---|---|
| `get_ytm_tags` | Теги контейнера |
| `get_ytm_triggers` | Триггеры контейнера |
| `get_ytm_variables` | Переменные (можно только пользовательские) |
| `audit_ytm_container` | Аудит: теги без триггеров, неиспользуемые переменные, дубликаты |

### Анализ сайтов
| Инструмент | Что делает |
|---|---|
| `fetch_page` | Забрать HTML (с рендером JS) |
| `analyze_site` | Полный анализ: формы, счётчики, dataLayer, события |

### Аудит изменений
| Инструмент | Что делает |
|---|---|
| `snapshot_ytm` | Снять снимок контейнера YTM в JSON |
| `list_snapshots` | Список сохранённых снимков |
| `audit_ytm_changes` | Diff текущего состояния с последним снимком |

## Установка

```bash
git clone https://github.com/dmitrii29esikov/yandex-direct-agent.git
cd yandex-direct-agent
pip install -r requirements.txt
python -m playwright install chromium
```

---

================================================================
ДОПОЛНЕНИЕ ОТ 20.09.2026 — НАЙДЕННЫЕ ОШИБКИ И ИСПРАВЛЕНИЯ
================================================================

НАЙДЕННЫЕ ОШИБКИ
----------------
1. Кириллица в пути проекта.
   Проект лежал в C:\Users\Дмитрий\Desktop\yandex_direct_agent.
   Из-за русских букв Chatbox и Cursor передавали Python путь в виде
   кракозябр (╨Ф╨╝╨╕╤В╤А╨╕╨╣) — файл server.py "не находился".

2. Дубликат папки проекта.
   Появились две копии: на Рабочем столе и в C:\.
   Программы читали разные версии файлов — правки не срабатывали.

3. Главный баг: пустой список инструментов (tools: []).
   В файлах tools/*.py был импорт `from server import mcp`.
   Python при этом создавал ВТОРОЙ экземпляр FastMCP — инструменты
   вешались на него, а mcp.run() запускал ПЕРВЫЙ (пустой) экземпляр.
   Результат: сервер отвечает, но "17 tools" не появляется.
   Именно поэтому в Cursor сервер работал, а инструменты не активировались.

4. start_mcp.bat содержал старый путь + BOM + кириллицу.
   Chatbox запускал не тот server.py.

ИСПРАВЛЕНИЯ
-----------
1. Рабочая папка перенесена в C:\yandex_direct_agent (без кириллицы).
   Старая копия на Рабочем столе удалена.

2. Создан общий модуль mcp_instance.py — единый объект mcp и api_client:

       from mcp.server.fastmcp import FastMCP
       from api_client import YandexDirectAPIClient

       mcp = FastMCP("YandexDirectPro")
       api_client = YandexDirectAPIClient()

3. server.py переписан — берёт mcp из mcp_instance:

       from mcp_instance import mcp
       from tools import campaigns, metrica, reports, enrich, tag_manager, site_parser, audit

       if __name__ == "__main__":
           mcp.run()

4. Во ВСЕХ файлах tools/*.py первая строка заменена:
       было:  from server import mcp, api_client
       стало: from mcp_instance import mcp, api_client

5. start_mcp.bat пересоздан (ASCII, без BOM, правильный путь):

       @echo off
       "C:\Program Files\Python314\python.exe" "C:\yandex_direct_agent\server.py"

ИТОГОВЫЕ НАСТРОЙКИ MCP (Chatbox / Cursor)
-----------------------------------------
Name:                  yandex-direct-agent
Type:                  stdio (Локально)
Command:               C:\yandex_direct_agent\start_mcp.bat
Переменные окружения:  пусто
Ожидаемый результат:   17 tools

ДИАГНОСТИКА
-----------
Проверить количество инструментов:

    "C:\Program Files\Python314\python.exe" -c "from mcp_instance import mcp; from tools import campaigns, metrica, reports, enrich, tag_manager, site_parser, audit; print('tools count:', len(mcp._tool_manager._tools))"

Ожидаем: tools count: 17

Проверить импорты в файлах:

    findstr /n "mcp_instance" C:\yandex_direct_agent\server.py C:\yandex_direct_agent\tools\*.py

В каждом файле должна быть строка с mcp_instance.

ПРАВИЛА НА БУДУЩЕЕ
------------------
- Рабочая папка только одна: C:\yandex_direct_agent. Копии не создавать.
- mcp_instance.py — ядро фикса, не удалять.
- Любые новые tools/*.py импортируют mcp и api_client ТОЛЬКО из mcp_instance.
- Токены и ключи — только в .env, в этом README не писать.
- Новый чат начинать фразой: "Читай PROJECT.md и README.md в C:\yandex_direct_agent"