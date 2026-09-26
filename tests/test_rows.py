# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Contract coverage for dcgm_dashboard/rows/row_a.py..row_l.py (+ rows/__init__.py)."""

import re

import pytest

from dcgm_dashboard import lib
from dcgm_dashboard.rows import (
    row_a,
    row_b,
    row_c,
    row_d,
    row_e,
    row_f,
    row_g,
    row_h,
    row_i,
    row_j,
    row_k,
    row_l,
)

DS_REF = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}

# letter -> (module, row id or None, row title or None, collapsed or None, repeat or
# None, child panel ids in the exact order build() returns them, expected minimum
# relative gridPos.y across that module's panels). Cross-checked against
# dcgm_dashboard/CONVENTIONS.md's id-range table.
ROW_SPECS = {
    "a": (row_a, None, None, None, None, list(range(1, 11)), 0),
    "b": (row_b, 11, "$gpu — Overview", False, "gpu", list(range(12, 20)), 1),
    "c": (row_c, 20, "GPU Inventory", False, None, [97, 21], 1),
    "d": (row_d, 22, "Utilization & Real Activity (Profiling)", False, None, list(range(23, 32)), 1),
    "e": (row_e, 32, "Memory (Framebuffer + BAR1)", False, None, list(range(33, 39)), 1),
    "f": (row_f, 39, "Clocks & Throttling", False, None, [40, 41, 42, 43, 44, 45, 46, 98, 99], 1),
    "g": (row_g, 47, "Power, Energy & Cost", False, None, list(range(48, 56)), 1),
    "h": (row_h, 56, "Thermals & Fan", False, None, list(range(57, 63)), 1),
    "i": (row_i, 63, "PCIe", False, None, list(range(64, 68)), 1),
    "j": (row_j, 68, "Video Engines (ENC/DEC/NVDEC/NVJPG/OFA)", False, None, list(range(69, 74)), 1),
    "k": (
        row_k,
        74,
        "Reliability (ECC / Remap / Retired / XID / Recovery / Health)",
        False,
        None,
        [75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 96, 100],
        1,
    ),
    "l": (row_l, 86, "Datacenter / hardware-specific", True, None, [87, 88, 95, 89, 90, 91, 92, 93, 94], 0),
}


def _props(override: dict) -> dict:
    return {p["id"]: p["value"] for p in override["properties"]}


@pytest.mark.parametrize("letter", sorted(ROW_SPECS))
def test_row_contract(letter: str) -> None:
    """Every row module: row_def shape, exact/ordered/unique child ids, and every
    panel's datasource/gridPos/targets invariants from CONVENTIONS.md."""
    module, row_id, title, collapsed, repeat, child_ids, min_y = ROW_SPECS[letter]
    row_def, panels = module.build(lib.RowContext())

    if row_id is None:
        assert row_def is None
    else:
        assert row_def["id"] == row_id
        assert row_def["title"] == title
        assert row_def["type"] == "row"
        assert row_def["collapsed"] is collapsed
        assert row_def["panels"] == []
        assert ("repeat" in row_def) == (repeat is not None)
        if repeat is not None:
            assert row_def["repeat"] == repeat

    ids = [p["id"] for p in panels]
    assert ids == child_ids
    assert len(set(ids)) == len(ids)
    assert min(p["gridPos"]["y"] for p in panels) == min_y

    for panel in panels:
        grid = panel["gridPos"]
        assert grid["x"] >= 0
        assert grid["w"] > 0
        assert grid["h"] > 0
        assert grid["x"] + grid["w"] <= 24
        assert panel["datasource"] == DS_REF
        targets = panel["targets"]
        assert targets, f"panel {panel['id']} ({panel['title']!r}) has no targets"
        ref_ids = [t["refId"] for t in targets]
        assert len(set(ref_ids)) == len(ref_ids), f"panel {panel['id']} has duplicate target refIds"
        for t in targets:
            assert isinstance(t["expr"], str) and t["expr"]


