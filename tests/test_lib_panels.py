# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Panel builders (_grid.._base_panel..row) and the build_layout engine in dcgm_dashboard.lib."""

import types

import pytest

from dcgm_dashboard import lib


def _panel(pid: int, x: int, y: int, w: int, h: int, title: str = "P") -> dict:
    """Minimal panel dict -- only the keys build_layout/_check_no_overlap read."""
    return {"id": pid, "title": title, "gridPos": lib._grid(x, y, w, h)}


# ---------------------------------------------------------------------------
# _grid
# ---------------------------------------------------------------------------


def test_grid_normal():
    assert lib._grid(2, 3, 4, 5) == {"x": 2, "y": 3, "w": 4, "h": 5}


def test_grid_width_too_large_raises():
    with pytest.raises(ValueError, match=r"panel width 25 > 24"):
        lib._grid(0, 0, 25, 1)


def test_grid_x_plus_w_too_large_raises():
    with pytest.raises(ValueError, match=r"panel x\(20\)\+w\(10\) > 24"):
        lib._grid(20, 0, 10, 1)


def test_grid_x_plus_w_exactly_25_raises():
    # x+w=25 is the exact boundary of the ">24" check -- x+w=30 above doesn't
    # distinguish ">24" from a mutated ">25".
    with pytest.raises(ValueError, match=r"panel x\(20\)\+w\(5\) > 24"):
        lib._grid(20, 0, 5, 1)


# ---------------------------------------------------------------------------
# _base_panel -- every optional-argument branch, both outcomes
# ---------------------------------------------------------------------------


