from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.db_connector import get_db
from src.utils.health_service import HealthService
from src.model.health import HealthResponse

health_router = APIRouter(
    prefix="/health",
    tags=["Health", "Monitoring"],
)

health_service = HealthService()

@health_router.get(
    "",
    response_model=HealthResponse,
    summary="Get service health status",
    description="Returns detailed health metrics including database status, latency, and memory consumption."
)
def get_health(db: Session = Depends(get_db)):
    health_data, status_code = health_service.assess_health(db)
    return JSONResponse(
        status_code=status_code,
        content=health_data.model_dump(),
    )
