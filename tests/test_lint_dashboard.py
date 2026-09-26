# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Tests for tools/lint_dashboard.py."""

import json
import runpy
import sys

import pytest

from tools import lint_dashboard


def test_load_csv_fields_handles_comments_blanks_short_and_unnamed_lines(tmp_path):
    """Exercises every branch of the CSV parser: comment/blank skip, the
    <2-fields skip, the empty-name skip, and the normal 2- and 3+-field adds."""
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text(
        "# a comment\n"
        "# COMMENTED_FIELD, counter, would be a real field if the comment check missed it\n"
        "\n"
        "   \n"
        "DCGM_FI_A, counter, some help, with, extra commas\n"
        "DCGM_FI_B,gauge\n"
        ",gauge,no name here\n"
        "onlyonefield\n"
        "  DCGM_FI_C  ,  label  ,  help  \n"
    )
    fields = lint_dashboard.load_csv_fields(csv_path)
    assert fields == {"DCGM_FI_A": "counter", "DCGM_FI_B": "gauge", "DCGM_FI_C": "label"}


def test_collect_panels_descends_only_into_collapsed_rows():
    nested = {"id": 2, "type": "timeseries"}
    collapsed_row = {"id": 1, "type": "row", "collapsed": True, "panels": [nested]}
    hidden_nested = {"id": 4, "type": "timeseries"}
    open_row = {"id": 3, "type": "row", "collapsed": False, "panels": [hidden_nested]}
    plain = {"id": 5, "type": "timeseries"}

    result = lint_dashboard.collect_panels([collapsed_row, open_row, plain])

    assert result == [
        (collapsed_row, "top"),
        (nested, "row1"),
        (open_row, "top"),
        (plain, "top"),
    ]


def test_collect_panels_handles_a_collapsed_row_with_no_panels_key():
    """A collapsed row missing its own "panels" key must not crash -- the recursive
    call has to fall back to an empty list, not None."""
    collapsed_row_no_panels = {"id": 9, "type": "row", "collapsed": True}

    result = lint_dashboard.collect_panels([collapsed_row_no_panels])

    assert result == [(collapsed_row_no_panels, "top")]


def test_check_ids_and_overlap_flags_missing_and_duplicate_ids():
    no_id = {"title": "NoID", "gridPos": {"x": 0, "y": 0, "w": 4, "h": 4}}
    first = {"id": 1, "title": "First", "gridPos": {"x": 4, "y": 0, "w": 4, "h": 4}}
    second = {"id": 1, "title": "Second", "gridPos": {"x": 8, "y": 0, "w": 4, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(no_id, "top"), (first, "top"), (second, "top")], errors)

    assert len(errors) == 2
    assert any("has no id" in e and "NoID" in e for e in errors)
    assert any("duplicate panel id 1" in e and "'First'" in e and "'Second'" in e for e in errors)


def test_check_ids_and_overlap_flags_width_and_x_plus_w_violations():
    ok = {"id": 1, "title": "OK", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}
    too_wide = {"id": 2, "title": "TooWide", "gridPos": {"x": 0, "y": 10, "w": 25, "h": 4}}
    off_right = {"id": 3, "title": "OffRight", "gridPos": {"x": 20, "y": 20, "w": 6, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(ok, "top"), (too_wide, "top"), (off_right, "top")], errors)

    assert len(errors) == 2
    assert any("id=2" in e and "'TooWide'" in e and "width/x+w>24" in e for e in errors)
    assert any("id=3" in e and "'OffRight'" in e and "width/x+w>24" in e for e in errors)


def test_check_ids_and_overlap_width_check_is_a_strict_greater_than_24():
    at_boundary = {"id": 1, "title": "AtBoundary", "gridPos": {"x": 0, "y": 0, "w": 24, "h": 4}}
    # x=-1 keeps x+w (=24) at the boundary too, so only the w>24 comparison can flag this.
    over_boundary = {"id": 2, "title": "OverBoundary", "gridPos": {"x": -1, "y": 10, "w": 25, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(at_boundary, "top"), (over_boundary, "top")], errors)

    assert len(errors) == 1
    assert "id=2" in errors[0]