def test_base_panel_minimal_omits_optionals():
    # unit_id/thresholds_steps have no default (every call site in this module already
    # computes and passes both) -- pass the values their old defaults used to supply.
    panel = lib._base_panel(1, "T", "stat", 0, 0, 4, 4, unit_id="none", thresholds_steps=lib.no_thresholds())
    assert panel["id"] == 1
    assert panel["title"] == "T"
    assert panel["type"] == "stat"
    assert panel["datasource"] == {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
    assert panel["gridPos"] == {"x": 0, "y": 0, "w": 4, "h": 4}
    assert panel["fieldConfig"] == {"defaults": {"unit": "none", "thresholds": lib.no_thresholds()}, "overrides": []}
    assert panel["targets"] == []
    assert "description" not in panel
    assert "options" not in panel
    assert "links" not in panel


def test_base_panel_full_includes_all_optionals():
    mappings = [{"type": "value"}]
    overrides = [{"matcher": {}, "properties": []}]
    custom = {"lineWidth": 2}
    color = {"mode": "fixed", "fixedColor": "blue"}
    links = [{"title": "x", "url": "y"}]
    steps = {"mode": "absolute", "steps": [{"color": "red", "value": None}]}
    panel = lib._base_panel(
        2,
        "Full",
        "timeseries",
        1,
        2,
        6,
        3,
        targets=[{"expr": "up"}],
        description="desc text",
        unit_id="not_a_real_unit",
        thresholds_steps=steps,
        mappings=mappings,
        overrides=overrides,
        min_=5,
        max_=50,
        custom=custom,
        color=color,
        options={"custom": True},
        links=links,
        display_name="Display",
        no_value="N/A",
    )
    defaults = panel["fieldConfig"]["defaults"]
    assert defaults["unit"] == "not_a_real_unit"  # not in UNITS -> passed through, never validated
    assert defaults["thresholds"] == steps
    assert defaults["mappings"] == mappings
    assert defaults["min"] == 5
    assert defaults["max"] == 50
    assert defaults["custom"] == custom
    assert defaults["color"] == color
    assert defaults["displayName"] == "Display"
    assert defaults["noValue"] == "N/A"
    assert panel["fieldConfig"]["overrides"] == overrides
    assert panel["description"] == "desc text"
    assert panel["options"] == {"custom": True}
    assert panel["links"] == links
    assert panel["targets"] == [{"expr": "up"}]


def test_base_panel_options_empty_dict_is_still_included():
    # options={} is falsy but not None -- the `is not None` check must still add the
    # key (distinguishes it from a bare truthiness check on options).
    panel = lib._base_panel(
        3, "T3", "stat", 0, 0, 4, 4, unit_id="none", thresholds_steps=lib.no_thresholds(), options={}
    )
    assert panel["options"] == {}


def test_base_panel_custom_and_color_empty_dicts_omitted():
    # custom={}/color={} are falsy -> both branches use plain truthiness, so an empty
    # dict must NOT appear in fieldConfig.defaults (unlike options, which uses `is not
    # None`).
    panel = lib._base_panel(
        4, "T4", "stat", 0, 0, 4, 4, unit_id="none", thresholds_steps=lib.no_thresholds(), custom={}, color={}
    )
    assert "custom" not in panel["fieldConfig"]["defaults"]
    assert "color" not in panel["fieldConfig"]["defaults"]


def test_base_panel_unit_id_and_thresholds_steps_are_required():
    with pytest.raises(TypeError, match="unit_id"):
        lib._base_panel(9, "T9", "stat", 0, 0, 4, 4, thresholds_steps=lib.no_thresholds())
    with pytest.raises(TypeError, match="thresholds_steps"):
        lib._base_panel(9, "T9", "stat", 0, 0, 4, 4, unit_id="none")


def test_base_panel_looks_up_known_unit_id_through_unit_not_passthrough(monkeypatch):
    # Every real UNITS entry maps a name to itself, so `unit(unit_id)`'s return value
    # is indistinguishable from `unit_id` passed straight through for any id this
    # dashboard actually uses. Add a temporary non-identity entry so a real call
    # into unit() (which validates + translates) is actually distinguishable from
    # skipping it and using unit_id verbatim.
    monkeypatch.setitem(lib.UNITS, "test_unit", "TRANSLATED")
    panel = lib._base_panel(10, "T10", "stat", 0, 0, 4, 4, unit_id="test_unit", thresholds_steps=lib.no_thresholds())
    assert panel["fieldConfig"]["defaults"]["unit"] == "TRANSLATED"


# ---------------------------------------------------------------------------
# Panel-type builders
# ---------------------------------------------------------------------------


def test_stat_panel():
    tgt = lib.target("dcgm_gpu_util", legend="{{gpu}}", ref_id="A")
    panel = lib.stat(
        1,
        "GPU Util",
        0,
        0,
        4,
        4,
        [tgt],
        unit_id="percent",
        thresholds_steps=lib.no_thresholds(),
        graph_mode="area",
        color_mode="background",
        reduce_calc="mean",
        description="util stat",
        text_mode="value",
    )
    assert panel["type"] == "stat"
    assert panel["gridPos"] == {"x": 0, "y": 0, "w": 4, "h": 4}
    assert panel["targets"] == [tgt]
    assert panel["fieldConfig"]["defaults"]["unit"] == "percent"
    assert panel["fieldConfig"]["defaults"]["thresholds"] == lib.no_thresholds()
    assert panel["options"] == {
        "reduceOptions": {"calcs": ["mean"], "fields": "", "values": False},
        "orientation": "auto",
        "textMode": "value",
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "auto",
    }
    assert panel["description"] == "util stat"


def test_stat_panel_default_reduce_calc_is_last_not_null():
    panel = lib.stat(
        12,
        "Default Calc",
        0,
        0,
        4,
        4,
        [lib.target("x", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
    )
    assert panel["options"]["reduceOptions"]["calcs"] == ["lastNotNull"]


def test_stat_panel_unit_id_and_thresholds_steps_are_required():
    with pytest.raises(TypeError, match="unit_id"):
        lib.stat(12, "T", 0, 0, 4, 4, [lib.target("x", ref_id="A")], thresholds_steps=lib.no_thresholds())


def test_stat_panel_links_passthrough():
    links = [lib.dashboard_link("Docs", "https://example.com")]
    panel = lib.stat(
        13,
        "T13",
        0,
        0,
        4,
        4,
        [lib.target("x", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
        links=links,
    )
    assert panel["links"] == links


def test_gauge_panel():
    panel = lib.gauge(
        2,
        "Temp",
        0,
        4,
        4,
        4,
        [lib.target("dcgm_gpu_temp", ref_id="A")],
        unit_id="percent",
        min_=0,
        max_=100,
        thresholds_steps=lib.no_thresholds(),
    )
    assert panel["type"] == "gauge"
    assert panel["fieldConfig"]["defaults"]["unit"] == "percent"
    assert panel["fieldConfig"]["defaults"]["min"] == 0
    assert panel["fieldConfig"]["defaults"]["max"] == 100
    assert panel["options"] == {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "orientation": "auto",
        "showThresholdLabels": False,
        "showThresholdMarkers": True,
    }


def test_gauge_panel_mappings_and_overrides_passthrough():
    mappings = [{"type": "value", "options": {}}]
    overrides = [{"matcher": {}, "properties": []}]
    panel = lib.gauge(
        20,
        "T20",
        0,
        0,
        4,
        4,
        [lib.target("x", ref_id="A")],
        unit_id="none",
        min_=0,
        max_=100,
        thresholds_steps=lib.no_thresholds(),
        mappings=mappings,
        overrides=overrides,
    )
    assert panel["fieldConfig"]["defaults"]["mappings"] == mappings
    assert panel["fieldConfig"]["overrides"] == overrides


def test_bargauge_panel_with_optionals():
    color = {"mode": "fixed", "fixedColor": "blue"}
    panel = lib.bargauge(
        3,
        "Mem",
        0,
        0,
        6,
        3,
        [lib.target("x", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
        min_=0,
        max_=1,
        color=color,
        display_mode="lcd",
        orientation="vertical",
    )
    assert panel["fieldConfig"]["defaults"]["min"] == 0
    assert panel["fieldConfig"]["defaults"]["max"] == 1
    assert panel["fieldConfig"]["defaults"]["color"] == color
    assert panel["options"] == {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "orientation": "vertical",
        "displayMode": "lcd",
        "showUnfilled": True,
    }


def test_bargauge_panel_defaults_omit_min_max_color():
    panel = lib.bargauge(
        4, "Mem2", 0, 0, 6, 3, [lib.target("y", ref_id="A")], unit_id="none", thresholds_steps=lib.no_thresholds()
    )
    assert "min" not in panel["fieldConfig"]["defaults"]
    assert "max" not in panel["fieldConfig"]["defaults"]
    assert "color" not in panel["fieldConfig"]["defaults"]
    assert panel["options"]["displayMode"] == "gradient"
    assert panel["options"]["orientation"] == "horizontal"
    assert "description" not in panel  # default description="" is falsy -> omitted


def test_bargauge_panel_mappings_passthrough():
    mappings = [{"type": "value", "options": {}}]
    panel = lib.bargauge(
        21,
        "Mem3",
        0,
        0,
        6,
        3,
        [lib.target("y", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
        mappings=mappings,
    )
    assert panel["fieldConfig"]["defaults"]["mappings"] == mappings


def test_timeseries_unstacked_has_no_legend_sort():
    panel = lib.timeseries(
        5, "TS", 0, 0, 24, 8, [lib.target("z", ref_id="A")], unit_id="none", thresholds_steps=lib.no_thresholds()
    )
    assert panel["fieldConfig"]["defaults"]["custom"]["fillOpacity"] == 5
    assert panel["fieldConfig"]["defaults"]["custom"]["stacking"] == {"mode": "none", "group": "A"}
    assert panel["options"] == {
        "legend": {"displayMode": "list", "placement": "bottom", "calcs": []},
        "tooltip": {"mode": "multi", "sort": "none"},
    }
    assert "description" not in panel  # default description="" is falsy -> omitted


def test_timeseries_stacked_with_legend_sort():
    panel = lib.timeseries(
        6,
        "TS2",
        0,
        0,
        24,
        8,
        [lib.target("z2", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
        stacked=True,
        legend_calcs=["lastNotNull"],
        legend_sort_by="Last *",
        legend_sort_desc=True,
        min_=0,
        max_=10,
        legend_mode="table",
        legend_placement="right",
    )
    assert panel["fieldConfig"]["defaults"]["custom"]["fillOpacity"] == 15
    assert panel["fieldConfig"]["defaults"]["custom"]["stacking"] == {"mode": "normal", "group": "A"}
    assert panel["fieldConfig"]["defaults"]["min"] == 0
    assert panel["fieldConfig"]["defaults"]["max"] == 10
    assert panel["options"] == {
        "legend": {
            "displayMode": "table",
            "placement": "right",
            "calcs": ["lastNotNull"],
            "sortBy": "Last *",
            "sortDesc": True,
        },
        "tooltip": {"mode": "multi", "sort": "none"},
    }


def test_timeseries_legend_sort_by_empty_string_still_sets_sort_keys():
    # "" is falsy but `is not None` -- the empty string must still turn the sort-key
    # branch on (distinguishes `is not None` from a bare truthiness check).
    panel = lib.timeseries(
        7,
        "TS3",
        0,
        0,
        24,
        8,
        [lib.target("z3", ref_id="A")],
        unit_id="none",
        thresholds_steps=lib.no_thresholds(),
        legend_sort_by="",
    )
    assert panel["options"]["legend"]["sortBy"] == ""
    assert panel["options"]["legend"]["sortDesc"] is False


def test_timeseries_unit_id_and_thresholds_steps_are_required():
    with pytest.raises(TypeError, match="unit_id"):
        lib.timeseries(7, "TS", 0, 0, 24, 8, [lib.target("z", ref_id="A")], thresholds_steps=lib.no_thresholds())


def test_state_timeline_panel():
    mappings = [{"type": "value", "options": {}}]
    panel = lib.state_timeline(
        7, "Health", 0, 0, 24, 4, [lib.target("h", ref_id="A")], mappings=mappings, merge_values=False
    )
    assert panel["type"] == "state-timeline"
    assert panel["options"] == {
        "mergeValues": False,
        "showValue": "auto",
        "rowHeight": 0.9,
        "legend": {"displayMode": "list", "placement": "bottom"},
    }
    assert panel["fieldConfig"]["defaults"]["mappings"] == mappings
    assert panel["fieldConfig"]["defaults"]["thresholds"] == lib.no_thresholds()
    assert panel["fieldConfig"]["defaults"]["unit"] == "none"


def test_state_timeline_merge_values_default_true():
    panel = lib.state_timeline(71, "Health2", 0, 0, 24, 4, [lib.target("h2", ref_id="A")])
    assert panel["options"]["mergeValues"] is True
    assert "description" not in panel  # default description="" is falsy -> omitted


def test_table_without_transformations():
    panel = lib.table(8, "Inventory", 0, 0, 24, 6, [lib.target("inv", ref_id="A")])
    assert "transformations" not in panel
    assert panel["options"] == {"showHeader": True, "cellHeight": "sm"}
    assert panel["fieldConfig"]["defaults"]["custom"] == {"align": "auto", "cellOptions": {"type": "auto"}}
    assert "description" not in panel  # default description="" is falsy -> omitted


def test_table_mappings_passthrough():
    mappings = [{"type": "value", "options": {}}]
    panel = lib.table(22, "Inventory3", 0, 0, 24, 6, [lib.target("inv3", ref_id="A")], mappings=mappings)
    assert panel["fieldConfig"]["defaults"]["mappings"] == mappings


def test_table_with_transformations():
    trans = [{"id": "organize", "options": {}}]
    links = [lib.dashboard_link("x", "y")]
    panel = lib.table(
        9,
        "Inventory2",
        0,
        0,
        24,
        6,
        [lib.target("inv2", ref_id="A")],
        transformations=trans,
        no_value="N/A",
        links=links,
    )
    assert panel["transformations"] == trans
    assert panel["fieldConfig"]["defaults"]["noValue"] == "N/A"
    assert panel["links"] == links


def test_heatmap_full():
    panel = lib.heatmap(
        3,
        "Temp Heatmap",
        0,
        0,
        12,
        8,
        [lib.target("x", ref_id="A")],
        bucket_size=2.5,
        unit_id="celsius",
        description="d",
        min_=0,
        max_=100,
    )
    assert panel["type"] == "heatmap"
    assert panel["gridPos"] == {"x": 0, "y": 0, "w": 12, "h": 8}
    assert panel["fieldConfig"]["defaults"] == {"min": 0, "max": 100}
    assert panel["options"] == {
        "calculate": True,
        "calculation": {"xBuckets": {"mode": "size"}, "yBuckets": {"mode": "size", "value": "2.5"}},
        "color": {"mode": "scheme", "scheme": "Turbo", "steps": 64},
        "yAxis": {"unit": "celsius"},
        "cellGap": 1,
        "tooltip": {"show": True, "yHistogram": True},
        "legend": {"show": True},
    }
    assert panel["description"] == "d"
    assert panel["targets"] == [lib.target("x", ref_id="A")]


def test_heatmap_minimal_defaults():
    panel = lib.heatmap(4, "T2", 0, 0, 6, 6, [], bucket_size=1.0, unit_id="none")
    assert panel["fieldConfig"]["defaults"] == {}
    assert "description" not in panel
    assert panel["options"]["yAxis"]["unit"] == "none"


def test_heatmap_unit_id_is_required():
    with pytest.raises(TypeError, match="unit_id"):
        lib.heatmap(4, "T2", 0, 0, 6, 6, [], bucket_size=1.0)


def test_heatmap_unit_id_passthrough_when_unverified():
    panel = lib.heatmap(5, "T3", 0, 0, 6, 6, [], bucket_size=1.0, unit_id="weird_unit")
    assert panel["options"]["yAxis"]["unit"] == "weird_unit"


def test_heatmap_looks_up_known_unit_id_through_unit_not_passthrough(monkeypatch):
    # Same reasoning as test_base_panel_looks_up_known_unit_id_through_unit_not_passthrough:
    # every real UNITS entry is an identity mapping, so a temporary non-identity entry
    # is needed to tell "called unit()" apart from "used unit_id verbatim".
    monkeypatch.setitem(lib.UNITS, "test_unit", "TRANSLATED")
    panel = lib.heatmap(6, "T4", 0, 0, 6, 6, [], bucket_size=1.0, unit_id="test_unit")
    assert panel["options"]["yAxis"]["unit"] == "TRANSLATED"


def test_histogram_panel():
    panel = lib.histogram(10, "Hist", 0, 0, 12, 6, [lib.target("h2", ref_id="A")], unit_id="short", description="d2")
    assert panel["type"] == "histogram"
    assert panel["options"] == {"bucketOffset": 0, "legend": {"showLegend": True}}
    assert panel["fieldConfig"]["defaults"]["unit"] == "short"
    assert panel["description"] == "d2"


def test_histogram_unit_id_is_required():
    with pytest.raises(TypeError, match="unit_id"):
        lib.histogram(10, "Hist", 0, 0, 12, 6, [lib.target("h2", ref_id="A")])


def test_histogram_default_description_is_omitted():
    panel = lib.histogram(23, "Hist2", 0, 0, 12, 6, [lib.target("h3", ref_id="A")], unit_id="short")
    assert "description" not in panel


def test_text_panel():
    panel = lib.text(11, "Note", 0, 0, 24, 2, "**hi**", mode="html")
    assert panel == {
        "id": 11,
        "title": "Note",
        "type": "text",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 2},
        "options": {"mode": "html", "content": "**hi**"},
        "fieldConfig": {"defaults": {}, "overrides": []},
        "targets": [],
    }


def test_text_panel_default_mode_is_markdown():
    panel = lib.text(13, "Note2", 0, 0, 24, 2, "plain")
    assert panel["options"]["mode"] == "markdown"


def test_query_variable():
    v = lib.query_variable(
        "gpu",
        "GPU",
        "label_values(dcgm_gpu_util, UUID)",
        multi=False,
        include_all=False,
        all_value="",
        refresh=1,
        sort=0,
    )
    assert v == {
        "current": {},
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "definition": "label_values(dcgm_gpu_util, UUID)",
        "hide": 0,
        "includeAll": False,
        "multi": False,
        "name": "gpu",
        "label": "GPU",
        "options": [],
        "query": {"query": "label_values(dcgm_gpu_util, UUID)", "refId": "StandardVariableQuery"},
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
        "sort": 0,
        "type": "query",
        "allValue": "",
    }


def test_query_variable_defaults():
    # No optional args passed: locks in multi/includeAll/allValue/refresh/sort defaults,
    # which the other query_variable test always overrides explicitly.
    v = lib.query_variable("gpu2", "GPU2", "label_values(x, UUID)")
    assert v["multi"] is True
    assert v["includeAll"] is True
    assert v["allValue"] == ".*"
    assert v["refresh"] == 2
    assert v["sort"] == 1


def test_textbox_variable():
    v = lib.textbox_variable("job", "Job", "dcgm")
    assert v == {
        "current": {"value": "dcgm", "text": "dcgm"},
        "hide": 0,
        "name": "job",
        "label": "Job",
        "options": [],
        "query": "dcgm",
        "skipUrlSync": False,
        "type": "textbox",
    }


def test_annotation_query():
    a = lib.annotation_query("Reloads", "dcgm_driver_reload", "red", "Driver reload")
    assert a == {
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "enable": True,
        "expr": "dcgm_driver_reload",
        "iconColor": "red",
        "name": "Reloads",
        "titleFormat": "Driver reload",
    }


def test_dashboard_link():
    link = lib.dashboard_link("Docs", "https://example.com")
    assert link == {
        "asDropdown": False,
        "icon": "external link",
        "includeVars": False,
        "keepTime": False,
        "tags": [],
        "targetBlank": True,
        "title": "Docs",
        "tooltip": "",
        "type": "link",
        "url": "https://example.com",
    }


def test_row_without_repeat():
    r = lib.row(20, "Overview", collapsed=False)
    assert r == {"id": 20, "title": "Overview", "type": "row", "collapsed": False, "panels": []}


def test_row_with_repeat():
    r = lib.row(21, "Per GPU", collapsed=True, repeat="gpu")
    assert r == {"id": 21, "title": "Per GPU", "type": "row", "collapsed": True, "panels": [], "repeat": "gpu"}


def test_row_collapsed_is_required():
    with pytest.raises(TypeError, match="collapsed"):
        lib.row(20, "Overview")


# ---------------------------------------------------------------------------
# RowContext
# ---------------------------------------------------------------------------


def test_row_context_defaults():
    ctx = lib.RowContext()
    assert ctx.filter_all == lib.FILTER_ALL
    assert ctx.filter_single == lib.FILTER_SINGLE
    assert ctx.filter_up == lib.FILTER_UP
    assert ctx.legend_std == lib.LEGEND_STD


def test_row_context_custom_values():
    ctx = lib.RowContext(filter_all="a", filter_single="b", filter_up="c", legend_std="d")
    assert (ctx.filter_all, ctx.filter_single, ctx.filter_up, ctx.legend_std) == ("a", "b", "c", "d")


# ---------------------------------------------------------------------------
# rects_overlap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ({"x": 0, "y": 0, "w": 4, "h": 4}, {"x": 0, "y": 0, "w": 4, "h": 4}, True),
        ({"x": 0, "y": 0, "w": 2, "h": 2}, {"x": 5, "y": 0, "w": 2, "h": 2}, False),
        ({"x": 0, "y": 0, "w": 2, "h": 2}, {"x": 2, "y": 0, "w": 2, "h": 2}, False),  # touching edges: not overlap
        ({"x": 4, "y": 0, "w": 2, "h": 2}, {"x": 0, "y": 0, "w": 4, "h": 2}, False),  # touching, roles reversed
        ({"x": 0, "y": 4, "w": 2, "h": 2}, {"x": 0, "y": 0, "w": 2, "h": 4}, False),  # touching on y, a below b
        ({"x": 0, "y": 0, "w": 2, "h": 2}, {"x": 0, "y": 2, "w": 2, "h": 4}, False),  # touching on y, b below a
        ({"x": 0, "y": 0, "w": 4, "h": 2}, {"x": 2, "y": 5, "w": 4, "h": 2}, False),  # x overlaps, y disjoint
        ({"x": 0, "y": 0, "w": 4, "h": 4}, {"x": 2, "y": 2, "w": 4, "h": 4}, True),
    ],
)
def test_rects_overlap(a: dict, b: dict, expected: bool):
    assert lib.rects_overlap(a, b) is expected


# ---------------------------------------------------------------------------
# _check_no_overlap
# ---------------------------------------------------------------------------


def test_check_no_overlap_empty_list_ok():
    assert lib._check_no_overlap([], "ctx") is None


def test_check_no_overlap_single_panel_ok():
    assert lib._check_no_overlap([_panel(1, 0, 0, 4, 4)], "ctx") is None


def test_check_no_overlap_width_over_24_raises():
    panels = [{"id": 9, "title": "Bad", "gridPos": {"x": 0, "y": 0, "w": 30, "h": 1}}]
    with pytest.raises(ValueError, match=r"ctx: panel id=9 title='Bad' has gridPos width/x\+w > 24"):
        lib._check_no_overlap(panels, "ctx")


def test_check_no_overlap_x_plus_w_over_24_raises():
    panels = [{"id": 9, "title": "Bad2", "gridPos": {"x": 10, "y": 0, "w": 20, "h": 1}}]
    with pytest.raises(ValueError, match=r"has gridPos width/x\+w > 24"):
        lib._check_no_overlap(panels, "ctx")


def test_check_no_overlap_width_exactly_25_isolated_from_x_plus_w_raises():
    # w=25 alone must trip the "w > 24" branch. x is deliberately very negative so
    # x+w stays <= 24 -- otherwise the "x+w > 24" branch would also fire for any
    # x >= 0 (since w > 24 implies x+w > 24 whenever x >= 0), masking a mutated
    # ">25" on the w-only check.
    panels = [{"id": 9, "title": "Bad3", "gridPos": {"x": -10, "y": 0, "w": 25, "h": 1}}]
    with pytest.raises(ValueError, match=r"has gridPos width/x\+w > 24"):
        lib._check_no_overlap(panels, "ctx")


def test_check_no_overlap_x_plus_w_exactly_25_raises():
    # w=24 keeps the "w > 24" branch false, isolating "x+w > 24" at its own boundary
    # (x+w=25) from a mutated ">25" on that branch.
    panels = [{"id": 9, "title": "Bad4", "gridPos": {"x": 1, "y": 0, "w": 24, "h": 1}}]
    with pytest.raises(ValueError, match=r"has gridPos width/x\+w > 24"):
        lib._check_no_overlap(panels, "ctx")


def test_check_no_overlap_overlapping_panels_raise():
    panels = [_panel(1, 0, 0, 12, 4, "A"), _panel(2, 6, 0, 12, 4, "B")]
    with pytest.raises(ValueError, match=r"panels id=1 \('A'\) and id=2 \('B'\) overlap"):
        lib._check_no_overlap(panels, "ctx")


def test_check_no_overlap_non_overlapping_panels_ok():
    panels = [_panel(1, 0, 0, 12, 4, "A"), _panel(2, 12, 0, 12, 4, "B")]
    assert lib._check_no_overlap(panels, "ctx") is None


# ---------------------------------------------------------------------------
# build_layout
# ---------------------------------------------------------------------------


def test_build_layout_row_a_is_absolute_no_wrapper():
    panels = [_panel(1, 0, 0, 12, 4, "A1"), _panel(2, 12, 0, 12, 6, "A2")]
    mod = types.SimpleNamespace(build=lambda _ctx: (None, panels))
    result = lib.build_layout([mod])
    assert [p["id"] for p in result] == [1, 2]
    assert result[0]["gridPos"] == {"x": 0, "y": 0, "w": 12, "h": 4}
    assert result[1]["gridPos"] == {"x": 12, "y": 0, "w": 12, "h": 6}


def test_build_layout_y_stacking_across_row_shapes():
    # cursor trace: row_a(0->6) -> row_a_empty(6->6) -> expanded(6->10) -> expanded_empty(10->11)
    # -> collapsed(11->12); each module's expected y is asserted below.
    row_a_panels = [_panel(1, 0, 0, 12, 4, "A1"), _panel(2, 12, 0, 12, 6, "A2")]
    row_a = types.SimpleNamespace(build=lambda _ctx: (None, row_a_panels))
    row_a_empty = types.SimpleNamespace(build=lambda _ctx: (None, []))
    expanded = types.SimpleNamespace(
        build=lambda _ctx: (
            lib.row(100, "Expanded", collapsed=False),
            [_panel(101, 0, 1, 6, 3, "E1"), _panel(102, 6, 1, 6, 3, "E2")],
        )
    )
    expanded_empty = types.SimpleNamespace(build=lambda _ctx: (lib.row(150, "EmptyExpanded", collapsed=False), []))
    collapsed = types.SimpleNamespace(
        build=lambda _ctx: (
            lib.row(200, "Collapsed", collapsed=True),
            [_panel(201, 0, 0, 12, 5, "C1"), _panel(202, 12, 0, 12, 5, "C2")],
        )
    )

    result = lib.build_layout([row_a, row_a_empty, expanded, expanded_empty, collapsed])
    by_id = {p["id"]: p for p in result}

    assert by_id[1]["gridPos"] == {"x": 0, "y": 0, "w": 12, "h": 4}
    assert by_id[2]["gridPos"] == {"x": 12, "y": 0, "w": 12, "h": 6}
    assert by_id[100]["gridPos"] == {"x": 0, "y": 6, "w": 24, "h": 1}
    assert by_id[101]["gridPos"] == {"x": 0, "y": 7, "w": 6, "h": 3}
    assert by_id[102]["gridPos"] == {"x": 6, "y": 7, "w": 6, "h": 3}
    assert by_id[150]["gridPos"] == {"x": 0, "y": 10, "w": 24, "h": 1}
    assert by_id[150]["panels"] == []
    assert by_id[200]["gridPos"] == {"x": 0, "y": 11, "w": 24, "h": 1}
    assert by_id[200]["panels"][0]["gridPos"] == {"x": 0, "y": 0, "w": 12, "h": 5}
    assert by_id[200]["panels"][1]["gridPos"] == {"x": 12, "y": 0, "w": 12, "h": 5}
    assert 201 not in by_id  # collapsed children are nested, not top-level siblings
    assert 202 not in by_id


def test_build_layout_repeat_field_passes_through():
    mod = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(300, "Repeat Row", collapsed=False, repeat="gpu"), [_panel(301, 0, 1, 24, 3, "R1")])
    )
    result = lib.build_layout([mod])
    assert result[0]["repeat"] == "gpu"
    assert result[0]["gridPos"] == {"x": 0, "y": 0, "w": 24, "h": 1}
    assert result[1]["gridPos"] == {"x": 0, "y": 1, "w": 24, "h": 3}


def test_build_layout_default_ctx_is_row_context():
    captured = {}

    def _build(ctx):
        captured["ctx"] = ctx
        return None, [_panel(1, 0, 0, 4, 4)]

    lib.build_layout([types.SimpleNamespace(build=_build)])
    assert captured["ctx"] == lib.RowContext()


def test_build_layout_custom_ctx_passed_through_unchanged():
    custom_ctx = lib.RowContext(filter_all="custom-filter")
    captured = {}

    def _build(ctx):
        captured["ctx"] = ctx
        return None, [_panel(1, 0, 0, 4, 4)]

    lib.build_layout([types.SimpleNamespace(build=_build)], ctx=custom_ctx)
    assert captured["ctx"] is custom_ctx


def test_build_layout_duplicate_id_top_level_raises():
    mod1 = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(1, 0, 0, 4, 4, "First")]))
    mod2 = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(1, 0, 0, 4, 4, "Second")]))
    with pytest.raises(ValueError, match=r"duplicate panel id 1: 'First' and 'Second'"):
        lib.build_layout([mod1, mod2])


