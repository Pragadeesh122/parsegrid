import pytest

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clear_ambient_env(monkeypatch):
    for var in ("FASTAPI_ENV", "AUTH_SECRET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_development_defaults_boot():
    settings = Settings(_env_file=None)
    assert settings.fastapi_env == "development"


def test_production_rejects_default_auth_secret():
    with pytest.raises(ValueError, match="AUTH_SECRET"):
        Settings(
            _env_file=None,
            fastapi_env="production",
            s3_access_key="prod-key",
            s3_secret_key="prod-secret",
        )


def test_production_rejects_default_minio_credentials():
    with pytest.raises(ValueError, match="S3_ACCESS_KEY"):
        Settings(_env_file=None, fastapi_env="production", auth_secret="x" * 40)


def test_production_with_real_secrets_boots():
    settings = Settings(
        _env_file=None,
        fastapi_env="production",
        auth_secret="x" * 40,
        s3_access_key="prod-key",
        s3_secret_key="prod-secret",
    )
    assert settings.is_production