def test_check_ids_and_overlap_x_plus_w_check_is_a_strict_greater_than_24():
    at_boundary = {"id": 1, "title": "AtBoundary", "gridPos": {"x": 14, "y": 0, "w": 10, "h": 4}}
    over_boundary = {"id": 2, "title": "OverBoundary", "gridPos": {"x": 15, "y": 10, "w": 10, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(at_boundary, "top"), (over_boundary, "top")], errors)

    assert len(errors) == 1
    assert "id=2" in errors[0]


def test_check_ids_and_overlap_width_check_keeps_scanning_past_a_gridless_panel():
    no_grid = {"id": 1, "title": "NoGrid"}
    too_wide = {"id": 2, "title": "TooWide", "gridPos": {"x": 0, "y": 0, "w": 25, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(no_grid, "top"), (too_wide, "top")], errors)

    assert len(errors) == 1
    assert "id=2" in errors[0]


def test_check_ids_and_overlap_flags_overlap_within_space_but_not_across_spaces():
    top_a = {"id": 10, "title": "TopA", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}
    top_b = {"id": 11, "title": "TopB", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}  # overlaps top_a
    row_a = {"id": 12, "title": "RowA", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}  # same coords, other space
    row_b = {"id": 13, "title": "RowB", "gridPos": {"x": 10, "y": 0, "w": 6, "h": 4}}  # no overlap within row1

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(top_a, "top"), (top_b, "top"), (row_a, "row1"), (row_b, "row1")], errors)

    assert len(errors) == 1
    assert "id=10" in errors[0]
    assert "'TopA'" in errors[0]
    assert "id=11" in errors[0]
    assert "'TopB'" in errors[0]
    assert "id=12" not in errors[0]


def test_check_ids_and_overlap_overlap_scan_keeps_scanning_past_a_gridless_leading_panel():
    """The outer `i` loop's `if not gi: continue` must not stop scanning entirely --
    a leading gridless panel would otherwise hide every real overlap after it."""
    no_grid = {"id": 1, "title": "NoGrid"}
    a = {"id": 2, "title": "A", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}
    b = {"id": 3, "title": "B", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}  # overlaps a

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(no_grid, "top"), (a, "top"), (b, "top")], errors)

    assert len(errors) == 1
    assert "id=2" in errors[0]
    assert "id=3" in errors[0]


def test_check_ids_and_overlap_overlap_scan_keeps_scanning_past_a_gridless_middle_panel():
    """The inner `j` loop's `if not gj: continue` must not stop scanning entirely --
    a gridless panel between two overlapping ones would otherwise hide the overlap."""
    a = {"id": 1, "title": "A", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}
    no_grid = {"id": 2, "title": "NoGrid"}
    b = {"id": 3, "title": "B", "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4}}  # overlaps a

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(a, "top"), (no_grid, "top"), (b, "top")], errors)

    assert len(errors) == 1
    assert "id=1" in errors[0]
    assert "id=3" in errors[0]


def test_check_ids_and_overlap_skips_panels_without_gridpos():
    with_grid = {"id": 1, "title": "A", "gridPos": {"x": 0, "y": 0, "w": 4, "h": 4}}
    no_grid = {"id": 2, "title": "NoGrid"}
    other_with_grid = {"id": 3, "title": "C", "gridPos": {"x": 10, "y": 0, "w": 4, "h": 4}}

    errors: list = []
    lint_dashboard.check_ids_and_overlap([(with_grid, "top"), (no_grid, "top"), (other_with_grid, "top")], errors)

    assert errors == []


def test_check_datasources_flags_bad_panel_and_target_ds_and_skips_row_text():
    good_ds = lint_dashboard.EXPECTED_DS
    row_panel = {"id": 1, "type": "row", "datasource": {"bad": "x"}}
    text_panel = {"id": 2, "type": "text", "datasource": {"bad": "x"}}
    clean_panel = {
        "id": 3,
        "type": "timeseries",
        "datasource": good_ds,
        "targets": [{"refId": "A", "datasource": good_ds}],
    }
    no_ds_panel = {"id": 4, "type": "timeseries", "targets": []}
    bad_panel = {
        "id": 5,
        "title": "Bad",
        "type": "timeseries",
        "datasource": {"type": "influx"},
        "targets": [{"refId": "A", "datasource": {"type": "influx"}}],
    }

    errors: list = []
    lint_dashboard.check_datasources(
        [
            (row_panel, "top"),
            (text_panel, "top"),
            (clean_panel, "top"),
            (no_ds_panel, "top"),
            (bad_panel, "top"),
        ],
        errors,
    )

    assert len(errors) == 2
    assert any("panel id=5" in e and "'Bad'" in e and "datasource !=" in e for e in errors)
    assert any("target[A]" in e and "id=5" in e for e in errors)