def test_build_layout_duplicate_id_row_vs_panel_raises():
    mod1 = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(5, 0, 0, 4, 4, "P5")]))
    mod2 = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(5, "DupRow", collapsed=False), [_panel(6, 0, 1, 4, 4, "P6")])
    )
    with pytest.raises(ValueError, match=r"duplicate panel id 5: 'P5' and 'DupRow'"):
        lib.build_layout([mod1, mod2])


def test_build_layout_collapsed_row_advances_cursor_by_one_regardless_of_child_height():
    # Per build_layout's docstring: "only the header occupies space while collapsed" --
    # a tall child (h=10) must NOT inflate the cursor for the row that follows.
    collapsed = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(60, "Tall Collapsed", collapsed=True), [_panel(61, 0, 0, 24, 10, "Tall")])
    )
    after = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(62, 0, 0, 4, 4, "After")]))
    result = lib.build_layout([collapsed, after])
    by_id = {p["id"]: p for p in result}
    assert by_id[60]["gridPos"] == {"x": 0, "y": 0, "w": 24, "h": 1}
    assert by_id[62]["gridPos"] == {"x": 0, "y": 1, "w": 4, "h": 4}


def test_build_layout_overlap_top_level_raises():
    mod = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(1, 0, 0, 12, 4, "OA"), _panel(2, 6, 0, 12, 4, "OB")]))
    # Anchored at start (^) so a mutated context string (e.g. "XXrow A (top strip)XX")
    # can't slip a real "row A (top strip)" substring past a mid-string search.
    with pytest.raises(ValueError, match=r"^row A \(top strip\): panels id=1 \('OA'\) and id=2 \('OB'\) overlap"):
        lib.build_layout([mod])


