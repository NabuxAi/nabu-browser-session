from nabu_browser_session import mask_account, mask_secret, mask_session

SECRET = "COOKIEVALUE_sentinel_9f3a"


def test_mask_session_hides_names_and_values() -> None:
    text = mask_session(
        {
            "cookies": [{"name": "sessionid", "value": SECRET}],
            "origins": [{"origin": "https://example.test", "localStorage": [{"name": "k", "value": SECRET}]}],
        }
    )
    assert text == "<session cookies=1 origins=1>"
    assert SECRET not in text
    assert "sessionid" not in text


def test_mask_secret_and_account() -> None:
    assert mask_secret(SECRET) == f"<redacted len={len(SECRET)}>"
    assert SECRET not in mask_secret(SECRET)
    assert mask_secret("") == "<empty>"
    assert mask_secret(None) == "<empty>"
    assert mask_account("ab") == "<account>"
    assert "account" not in mask_account("igp_account") or "ig" in mask_account("igp_account")
    assert SECRET not in mask_account(SECRET)


def test_mask_bytes_and_token() -> None:
    assert mask_session(b"fernet-token-bytes") == "<redacted bytes len=18>"
    assert mask_session(None) == "<session empty>"