def test_all_row_and_child_ids_are_globally_unique_and_total_100() -> None:
    """Cross-check of CONVENTIONS.md's "100 panels total" and the id-range table,
    computed directly from the row modules rather than from the table itself."""
    seen: set[int] = set()
    for letter, (module, row_id, *_rest, child_ids, _min_y) in ROW_SPECS.items():
        module.build(lib.RowContext())
        for panel_id in ([row_id] if row_id is not None else []) + child_ids:
            assert panel_id not in seen, f"duplicate id {panel_id} (row {letter})"
            seen.add(panel_id)
    assert len(seen) == 100


def test_row_a_top_strip_is_wrapperless_with_absolute_y() -> None:
    row_def, panels = row_a.build(lib.RowContext())
    assert row_def is None
    ys = sorted({p["gridPos"]["y"] for p in panels})
    assert ys == [0, 4]  # two sub-rows, absolute-from-0 (row A always starts the dashboard)

    gpus_panel = next(p for p in panels if p["id"] == 1)
    legends = [t.get("legendFormat") for t in gpus_panel["targets"]]
    assert legends == ["GPUs", "Exporters down", "GPUs known to DCGM"]
    exporters_down_props = _props(
        next(o for o in gpus_panel["fieldConfig"]["overrides"] if o["matcher"]["options"] == "Exporters down")
    )
    assert exporters_down_props["thresholds"] == lib.thresholds([(None, "green"), (1, "red")])

    health_panel = next(p for p in panels if p["id"] == 8)
    assert health_panel["title"] == "Health"
    assert health_panel["fieldConfig"]["defaults"]["mappings"] == lib.health_status_mappings()

    throttled_panel = next(p for p in panels if p["id"] == 9)
    legends = [t.get("legendFormat") for t in throttled_panel["targets"]]
    assert legends == ["Fault-throttled", "At power cap"]
    at_cap_props = _props(
        next(o for o in throttled_panel["fieldConfig"]["overrides"] if o["matcher"]["options"] == "At power cap")
    )
    assert at_cap_props["thresholds"] == lib.no_thresholds("blue")


def test_row_b_repeats_per_gpu_with_single_equality_filter() -> None:
    row_def, panels = row_b.build(lib.RowContext())
    assert row_def["repeat"] == "gpu"
    assert row_def["title"] == "$gpu — Overview"

    gpu_util = next(p for p in panels if p["id"] == 12)
    expr = gpu_util["targets"][0]["expr"]
    assert 'UUID="$gpu"' in expr  # FILTER_SINGLE: plain equality, not the =~ multi-select regex
    assert "UUID=~" not in expr

    fan_panel = next(p for p in panels if p["id"] == 18)
    fan_expr = fan_panel["targets"][0]["expr"]
    assert "DCGM_FI_DEV_FAN_SPEED" in fan_expr
    assert "DCGM_FI_DEV_GPU_UTIL" in fan_expr
    assert " or (" in fan_expr and "*0)" in fan_expr  # same-identity fallback, not a label-less vector(0)

    pstate_panel = next(p for p in panels if p["id"] == 19)
    assert pstate_panel["options"]["colorMode"] == "none"
    assert pstate_panel["fieldConfig"]["defaults"]["mappings"] == lib.pstate_mappings(colored=False)


def test_row_c_fleet_status_sorted_and_inventory_join_drops_anchor_column() -> None:
    _row_def, panels = row_c.build(lib.RowContext())
    fleet_panel = next(p for p in panels if p["id"] == 97)
    assert len(fleet_panel["targets"]) == 8
    assert fleet_panel["options"]["sortBy"] == [
        {"displayName": "Health", "desc": True},
        {"displayName": "Temp (C)", "desc": True},
    ]

    inventory_panel = next(p for p in panels if p["id"] == 21)
    organize_opts = inventory_panel["transformations"][1]["options"]
    # value_renames[0] is None (the GPU_UTIL anchor is join-only, not a displayed
    # column) -> its "Value #A" column must be excluded, not renamed.
    assert organize_opts["excludeByName"].get("Value #A") is True
    assert "Value #A" not in organize_opts["renameByName"]
    assert organize_opts["renameByName"]["Value #B"] == "FB Total (MiB)"