def test_check_datasources_missing_panel_datasource_is_not_an_error():
    """A missing "datasource" key is allowed (panels can inherit it) -- only an
    explicit, wrong one is an error. Isolated from any target check (no "targets"
    key at all) so the panel-level `ds is not None` branch is what's on trial."""
    no_ds_panel = {"id": 4, "type": "timeseries"}

    errors: list = []
    lint_dashboard.check_datasources([(no_ds_panel, "top")], errors)

    assert errors == []


def test_check_datasources_panel_level_message_reports_this_panels_id_and_title():
    """Isolated from the target-level message (no "targets" key) so a mutation of
    the panel-level id/title can't hide behind the identical-looking target error."""
    bad_panel = {"id": 9, "title": "PanelOnly", "type": "timeseries", "datasource": {"type": "influx"}}

    errors: list = []
    lint_dashboard.check_datasources([(bad_panel, "top")], errors)

    assert len(errors) == 1
    assert "panel id=9" in errors[0]
    assert "'PanelOnly'" in errors[0]


def test_check_datasources_target_level_message_reports_panel_id_title_and_refid():
    """Panel-level datasource is good (EXPECTED_DS), so only the target-level branch
    can produce this error, isolating its id/title/refId from the panel-level ones."""
    panel = {
        "id": 9,
        "title": "PanelOnly",
        "type": "timeseries",
        "datasource": lint_dashboard.EXPECTED_DS,
        "targets": [{"refId": "B", "datasource": {"type": "influx"}}],
    }

    errors: list = []
    lint_dashboard.check_datasources([(panel, "top")], errors)

    assert len(errors) == 1
    assert "panel id=9" in errors[0]
    assert "'PanelOnly'" in errors[0]
    assert "target[B]" in errors[0]


def test_check_units_flags_only_unverified_units():
    no_field_config = {"id": 1, "type": "timeseries"}
    valid_unit = {"id": 2, "fieldConfig": {"defaults": {"unit": "watt"}}}
    bad_unit = {"id": 3, "title": "Bad", "fieldConfig": {"defaults": {"unit": "furlongs"}}}

    errors: list = []
    lint_dashboard.check_units([(no_field_config, "top"), (valid_unit, "top"), (bad_unit, "top")], errors)

    assert len(errors) == 1
    assert "id=3" in errors[0]
    assert "'Bad'" in errors[0]
    assert "'furlongs'" in errors[0]


def test_check_units_validates_against_units_values_not_keys(monkeypatch):
    """lib.UNITS maps an internal name -> the actual Grafana unit id a panel must use;
    the allowlist has to be the *values*. In the real lib.UNITS every key happens to
    equal its value, so a check_units that used .keys() instead of .values() would
    still pass every other test here -- this monkeypatches a UNITS table where they
    differ to pin down which side check_units actually validates against."""
    monkeypatch.setattr(lint_dashboard.lib, "UNITS", {"internal_name": "real_unit_id"})
    panel_using_value = {"id": 1, "fieldConfig": {"defaults": {"unit": "real_unit_id"}}}
    panel_using_key = {"id": 2, "title": "Bad", "fieldConfig": {"defaults": {"unit": "internal_name"}}}

    errors: list = []
    lint_dashboard.check_units([(panel_using_value, "top"), (panel_using_key, "top")], errors)

    assert len(errors) == 1
    assert "id=2" in errors[0]


@pytest.mark.parametrize(
    ("options", "expect_error"),
    [
        (123, True),  # not a string at all
        ("/", True),  # too short to hold both delimiters
        ("ab", True),  # bare pattern, no delimiters
        ("/ab", True),  # opening delimiter only
        ("/ab/x", True),  # starts with '/' but Grafana can't parse it -> matcher disabled
        ("/ab/", False),  # correctly delimited
        ("/ab/i", False),  # delimited, with a flag
    ],
)
def test_check_byregexp_delimited_requires_slash_delimiters(options, expect_error):
    panel = {
        "id": 1,
        "title": "P",
        "fieldConfig": {"overrides": [{"matcher": {"id": "byRegexp", "options": options}}]},
    }
    errors: list = []
    lint_dashboard.check_byregexp_delimited([(panel, "top")], errors)

    assert bool(errors) is expect_error
    if expect_error:
        assert "byRegexp override options not slash-delimited" in errors[0]


