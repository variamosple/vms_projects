import os
import time
import resource
from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.model.health import (
    HealthResponse,
    HealthStatus,
    HealthChecks,
    DependencyCheck,
    SystemMemoryInfo,
)

_SERVICE_START_TIME = time.time()

class HealthService:
    def __init__(self, service_name: str = "vms_projects", version: Optional[str] = None):
        self.service_name = service_name
        self.version = version or os.getenv("APP_VERSION", "1.0.0")

    def check_database(self, db: Session) -> DependencyCheck:
        start_time = time.time()
        try:
            db.execute(text("SELECT 1"))
            latency_ms = round((time.time() - start_time) * 1000, 2)
            return DependencyCheck(status="UP", latencyMs=latency_ms)
        except Exception as exc:
            latency_ms = round((time.time() - start_time) * 1000, 2)
            return DependencyCheck(
                status="DOWN",
                latencyMs=latency_ms,
                message=str(exc),
            )

    def get_memory_info(self) -> SystemMemoryInfo:
        try:
            # Memory usage using Python's standard library (resource & /proc/meminfo on Linux)
            usage = resource.getrusage(resource.RUSAGE_SELF)
            used_mb = round(usage.ru_maxrss / 1024.0, 2)  # on Linux ru_maxrss is in KB

            total_mb = 0.0
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            total_kb = float(line.split()[1])
                            total_mb = round(total_kb / 1024.0, 2)
                            break

            percent = round((used_mb / total_mb * 100), 1) if total_mb > 0 else 0.0
        except Exception:
            used_mb, total_mb, percent = 0.0, 0.0, 0.0

        return SystemMemoryInfo(
            usedMb=used_mb,
            totalMb=total_mb,
            percentage=percent,
        )

    def assess_health(self, db: Session) -> Tuple[HealthResponse, int]:
        req_start = time.time()
        db_check = self.check_database(db)
        memory_info = self.get_memory_info()

        overall_status: HealthStatus = "UP" if db_check.status == "UP" else "DEGRADED"
        http_status_code = 200 if overall_status == "UP" else 503

        response_payload = HealthResponse(
            status=overall_status,
            serviceName=self.service_name,
            version=self.version,
            uptimeSeconds=int(time.time() - _SERVICE_START_TIME),
            timestamp=datetime.now(timezone.utc).isoformat(),
            responseTimeMs=round((time.time() - req_start) * 1000, 2),
            checks=HealthChecks(
                database=db_check,
                memory=memory_info,
            ),
        )

        return response_payload, http_status_code