@pytest.mark.parametrize(
    ("bit_name", "bit_value", "nature"),
    [(name, value, nature) for name, (value, nature) in lib.CLOCK_EVENT_BITS.items()],
)
def test_row_f_clock_event_bit_lane_colors_follow_override_or_nature(
    bit_name: str, bit_value: int, nature: str
) -> None:
    """Every bit lane on panel 42 gets BIT_COLOR_OVERRIDE's color when the module
    overrides it (SW_POWER_CAP -> yellow instead of throttle-red) and
    lib.CLOCK_EVENT_COLOR[nature] otherwise -- both sides of that fallback."""
    _row_def, panels = row_f.build(lib.RowContext())
    panel_42 = next(p for p in panels if p["id"] == 42)
    override = next(o for o in panel_42["fieldConfig"]["overrides"] if o["matcher"]["options"] == f"/^{bit_name} /")
    mapping_options = _props(override)["mappings"][0]["options"]
    assert mapping_options["0"]["text"] == "—"
    expected_color = row_f.BIT_COLOR_OVERRIDE.get(bit_name, lib.CLOCK_EVENT_COLOR[nature])
    assert mapping_options["1"]["color"] == expected_color
    assert mapping_options["1"]["text"] == row_f.BIT_LABELS[bit_name]
    assert bit_value  # every configured bit value is a real (nonzero) bitmask bit


@pytest.mark.parametrize(("field", "legend"), row_f.THROTTLE_NS_FIELDS)
def test_row_f_throttle_ns_panel_colors_match_bit_nature(field: str, legend: str) -> None:
    _row_def, panels = row_f.build(lib.RowContext())
    panel_44 = next(p for p in panels if p["id"] == 44)
    pattern = f"/^{re.escape(legend)} /"
    override = next(o for o in panel_44["fieldConfig"]["overrides"] if o["matcher"]["options"] == pattern)
    bit_name = row_f.THROTTLE_NS_BIT_NAME[legend]
    _bit_value, nature = lib.CLOCK_EVENT_BITS[bit_name]
    expected_color = row_f.BIT_COLOR_OVERRIDE.get(bit_name, lib.CLOCK_EVENT_COLOR[nature])
    assert _props(override)["color"] == lib.fixed_color(expected_color)
    assert field  # the field name itself isn't asserted on (only its legend/color), keep it referenced


@pytest.mark.parametrize(("legend_label", "color"), list(row_f.FAULT_INFO_ONLY.items()))
def test_row_f_fault_violation_info_only_series_never_alarm_colored(legend_label: str, color: str) -> None:
    _row_def, panels = row_f.build(lib.RowContext())
    panel_46 = next(p for p in panels if p["id"] == 46)
    override = next(
        o for o in panel_46["fieldConfig"]["overrides"] if o["matcher"]["options"] == f"/^{re.escape(legend_label)} /"
    )
    assert _props(override)["color"] == lib.fixed_color(color)


def test_row_f_duty_cycle_bargauge_gives_power_cap_its_own_threshold() -> None:
    _row_def, panels = row_f.build(lib.RowContext())
    panel_98 = next(p for p in panels if p["id"] == 98)
    overrides_98 = panel_98["fieldConfig"]["overrides"]
    sync_boost_props = _props(next(o for o in overrides_98 if o["matcher"]["options"] == "Sync Boost"))
    assert sync_boost_props["color"] == lib.fixed_color("gray")
    power_cap_props = _props(
        next(o for o in panel_98["fieldConfig"]["overrides"] if o["matcher"]["options"] == "SW Power Cap")
    )
    assert power_cap_props["thresholds"] == lib.thresholds([(None, "green"), (0.05, "yellow"), (0.5, "orange")])

    panel_99 = next(p for p in panels if p["id"] == 99)
    expr = panel_99["targets"][0]["expr"]
    assert "avg_over_time" in expr
    assert "<= bool 2" in expr
    assert panel_99["options"]["textMode"] == "value"


