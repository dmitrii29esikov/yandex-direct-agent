Yandex Direct Agent
Путь: C:\yandex_direct_agent

Назначение: MCP-сервер для Яндекс.Директа, Яндекс.Метрики,
Яндекс Тег Менеджера и анализа сайтов.

Инструментов: 33. Проверок аудита: 30. Фиксеров правок: 2.

Быстрый старт для нового чата:
  Читай PROJECT.md и README.md в C:\yandex_direct_agent

Запуск:
  1) Как MCP-сервер (для Chatbox / Cursor):
     Command: C:\yandex_direct_agent\start_mcp.bat
     Ожидаемый результат: 33 tools
  2) Из командной строки, без Chatbox:
     python cli.py audit dmitrii
     python cli.py accounts
     python cli.py --help

Ключевые файлы:
  server.py           — точка входа MCP
  mcp_instance.py     — единый объект mcp (ядро, не удалять)
  access.py           — доступ к API, режимы agency / token
  accounts_store.py   — реестр аккаунтов и токенов
  audit_engine/       — движок аудита и правок
  tools/              — MCP-инструменты

Аккаунты (реестр data/accounts.json):
  dmitrii — Дмитрий Есиков, владелец. Директ dmitrii-esikov, 7 кампаний
            (ЕПК Клининг, Офсетная печать). Счётчик 105457694 (Клининг),
            контейнер 872525.
  anton   — Антон Есиков, клиент. Директ anton-anima, 27 кампаний (Print Yard,
            Кладовкер, Outletika, LoveScore, Анима Спейс). Счётчики 46756407,
            73028443, 106104483, 108385456, 108385562. Контейнеры 142940,
            1007795, 1334804, 1346322. Целевой CPA 550 руб.
  Старые имена работают как псевдонимы: default -> dmitrii, printyard -> anton.

Данные и секреты:
  .env                — токены аккаунта dmitrii
  data/accounts.json  — реестр аккаунтов (без токенов)
  secrets/            — токены остальных аккаунтов (в .gitignore)
  data/audits/        — отчёты аудита (в .gitignore)

Важно:
  - Рабочая папка только одна: C:\yandex_direct_agent. Копии не создавать.
  - Токены и ключи — только в .env и secrets/, в документации не писать.
