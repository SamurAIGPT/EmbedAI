from app.core.engine_env import allows, engine_environment

SECRETS = {
    "BOAT_API_KEY": "a", "TOKEN_ENCRYPTION_KEYS": "b", "HOOK_TOKEN": "c", "TELEGRAM_BOT_TOKEN": "d",
    "POSTGRES_PASSWORD": "e", "POSTGRES_HOST": "h", "DATABASE_URL": "postgresql://u:p@h/db",
    "SQLITE_PATH": "/data/x.db", "OPEN_DOTS_PUBLIC_URL": "https://x", "SECRET_KEY": "s",
}


def test_unlisted_names_are_dropped_and_engine_names_kept():
    env = {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "k", "CODEX_HOME": "/c", "LC_ALL": "C",
           "HTTPS_PROXY": "p", "SOMETHING_ELSE": "x", **SECRETS}
    kept = engine_environment(env)
    assert set(kept) == {"PATH", "HOME", "ANTHROPIC_API_KEY", "CODEX_HOME", "LC_ALL", "HTTPS_PROXY"}


def test_windows_system_names_are_kept():
    env = {"SYSTEMROOT": "C:\\Windows", "APPDATA": "a", "USERPROFILE": "u", "PATHEXT": ".EXE", "COMSPEC": "c"}
    assert engine_environment(env) == env


def test_operator_list_adds_names_and_prefixes():
    env = {"OPEN_DOTS_ENGINE_ENV": "GH_TOKEN, MY_*", "GH_TOKEN": "t", "MY_A": "1", "OTHER": "2"}
    assert engine_environment(env) == {"GH_TOKEN": "t", "MY_A": "1"}


def test_operator_list_cannot_add_app_secrets():
    names = ",".join([*SECRETS, "OPEN_DOTS_*", "POSTGRES_*", "TOKEN_ENCRYPTION*"])
    env = {"OPEN_DOTS_ENGINE_ENV": names, **SECRETS}
    assert engine_environment(env) == {}


def test_the_list_variable_itself_is_not_passed():
    assert "OPEN_DOTS_ENGINE_ENV" not in engine_environment({"OPEN_DOTS_ENGINE_ENV": "X", "X": "1"})
    assert not allows("OPEN_DOTS_ENGINE_ENV", {"OPEN_DOTS_ENGINE_ENV": "OPEN_DOTS_ENGINE_ENV"})


def test_default_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv("BOAT_API_KEY", "no")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "yes")
    kept = engine_environment()
    assert "BOAT_API_KEY" not in kept and kept["ANTHROPIC_API_KEY"] == "yes"


def test_cloud_account_and_agent_socket_names_are_opt_in():
    env = {"AWS_ACCESS_KEY_ID": "a", "GOOGLE_APPLICATION_CREDENTIALS": "/k.json", "AZURE_CLIENT_SECRET": "z",
           "SSH_AUTH_SOCK": "/s", "GOOGLE_API_KEY": "g"}
    assert engine_environment(env) == {"GOOGLE_API_KEY": "g"}
    env["OPEN_DOTS_ENGINE_ENV"] = "AWS_*,SSH_AUTH_SOCK"
    assert set(engine_environment(env)) == {"AWS_ACCESS_KEY_ID", "SSH_AUTH_SOCK", "GOOGLE_API_KEY"}


def test_adapter_names_are_exact_local_and_cannot_override_never_list():
    source = {"X_KEY": "yes", "X_OTHER": "no", **SECRETS}
    assert engine_environment(source, extra_names=("X_KEY", *SECRETS)) == {"X_KEY": "yes"}
    assert engine_environment(source, extra_names=("X_*",)) == {}
    assert engine_environment(source) == {}


async def test_scoped_values_reach_a_real_probe_process_only(monkeypatch):
    import json
    import sys

    from app.services.cli_probe import run
    from app.services.local_claude_runner import child_env

    monkeypatch.setenv("X_KEY", "ambient")
    env = child_env(extra_names=("X_KEY",), extra_env={"X_KEY": "saved", "SECRET_KEY": "never"})
    result = await run(
        [sys.executable, "-c", "import os,json; print(json.dumps({k:os.getenv(k) for k in ['X_KEY','SECRET_KEY']}))"],
        timeout=5, env=env,
    )
    assert result.ok and json.loads(result.stdout) == {"X_KEY": "saved", "SECRET_KEY": None}
    assert "X_KEY" not in child_env()
