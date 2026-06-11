from app.services.safe_errors import (
    blocked_reason,
    public_error_message,
    sanitize_connection_error,
)


class TestBlockedReason:
    def test_metadata_database_is_blocked(self):
        # settings.database_url default → localhost:5436
        reason = blocked_reason("postgresql://x:y@localhost:5436/parsegrid")
        assert reason is not None
        assert "internal" in reason.lower()

    def test_redis_is_blocked(self):
        assert blocked_reason("redis://localhost:6380/0") is not None

    def test_user_database_on_other_port_is_allowed(self):
        assert blocked_reason("postgresql://u:p@localhost:5999/mydb") is None

    def test_external_host_is_allowed(self):
        assert blocked_reason("postgresql://u:p@db.example.com:5432/prod") is None

    def test_unparseable_string_is_allowed(self):
        # Provider-level parsing will reject it with its own error.
        assert blocked_reason("not a dsn at all") is None


class TestSanitizeConnectionError:
    def test_auth_errors_classified(self):
        exc = Exception('password authentication failed for user "admin"')
        assert sanitize_connection_error(exc) == "Connection failed: authentication failed."

    def test_unreachable_errors_classified(self):
        exc = Exception("connection to server at 10.0.0.5 port 5432 timed out")
        msg = sanitize_connection_error(exc)
        assert msg == "Connection failed: could not reach the database host."

    def test_other_errors_generic_without_detail_leak(self):
        exc = Exception("FATAL: secret internal detail xyz")
        msg = sanitize_connection_error(exc)
        assert "xyz" not in msg
        assert msg.startswith("Connection failed")


class TestPublicErrorMessage:
    def test_scrubs_embedded_dsns(self):
        exc = ValueError("insert failed on postgresql://user:hunter2@db:5432/x")
        msg = public_error_message(exc)
        assert "hunter2" not in msg
        assert "ValueError" in msg

    def test_plain_errors_keep_their_message(self):
        msg = public_error_message(KeyError("missing_table"))
        assert "missing_table" in msg