def test_build_layout_overlap_inside_collapsed_row_raises():
    mod = types.SimpleNamespace(
        build=lambda _ctx: (
            lib.row(50, "Bad Collapsed", collapsed=True),
            [_panel(51, 0, 0, 12, 4, "N1"), _panel(52, 6, 0, 12, 4, "N2")],
        )
    )
    with pytest.raises(ValueError, match=r"row 'Bad Collapsed' \(collapsed children\).*overlap"):
        lib.build_layout([mod])


def test_build_layout_overlap_inside_expanded_row_raises():
    # The expanded (non-collapsed) row branch has its own _check_no_overlap call,
    # separate from row A's and the collapsed-row one above -- confirms its context
    # string is the real f"row {title!r}" (e.g. not passed as None).
    mod = types.SimpleNamespace(
        build=lambda _ctx: (
            lib.row(80, "Bad Expanded", collapsed=False),
            [_panel(81, 0, 1, 12, 4, "N1"), _panel(82, 6, 1, 12, 4, "N2")],
        )
    )
    with pytest.raises(ValueError, match=r"^row 'Bad Expanded': panels id=81 \('N1'\) and id=82 \('N2'\) overlap"):
        lib.build_layout([mod])


def test_build_layout_deep_copies_row_a_panels_not_shared_with_source():
    # panels = [copy.deepcopy(p) for p in panels] must not share nested dicts (like
    # gridPos) with the row module's own panel list -- a shallow copy.copy(p) would
    # still let build_layout's in-place `p["gridPos"]["y"] += cursor` corrupt the
    # source. cursor must be nonzero when the second module's panels get shifted, or
    # `+= 0` wouldn't show the difference either way.
    row1_panels = [_panel(1, 0, 0, 4, 4)]
    row2_panels = [_panel(2, 0, 0, 4, 4)]
    mod1 = types.SimpleNamespace(build=lambda _ctx: (None, row1_panels))
    mod2 = types.SimpleNamespace(build=lambda _ctx: (None, row2_panels))
    lib.build_layout([mod1, mod2])
    assert row2_panels[0]["gridPos"]["y"] == 0


