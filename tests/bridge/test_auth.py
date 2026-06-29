from scripts.bridge_daemon.auth import generate_token, get_or_create_token, validate

def test_generate_token_deterministic_per_workspace(workspace_root):
    t1 = generate_token(workspace_root)
    t2 = generate_token(workspace_root)
    assert t1 != t2  # nonce makes each call distinct

def test_get_or_create_persists(workspace_root):
    t1 = get_or_create_token(workspace_root)
    t2 = get_or_create_token(workspace_root)
    assert t1 == t2  # second call reads the persisted token
    token_file = workspace_root / ".daemon-state" / "token"
    assert token_file.read_text().strip() == t1

def test_validate_accepts_matching_token():
    assert validate("abc123def", "abc123def") is True  # pragma: allowlist secret

def test_validate_rejects_mismatch():
    assert validate("abc123def", "xyz789xyz") is False  # pragma: allowlist secret

def test_validate_handles_none_and_empty():
    # None or empty `provided` -> False (does not raise)
    assert validate(None, "abc") is False
    assert validate("", "abc") is False
    # None or empty `expected` -> False (short-circuit, depends on Fix 1)
    assert validate("abc", None) is False
    assert validate("abc", "") is False