def test_check_byregexp_delimited_skips_other_matchers_and_missing_overrides():
    other_matcher = {"id": 1, "fieldConfig": {"overrides": [{"matcher": {"id": "byName", "options": "foo"}}]}}
    no_overrides = {"id": 2, "fieldConfig": {"defaults": {}}}
    no_field_config = {"id": 3}
    missing_matcher = {"id": 4, "fieldConfig": {"overrides": [{}]}}  # override with no "matcher" key at all

    errors: list = []
    lint_dashboard.check_byregexp_delimited(
        [(other_matcher, "top"), (no_overrides, "top"), (no_field_config, "top"), (missing_matcher, "top")], errors
    )

    assert errors == []


def test_check_byregexp_delimited_keeps_scanning_overrides_past_a_non_byregexp_one():
    panel = {
        "id": 5,
        "title": "P",
        "fieldConfig": {
            "overrides": [
                {"matcher": {"id": "byName", "options": "foo"}},
                {"matcher": {"id": "byRegexp", "options": "bad"}},
            ]
        },
    }

    errors: list = []
    lint_dashboard.check_byregexp_delimited([(panel, "top")], errors)

    assert len(errors) == 1


def test_check_byregexp_delimited_missing_options_key_defaults_to_empty_string():
    """No "options" key at all: the message must show the empty-string default
    (`''`), not a `None` (or other) fallback -- pins the exact default value, not
    just that *an* error fires (isinstance(None, str) is False too, so a wrong
    default would still trip the same error without this check)."""
    panel = {"id": 6, "title": "P", "fieldConfig": {"overrides": [{"matcher": {"id": "byRegexp"}}]}}

    errors: list = []
    lint_dashboard.check_byregexp_delimited([(panel, "top")], errors)

    assert len(errors) == 1
    assert "''" in errors[0]


def test_check_byregexp_delimited_message_reports_panel_id_and_title():
    panel = {
        "id": 7,
        "title": "Regexy",
        "fieldConfig": {"overrides": [{"matcher": {"id": "byRegexp", "options": "bad"}}]},
    }

    errors: list = []
    lint_dashboard.check_byregexp_delimited([(panel, "top")], errors)

    assert len(errors) == 1
    assert "panel id=7" in errors[0]
    assert "'Regexy'" in errors[0]


def test_check_no_kelvin_flags_273_15_literal_only():
    no_match = {"id": 1, "targets": [{"refId": "A", "expr": "foo_bar"}]}
    missing_expr = {"id": 2, "targets": [{"refId": "A"}]}
    kelvin_bug = {"id": 3, "title": "K", "targets": [{"refId": "B", "expr": "(x - 273.15) / y"}]}

    errors: list = []
    lint_dashboard.check_no_kelvin([(no_match, "top"), (missing_expr, "top"), (kelvin_bug, "top")], errors)

    assert len(errors) == 1
    assert "273.15" in errors[0]
    assert "id=3" in errors[0]
    assert "'K'" in errors[0]
    assert "target[B]" in errors[0]


def test_check_csv_coverage_warns_only_for_unused_fields():
    dashboard_text = json.dumps({"panels": [{"targets": [{"expr": "rate(USED_FIELD[5m])"}]}]})
    csv_fields = {"USED_FIELD": "counter", "UNUSED_A": "gauge", "UNUSED_B": "label"}

    warnings: list = []
    lint_dashboard.check_csv_coverage(dashboard_text, csv_fields, warnings)

    assert warnings[0] == "2/3 CSV fields not referenced anywhere in the dashboard JSON:"
    assert "    unused: UNUSED_A (gauge)" in warnings
    assert "    unused: UNUSED_B (label)" in warnings


def test_check_csv_coverage_silent_when_all_fields_used():
    warnings: list = []
    lint_dashboard.check_csv_coverage("USED_FIELD everywhere", {"USED_FIELD": "counter"}, warnings)

    assert warnings == []