def test_build_layout_row_a_height_uses_cursor_relative_y_not_absolute():
    # row_height = max((y - cursor) + h ...) -- the "- cursor" un-shifts y back to the
    # module's own relative coordinate before adding h. A mutated "+ cursor" only
    # shows up once cursor is already nonzero when a row-A-shaped module's height is
    # computed, so this needs 3 modules: the first makes cursor nonzero, the second's
    # height computation is the one under test, and the third's position reveals it.
    first_panels = [_panel(1, 0, 0, 4, 4)]  # height 4 -> cursor becomes 4
    second_panels = [_panel(2, 0, 2, 4, 3)]  # relative y=2, h=3 -> height 5
    third_panels = [_panel(3, 0, 0, 4, 4)]
    mod1 = types.SimpleNamespace(build=lambda _ctx: (None, first_panels))
    mod2 = types.SimpleNamespace(build=lambda _ctx: (None, second_panels))
    mod3 = types.SimpleNamespace(build=lambda _ctx: (None, third_panels))
    result = lib.build_layout([mod1, mod2, mod3])
    by_id = {p["id"]: p for p in result}
    assert by_id[2]["gridPos"]["y"] == 6  # 2 (relative) + cursor(4)
    assert by_id[3]["gridPos"]["y"] == 9  # cursor after mod2: 4 + (2 + 3) = 9


