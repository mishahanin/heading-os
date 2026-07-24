# Synthetic IMEIs/TAC only — never real captured device values.
import json
from scripts.utils import modem_drivers as md


class FakeSSH:
    """Records commands and replies from a scripted map (substring match)."""
    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def __call__(self, cmd, timeout=30):
        self.sent.append(cmd)
        for needle, reply in self.replies.items():
            if needle in cmd:
                return reply
        return ""


def test_e5800_read_imei_via_ubus():
    ssh = FakeSSH({"AT+GSN": json.dumps(
        {"data": "\r\n356741100000016\r\n\r\nOK\r\n", "channel_status": True})})
    d = md.E5800Driver(ssh)
    assert d.read_imei() == "356741100000016"


def test_e5800_send_egmr_ok():
    ssh = FakeSSH({"EGMR": json.dumps({"data": "\r\nOK\r\n", "channel_status": True})})
    d = md.E5800Driver(ssh)
    ok, raw = d.send_egmr("356741100000024")
    assert ok is True
    assert "AT+EGMR=1,7" in ssh.sent[0]


def test_e5800_send_egmr_channel_false_is_failure():
    ssh = FakeSSH({"EGMR": json.dumps({"data": "", "channel_status": False})})
    d = md.E5800Driver(ssh)
    ok, raw = d.send_egmr("356741100000024")
    assert ok is False


def test_xe300_read_imei_via_gl_modem():
    ssh = FakeSSH({"gl_modem AT": "\r\n356741100000032\r\nOK\r\n"})
    d = md.Xe300Driver(ssh)
    assert d.read_imei() == "356741100000032"


def test_xe300_send_egmr_ok():
    ssh = FakeSSH({"gl_modem AT": "\r\nOK\r\n"})
    d = md.Xe300Driver(ssh)
    ok, _ = d.send_egmr("356741100000032")
    assert ok is True


def test_xe300_read_status_surfaces_sim_net_signal():
    """XE300 has no ubus SIM listing -- read_status instead returns the raw
    +CPIN/+COPS/+CSQ AT replies alongside the IMEI. Regression test for the
    cmd_status refactor that used to drop these on the floor."""
    ssh = FakeSSH({
        "AT+GSN": "\r\n356741100000032\r\nOK\r\n",
        "AT+CPIN?": "\r\n+CPIN: READY\r\n\r\nOK\r\n",
        "AT+COPS?": '\r\n+COPS: 0,0,"Synthetic Carrier",7\r\n\r\nOK\r\n',
        "AT+CSQ": "\r\n+CSQ: 22,99\r\n\r\nOK\r\n",
    })
    d = md.Xe300Driver(ssh)
    st = d.read_status()
    assert "READY" in st["cpin"]
    assert "Synthetic Carrier" in st["cops"]
    assert "+CSQ: 22,99" in st["csq"]


def test_driver_for_dispatch():
    assert md.driver_for("e5800", lambda c, t=30: "").device_id == "e5800"
    assert md.driver_for("xe300", lambda c, t=30: "").device_id == "xe300"


def test_e5800_read_status_flat_reply():
    """The bus-scoped `info '{"bus":"cpu"}'` call this driver actually makes
    replies with a FLAT single-modem dict (no "modems" wrapper) on a real
    GL-E5800. Synthetic IMEIs -- never real captured values."""
    ssh = FakeSSH({
        "cellular.modem": json.dumps({
            "name": "RG650V-EU",
            "imei": [
                {"slot": "1", "imei": "111111111111111"},
                {"slot": "2", "imei": "222222222222222"},
            ],
        }),
        "cellular.sim": json.dumps({"sims": [{"slot": "1", "carrier": "TestCarrier"}]}),
    })
    d = md.E5800Driver(ssh)
    st = d.read_status()
    assert st["imeis"] == [
        {"slot": "1", "imei": "111111111111111"},
        {"slot": "2", "imei": "222222222222222"},
    ]
    assert st["sims"] == [{"slot": "1", "carrier": "TestCarrier"}]


def test_e5800_read_status_wrapped_reply():
    """A bus-less `info '{}'` call wraps the result in {"modems": [...]}."""
    ssh = FakeSSH({
        "cellular.modem": json.dumps({"modems": [{
            "name": "RG650V-EU",
            "imei": [{"slot": "1", "imei": "333333333333333"}],
        }]}),
        "cellular.sim": json.dumps({"sims": []}),
    })
    d = md.E5800Driver(ssh)
    st = d.read_status()
    assert st["imeis"] == [{"slot": "1", "imei": "333333333333333"}]


def test_e5800_modem_info_empty_reply_is_empty_dict():
    ssh = FakeSSH({"cellular.modem": json.dumps({"modems": []})})
    d = md.E5800Driver(ssh)
    assert d._modem_info() == {}
