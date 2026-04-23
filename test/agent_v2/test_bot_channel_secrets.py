from api.db.services import bot_channel_service as svc


def test_bot_channel_secret_roundtrip(monkeypatch):
    monkeypatch.setattr(svc.settings, "SECRET_KEY", "unit-test-bot-secret")

    encrypted = svc.encrypt_config_secrets({
        "app_id": "cli_xxx",
        "app_secret": "secret-1",
        "encrypt_key": "event-key",
        "verification_token": "verify-token",
        "api_base": "https://open.feishu.cn",
    })

    assert encrypted["app_id"] == "cli_xxx"
    assert encrypted["app_secret"].startswith("enc:v1:")
    assert encrypted["encrypt_key"].startswith("enc:v1:")
    assert encrypted["verification_token"].startswith("enc:v1:")

    decrypted = svc.decrypt_config_secrets(encrypted)
    assert decrypted["app_secret"] == "secret-1"
    assert decrypted["encrypt_key"] == "event-key"
    assert decrypted["verification_token"] == "verify-token"


def test_bot_channel_plaintext_is_backward_compatible(monkeypatch):
    monkeypatch.setattr(svc.settings, "SECRET_KEY", "unit-test-bot-secret")

    assert svc.decrypt_config_secrets({"app_secret": "legacy-secret"}) == {
        "app_secret": "legacy-secret"
    }


def test_bot_channel_config_update_preserves_omitted_secrets(monkeypatch):
    monkeypatch.setattr(svc.settings, "SECRET_KEY", "unit-test-bot-secret")
    existing = svc.encrypt_config_secrets({
        "app_id": "old-id",
        "app_secret": "old-secret",
        "encrypt_key": "old-key",
    })

    merged = svc.merge_config_update(existing, {
        "app_id": "new-id",
        "app_secret": "",
        "encrypt_key": "old***ey",
        "api_base": "https://example.com",
    })
    decrypted = svc.decrypt_config_secrets(merged)

    assert decrypted["app_id"] == "new-id"
    assert decrypted["app_secret"] == "old-secret"
    assert decrypted["encrypt_key"] == "old-key"
    assert decrypted["api_base"] == "https://example.com"