def test_build_layout_row_a_panel_without_title_key_registers_empty_string():
    # p.get("title", "") -- the default only matters when "title" is absent; confirms
    # it's specifically "" (not None, and looked up under the key "title").
    panel_no_title = {"id": 1, "gridPos": lib._grid(0, 0, 4, 4)}
    panel_dup = _panel(1, 4, 0, 4, 4, "Dup")
    mod = types.SimpleNamespace(build=lambda _ctx: (None, [panel_no_title, panel_dup]))
    with pytest.raises(ValueError, match=r"duplicate panel id 1: '' and 'Dup'"):
        lib.build_layout([mod])


def test_build_layout_row_def_without_title_key_registers_empty_string():
    # Same as above but for the row_def itself (row_def.get("title", "")), reached
    # right after `row_def = copy.deepcopy(row_def)`.
    row_def_no_title = {"id": 500, "type": "row", "collapsed": False, "panels": []}
    mod1 = types.SimpleNamespace(build=lambda _ctx: (row_def_no_title, []))
    mod2 = types.SimpleNamespace(build=lambda _ctx: (lib.row(500, "Dup", collapsed=False), []))
    with pytest.raises(ValueError, match=r"duplicate panel id 500: '' and 'Dup'"):
        lib.build_layout([mod1, mod2])


