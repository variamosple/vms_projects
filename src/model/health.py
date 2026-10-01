from datetime import datetime, timezone
from typing import Literal, Optional, Dict
from pydantic import BaseModel, Field

HealthStatus = Literal["UP", "DEGRADED", "DOWN"]

class SystemMemoryInfo(BaseModel):
    usedMb: float = Field(..., description="Used memory in MB")
    totalMb: float = Field(..., description="Total system memory in MB")
    percentage: float = Field(..., description="Memory usage percentage")

class DependencyCheck(BaseModel):
    status: HealthStatus
    latencyMs: float
    message: Optional[str] = None

class HealthChecks(BaseModel):
    database: DependencyCheck
    memory: SystemMemoryInfo

class HealthResponse(BaseModel):
    status: HealthStatus
    serviceName: str
    version: str
    uptimeSeconds: int
    timestamp: str
    responseTimeMs: float
    checks: HealthChecks