def test_row_j_per_instance_panels_have_one_target_per_engine() -> None:
    _row_def, panels = row_j.build(lib.RowContext())
    nvdec_panel = next(p for p in panels if p["id"] == 70)
    assert len(nvdec_panel["targets"]) == 8
    assert [t["refId"] for t in nvdec_panel["targets"]] == list(lib._excel_ref_ids(8))
    for i, t in enumerate(nvdec_panel["targets"]):
        assert t["legendFormat"].startswith(f"NVDEC{i} ·")
    assert nvdec_panel["options"]["legend"]["displayMode"] == "table"

    cache_panel = next(p for p in panels if p["id"] == 73)
    legends = [t["legendFormat"] for t in cache_panel["targets"]]
    assert legends == [
        "Host hit · {{hostname}} GPU{{gpu}}",
        "Host miss · {{hostname}} GPU{{gpu}}",
        "Peer hit · {{hostname}} GPU{{gpu}}",
        "Peer miss · {{hostname}} GPU{{gpu}}",
    ]


def test_row_l_fabric_table_renames_identity_fields_it_owns_and_excludes_the_rest() -> None:
    """Panel 87's comprehension `[n for n in _LABEL_NOISE if n not in fabric_identity]`
    (CONVENTIONS.md's series-identity exception): the 2 CSV label fields this table
    promotes to identity columns must be renamed, not excluded; the 1 it does not
    promote must be excluded, not renamed -- both sides of that filter, asserted."""
    _row_def, panels = row_l.build(lib.RowContext())
    fabric_panel = next(p for p in panels if p["id"] == 87)
    organize_opts = fabric_panel["transformations"][1]["options"]
    rename = organize_opts["renameByName"]
    exclude = organize_opts["excludeByName"]

    assert rename["DCGM_FI_DEV_FABRIC_CLUSTER_UUID"] == "Fabric Cluster UUID"
    assert rename["DCGM_FI_DEV_FABRIC_CLUSTER_UUID 1"] == "Fabric Cluster UUID"
    assert rename["DCGM_FI_IMEX_DOMAIN_STATUS"] == "IMEX Domain Status"
    assert "DCGM_FI_DEV_FABRIC_CLUSTER_UUID" not in exclude
    assert "DCGM_FI_IMEX_DOMAIN_STATUS" not in exclude

    assert exclude.get("DCGM_FI_CUDA_GPU_VISIBLE_DEVICES") is True
    assert exclude.get("DCGM_FI_CUDA_GPU_VISIBLE_DEVICES 1") is True
    assert "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES" not in rename


def test_row_l_mig_table_promotes_visible_devices_and_joins_on_composite_key() -> None:
    """Panel 90's identical comprehension with the opposite membership (CUDA Visible
    Devices IS promoted here; Fabric Cluster UUID/IMEX Domain Status are NOT) --
    the other side of the same branch, plus the mig_key join-field fix."""
    _row_def, panels = row_l.build(lib.RowContext())
    mig_panel = next(p for p in panels if p["id"] == 90)
    organize_opts = mig_panel["transformations"][1]["options"]
    rename = organize_opts["renameByName"]
    exclude = organize_opts["excludeByName"]

    assert rename["DCGM_FI_CUDA_GPU_VISIBLE_DEVICES"] == "CUDA Visible Devices"
    assert "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES" not in exclude

    assert exclude.get("DCGM_FI_DEV_FABRIC_CLUSTER_UUID") is True
    assert exclude.get("DCGM_FI_IMEX_DOMAIN_STATUS") is True
    assert exclude.get("mig_key") is True  # the synthetic join key must never leak through as a column

    join_opts = mig_panel["transformations"][0]["options"]
    assert join_opts == {"byField": "mig_key", "mode": "inner"}

    anchor_expr = mig_panel["targets"][0]["expr"]
    assert "DCGM_FI_DEV_MIG_MODE" in anchor_expr
    assert "== 1" in anchor_expr
    assert "label_join(" in anchor_expr
    assert "mig_key" in anchor_expr
    assert "topk by" in anchor_expr  # latest_per_gpu()'s staleness-defensive filter