def test_build_layout_collapsed_children_register_actual_title_when_present():
    # Distinguishes the real key "title" from a mutated one (None/"TITLE"/"XXtitleXX")
    # that would silently fall back to the default and lose the real title text.
    panels = [_panel(601, 0, 0, 12, 4, "First"), _panel(601, 12, 0, 12, 4, "Second")]
    mod = types.SimpleNamespace(build=lambda _ctx: (lib.row(600, "Collapsed", collapsed=True), panels))
    with pytest.raises(ValueError, match=r"duplicate panel id 601: 'First' and 'Second'"):
        lib.build_layout([mod])


def test_build_layout_collapsed_children_missing_title_registers_empty_string():
    panel_no_title = {"id": 602, "gridPos": lib._grid(0, 0, 12, 4)}
    panel_dup = _panel(602, 12, 0, 12, 4, "Dup")
    mod = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(650, "Collapsed2", collapsed=True), [panel_no_title, panel_dup])
    )
    with pytest.raises(ValueError, match=r"duplicate panel id 602: '' and 'Dup'"):
        lib.build_layout([mod])


def test_build_layout_expanded_children_register_actual_title_when_present():
    panels = [_panel(701, 0, 1, 12, 4, "First"), _panel(701, 12, 1, 12, 4, "Second")]
    mod = types.SimpleNamespace(build=lambda _ctx: (lib.row(700, "Expanded", collapsed=False), panels))
    with pytest.raises(ValueError, match=r"duplicate panel id 701: 'First' and 'Second'"):
        lib.build_layout([mod])


