import asyncio
from datetime import datetime

from src.db_connector import SessionLocal
from src.model.modelDAO import ProjectDao

CHECK_INTERVAL_SECONDS = 15 * 60  


async def run_conflict_auto_resolve_loop():
    while True:
        try:
            db = SessionLocal()
            dao = ProjectDao(db)
            result = dao.auto_resolve_expired_conflicts()
            print(f"[{datetime.now()}] {result['message']}")
        except Exception as e:
            print("Error auto-resolving conflicts:", e)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)