@pytest.mark.parametrize(
    ("expr", "should_warn"),
    [
        ('rate(GAUGE_FIELD{job="x"}[5m])', True),
        ("increase(GAUGE_FIELD[5m])", True),
        ('rate(COUNTER_FIELD{job="x"}[5m])', False),
        ('rate(UNKNOWN_FIELD{job="x"}[5m])', False),
        ("sum(GAUGE_FIELD)", False),
    ],
)
def test_check_rate_over_gauge_warns_only_for_non_counter_fields(expr, should_warn):
    csv_fields = {"GAUGE_FIELD": "gauge", "COUNTER_FIELD": "counter"}
    panels = [({"id": 7, "title": "P", "targets": [{"refId": "A", "expr": expr}]}, "top")]

    warnings: list = []
    lint_dashboard.check_rate_over_gauge(panels, csv_fields, warnings)

    assert bool(warnings) is should_warn


def test_check_rate_over_gauge_skips_targets_with_no_expr_and_keeps_checking_the_rest():
    """A target with no "expr" key at all must be skipped, not crash the scan (the
    `if not expr: continue` guard), and scanning must continue past it to later
    targets in the same panel."""
    csv_fields = {"GAUGE_FIELD": "gauge"}
    no_expr_target = {"refId": "A"}
    warn_target = {"refId": "B", "expr": "rate(GAUGE_FIELD[5m])"}
    panels = [({"id": 1, "title": "P", "targets": [no_expr_target, warn_target]}, "top")]

    warnings: list = []
    lint_dashboard.check_rate_over_gauge(panels, csv_fields, warnings)

    assert len(warnings) == 1
    assert "target[B]" in warnings[0]


def test_check_rate_over_gauge_message_reports_panel_id_title_refid_field_and_type():
    csv_fields = {"GAUGE_FIELD": "gauge"}
    panel = {"id": 42, "title": "GaugePanel", "targets": [{"refId": "C", "expr": "rate(GAUGE_FIELD[5m])"}]}

    warnings: list = []
    lint_dashboard.check_rate_over_gauge([(panel, "top")], csv_fields, warnings)

    assert len(warnings) == 1
    assert "panel id=42" in warnings[0]
    assert "'GaugePanel'" in warnings[0]
    assert "target[C]" in warnings[0]
    assert "'GAUGE_FIELD'" in warnings[0]
    assert "'gauge'" in warnings[0]


def _build_panel(panel_id, *, unit="watt", expr="sum(rate(COUNTER_FIELD[5m]))", x=0, y=0, w=6, h=4):
    return {
        "id": panel_id,
        "title": f"Panel {panel_id}",
        "type": "timeseries",
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": lint_dashboard.EXPECTED_DS,
        "fieldConfig": {"defaults": {"unit": unit}},
        "targets": [{"refId": "A", "datasource": lint_dashboard.EXPECTED_DS, "expr": expr}],
    }


