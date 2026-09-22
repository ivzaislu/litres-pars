import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_northflank_manifest_matches_server_contract():
    manifest = json.loads(
        (ROOT / "northflank" / "litres-api.json").read_text(encoding="utf-8")
    )
    service = manifest["service"]

    assert service["name"] == "litres-api"
    assert service["branch"] == "northflank-postgres"
    assert service["dockerfile"] == "/Dockerfile"
    assert service["buildContext"] == "/"
    assert service["port"] == {
        "name": "http",
        "internalPort": 8000,
        "protocol": "HTTP",
        "public": True,
    }
    assert service["healthCheck"]["path"] == "/health"
    assert service["healthCheck"]["port"] == 8000
    assert manifest["postgres"]["connectionSecret"] == "POSTGRES_URI"
    assert manifest["postgres"]["applicationAlias"] == "DATABASE_URL"
    assert set(manifest["environment"]) == {
        "LITRES_APP_TOKEN",
        "DATABASE_URL",
        "LITRES_CACHE_TTL_SECONDS",
    }


def test_northflank_health_payload_and_docker_command():
    health = json.loads(
        (ROOT / "northflank" / "health-check.json").read_text(encoding="utf-8")
    )
    assert health["healthChecks"] == [
        {
            "protocol": "HTTP",
            "type": "livenessProbe",
            "path": "/health",
            "port": 8000,
            "initialDelaySeconds": 10,
            "periodSeconds": 30,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
            "successThreshold": 1,
        }
    ]

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'EXPOSE 8000' in dockerfile
    assert '"litres_parser.api:create_app"' in dockerfile
    assert '"--factory"' in dockerfile
    assert '"--port", "8000"' in dockerfile
    assert "LITRES_DB_PATH" not in dockerfile
