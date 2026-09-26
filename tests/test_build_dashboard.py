# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Coverage for build_dashboard.py: templating, top-level assembly, the golden JSON, and the CLI."""

import json
import pathlib
import runpy
import sys

import pytest

import build_dashboard

# Full-build panel count, cross-checked against dcgm_dashboard/CONVENTIONS.md's
# "That is 100 panels total" and test_rows.py's own independent count.
FULL_PANEL_COUNT = 100


def test_build_templating_defines_expected_variables_in_order() -> None:
    variables = build_dashboard.build_templating()
    assert [v["name"] for v in variables] == [
        "job",
        "instance",
        "gpu",
        "energy_price",
        "currency",
        "peak_fp32_tflops",
        "peak_mem_bw_gbs",
    ]
    assert [v["type"] for v in variables] == ["query"] * 3 + ["textbox"] * 4

    job, instance, gpu = variables[0], variables[1], variables[2]
    assert "$job" not in job["query"]["query"]
    assert "$job" in instance["query"]["query"]
    assert "$job" in gpu["query"]["query"] and "$instance" in gpu["query"]["query"]

    energy_price, currency, peak_fp32, peak_mem_bw = variables[3], variables[4], variables[5], variables[6]
    assert energy_price["query"] == "0.25"
    assert currency["query"] == "$"
    assert peak_fp32["query"] == "40"
    assert peak_mem_bw["query"] == "672"


def test_build_dashboard_top_level_structure() -> None:
    dashboard = build_dashboard.build_dashboard(build_dashboard.ROW_ORDER)
    assert dashboard["uid"] == build_dashboard.DASHBOARD_UID
    assert dashboard["title"] == build_dashboard.DASHBOARD_TITLE
    assert dashboard["schemaVersion"] == 39
    assert dashboard["templating"]["list"] == build_dashboard.build_templating()
    assert len(dashboard["__inputs"]) == 1
    assert [a["name"] for a in dashboard["annotations"]["list"]] == [
        "Annotations & Alerts",
        "Xid event",
        "Driver reload (energy counter reset)",
    ]

    panels = dashboard["panels"]
    top_level_ids = [p["id"] for p in panels]
    assert len(set(top_level_ids)) == len(top_level_ids)

    # Row L ships collapsed=True: its 9 children are nested inside its own
    # "panels" array (CONVENTIONS.md), never as top-level siblings.
    row_l_panel = next(p for p in panels if p.get("title") == "Datacenter / hardware-specific")
    assert row_l_panel["type"] == "row"
    assert row_l_panel["collapsed"] is True
    nested = [(p["id"], p["gridPos"]["y"]) for p in row_l_panel["panels"]]
    assert nested == [
        (87, 0),
        (88, 8),
        (95, 8),
        (89, 16),
        (90, 24),
        (91, 24),
        (92, 32),
        (93, 40),
        (94, 40),
    ]
    assert all(p["id"] not in top_level_ids for p in row_l_panel["panels"])

    # Every other row's children DO appear as top-level siblings, immediately
    # after their own row header.
    row_b_header = next(p for p in panels if p.get("title") == "$gpu — Overview")
    assert row_b_header["collapsed"] is False
    assert row_b_header["panels"] == []
    idx = panels.index(row_b_header)
    assert [p["id"] for p in panels[idx + 1 : idx + 9]] == list(range(12, 20))


def test_build_dashboard_matches_committed_json(project_root: pathlib.Path) -> None:
    """GOLDEN: the committed nvidia-dcgm-dashboard.json must be exactly what
    build_dashboard(ROW_ORDER) produces -- read-only, never rewritten by this test."""
    committed = (project_root / "nvidia-dcgm-dashboard.json").read_text()
    built = json.dumps(build_dashboard.build_dashboard(build_dashboard.ROW_ORDER), indent=2, sort_keys=True) + "\n"
    assert built == committed


def test_main_help_text_pins_docstring_and_option_help(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """--help must show the module docstring verbatim (raw, not rewrapped -- pins both
    description=__doc__ and formatter_class=RawDescriptionHelpFormatter) plus the exact
    --out/--rows help strings. COLUMNS is fixed so argparse's line-wrapping of the options
    section is deterministic regardless of the terminal this test runs in."""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        build_dashboard.main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "usage: build_dashboard.py [-h] [--out OUT] [--rows ROWS]\n"
        "\n"
        'Build the "NVIDIA GPU -- DCGM Ultimate Dashboard" Grafana JSON.\n'
        "\n"
        "Usage:\n"
        "    uv run build_dashboard.py [--out PATH] [--rows a,b,c,...]\n"
        "\n"
        'Default output: "nvidia-dcgm-dashboard.json" next to this script.\n'
        "Row selection lets a row author build a dashboard containing only their row(s)\n"
        "plus row A (top strip) for fast local iteration -- see dcgm_dashboard/CONVENTIONS.md.\n"
        "\n"
        "Output is deterministic: stable panel ids (fixed, not auto-assigned -- see\n"
        "dcgm_dashboard/CONVENTIONS.md's id-range table), stable key order (json.dump\n"
        "with sort_keys=True), pretty-printed with indent=2.\n"
        "\n"
        "options:\n"
        "  -h, --help   show this help message and exit\n"
        "  --out OUT    output JSON path\n"
        "  --rows ROWS  comma-separated row letters to include, in ['a', 'b', 'c', 'd',\n"
        "               'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'] order regardless of the\n"
        "               order given (default: all)\n"
    )


