from scripts.utils.modem_ssh import shquote


def test_shquote_wraps_and_escapes_single_quotes():
    assert shquote("AT+GSN") == "'AT+GSN'"
    assert shquote("it's") == "'it'\"'\"'s'"
