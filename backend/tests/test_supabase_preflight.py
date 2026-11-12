"""Readiness checks must fail safely without exposing connection credentials."""

import json
from types import SimpleNamespace

import pytest

from scripts import supabase_preflight as preflight


@pytest.mark.parametrize("origin", ["", "http://example.com", "https://example.com/auth", "https://user:pass@example.com"])
def test_signing_key_check_rejects_invalid_origins_without_network(origin):
    with pytest.raises(ValueError, match="HTTPS project origin"):
        preflight.check_signing_keys(origin)


def test_public_key_check_accepts_supported_key(monkeypatch):
    import io
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.StringIO(json.dumps({
        "keys": [{"kid": "one", "alg": "ES256"}, {"kid": "unsupported", "alg": "HS256"}],
    })))
    assert preflight.check_signing_keys("https://example.supabase.co") == {"supported_keys": 1}


def test_missing_asymmetric_keys_are_not_reported_ready(monkeypatch):
    import io
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: io.StringIO('{"keys": []}'))
    with pytest.raises(ValueError, match="No supported"):
        preflight.check_signing_keys("https://example.supabase.co")


def test_malformed_connection_string_does_not_expose_password(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "not-a-uri-private-password")
    assert preflight.main(["--mode", "operator"]) == 1
    output = capsys.readouterr().out
    assert "private-password" not in output
    assert "Invalid database connection string" in output


class Connection:
    """A metadata-only DB boundary for TLS, privilege, and role-isolation cases."""

    def __init__(self, *, tls=True, privileged=False, owns=False):
        self.tls, self.privileged, self.owns = tls, privileged, owns
        self.statements = []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "FROM pg_roles" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: {
                "role": "runtime", "can_create_roles": self.privileged,
                "superuser": False, "bypass_rls": False,
            }))
        return None

    def scalar(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "FROM pg_stat_ssl" in sql:
            return self.tls
        if "to_regclass" in sql or "pg_has_role" in sql:
            return True
        if "tableowner" in sql:
            return int(self.owns)
        if "relforcerowsecurity" in sql:
            return len(preflight.TABLES)
        pytest.fail("unexpected scalar metadata query")

    def scalars(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        assert "FROM pg_tables" in sql
        return preflight.TABLES


def test_runtime_inspection_never_reads_operator_version_table_or_user_data():
    connection = Connection()
    report = preflight.check_database(connection, "runtime")
    assert report["migration_revisions"] is None
    assert report["tls"] is True
    sql = "\n".join(connection.statements)
    assert "SELECT version_num" not in sql
    assert "FROM app." not in sql
    assert not any(word in sql for word in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))


@pytest.mark.parametrize("configuration, message", [
    ({"tls": False}, "not using TLS"),
    ({"privileged": True}, "restricted runtime login"),
    ({"owns": True}, "must not own"),
])
def test_runtime_inspection_rejects_unsafe_connection(configuration, message):
    with pytest.raises(ValueError, match=message):
        preflight.check_database(Connection(**configuration), "runtime")
