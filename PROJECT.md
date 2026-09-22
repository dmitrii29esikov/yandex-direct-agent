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
     Ожидаемый результат: 32 tools
  2) Из командной строки, без Chatbox:
     python cli.py audit default
     python cli.py accounts
     python cli.py --help

Ключевые файлы:
  server.py           — точка входа MCP
  mcp_instance.py     — единый объект mcp (ядро, не удалять)
  access.py           — доступ к API, режимы agency / token
  accounts_store.py   — реестр аккаунтов и токенов
  audit_engine/       — движок аудита и правок
  tools/              — MCP-инструменты

Данные и секреты:
  .env                — токены аккаунта default
  data/accounts.json  — реестр аккаунтов (без токенов)
  secrets/            — токены остальных аккаунтов (в .gitignore)
  data/audits/        — отчёты аудита (в .gitignore)

Важно:
  - Рабочая папка только одна: C:\yandex_direct_agent. Копии не создавать.
  - Токены и ключи — только в .env и secrets/, в документации не писать.
