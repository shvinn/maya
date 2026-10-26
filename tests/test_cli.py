"""The ``maya residents`` commands: the admin's only way to create, change
and fund residents."""

from __future__ import annotations

import re

import pytest

from maya import cli
from maya.simulations.residents import residents


def run(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    """Run ``maya <argv>``; returns (exit code, stdout, stderr)."""
    monkeypatch.setattr("sys.argv", ["maya", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code or 0
    out, err = capsys.readouterr()
    return code, out, err


def created_id(out: str) -> str:
    return re.search(r"Created (R-\d{6})", out).group(1)


def test_create_prints_the_key_once_and_a_ready_config_line(monkeypatch, capsys):
    code, out, _ = run(
        monkeypatch, capsys, "residents", "create",
        "--first-name", "Lena", "--last-name", "Okafor", "--age", "34", "--gender", "female",
        "--opening-balance", "1200", "--url", "http://maya.local:7000/",
    )
    assert code == 0
    key = re.search(r"(maya_rk_\S+)", out).group(1)
    resident_id = created_id(out)
    assert residents.authenticate(key)["resident_id"] == resident_id
    assert residents.balance(resident_id) == 1200
    assert f'http://maya.local:7000/flights/mcp --header "Authorization: Bearer {key}"' in out


def test_list_show_update_funds_and_transactions(monkeypatch, capsys):
    _, out, _ = run(monkeypatch, capsys, "residents", "create", "--first-name", "Ari", "--last-name", "Venn", "--age", "30")
    rid = created_id(out)

    assert rid in run(monkeypatch, capsys, "residents", "list")[1]
    assert run(monkeypatch, capsys, "residents", "add-funds", rid.lower(), "250")[0] == 0
    assert run(monkeypatch, capsys, "residents", "update", rid, "--age", "31")[0] == 0
    assert residents.get(rid)["age"] == 31

    _, shown, _ = run(monkeypatch, capsys, "residents", "show", rid)
    assert f"Balance: {residents.DEFAULT_OPENING_BALANCE + 250} bucks" in shown
    _, history, _ = run(monkeypatch, capsys, "residents", "transactions", rid)
    assert "opening_balance" in history and "top_up" in history


def test_rotate_key_retires_the_old_one(monkeypatch, capsys):
    r = residents.create("Lena", "Okafor", age=34)
    _, out, _ = run(monkeypatch, capsys, "residents", "rotate-key", r["resident_id"])
    new_key = re.search(r"(maya_rk_\S+)", out).group(1)
    assert residents.authenticate(new_key)["resident_id"] == r["resident_id"]
    with pytest.raises(residents.InvalidResidentKey):
        residents.authenticate(r["key"])


@pytest.mark.parametrize(
    "argv",
    [
        ("residents", "show", "R-999999"),
        ("residents", "add-funds", "R-999999", "10"),
        ("residents", "create", "--first-name", "X", "--last-name", "Y", "--age", "200"),
        ("residents", "update", "R-000001"),
    ],
)
def test_refusals_go_to_stderr_with_a_failing_exit_code(monkeypatch, capsys, argv):
    code, out, err = run(monkeypatch, capsys, *argv)
    assert code == 1
    assert err.startswith("error: ")
    assert out == ""
