"""Runtime settings API and persisted override contracts."""

import json
from pathlib import Path

import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _make_settings(tmp_path, **updates) -> Settings:
    values = {
        "environment": "test",
        "llm_provider": "stub",
        "llm_base_url": "",
        "llm_model": "original-model",
        "llm_api_key": "server-secret",
        "embedding_provider": "stub",
        "embedding_base_url": "",
        "embedding_api_key": "embedding-secret",
        "database_url": f"sqlite:///{tmp_path}/settings.db",
        "trace_file": str(tmp_path / "settings-trace.jsonl"),
        "trace_enabled": False,
        "skills_enabled": False,
        "memory_enabled": False,
        "orchestrator_enabled": True,
        "agent_profiles_file": str(tmp_path / "profiles.json"),
        "runtime_config_file": str(tmp_path / "runtime-settings.json"),
    }
    values.update(updates)
    return Settings(**values)


def _make_app(settings: Settings):
    return create_app(
        settings,
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )


def test_settings_api_masks_secrets_and_reports_active_subagents(tmp_path):
    settings = _make_settings(tmp_path)

    with TestClient(_make_app(settings)) as client:
        response = client.get("/api/web/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["settings"]["llm"] == {
        "provider": "stub",
        "base_url": "",
        "model": "original-model",
        "api_key_configured": True,
    }
    assert data["settings"]["embedding"]["api_key_configured"] is True
    assert data["runtime"]["orchestrator_available"] is True
    assert data["runtime"]["agent_profile_count"] == 4
    assert "server-secret" not in response.text
    assert "embedding-secret" not in response.text


def test_settings_update_persists_only_submitted_overrides_and_never_echoes_secret(tmp_path):
    settings = _make_settings(tmp_path)

    with TestClient(_make_app(settings)) as client:
        response = client.patch(
            "/api/web/settings",
            json={
                "llm_provider": "openai",
                "llm_base_url": "https://llm.example/v1",
                "llm_model": "next-model",
                "llm_api_key": "new-secret",
                "tavily_api_key": "tavily-secret",
                "orchestrator_enabled": True,
                "orchestrator_max_parallel": 5,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["restart_required"] is True
    assert data["settings"]["llm"]["model"] == "next-model"
    assert data["settings"]["llm"]["api_key_configured"] is True
    assert "new-secret" not in response.text
    assert "tavily-secret" not in response.text

    persisted = json.loads((tmp_path / "runtime-settings.json").read_text(encoding="utf-8"))
    assert persisted == {
        "llm_provider": "openai",
        "llm_base_url": "https://llm.example/v1",
        "llm_model": "next-model",
        "llm_api_key": "new-secret",
        "tavily_api_key": "tavily-secret",
        "orchestrator_enabled": True,
        "orchestrator_max_parallel": 5,
    }

    with TestClient(_make_app(settings)) as restarted_client:
        restarted = restarted_client.get("/api/web/settings").json()
        active_settings = restarted_client.app.state.settings

    assert restarted["settings"]["llm"]["provider"] == "openai"
    assert restarted["settings"]["llm"]["model"] == "next-model"
    assert active_settings.llm_api_key == "new-secret"
    assert active_settings.tavily_api_key == "tavily-secret"
    assert active_settings.orchestrator_max_parallel == 5


def test_settings_update_preserves_omitted_secret_override(tmp_path):
    settings = _make_settings(tmp_path)
    config_path = tmp_path / "runtime-settings.json"
    config_path.write_text(
        json.dumps({"llm_api_key": "persisted-secret", "llm_model": "old-model"}),
        encoding="utf-8",
    )

    with TestClient(_make_app(settings)) as client:
        response = client.patch("/api/web/settings", json={"llm_model": "new-model"})

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["llm_api_key"] == "persisted-secret"
    assert persisted["llm_model"] == "new-model"


def test_settings_rejects_openai_provider_without_base_url(tmp_path):
    settings = _make_settings(tmp_path, llm_api_key="")

    with TestClient(_make_app(settings)) as client:
        response = client.patch(
            "/api/web/settings",
            json={"llm_provider": "openai", "llm_base_url": ""},
        )

    assert response.status_code == 422
    assert "LLM Base URL" in response.json()["detail"]
    assert not (tmp_path / "runtime-settings.json").exists()


def test_agents_api_explains_when_orchestration_is_disabled(tmp_path):
    settings = _make_settings(tmp_path, orchestrator_enabled=False)

    with TestClient(_make_app(settings)) as client:
        response = client.get("/api/web/agents")

    assert response.status_code == 200
    assert response.json() == {
        "agents": [],
        "count": 0,
        "enabled": False,
        "reason": "多 Agent 编排已关闭，请在设置中启用后重启服务。",
    }


def test_compose_persists_runtime_settings_and_custom_agent_profiles():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "RUNTIME_CONFIG_FILE: /data/runtime-settings.json" in compose
    assert "AGENT_PROFILES_FILE: /data/agent_profiles.json" in compose
