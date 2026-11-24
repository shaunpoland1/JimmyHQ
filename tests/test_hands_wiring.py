"""What holds the hands packages together: trust in a provider's hints, limits that are
settings, what may be written in a provider's options, starting a program on every
system, and the routes the screens need before they save anything."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jimmyhq import hands
from jimmyhq.hands import fake, mcp, mcp_stdio, options, rules
from jimmyhq.hands.provider import (
    TYPES,
    Action,
    ProviderRecord,
    ProviderType,
    RecordField,
    Wiring,
    hint_trusted,
    register_type,
)
from jimmyhq.hands.rules import Invalid
from jimmyhq.web.app import create_app
from test_hands_core_fakes import ACCOUNT, AGENT, KEY, Clock, demo, events, run, switch_on
from test_hands_mcp_fakes import SCRIPT, Stdio, call, fast

PASSWORD = "correct horse battery"
RANDOM = "q7Zp2Lx9Vb4Tn8Rw3Ks6Hd1Fy5Gm0Jc"  # what a generated key looks like


@pytest.fixture
def stdio(tmp_path, monkeypatch):
    for _ in fast(monkeypatch):
        yield Stdio(tmp_path)


@pytest.fixture
def owner(core):
    fake.register()
    try:
        with TestClient(create_app(core), base_url="http://testserver") as client:
            assert client.post("/api/auth/setup",
                               json={"name": "Sam", "password": PASSWORD}).status_code == 200
            hands.hands(core).now = Clock()
            yield client
    finally:
        fake.unregister()
        hands.set_level_gate(None)


@pytest.fixture
def ready(owner):
    assert owner.post("/api/hands/providers",
                      json={"id": "demo", "type": "fake", "label": "Demo"}).status_code == 201
    assert owner.put("/api/hands/providers/demo/key", json={"key": KEY}).status_code == 200
    assert owner.post("/api/hands/accounts", json={
        "provider_id": "demo", "account": "notes", "label": "Notes"}).json()["status"] == "ready"
    return owner


# --- an untrusted hint never preselects "alone" ---------------------------------------------


def test_a_hint_is_trusted_only_from_the_owner_or_a_type_that_declares_it():
    fake.register()
    try:
        assert hint_trusted("fake", "adapter") and hint_trusted("fake", "provider")
        assert not hint_trusted("fake", "guess")  # a guess is never trusted, whoever made it
    finally:
        fake.unregister()
    for source in ("provider", "adapter", "guess"):
        assert not hint_trusted("mcp", source) and not hint_trusted("composio", source)
        assert not hint_trusted("no-such-type", source)
    for kind in ("mcp", "composio", "fake", "no-such-type"):
        assert hint_trusted(kind, "owner")


def test_alone_is_the_default_only_for_a_reading_whose_classification_is_trusted(core):
    assert rules.default_level(core, "read", trusted=True) == "alone"
    assert rules.default_level(core, "read", trusted=False) == "ask"
    assert rules.default_level(core, "read") == "ask"  # not saying is not trusting
    for effect in ("send", "create", "update", "delete", "spend", "access", "change"):
        assert rules.default_level(core, effect, trusted=True) == "ask"
    core.settings.set("hands.default_level_read", "ask", actor="owner")
    assert rules.default_level(core, "read", trusted=True) == "ask"


def test_the_owner_is_told_where_an_untrusted_reading_came_from_and_that_alone_is_theirs():
    said = rules.level_note("read", "provider", False)
    assert "provider itself says" in said and "Ask me each time" in said
    assert "you can still choose \"Alone\"" in said
    assert "from its name" in rules.level_note("read", "adapter", False)
    assert "guessed" in rules.level_note("read", "guess", False)
    assert rules.level_note("read", "adapter", True) is None
    assert rules.level_note("send", "provider", False) is None


def test_a_guessed_reading_of_a_trusted_type_still_starts_at_ask(core):
    with demo(core) as (h, _):
        listed = {a["name"]: a for a in run(h.describe_actions(ACCOUNT))}
        assert listed["notes.list"]["default_level"] == "alone"
        assert listed["notes.list"]["effect_trusted"] and listed["notes.list"]["level_note"] is None
        peek = listed["status.peek"]  # the fake says "read", and marks it a guess
        assert peek["effect"] == "read" and not peek["effect_trusted"]
        assert peek["default_level"] == "ask" and "guessed" in peek["level_note"]
        assert switch_on(h, "status.peek")["level"] == "ask"
        assert switch_on(h, "status.peek", level="alone")["level"] == "alone"  # the owner's choice


def test_an_action_the_owner_marks_as_a_reading_is_the_owners_word(core):
    with demo(core) as (h, _):
        rule = switch_on(h, "things.poke", effect="read")
        assert rule["effect_from"] == "owner" and rule["level"] == "alone"


def test_listing_actions_switches_nothing_on_until_the_owner_saves_a_rule(core, stdio):
    h = hands.hands(core)
    h.save_provider({"id": "tools", "type": "mcp", "label": "Tools",
                     "options": stdio.options}, actor="owner")
    for name, value in stdio.saved.items():
        h.set_secret("tools", value, name)
    assert run(h.connect(provider_id="tools", slug="desk", actor="owner"))["status"] == "ready"
    listed = run(h.describe_actions("desk"))
    assert any(a["effect"] == "read" for a in listed)
    assert run(h.preflight(agent=AGENT, account="desk", action="echo"))["levels"]
    assert h.rules() == [] and events(core, "hands.rule_set") == []
    assert run(hands.tools_for(core, AGENT)) == []
    with pytest.raises(hands.HandRefused) as refused:
        run(hands.call(core, agent=AGENT, tool="desk_echo", arguments={"words": "x"}))
    assert refused.value.rule == "hand_needs_a_rule"
    assert "tools/call" not in stdio.methods()


# --- limits are settings --------------------------------------------------------------------


def test_every_adapter_limit_is_a_setting_in_the_integrations_section(core):
    described = {s["key"]: s for s in core.settings.describe()["settings"]}
    for name, default in (("composio_timeout_s", 30), ("mcp_start_timeout_s", 30),
                          ("mcp_call_timeout_s", 60), ("mcp_idle_s", 300),
                          ("mcp_max_message_bytes", 4_000_000), ("mcp_max_tools", 200)):
        assert core.settings.get(f"hands.{name}") == default
        assert described[f"hands.{name}"]["section"] == "hands"
        assert "option" in described[f"hands.{name}"]["help"]  # says a provider may override


def test_a_wiring_reads_a_limit_from_the_option_then_the_setting_and_never_invents_one():
    wiring = Wiring(key=None, secret=lambda n: None, setting={"mcp_max_tools": 7}.get)
    assert wiring.limit({}, "max_tools", "mcp_max_tools", low=1, high=1000) == 7
    assert wiring.limit({"max_tools": 3}, "max_tools", "mcp_max_tools", low=1, high=1000) == 3
    for wrong in (0, 5000, "many", True):
        assert wiring.limit({"max_tools": wrong}, "max_tools", "mcp_max_tools",
                            low=1, high=1000) is None
    bare = Wiring(key=None, secret=lambda n: None)  # no Core: the declared defaults
    assert bare.setting("mcp_call_timeout_s") == 60
    with pytest.raises(KeyError):
        bare.setting("no_such_limit")


def test_an_mcp_servers_limits_come_from_settings_unless_its_options_override_them(core, stdio):
    for key, value in (("mcp_start_timeout_s", 11), ("mcp_call_timeout_s", 22),
                       ("mcp_idle_s", 33), ("mcp_max_message_bytes", 44_000),
                       ("mcp_max_tools", 5)):
        core.settings.set(f"hands.{key}", value, actor="owner")
    h = hands.hands(core)
    h.save_provider({"id": "tools", "type": "mcp", "label": "Tools",
                     "options": stdio.options | {"call_timeout_s": 2.5}}, actor="owner")
    for name, value in stdio.saved.items():
        h.set_secret("tools", value, name)
    limits = h.adapter("tools").session.limits
    assert limits == mcp.Limits(start_timeout_s=11, call_timeout_s=2.5, idle_s=33,
                                max_message_bytes=44_000, max_tools=5)
    account = run(h.adapter("tools").accounts())[0]
    assert len(run(h.adapter("tools").actions(account))) == 5  # the setting's cap, in use


# --- what may be written in options ---------------------------------------------------------

FINE = [
    "--root", "/srv/example/projects/a-very-long-folder-name/with/many/parts/under/it/notes",
    "C:\\Program Files\\Example Tools\\server\\dist\\index.js",
    "@example-scope/mcp-server-with-a-rather-long-package-name",
    "example-mcp-server==2024.11.5", "--port=8440", "--mode=read-only", "-y",
    "/var/cache/example/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8/data",  # a hash in a path
    "123e4567-e89b-12d3-a456-426614174000",  # an id, not a key
    "--token={secret:api_token}", "{secret:connection}", "https://example.com/mcp?mode=fast",
    "--workspace", "team-notes-for-the-whole-of-next-year",
]
CREDENTIALS = [
    "--token=abc123", "key=hunter2hunter2", "--api-key=anything", "API_KEY:anything",
    "--password=correct-horse", "sk-test-SECRET-0123456789", "ghp_" + "a1B2" * 6, RANDOM,
    f"--header={RANDOM}", "https://example.com/mcp?api_key=abc123",
    "https://sam:hunter2@example.com/mcp", "0123456789abcdef0123456789abcdef",
]


def test_long_paths_package_names_ids_and_secret_references_are_not_credentials():
    for item in FINE:
        assert not options.credential_in(item), item
    options.check_argv(FINE)  # and the whole list is accepted as given


@pytest.mark.parametrize("item", CREDENTIALS)
def test_an_argument_that_looks_like_a_credential_is_refused_and_the_owner_told_what_to_do(item):
    assert options.credential_in(item)
    with pytest.raises(Invalid) as refused:
        options.check_argv(["--root", "notes", item])
    said = str(refused.value)
    assert "Nothing was saved" in said and "argument 3" in said
    assert "{secret:NAME}" in said and item not in said  # the value is never repeated


def test_the_value_after_a_flag_that_names_a_secret_is_a_credential_too():
    with pytest.raises(Invalid, match="argument 2"):
        options.check_argv(["--token", "abc123"])
    options.check_argv(["--token", "{secret:api_token}"])
    options.check_argv(["--token", "--verbose"])  # the next flag is not its value


def test_a_secret_reference_that_cannot_be_read_is_refused_with_the_right_form():
    for wrong in ("{secret:}", "{secret:Has Space}", "--token={secret:UPPER}", "{secret:x"):
        with pytest.raises(Invalid, match="Write it as"):
            options.check_argv([wrong])


def test_a_secret_reference_is_replaced_only_when_asked_and_a_missing_one_is_named():
    saved = {"api_token": RANDOM, "region": "eu"}
    argv, used = options.with_secrets(["--token={secret:api_token}", "{secret:region}", "x"],
                                      saved.get)
    assert argv == (f"--token={RANDOM}", "eu", "x") and used == [RANDOM, "eu"]
    with pytest.raises(KeyError) as missing:
        options.with_secrets(["{secret:nope}"], saved.get)
    assert missing.value.args == ("nope",)
    # A saved value that happens to hold the pattern is put in as it is, not read again.
    assert options.with_secrets(["{secret:a}"], {"a": "{secret:b}", "b": "no"}.get)[0] == (
        "{secret:b}",)
    assert options.secret_names(["{secret:a} {secret:b}", "{secret:a}", 3]) == ["a", "b"]


def test_an_mcp_record_keeps_a_long_path_and_refuses_a_credential_in_its_arguments(core):
    h = hands.hands(core)
    long_path = "/srv/example/" + "/".join(["a-long-folder-name"] * 12) + "/server.py"
    saved = h.save_provider({"id": "files", "type": "mcp", "label": "Files", "options": {
        "command": long_path, "args": FINE, "cwd": "/srv/example/projects/notes",
        "env": ["EXAMPLE_API_TOKEN"]}}, actor="owner")
    assert saved.options["command"] == long_path and saved.options["args"] == FINE
    described = h.describe_provider(saved)
    assert described["secrets_wanted"] == ["api_token", "connection", "env_example_api_token"]
    for item in CREDENTIALS:
        with pytest.raises(Invalid, match="looks like a key"):
            h.save_provider({"id": "files", "type": "mcp", "label": "Files",
                             "options": {"command": "uvx", "args": [item]}}, actor="owner")
    assert h.provider("files").options["args"] == FINE  # the refused ones changed nothing
    assert RANDOM not in str([e.payload for e in events(core, "hands.provider_saved")])


def test_any_other_option_that_looks_like_a_secret_is_still_refused(core):
    h = hands.hands(core)
    for wrong in ({"user_id": f"Bearer {RANDOM}"}, {"note": "sk-test-SECRET-0123456789"}):
        with pytest.raises(Invalid, match="looks like a key"):
            h.save_provider({"id": "cmp", "type": "composio", "label": "C",
                             "options": wrong}, actor="owner")
    with pytest.raises(Invalid, match="must be a path"):
        h.save_provider({"id": "files", "type": "mcp", "label": "F",
                         "options": {"command": ["uvx"]}}, actor="owner")


def test_a_type_can_only_declare_fields_the_screen_knows():
    with pytest.raises(ValueError, match="does not know"):
        register_type(ProviderType("odd", "Odd", lambda r, w: None,
                                   fields=(RecordField("x", "X", kind="colour"),)))
    with pytest.raises(ValueError, match="does not know"):
        register_type(ProviderType("odd", "Odd", lambda r, w: None, tokens="elsewhere"))
    assert "odd" not in TYPES


def test_a_secret_named_in_an_argument_reaches_the_program_and_is_never_in_the_record(
        core, stdio):
    h = hands.hands(core)
    record = {"id": "tools", "type": "mcp", "label": "Tools", "options": stdio.options | {
        "args": [SCRIPT, "--token={secret:api_token}", "--plain"]}}
    h.save_provider(record, actor="owner")
    for name, value in stdio.saved.items():
        h.set_secret("tools", value, name)
    # Until the value is saved the program is not started, and the owner is told which.
    health = run(h.check("tools"))
    assert not health["ok"] and "\"api_token\"" in health["error"] and stdio.starts() == 0
    h.set_secret("tools", RANDOM, "api_token")
    adapter = h.adapter("tools")
    out = call(adapter, "argv")
    # The program was given the value; what it says back has the value taken out.
    assert out.ok and out.data == {"argv": ["--token=[removed]", "--plain"]}
    assert RANDOM not in repr(mcp.read_config(
        ProviderRecord(**record), Wiring(key=None, secret={
            **stdio.saved, "api_token": RANDOM}.get)))
    assert RANDOM not in str([e.payload for e in events(core, "hands.provider_saved")])


# --- starting a program on every system -----------------------------------------------------

NPX = "C:\\Program Files\\nodejs\\npx.cmd"
SHELL = "C:\\Windows\\System32\\cmd.exe"


def test_a_program_is_started_from_a_list_of_arguments_on_every_system():
    args = ("server.js", "a b", "$(id)", "; rm -rf x", "100%")
    assert mcp_stdio.launch("/usr/bin/node", args, windows=False) == ["/usr/bin/node", *args]
    assert mcp_stdio.launch("C:\\node\\node.exe", args, windows=True) == [
        "C:\\node\\node.exe", *args]
    # A batch file somewhere that is not Windows is just a file: no shell is involved.
    assert mcp_stdio.launch("/opt/x/run.cmd", ("a",), windows=False) == ["/opt/x/run.cmd", "a"]


def test_a_batch_file_on_windows_runs_through_the_command_shell_with_every_part_quoted():
    line = mcp_stdio.launch(NPX, ("-y", "@example/server", "C:\\My Notes\\", "(x)"),
                            windows=True, shell=SHELL)
    assert line == (f'"{SHELL}" /d /s /c ""{NPX}" "-y" "@example/server" '
                    '"C:\\My Notes\\\\" "(x)""')
    assert mcp_stdio.launch("run.BAT", (), windows=True, shell=SHELL) == (
        f'"{SHELL}" /d /s /c ""run.BAT""')


@pytest.mark.parametrize("bad", ['say "hi"', "100%", "%PATH%", "a&b", "a|b", "a>b", "a<b",
                                 "a^b", "wow!", "two\nlines", "tab\there"])
def test_an_argument_the_command_shell_would_read_as_syntax_is_refused_for_a_batch_file(bad):
    assert mcp_stdio.cmd_quoted(bad) is None
    with pytest.raises(mcp.Unavailable) as refused:
        mcp_stdio.launch(NPX, ("-y", bad), windows=True, shell=SHELL)
    said = refused.value.sentence
    assert "Argument 2" in said and "batch file" in said and bad not in said
    # The same argument is plain text to a real program, which needs no shell.
    assert mcp_stdio.launch("C:\\node\\node.exe", (bad,), windows=True) == [
        "C:\\node\\node.exe", bad]


def test_a_batch_file_whose_own_path_cannot_be_quoted_is_refused():
    with pytest.raises(mcp.Unavailable, match="its path"):
        mcp_stdio.launch("C:\\100%\\run.cmd", (), windows=True, shell=SHELL)


def test_quoting_keeps_spaces_and_brackets_and_doubles_backslashes_at_the_end():
    assert mcp_stdio.cmd_quoted("a b (c)") == '"a b (c)"'
    assert mcp_stdio.cmd_quoted("C:\\dir\\") == '"C:\\dir\\\\"'
    assert mcp_stdio.cmd_quoted("C:\\dir\\file") == '"C:\\dir\\file"'
    assert mcp_stdio.cmd_quoted("") == '""'


def test_the_command_shell_is_the_systems_own(tmp_path):
    system = tmp_path / "System32"
    system.mkdir()
    (system / "cmd.exe").write_bytes(b"")
    assert mcp_stdio.command_shell({"SYSTEMROOT": str(tmp_path), "COMSPEC": "x.exe"}) == str(
        system / "cmd.exe")
    assert mcp_stdio.command_shell({"SYSTEMROOT": str(tmp_path / "gone"),
                                    "COMSPEC": "D:\\cmd.exe"}) == "D:\\cmd.exe"


def test_the_pretend_server_is_started_called_and_stopped_on_this_system(stdio):
    """Runs on Linux, macOS and Windows alike: the interpreter is a real program."""
    adapter = stdio.adapter()
    first = call(adapter, "pid")
    assert first.ok and int(first.data) != os.getpid()
    transport = adapter.session.transport
    proc = transport._proc
    assert transport.running()
    mcp.close_all()
    assert proc.poll() is not None and not transport.running()


def test_a_program_that_will_not_stop_is_ended_with_everything_it_started(stdio):
    adapter = stdio.adapter(call_timeout_s=0.3)
    assert call(adapter, "pid").ok
    proc = adapter.session.transport._proc
    mcp_stdio.kill_tree(proc)
    assert proc.wait(timeout=5) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="a .cmd file only runs on Windows")
def test_on_windows_a_cmd_wrapper_starts_the_server_and_stopping_it_leaves_nothing_running(
        stdio, tmp_path):
    folder = tmp_path / "with space"
    folder.mkdir()
    wrapper = folder / "server.cmd"
    wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{SCRIPT}" %*\r\n', "utf-8")
    adapter = stdio.adapter(command=str(wrapper), args=["--label", "two words", "(x)"])
    assert call(adapter, "argv").data == {"argv": ["--label", "two words", "(x)"]}
    child = int(call(adapter, "pid").data)
    refused = run(stdio.adapter(command=str(wrapper), args=["a&b"]).check())
    assert not refused.ok and "batch file" in refused.error
    mcp.close_all()
    # The interpreter the wrapper started is gone too, not only the command shell.
    import ctypes  # noqa: PLC0415

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, child)
    if handle:
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        assert code.value != 259  # 259 is "still running"


# --- the routes the screens need ------------------------------------------------------------


def test_a_pending_connect_is_cancelled_without_forgetting_anything(core, ready):
    fake.world("demo").connect = "redirect"
    assert ready.post("/api/hands/accounts", json={
        "provider_id": "demo", "account": "cal", "label": "Calendar"}).json()[
            "status"] == "redirect"
    before = len(core.tape.after(0, limit=5000))
    assert ready.delete("/api/hands/accounts/cal/connect").json() == {"ok": True}
    assert ready.get("/api/hands/accounts").json()["connecting"] == []
    assert ready.post("/api/hands/accounts/cal/finish").status_code == 422
    assert len(core.tape.after(0, limit=5000)) == before  # nothing was recorded for it
    # Nothing is waiting any more, and a connected account is never touched by this route.
    assert ready.delete("/api/hands/accounts/cal/connect").status_code == 404
    assert ready.delete("/api/hands/accounts/notes/connect").status_code == 404
    assert [a["account"] for a in ready.get("/api/hands/accounts").json()["accounts"]] == [
        "notes"]
    assert ready.delete("/api/hands/accounts/Not-A-Slug/connect").status_code == 404


def test_the_preflight_says_which_levels_a_rule_may_take_and_why_not(core, ready):
    body = {"agent": "ada", "account": "notes", "action": "messages.send"}
    said = ready.post("/api/hands/rules/preflight", json=body).json()
    assert said["effect"] == "send" and said["default_level"] == "ask"
    assert said["levels"]["read"] == {"allowed": True, "reason": None}
    assert said["levels"]["ask"] == {"allowed": True, "reason": None}
    # A running JimmyHQ lets the owner choose any level: cautious only picks the start.
    assert said["levels"]["alone"] == {"allowed": True, "reason": None}
    assert said["cautious"] is True
    assert ready.get("/api/hands/rules").json()["rules"] == []
    # Without the ladder's answer the core alone still says no, in the sentence saving gives.
    hands.set_level_gate(None)
    said = ready.post("/api/hands/rules/preflight", json=body).json()
    assert said["levels"]["alone"] == {"allowed": False, "reason": rules.NOT_YET}
    refused = ready.put("/api/hands/rules", json=body | {"level": "alone"})
    assert refused.status_code == 422 and refused.json()["detail"] == rules.NOT_YET
    assert ready.get("/api/hands/rules").json()["rules"] == []

    reading = ready.post("/api/hands/rules/preflight",
                         json=body | {"action": "notes.list"}).json()
    assert all(level["allowed"] for level in reading["levels"].values())
    assert reading["default_level"] == "alone" and reading["effect_trusted"] is True
    # Marking the send as a reading is the owner's word: every level opens, as on saving.
    marked = ready.post("/api/hands/rules/preflight", json=body | {"effect": "read"}).json()
    assert marked["effect_from"] == "owner" and marked["levels"]["alone"]["allowed"]


def test_the_preflight_asks_the_same_gate_that_saving_does(core, ready):
    asked = []

    def gate(core, **how):
        asked.append((how["level"], how["confirmed"], how["limits"]["per_day"]))
        if how["level"] == "alone" and not how["confirmed"]:
            return "Nothing was changed: confirm it first."
        return None

    hands.set_level_gate(gate)
    body = {"agent": "ada", "account": "notes", "action": "messages.send",
            "limits": {"per_day": 3}}
    first = ready.post("/api/hands/rules/preflight", json=body).json()
    assert first["levels"]["alone"] == {"allowed": False,
                                        "reason": "Nothing was changed: confirm it first."}
    again = ready.post("/api/hands/rules/preflight", json=body | {"confirmed": True}).json()
    assert again["levels"]["alone"]["allowed"] is True
    assert ("alone", True, 3) in asked and ("alone", False, 3) in asked


def test_the_preflight_refuses_what_saving_would_refuse_about_the_question_itself(ready):
    for wrong, status in (({"account": "gone"}, 422), ({"action": "no.such"}, 422),
                          ({"agent": "Not An Agent"}, 422), ({"effect": "explode"}, 422),
                          ({"limits": {"per_day": 0}}, 422), ({"extra": 1}, 422)):
        body = {"agent": "ada", "account": "notes", "action": "notes.list"} | wrong
        assert ready.post("/api/hands/rules/preflight", json=body).status_code == status, wrong


def test_dependents_lists_each_agent_whose_rules_stop_when_an_account_is_forgotten(
        core, ready):
    assert ready.get("/api/hands/accounts/notes/dependents").json() == {
        "account": "notes", "agents": [], "rules": 0, "waiting_requests": 0}
    for agent, action in (("ada", "notes.list"), ("ada", "messages.send"), ("bo", "notes.get")):
        assert ready.put("/api/hands/rules", json={
            "agent": agent, "account": "notes", "action": action}).status_code == 200
    asked = run(hands.call(core, agent="ada", tool="notes_messages_send",
                           arguments={"to": "kit@example.com", "text": "hello"}))
    assert isinstance(asked, hands.NeedsApproval)
    said = ready.get("/api/hands/accounts/notes/dependents").json()
    assert said["rules"] == 3 and said["waiting_requests"] == 1
    assert [(a["agent"], [r["tool"] for r in a["rules"]]) for a in said["agents"]] == [
        ("ada", ["notes_messages_send", "notes_notes_list"]), ("bo", ["notes_notes_get"])]
    assert said["agents"][0]["rules"][0] == {
        "action": "messages.send", "tool": "notes_messages_send", "label": "Send a message",
        "level": "ask", "effect": "send"}
    assert "kit@example.com" not in str(said)  # names and levels, never a value
    assert ready.get("/api/hands/accounts/gone/dependents").status_code == 404
    ready.delete("/api/hands/accounts/notes")
    assert ready.get("/api/hands/accounts/notes/dependents").status_code == 404


def test_types_carry_what_the_settings_screen_needs(ready):
    listed = {t["type"]: t for t in ready.get("/api/hands/types").json()["types"]}
    assert set(listed) == {"agentmail", "composio", "fake", "mcp"}
    program = {f["name"]: f for f in listed["mcp"]["fields"]}
    assert program["args"]["kind"] == "argv" and program["env"]["secret_prefix"] == "env_"
    assert program["base_url"]["where"] == "record"
    assert listed["mcp"]["needs_key"] is False and listed["mcp"]["tokens"] == "here"
    assert listed["composio"]["tokens"] == "provider" and listed["composio"]["needs_key"]
    assert all("factory" not in t and t["description"] for t in listed.values())


def test_an_action_says_whether_its_effect_is_trusted_in_the_answer_the_form_uses(ready):
    listed = {a["name"]: a for a in ready.get("/api/hands/accounts/notes/actions").json()[
        "actions"]}
    assert listed["notes.list"]["effect_trusted"] is True
    assert listed["status.peek"]["effect_trusted"] is False
    assert listed["status.peek"]["default_level"] == "ask"
    assert "still choose" in listed["status.peek"]["level_note"]


def test_an_action_shape_is_unchanged_by_trust():
    action = Action("x", "X", "Does x.", {"type": "object", "properties": {}}, effect="read",
                    effect_from="provider", version="1")
    assert "effect_trusted" not in action.as_dict()  # trust is the type's, not the action's


def test_the_hands_module_does_not_import_the_village():
    """The scrubber is shared, so the core depends on nothing of the village."""
    for source in Path(hands.__file__).parent.glob("*.py"):
        assert "jimmyhq.village" not in source.read_text("utf-8"), source.name
