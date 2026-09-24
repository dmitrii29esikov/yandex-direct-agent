import os

from mcp_instance import mcp
from tools import campaigns, metrica, reports, enrich, tag_manager, site_parser, audit, audit_files
from tools import accounts, audit_online, audiences

# Всегда работаем из папки проекта: инструменты пишут отчёты/снимки относительными путями.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    mcp.run()