def test_build_layout_expanded_children_missing_title_registers_empty_string():
    panel_no_title = {"id": 702, "gridPos": lib._grid(0, 1, 12, 4)}
    panel_dup = _panel(702, 12, 1, 12, 4, "Dup")
    mod = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(750, "Expanded2", collapsed=False), [panel_no_title, panel_dup])
    )
    with pytest.raises(ValueError, match=r"duplicate panel id 702: '' and 'Dup'"):
        lib.build_layout([mod])


def test_build_layout_collapsed_row_cursor_accumulates_not_resets():
    # cursor += 1 (not "cursor = 1") -- only visible once cursor is already nonzero
    # before the collapsed row is processed.
    first = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(1, 0, 0, 4, 4)]))  # cursor -> 4
    collapsed = types.SimpleNamespace(
        build=lambda _ctx: (lib.row(60, "Collapsed", collapsed=True), [_panel(61, 0, 0, 24, 10, "Tall")])
    )
    after = types.SimpleNamespace(build=lambda _ctx: (None, [_panel(62, 0, 0, 4, 4, "After")]))
    result = lib.build_layout([first, collapsed, after])
    by_id = {p["id"]: p for p in result}
    assert by_id[60]["gridPos"]["y"] == 4
    assert by_id[62]["gridPos"]["y"] == 5  # 4 (first) + 1 (collapsed header), not reset to 1