def test_main_writes_pretty_printed_json_with_sorted_top_level_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the exact bytes main() writes (json.dump(..., indent=2, sort_keys=True)), not just
    the parsed value -- catches indent/sort_keys being dropped, flipped, or changed."""
    out_path = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", "a", "--out", str(out_path)])

    assert build_dashboard.main() == 0

    raw = out_path.read_text()
    assert raw == json.dumps(build_dashboard.build_dashboard(["a"]), indent=2, sort_keys=True) + "\n"
    # indent=2 -> multi-line, two-space-indented; sort_keys=True -> "__inputs" (first
    # alphabetically) is the first key, well before "annotations".
    lines = raw.splitlines()
    assert lines[0] == "{"
    assert lines[1] == '  "__inputs": ['
    assert raw.index('"__inputs"') < raw.index('"annotations"')


def test_main_default_writes_full_dashboard(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out_path = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--out", str(out_path)])

    assert build_dashboard.main() == 0

    written = json.loads(out_path.read_text())
    assert written == build_dashboard.build_dashboard(build_dashboard.ROW_ORDER)
    captured = capsys.readouterr()
    expected_rows = ",".join(build_dashboard.ROW_ORDER)
    assert captured.out == f"wrote {out_path} ({FULL_PANEL_COUNT} panels, rows: {expected_rows})\n"
    assert captured.err == ""


def test_main_out_option_defaults_to_default_out_constant(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """No --out given -> args.out must fall back to the DEFAULT_OUT module constant (never to
    no path / None), so main()'s writer always has a real destination. DEFAULT_OUT itself is
    monkeypatched to a tmp_path location first -- this must NEVER write the committed
    nvidia-dcgm-dashboard.json, and main() reads the module attribute fresh on each call, so
    patching it here is safe (argparse's `default=str(DEFAULT_OUT)` is evaluated inside main())."""
    patched_default = tmp_path / "default-out.json"
    monkeypatch.setattr(build_dashboard, "DEFAULT_OUT", patched_default)
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py"])

    assert build_dashboard.main() == 0

    written = json.loads(patched_default.read_text())
    assert written == build_dashboard.build_dashboard(build_dashboard.ROW_ORDER)
    captured = capsys.readouterr()
    assert (
        captured.out
        == f"wrote {patched_default} ({FULL_PANEL_COUNT} panels, rows: {','.join(build_dashboard.ROW_ORDER)})\n"
    )


@pytest.mark.parametrize(
    "rows_arg",
    ["C,a,,B", "b,,C,a", " a , b , c "],  # out of order, mixed case, blank entries, stray whitespace
)
def test_main_rows_option_normalizes_to_row_order(
    rows_arg: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    out_path = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", rows_arg, "--out", str(out_path)])

    assert build_dashboard.main() == 0

    written = json.loads(out_path.read_text())
    assert written == build_dashboard.build_dashboard(["a", "b", "c"])
    expected_count = 10 + 9 + 3  # row A (no header) + row B (header+8) + row C (header+2)
    captured = capsys.readouterr()
    assert captured.out == f"wrote {out_path} ({expected_count} panels, rows: a,b,c)\n"


def test_main_unknown_row_letters_exit_2_without_writing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    out_path = tmp_path / "should-not-exist.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", "a,z,Q", "--out", str(out_path)])

    assert build_dashboard.main() == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: unknown row letter(s): ['q', 'z']" in captured.err
    assert not out_path.exists()


def test_main_creates_missing_nested_output_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_path = tmp_path / "nested" / "deeper" / "out.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", "a", "--out", str(out_path)])

    assert build_dashboard.main() == 0

    assert out_path.exists()
    assert json.loads(out_path.read_text())["panels"]


def test_dunder_main_block_runs_via_runpy(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, project_root: pathlib.Path
) -> None:
    out_path = tmp_path / "cli_out.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", "a,b", "--out", str(out_path)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(project_root / "build_dashboard.py"), run_name="__main__")

    assert exc_info.value.code == 0
    assert json.loads(out_path.read_text()) == build_dashboard.build_dashboard(["a", "b"])


def test_dunder_main_block_propagates_nonzero_exit_via_runpy(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, project_root: pathlib.Path
) -> None:
    out_path = tmp_path / "unused.json"
    monkeypatch.setattr(sys, "argv", ["build_dashboard.py", "--rows", "nope", "--out", str(out_path)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(project_root / "build_dashboard.py"), run_name="__main__")

    assert exc_info.value.code == 2
    assert not out_path.exists()
