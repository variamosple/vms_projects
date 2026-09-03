import pytest
from unittest.mock import MagicMock
from src.utils.health_service import HealthService
from src.model.health import HealthResponse

def test_health_service_database_up():
    mock_db = MagicMock()
    mock_db.execute.return_value = None

    service = HealthService(service_name="vms_projects", version="1.2.3")
    response, status_code = service.assess_health(mock_db)

    assert status_code == 200
    assert response.status == "UP"
    assert response.serviceName == "vms_projects"
    assert response.version == "1.2.3"
    assert response.checks.database.status == "UP"
    assert response.checks.database.latencyMs >= 0
    assert response.checks.memory.percentage >= 0

def test_health_service_database_down():
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("Database connection refused")

    service = HealthService(service_name="vms_projects")
    response, status_code = service.assess_health(mock_db)

    assert status_code == 503
    assert response.status == "DEGRADED"
    assert response.checks.database.status == "DOWN"
    assert "Database connection refused" in response.checks.database.message

if __name__ == "__main__":
    test_health_service_database_up()
    test_health_service_database_down()
    print("All HealthService unit tests passed successfully! 🎉")
