"""Tests for agent.config — stage env loading and validation."""

import pytest

from agent.config import ConfigError, Settings, load_settings

MINIMAL_ENV = (
    "LOCATION_ID=TEST\n"
    "DEVICE_ID=dam-test\n"
    "TIMEZONE=Asia/Seoul\n"
    "UPLOAD_SIGNER_URL=https://signer.example\n"
    "DEVICE_TOKEN=test-token\n"
)


def _write_env(tmp_path, content):
    env_file = tmp_path / ".env.test"
    env_file.write_text(content, encoding="utf-8")
    return env_file


def test_minimal_env_uses_defaults(tmp_path):
    settings = load_settings(stage="test", env_file=_write_env(tmp_path, MINIMAL_ENV))
    assert settings == Settings(
        stage="test",
        location_id="TEST",
        device_id="dam-test",
        timezone="Asia/Seoul",
        upload_signer_url="https://signer.example",
        device_token="test-token",
    )
    assert settings.s3_bucket == "knh-dam-store"
    assert settings.video_minutes == 1
    assert settings.capture_size == (1280, 720)


@pytest.mark.parametrize(
    ("video_minutes", "expected_interval"),
    [(1, 48), (2, 24), (3, 16)],
)
def test_interval_table(tmp_path, video_minutes, expected_interval):
    env_file = _write_env(tmp_path, MINIMAL_ENV + f"VIDEO_MINUTES={video_minutes}\n")
    settings = load_settings(stage="test", env_file=env_file)
    assert settings.interval_s == expected_interval


def test_video_minutes_must_be_positive(tmp_path):
    env_file = _write_env(tmp_path, MINIMAL_ENV + "VIDEO_MINUTES=0\n")
    with pytest.raises(ConfigError, match="VIDEO_MINUTES"):
        load_settings(stage="test", env_file=env_file)


def test_overrides_are_read(tmp_path):
    env_file = _write_env(
        tmp_path,
        MINIMAL_ENV + "VIDEO_MINUTES=2\nCAPTURE_SIZE=1920,1080\nVIEWER_PORT=0\n",
    )
    settings = load_settings(stage="test", env_file=env_file)
    assert settings.video_minutes == 2
    assert settings.capture_size == (1920, 1080)
    assert settings.viewer_port == 0


def test_missing_required_key_fails_loudly(tmp_path):
    env_file = _write_env(tmp_path, "LOCATION_ID=TEST\n")
    with pytest.raises(ConfigError, match="DEVICE_ID"):
        load_settings(stage="test", env_file=env_file)


def test_missing_stage_file_fails_loudly(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(stage="nope", env_file=tmp_path / ".env.nope")


def test_no_stage_fails_loudly(monkeypatch):
    monkeypatch.delenv("STAGE", raising=False)
    with pytest.raises(ConfigError, match="STAGE"):
        load_settings()


def test_bad_capture_size_fails_loudly(tmp_path):
    env_file = _write_env(tmp_path, MINIMAL_ENV + "CAPTURE_SIZE=wide\n")
    with pytest.raises(ConfigError, match="CAPTURE_SIZE"):
        load_settings(stage="test", env_file=env_file)
