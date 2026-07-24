# Synthetic IMEIs/TAC only — never real captured device values.
import json

from scripts.utils import modem_core as mc

def test_luhn_valid_known_good():
    assert mc.luhn_valid("356741100000016") is True

def test_luhn_invalid_bad_check_digit():
    assert mc.luhn_valid("356741100000010") is False

def test_make_imei_uses_given_tac_and_valid_luhn():
    imei = mc.make_imei("35674110", "123456")
    assert imei.startswith("35674110123456")
    assert len(imei) == 15 and mc.luhn_valid(imei)

def test_generate_unique_skips_used_values():
    tac = "35674110"
    first = mc.make_imei(tac, f"{0:06d}")
    imei = mc.generate_unique(tac, {first}, rng_seed=0)
    assert imei != first and mc.luhn_valid(imei) and imei.startswith(tac)

def test_parse_at_imei_from_ubus_payload():
    assert mc.parse_at_imei("\r\n356741100000024\r\n\r\nOK\r\n") == "356741100000024"

def test_parse_at_imei_none_when_absent():
    assert mc.parse_at_imei("\r\nERROR\r\n") == ""

def test_classify_modem_rg650_is_e5800():
    assert mc.classify_modem("RG650V-EU") == "e5800"

def test_classify_modem_eg25_is_xe300():
    assert mc.classify_modem("EG25GGCR07A02M1G") == "xe300"

def test_classify_modem_unknown_is_none():
    assert mc.classify_modem("SDX99-FOO") is None

def test_migrate_config_flat_becomes_xe300():
    out = mc.migrate_config({"tac": "35674110", "factory_imei": "356741100000032"})
    assert out["devices"]["xe300"]["tac"] == "35674110"
    assert out["devices"]["xe300"]["factory_imei"] == "356741100000032"
    assert out["devices"]["xe300"]["transport"] == "gl_modem"

def test_migrate_config_already_new_is_unchanged():
    new = {"devices": {"e5800": {"transport": "ubus", "host": "192.168.8.1",
                                 "tac": "35674110", "factory_imei": "356741100000016"}}}
    assert mc.migrate_config(new) == new

def test_device_config_returns_entry():
    cfg = {"devices": {"e5800": {"transport": "ubus", "host": "h",
                                 "tac": "35674110", "factory_imei": "x"}}}
    assert mc.device_config(cfg, "e5800")["transport"] == "ubus"

def test_device_config_missing_raises():
    import pytest
    with pytest.raises(KeyError):
        mc.device_config({"devices": {}}, "e5800")

def test_migrate_ledger_flat_to_per_device_preserves_used():
    flat = {"tac": "35674110", "_note": "Synthetic test device.",
            "current": {"imei": "356741100000016", "verified": True},
            "history": [{"imei": "356741100000024"}],
            "used": ["356741100000016", "356741100000032"]}
    out = mc.migrate_ledger(flat)
    assert out["devices"]["xe300"]["current"]["imei"] == "356741100000016"
    assert out["devices"]["xe300"]["history"][0]["imei"] == "356741100000024"
    assert out["used"] == ["356741100000016", "356741100000032"]
    assert out["_note"] == "Synthetic test device."  # unknown keys carried through
    assert "current" not in out and "history" not in out

def test_migrate_ledger_idempotent():
    flat = {"tac": "35674110", "current": None, "history": [], "used": []}
    once = mc.migrate_ledger(flat)
    assert mc.migrate_ledger(once) == once

def test_device_ledger_inits_missing_device():
    led = {"devices": {}, "used": []}
    entry = mc.device_ledger(led, "e5800", "35674110")
    assert entry == {"tac": "35674110", "current": None, "history": []}
    assert led["devices"]["e5800"] is entry

def test_save_ledger_atomic_roundtrip(tmp_path):
    p = tmp_path / "led.json"
    led = {"devices": {"xe300": {"tac": "1", "current": None, "history": []}}, "used": []}
    mc.save_ledger(p, led)
    assert json.loads(p.read_text()) == led
    assert not list(tmp_path.glob("*.tmp"))  # temp file cleaned up