def test_main_clean_dashboard_exits_zero_with_no_warn_or_error_lines(tmp_path, monkeypatch, capsys):
    dashboard = {"panels": [_build_panel(1)]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("COUNTER_FIELD,counter,a counter\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "WARN:" not in out
    assert "ERROR:" not in out
    assert "1 panels checked, 0 error(s), 0 warning(s)." in out


def test_main_warnings_only_still_exits_zero(tmp_path, monkeypatch, capsys):
    dashboard = {"panels": [_build_panel(1, expr="sum(rate(GAUGE_FIELD[5m]))")]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("GAUGE_FIELD,gauge,a gauge\nUNUSED_FIELD,label,never referenced\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out
    warn_lines = [line for line in out.splitlines() if line.startswith("WARN:")]

    assert exit_code == 0
    assert len(warn_lines) >= 2  # unused CSV field + rate() over a non-counter
    assert "ERROR:" not in out


def test_main_errors_exit_nonzero(tmp_path, monkeypatch, capsys):
    panel_a = _build_panel(1, x=0)
    panel_b = _build_panel(1, x=6)  # duplicate id
    dashboard = {"panels": [panel_a, panel_b]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("COUNTER_FIELD,counter,a counter\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out
    error_lines = [line for line in out.splitlines() if line.startswith("ERROR:")]

    assert exit_code == 1
    assert any("duplicate panel id 1" in line for line in error_lines)


def test_main_summary_line_reports_distinct_error_and_warning_counts(tmp_path, monkeypatch, capsys):
    """Errors and warnings must land in their own slot of the summary line. Uses a
    dashboard/CSV combo where the two counts differ (1 error, 3 warnings: the
    unused-field header line, its one "unused:" line, and one rate-over-gauge line)
    so a swap of {len(errors)} and {len(warnings)} in the f-string is caught."""
    dup_a = _build_panel(1, expr="sum(rate(GAUGE_FIELD[5m]))", x=0)  # duplicate id + gauge-rate warning
    dup_b = _build_panel(1, x=6)  # duplicate id -> the single error; default expr uses COUNTER_FIELD
    dashboard = {"panels": [dup_a, dup_b]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("GAUGE_FIELD,gauge,a gauge\nCOUNTER_FIELD,counter,a counter\nUNUSED_FIELD,label,never used\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    assert exit_code == 1
    assert len([line for line in out.splitlines() if line.startswith("ERROR:")]) == 1
    assert len([line for line in out.splitlines() if line.startswith("WARN:")]) == 3
    assert "2 panels checked, 1 error(s), 3 warning(s)." in out


def test_main_help_shows_the_docstring_with_its_raw_line_breaks_preserved(monkeypatch, capsys):
    """Pins both description=__doc__ (the text appears at all) and
    formatter_class=RawDescriptionHelpFormatter (the bullet list's own line breaks
    and indentation survive) -- the default HelpFormatter would reflow the
    docstring into wrapped paragraphs and lose the "  - " indentation entirely."""
    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        lint_dashboard.main()
    out = capsys.readouterr().out

    assert exc_info.value.code == 0
    assert "Static checks on a built dashboard JSON." in out
    assert "\n  - duplicate panel ids\n" in out


def test_main_dashboard_with_no_panels_key_checks_zero_panels_without_crashing(tmp_path, monkeypatch, capsys):
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({}))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "0 panels checked, 0 error(s), 0 warning(s)." in out


def test_main_reports_an_error_from_every_hard_error_category(tmp_path, monkeypatch, capsys):
    """Each check_*(all_panels, errors) call in main() must be wired to the real,
    shared `errors` list: a dashboard that trips every category at once means a call
    accidentally passed something else (e.g. None) would blow up with an
    AttributeError instead of quietly doing nothing, failing this test either way."""
    bad_ds_panel = _build_panel(1, x=0)
    bad_ds_panel["datasource"] = {"type": "influx"}
    bad_unit_panel = _build_panel(2, x=8, unit="furlongs")
    bad_regexp_panel = _build_panel(3, x=16)
    bad_regexp_panel["fieldConfig"]["overrides"] = [{"matcher": {"id": "byRegexp", "options": "bad"}}]
    kelvin_panel = _build_panel(4, x=0, y=10, expr="(x - 273.15) / y")
    dashboard = {"panels": [bad_ds_panel, bad_unit_panel, bad_regexp_panel, kelvin_panel]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("COUNTER_FIELD,counter,a counter\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "datasource !=" in out
    assert "unknown/unverified unit" in out
    assert "byRegexp override options not slash-delimited" in out
    assert "273.15" in out


def test_main_default_csv_resolves_from_project_root_regardless_of_cwd(tmp_path, monkeypatch, capsys):
    dashboard = {"panels": [_build_panel(1)]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path)])
    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    # No --csv passed: falls back to the real custom-counters.csv (an absolute
    # path computed from __file__, so it must resolve even after chdir). That
    # file has ~190 fields this tiny dashboard doesn't reference -> warnings,
    # but never errors.
    assert exit_code == 0
    assert "WARN:" in out


def test_main_lints_the_committed_dashboard_with_zero_errors(project_root, monkeypatch, capsys):
    dashboard_path = project_root / "nvidia-dcgm-dashboard.json"
    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(dashboard_path)])

    exit_code = lint_dashboard.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "ERROR:" not in out


def test_dunder_main_exits_zero_for_a_clean_dashboard(tmp_path, monkeypatch):
    dashboard = {"panels": [_build_panel(1)]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("COUNTER_FIELD,counter,a counter\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(lint_dashboard.__file__, run_name="__main__")
    assert exc_info.value.code == 0


def test_dunder_main_exits_nonzero_for_a_dashboard_with_errors(tmp_path, monkeypatch):
    dashboard = {"panels": [_build_panel(1, x=0), _build_panel(1, x=6)]}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    csv_path = tmp_path / "counters.csv"
    csv_path.write_text("COUNTER_FIELD,counter,a counter\n")

    monkeypatch.setattr(sys, "argv", ["lint_dashboard.py", str(json_path), "--csv", str(csv_path)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(lint_dashboard.__file__, run_name="__main__")
    assert exc_info.value.code == 1
