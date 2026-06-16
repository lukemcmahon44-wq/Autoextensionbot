"""Config tests: fail-closed live gate, FORCE_PAPER override, base-URL choice."""
import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src import config as config_mod


def write_key(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def _dump(tmp_path, cfg):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def test_force_paper_overrides_live_default(config, tmp_path, monkeypatch):
    config["mode"]["paper_trading"] = False           # shipped live...
    monkeypatch.setenv("FORCE_PAPER", "true")          # ...but force paper
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    s = config_mod.load_settings(_dump(tmp_path, config))
    assert s.paper is True
    assert s.force_paper is True
    assert s.api_base == config["mode"]["api_base_demo"]


def test_live_without_credentials_refuses(config, tmp_path, monkeypatch):
    config["mode"]["paper_trading"] = False
    monkeypatch.delenv("FORCE_PAPER", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_live_with_credentials_uses_live_base(config, tmp_path, monkeypatch):
    key = tmp_path / "k.pem"
    write_key(key)
    config["mode"]["paper_trading"] = False
    monkeypatch.delenv("FORCE_PAPER", raising=False)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(key))
    s = config_mod.load_settings(_dump(tmp_path, config))
    assert s.paper is False
    assert s.api_base == config["mode"]["api_base_live"]


def test_paper_without_credentials_ok(config, tmp_path, monkeypatch):
    config["mode"]["paper_trading"] = True
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    s = config_mod.load_settings(_dump(tmp_path, config))
    assert s.paper is True


def test_bad_private_key_refused(config, tmp_path, monkeypatch):
    bad = tmp_path / "bad.pem"
    bad.write_text("definitely not a key")
    config["mode"]["paper_trading"] = False
    monkeypatch.delenv("FORCE_PAPER", raising=False)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "abc-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(bad))
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_invalid_entry_band_rejected(config, tmp_path):
    config["strategy"]["entry_min_cents"] = 50
    config["strategy"]["entry_max_cents"] = 40   # min > max
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_stop_at_or_above_band_rejected(config, tmp_path):
    config["strategy"]["stop_loss_cents"] = 97   # >= entry_min 96 -> would fire instantly
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_take_profit_below_stop_rejected(config, tmp_path):
    config["strategy"]["take_profit_cents"] = 90   # below stop 93 -> contradictory
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_position_cap_above_total_rejected(config, tmp_path):
    config["risk"]["max_position_usd"] = 500
    config["risk"]["max_total_exposure_usd"] = 250
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_zero_concurrent_positions_rejected(config, tmp_path):
    config["risk"]["max_concurrent_positions"] = 0
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_daily_loss_limit_out_of_range_rejected(config, tmp_path):
    config["risk"]["daily_loss_limit_pct"] = 0
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_settings(_dump(tmp_path, config))


def test_valid_take_profit_accepted(config, tmp_path):
    config["mode"]["paper_trading"] = True
    config["strategy"]["take_profit_cents"] = 99
    s = config_mod.load_settings(_dump(tmp_path, config))
    assert s.strategy["take_profit_cents"] == 99
