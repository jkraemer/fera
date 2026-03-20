import pytest
from fera.config import DEFAULT_AGENT
from fera.gateway.sessions import SessionManager


def _mgr(tmp_path):
    return SessionManager(tmp_path / "sessions.json", fera_home=tmp_path)


def test_create_session_returns_composite_id(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.create("default")
    assert info["id"] == f"{DEFAULT_AGENT}/default"
    assert info["name"] == "default"
    assert info["agent"] == DEFAULT_AGENT
    assert "sdk_session_id" not in info


def test_create_session_stores_workspace_dir(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.create("default")
    expected = str(tmp_path / "agents" / DEFAULT_AGENT / "workspace")
    assert info["workspace_dir"] == expected


def test_create_same_name_different_agent_has_different_workspace(tmp_path):
    mgr = _mgr(tmp_path)
    info_main = mgr.create("default")
    info_forge = mgr.create("default", agent="forge")
    assert info_main["workspace_dir"] == str(tmp_path / "agents" / DEFAULT_AGENT / "workspace")
    assert info_forge["workspace_dir"] == str(tmp_path / "agents" / "forge" / "workspace")


def test_create_duplicate_same_agent_raises(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    with pytest.raises(ValueError, match="already exists"):
        mgr.create("default")


def test_create_same_name_different_agent_allowed(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    info = mgr.create("default", agent="forge")
    assert info["id"] == "forge/default"
    assert len(mgr.list()) == 2


def test_list_sessions_returns_all(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("one")
    mgr.create("two")
    ids = [s["id"] for s in mgr.list()]
    assert f"{DEFAULT_AGENT}/one" in ids
    assert f"{DEFAULT_AGENT}/two" in ids


def test_get_session_by_composite_id(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    info = mgr.get(f"{DEFAULT_AGENT}/default")
    assert info is not None
    assert info["name"] == "default"


def test_get_bare_name_normalises_to_default_agent(tmp_path):
    """Bare names (no slash) are treated as DEFAULT_AGENT/{name}."""
    mgr = _mgr(tmp_path)
    mgr.create("default")
    info = mgr.get("default")  # backward-compat: bare name → main/default
    assert info is not None
    assert info["id"] == f"{DEFAULT_AGENT}/default"


def test_get_nonexistent_returns_none(tmp_path):
    mgr = _mgr(tmp_path)
    assert mgr.get("main/nope") is None


def test_set_sdk_session_id(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    mgr.set_sdk_session_id(f"{DEFAULT_AGENT}/default", "sdk-abc-123")
    info = mgr.get(f"{DEFAULT_AGENT}/default")
    assert info["sdk_session_id"] == "sdk-abc-123"


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "sessions.json"
    mgr1 = SessionManager(path, fera_home=tmp_path)
    mgr1.create("default")
    mgr1.set_sdk_session_id(f"{DEFAULT_AGENT}/default", "sdk-abc")

    mgr2 = SessionManager(path, fera_home=tmp_path)
    info = mgr2.get(f"{DEFAULT_AGENT}/default")
    assert info is not None
    assert info["sdk_session_id"] == "sdk-abc"


def test_workspace_dir_backfilled_on_load(tmp_path):
    """Sessions created without workspace_dir get it backfilled on load."""
    import json

    path = tmp_path / "sessions.json"
    # Write a legacy session dict without workspace_dir
    path.write_text(json.dumps({
        f"{DEFAULT_AGENT}/default": {
            "id": f"{DEFAULT_AGENT}/default",
            "name": "default",
            "agent": DEFAULT_AGENT,
        }
    }))
    mgr = SessionManager(path, fera_home=tmp_path)
    info = mgr.get(f"{DEFAULT_AGENT}/default")
    assert info is not None
    expected = str(tmp_path / "agents" / DEFAULT_AGENT / "workspace")
    assert info["workspace_dir"] == expected


def test_missing_id_field_backfilled_on_load(tmp_path):
    """Sessions with composite key but no id field get id backfilled."""
    import json

    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        f"{DEFAULT_AGENT}/default": {
            "name": "default",
            "agent": DEFAULT_AGENT,
            # no id field
        }
    }))
    mgr = SessionManager(path, fera_home=tmp_path)
    info = mgr.get(f"{DEFAULT_AGENT}/default")
    assert info is not None
    assert info["id"] == f"{DEFAULT_AGENT}/default"


def test_get_or_create_composite_id(tmp_path):
    mgr = _mgr(tmp_path)
    info1 = mgr.get_or_create(f"{DEFAULT_AGENT}/default")
    info2 = mgr.get_or_create(f"{DEFAULT_AGENT}/default")
    assert info1["id"] == info2["id"]
    assert len(mgr.list()) == 1


def test_get_or_create_bare_name_uses_default_agent(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.get_or_create("default")
    assert info["id"] == f"{DEFAULT_AGENT}/default"
    assert info["agent"] == DEFAULT_AGENT


def test_create_session_stores_explicit_agent(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.create("coding-1", agent="forge")
    assert info["agent"] == "forge"
    assert info["id"] == "forge/coding-1"


def test_agent_persists_across_instances(tmp_path):
    path = tmp_path / "sessions.json"
    mgr1 = SessionManager(path, fera_home=tmp_path)
    mgr1.create("coding-1", agent="forge")

    mgr2 = SessionManager(path, fera_home=tmp_path)
    info = mgr2.get("forge/coding-1")
    assert info["agent"] == "forge"


def test_sessions_for_agent_filters_by_agent(tmp_path):
    sessions_file = tmp_path / "data" / "sessions.json"
    sessions_file.parent.mkdir(parents=True)
    mgr = SessionManager(sessions_file, fera_home=tmp_path)
    (tmp_path / "agents" / "main" / "workspace").mkdir(parents=True)
    (tmp_path / "agents" / "forge" / "workspace").mkdir(parents=True)

    mgr.create("alpha", agent="main")
    mgr.create("beta", agent="main")
    mgr.create("work", agent="forge")

    main_sessions = mgr.sessions_for_agent("main")
    assert len(main_sessions) == 2
    assert all(s["agent"] == "main" for s in main_sessions)

    forge_sessions = mgr.sessions_for_agent("forge")
    assert len(forge_sessions) == 1
    assert forge_sessions[0]["agent"] == "forge"

    assert mgr.sessions_for_agent("unknown") == []


def test_delete_removes_session(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("temp")
    assert mgr.get(f"{DEFAULT_AGENT}/temp") is not None
    mgr.delete(f"{DEFAULT_AGENT}/temp")
    assert mgr.get(f"{DEFAULT_AGENT}/temp") is None


def test_delete_nonexistent_is_noop(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.delete("main/nope")  # must not raise


def test_delete_persists_removal_to_disk(tmp_path):
    import json
    path = tmp_path / "sessions.json"
    mgr = SessionManager(path, fera_home=tmp_path)
    mgr.create("temp")
    mgr.delete(f"{DEFAULT_AGENT}/temp")

    mgr2 = SessionManager(path, fera_home=tmp_path)
    assert mgr2.get(f"{DEFAULT_AGENT}/temp") is None


def test_set_last_inbound_adapter(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    sid = f"{DEFAULT_AGENT}/default"
    mgr.set_last_inbound_adapter(sid, "telegram")
    info = mgr.get(sid)
    assert info["last_inbound_adapter"] == "telegram"


def test_last_inbound_adapter_persists_across_instances(tmp_path):
    path = tmp_path / "sessions.json"
    mgr1 = SessionManager(path, fera_home=tmp_path)
    mgr1.create("default")
    sid = f"{DEFAULT_AGENT}/default"
    mgr1.set_last_inbound_adapter(sid, "mattermost")

    mgr2 = SessionManager(path, fera_home=tmp_path)
    info = mgr2.get(sid)
    assert info["last_inbound_adapter"] == "mattermost"


def test_set_last_inbound_adapter_nonexistent_raises(tmp_path):
    mgr = _mgr(tmp_path)
    with pytest.raises(KeyError):
        mgr.set_last_inbound_adapter("main/nope", "telegram")


def test_new_session_has_no_last_inbound_adapter(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.create("default")
    assert "last_inbound_adapter" not in info


def test_new_session_has_canary_token(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.create("default")
    assert "canary_token" in info
    assert len(info["canary_token"]) == 32  # uuid4().hex


def test_canary_token_is_unique_per_session(tmp_path):
    mgr = _mgr(tmp_path)
    info1 = mgr.create("one")
    info2 = mgr.create("two")
    assert info1["canary_token"] != info2["canary_token"]


def test_canary_token_persists_across_instances(tmp_path):
    path = tmp_path / "sessions.json"
    mgr1 = SessionManager(path, fera_home=tmp_path)
    info1 = mgr1.create("default")
    token = info1["canary_token"]

    mgr2 = SessionManager(path, fera_home=tmp_path)
    info2 = mgr2.get(f"{DEFAULT_AGENT}/default")
    assert info2["canary_token"] == token


def test_get_or_create_generates_canary_token(tmp_path):
    mgr = _mgr(tmp_path)
    info = mgr.get_or_create("default")
    assert "canary_token" in info
    assert len(info["canary_token"]) == 32


def test_clear_sdk_session_id_regenerates_canary_token(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.create("default")
    sid = f"{DEFAULT_AGENT}/default"
    mgr.set_sdk_session_id(sid, "sdk-abc")
    old_token = mgr.get(sid)["canary_token"]

    mgr.clear_sdk_session_id(sid)
    new_token = mgr.get(sid)["canary_token"]
    assert new_token != old_token
    assert len(new_token) == 32


def test_load_removes_stale_bare_key_when_composite_exists(tmp_path):
    """Loading sessions.json removes bare-key entries whose composite form already exists."""
    import json
    path = tmp_path / "sessions.json"
    ws = str(tmp_path / "agents" / "main" / "workspace")
    path.write_text(json.dumps({
        "default": {
            "id": "main/default", "name": "default", "agent": "main",
            "workspace_dir": ws,
        },
        "main/default": {
            "id": "main/default", "name": "default", "agent": "main",
            "workspace_dir": ws,
        },
    }))

    mgr = SessionManager(path, fera_home=tmp_path)

    assert mgr.get("default") is not None   # still reachable via bare name normalisation
    assert len(mgr.list()) == 1             # only one entry in the listing
    assert mgr.list()[0]["id"] == "main/default"
    # stale key must be persisted away
    saved = json.loads(path.read_text())
    assert "default" not in saved
    assert "main/default" in saved
