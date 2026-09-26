# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Tests for dcgm_dashboard.lib's datasource/target/mapping/decode helpers (lib-core scope)."""

import math

import pytest

from dcgm_dashboard import lib


def test_ds_ref_returns_a_fresh_dict_each_call():
    assert lib.DS_VAR == "${DS_PROMETHEUS}"
    first = lib.ds_ref()
    second = lib.ds_ref()
    assert first == {"type": "prometheus", "uid": lib.DS_VAR}
    first["uid"] = "mutated"
    assert second["uid"] == lib.DS_VAR  # confirms callers never share one instance


def test_filter_and_legend_constants():
    assert lib.FILTER_ALL == '{job=~"$job", instance=~"$instance", UUID=~"$gpu"}'
    assert lib.FILTER_SINGLE == '{job=~"$job", instance=~"$instance", UUID="$gpu"}'
    assert lib.FILTER_UP == '{job=~"$job", instance=~"$instance"}'
    assert lib.LEGEND_STD == "{{hostname}} GPU{{gpu}}"


def test_gpu_id_label_sets():
    assert lib.GPU_ID_LABELS == (
        "job",
        "instance",
        "hostname",
        "gpu",
        "UUID",
        "modelName",
        "pci_bus_id",
        "device",
        "GPU_I_ID",
        "GPU_I_PROFILE",
    )
    assert lib.LATEST_GPU_ID_LABELS == ("job", "instance", "UUID", "GPU_I_ID")


def test_per_gpu_default_agg_wraps_expr_over_gpu_id_labels():
    labels = ", ".join(lib.GPU_ID_LABELS)
    assert lib.per_gpu("DCGM_FI_DEV_GPU_UTIL{...}") == f"max by ({labels}) (DCGM_FI_DEV_GPU_UTIL{{...}})"


def test_per_gpu_custom_agg_and_extra_labels():
    labels = ", ".join((*lib.GPU_ID_LABELS, "health_watch"))
    assert lib.per_gpu("x", agg="sum", extra=("health_watch",)) == f"sum by ({labels}) (x)"


def test_latest_per_gpu_without_base_times_the_expr_itself():
    result = lib.latest_per_gpu("some_selector")
    assert result == "some_selector and topk by (job, instance, UUID, GPU_I_ID) (1, timestamp(some_selector))"


def test_latest_per_gpu_with_base_times_the_raw_selector():
    # Worked example from the docstring.
    result = lib.latest_per_gpu("floor(x/65536)", base="x")
    assert result == "floor(x/65536) and topk by (job, instance, UUID, GPU_I_ID) (1, timestamp(x))"


@pytest.mark.parametrize(
    ("single", "expected_filter"),
    [(False, lib.FILTER_ALL), (True, lib.FILTER_SINGLE)],
)
def test_with_filter_selects_single_vs_multi_gpu_filter(single, expected_filter):
    assert lib.with_filter("DCGM_FI_DEV_GPU_UTIL", single=single) == "DCGM_FI_DEV_GPU_UTIL" + expected_filter


def test_with_filter_default_single_is_false():
    # single has no explicit arg here -- locks in the default (FILTER_ALL, not
    # FILTER_SINGLE) separately from the parametrized, always-explicit test above.
    assert lib.with_filter("DCGM_FI_DEV_GPU_UTIL") == "DCGM_FI_DEV_GPU_UTIL" + lib.FILTER_ALL


def test_units_table_is_exactly_the_verified_grafana_ids():
    # Literal expectation (not mirrored from lib.UNITS) so a mutated/dropped/renamed
    # entry is caught -- tools/lint_dashboard.py trusts this exact table.
    assert lib.UNITS == {
        "none": "none",
        "short": "short",
        "percent": "percent",
        "percentunit": "percentunit",
        "celsius": "celsius",
        "watt": "watt",
        "kwatth": "kwatth",
        "mbytes": "mbytes",
        "bytes": "bytes",
        "rotmhz": "rotmhz",
        "Bps": "Bps",
        "ns": "ns",
    }


@pytest.mark.parametrize("name", sorted(lib.UNITS))
def test_unit_returns_verified_unit_id(name):
    assert lib.unit(name) == lib.UNITS[name]


def test_unit_rejects_unverified_unit_id():
    with pytest.raises(ValueError, match="unverified Grafana unit id: 'furlongs'"):
        lib.unit("furlongs")


def test_excel_ref_ids_empty():
    assert lib._excel_ref_ids(0) == []


def test_excel_ref_ids_single_letters():
    assert lib._excel_ref_ids(1) == ["A"]
    assert lib._excel_ref_ids(26)[-1] == "Z"


def test_excel_ref_ids_double_letters_after_z():
    ids = lib._excel_ref_ids(30)
    assert ids[25] == "Z"
    assert ids[26] == "AA"
    assert ids[27] == "AB"
    assert ids[29] == "AD"
    assert len(ids) == len(set(ids)) == 30  # every refId unique


def test_excel_ref_ids_three_letters_after_zz():
    # Exercises a 3rd bijective-base-26 digit -- the inner loop's `x // 26 - 1` step
    # (not `+ 1`, which would never reach the `x < 0` break) is what makes ZZ roll
    # over to AAA rather than looping forever or wrapping wrong. 703 = 26 singles +
    # 26**2 doubles + the first triple.
    ids = lib._excel_ref_ids(703)
    assert ids[-2] == "ZZ"
    assert ids[-1] == "AAA"


def test_target_defaults_have_no_legend_and_range_true():
    # ref_id has no default (every call site in this codebase already assigns one) --
    # pass it explicitly and lock in every OTHER param's default.
    t = lib.target("up", ref_id="A")
    assert t == {
        "expr": "up",
        "refId": "A",
        "instant": False,
        "range": True,
        "hide": False,
        "format": "time_series",
    }
    assert "legendFormat" not in t


def test_target_ref_id_is_required():
    with pytest.raises(TypeError, match="ref_id"):
        lib.target("up")


def test_target_with_legend_instant_and_hide():
    t = lib.target("up", legend="{{job}}", ref_id="Q", instant=True, hide=True, fmt="table")
    assert t == {
        "expr": "up",
        "refId": "Q",
        "instant": True,
        "range": False,
        "hide": True,
        "format": "table",
        "legendFormat": "{{job}}",
    }


def test_make_targets_assigns_sequential_ref_ids_and_applies_defaults():
    specs = [
        {"expr": "a"},
        {"expr": "b", "legend": "{{x}}", "instant": True, "hide": True, "format": "table"},
    ]
    targets = lib.make_targets(specs)
    assert [t["refId"] for t in targets] == ["A", "B"]
    assert targets[0] == {
        "expr": "a",
        "refId": "A",
        "instant": False,
        "range": True,
        "hide": False,
        "format": "time_series",
    }
    assert targets[1] == {
        "expr": "b",
        "refId": "B",
        "instant": True,
        "range": False,
        "hide": True,
        "format": "table",
        "legendFormat": "{{x}}",
    }


def test_make_targets_empty_specs():
    assert lib.make_targets([]) == []


def test_make_targets_strict_zip_raises_on_ref_id_length_mismatch(monkeypatch):
    # _excel_ref_ids(len(specs)) always returns exactly len(specs) ids in normal use,
    # so zip's strict=True is never exercised through the public API alone -- patch it
    # to return a mismatched length so a real length mismatch actually reaches the
    # zip, and confirm strict=True turns it into a raise (strict=False/None/omitted
    # would instead silently truncate to the shorter of the two).
    monkeypatch.setattr(lib, "_excel_ref_ids", lambda _n: ["A"])
    with pytest.raises(ValueError, match="zip"):
        lib.make_targets([{"expr": "a"}, {"expr": "b"}])


def test_thresholds_first_step_value_none_builds_the_step_list():
    result = lib.thresholds([(None, "green"), (75, "yellow"), (85, "red")])
    assert result == {
        "mode": "absolute",
        "steps": [
            {"color": "green", "value": None},
            {"color": "yellow", "value": 75},
            {"color": "red", "value": 85},
        ],
    }


def test_thresholds_rejects_a_non_none_first_step_value():
    # Grafana's base step never carries a value -- thresholds() used to silently
    # discard whatever was passed there; it now raises instead of masking the bug.
    # Full literal message (not just the "got 999" substring) so a wrong index into
    # `steps` for the suggested rewrite (e.g. steps[1] instead of steps[0]) is caught:
    # it would quote the *second* step's color ('yellow') instead of the first's.
    with pytest.raises(ValueError) as exc_info:
        lib.thresholds([(999, "green"), (75, "yellow"), (85, "red")])
    assert str(exc_info.value) == (
        "thresholds(): the first step's value must be None (Grafana's base step never "
        "carries one), got 999 -- write it as (None, 'green')"
    )


def test_thresholds_custom_mode_and_empty_steps():
    assert lib.thresholds([], mode="percentage") == {"mode": "percentage", "steps": []}


def test_no_thresholds_default_and_custom_color():
    assert lib.no_thresholds() == {"mode": "absolute", "steps": [{"color": "gray", "value": None}]}
    assert lib.no_thresholds("blue") == {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}


def test_value_mapping_indexes_entries_in_order_with_optional_color():
    result = lib.value_mapping([(0, "No", None), (1, "Yes", "red")])
    assert result == [
        {
            "type": "value",
            "options": {
                "0": {"text": "No", "index": 0},
                "1": {"text": "Yes", "index": 1, "color": "red"},
            },
        }
    ]


def test_value_mapping_empty_entries():
    assert lib.value_mapping([]) == [{"type": "value", "options": {}}]


def test_range_mapping_with_and_without_color():
    assert lib.range_mapping(0, 10, "Low") == {
        "type": "range",
        "options": {"from": 0, "to": 10, "result": {"text": "Low", "index": 0}},
    }
    assert lib.range_mapping(None, None, "Unknown", "orange", index=3) == {
        "type": "range",
        "options": {"from": None, "to": None, "result": {"text": "Unknown", "index": 3, "color": "orange"}},
    }


def test_override_by_name_builds_byname_matcher():
    result = lib.override_by_name("Value", [("color", lib.fixed_color("blue")), ("unit", "watt")])
    assert result == {
        "matcher": {"id": "byName", "options": "Value"},
        "properties": [
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}},
            {"id": "unit", "value": "watt"},
        ],
    }


@pytest.mark.parametrize(
    ("pattern", "expected_options"),
    [
        ("^Free", "/^Free/"),  # bare pattern -> delimiters added
        ("", "//"),
        ("/^Free$/", "/^Free$/"),  # already delimited -> left untouched
        ("/^free/i", "/^free/i"),  # delimited with a flag -> left untouched, not re-wrapped
        ("/a/gimys", "/a/gimys"),  # every flag, in the only order Grafana accepts
        ("/a/ig", "//a/ig/"),  # flags out of order: not delimited to Grafana, so a bare regex
        ("/a/x", "//a/x/"),  # not a flag: likewise
        # A bare regex that merely starts or ends with '/' is wrapped too: Grafana reads the
        # body back as exactly the original pattern ("/^Free", "Free$/").
        ("/^Free", "//^Free/"),
        ("Free$/", "/Free$//"),
        ("\\/dev/", "/\\/dev//"),  # escaped first slash: the documented way to force wrapping
        ("/a\u2028b/", "//a\u2028b//"),  # JS `.` excludes U+2028, so Grafana wouldn't parse it as delimited
    ],
)
def test_override_by_regex_wraps_bare_patterns_in_slash_delimiters(pattern, expected_options):
    result = lib.override_by_regex(pattern, [("color", lib.fixed_color("blue"))])
    assert result == {
        "matcher": {"id": "byRegexp", "options": expected_options},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}],
    }


@pytest.mark.parametrize(
    ("options", "delimited"),
    [
        ("//", True),
        ("/a/", True),
        ("/a/b/", True),  # inner slashes are part of the body
        ("/a/i", True),
        ("/a/gimys", True),
        ("/a/gi", True),
        ("/a/ig", False),  # Grafana's flag group is g?i?m?y?s? -- fixed order, each at most once
        ("/a/gg", False),
        ("/a/u", False),  # JS supports u, Grafana's pattern doesn't
        ("/", False),
        ("a/", False),
        ("/a", False),
        ("a", False),
        ("", False),
        ("/a\nb/", False),
        ("/a\rb/", False),
        ("/a\u2029b/", False),
        ("/a/\n", False),  # fullmatch: a trailing newline is not "end of string"
    ],
)
def test_grafana_delimited_regex_mirrors_string_to_js_regex(options, delimited):
    assert bool(lib.GRAFANA_DELIMITED_REGEX.fullmatch(options)) is delimited


def test_fixed_color():
    assert lib.fixed_color("red") == {"mode": "fixed", "fixedColor": "red"}


def test_transformation_default_and_explicit_options():
    assert lib.transformation("organize") == {"id": "organize", "options": {}}
    assert lib.transformation("organize", {"a": 1}) == {"id": "organize", "options": {"a": 1}}


def test_transform_join_by_field_default_and_custom_mode():
    assert lib.transform_join_by_field("UUID") == {
        "id": "joinByField",
        "options": {"byField": "UUID", "mode": "outer"},
    }
    assert lib.transform_join_by_field("UUID", mode="inner") == {
        "id": "joinByField",
        "options": {"byField": "UUID", "mode": "inner"},
    }


def test_transform_organize_empty_when_nothing_passed():
    assert lib.transform_organize() == {"id": "organize", "options": {}}


def test_transform_organize_builds_exclude_rename_and_index_maps():
    result = lib.transform_organize(
        exclude=["Time", "junk"],
        rename={"UUID": "GPU UUID"},
        order=["GPU UUID", "Value"],
    )
    assert result == {
        "id": "organize",
        "options": {
            "excludeByName": {"Time": True, "junk": True},
            "renameByName": {"UUID": "GPU UUID"},
            "indexByName": {"GPU UUID": 0, "Value": 1},
        },
    }


def test_table_join_assembles_targets_transformations_and_overrides():
    result = lib.table_join(
        panel_id=90,
        title="Fabric",
        x=0,
        y=0,
        w=24,
        h=8,
        exprs=["e1", "e2", "e3"],
        identity_fields={"UUID": "GPU UUID"},
        noise_fields=["Time"],
        value_renames=["Watts", None, "Temp"],
        overrides=[lib.override_by_name("Watts", [("unit", "watt")])],
        description="join test",
    )
    assert result["id"] == 90
    assert result["title"] == "Fabric"
    assert result["type"] == "table"
    assert result["description"] == "join test"
    assert [t["refId"] for t in result["targets"]] == ["A", "B", "C"]
    assert all(t["instant"] is True and t["format"] == "table" for t in result["targets"])
    join_by_field, organize = result["transformations"]
    assert join_by_field == {"id": "joinByField", "options": {"byField": "UUID", "mode": "outer"}}
    assert organize["options"]["excludeByName"] == {
        "Time": True,
        "Time 1": True,
        "Time 2": True,
        "Time 3": True,
        "UUID 2": True,
        "UUID 3": True,
        "Value #B": True,
    }
    assert organize["options"]["renameByName"] == {
        "UUID": "GPU UUID",
        "UUID 1": "GPU UUID",
        "Value #A": "Watts",
        "Value #C": "Temp",
    }
    assert result["fieldConfig"]["overrides"] == [lib.override_by_name("Watts", [("unit", "watt")])]


def test_table_join_defaults_no_overrides_no_description_and_custom_join_field():
    result = lib.table_join(
        panel_id=91,
        title="MIG",
        x=0,
        y=0,
        w=12,
        h=6,
        exprs=["only"],
        identity_fields={},
        noise_fields=[],
        value_renames=[None],
        join_field="mig_key",
    )
    join_by_field, organize = result["transformations"]
    assert join_by_field == {"id": "joinByField", "options": {"byField": "mig_key", "mode": "outer"}}
    assert organize["options"] == {"excludeByName": {"Value #A": True}}
    assert result["fieldConfig"]["overrides"] == []
    assert "description" not in result  # falsy description -> _base_panel omits the key entirely


def test_table_join_targets_strict_zip_raises_on_ref_id_length_mismatch(monkeypatch):
    # Same reasoning as test_make_targets_strict_zip_raises_on_ref_id_length_mismatch:
    # ref_ids = _excel_ref_ids(len(exprs)) always matches len(exprs) through the
    # public API, so patch it to a mismatched length to actually exercise strict=True.
    # value_renames is kept the same (mismatched) length as the patched ref_ids so the
    # *second* zip (over value_renames, untouched by this mutation) can't raise first
    # and mask whether the targets-building zip's own strict=True actually fired.
    monkeypatch.setattr(lib, "_excel_ref_ids", lambda _n: ["A"])
    with pytest.raises(ValueError, match="zip"):
        lib.table_join(
            panel_id=1,
            title="T",
            x=0,
            y=0,
            w=24,
            h=6,
            exprs=["e1", "e2"],
            identity_fields={},
            noise_fields=[],
            value_renames=["only_one"],
        )


def test_table_join_value_renames_strict_zip_raises_on_length_mismatch():
    # Unlike the exprs/ref_ids zip above, value_renames is caller-supplied and
    # independent of exprs' length -- a real mismatch here is a genuine caller bug
    # this codebase's own call sites could make, so it's tested directly (no need to
    # patch _excel_ref_ids): strict=True must raise rather than silently zip only the
    # shorter of the two and drop/misalign a rename.
    with pytest.raises(ValueError, match="zip"):
        lib.table_join(
            panel_id=2,
            title="T2",
            x=0,
            y=0,
            w=24,
            h=6,
            exprs=["e1", "e2"],
            identity_fields={},
            noise_fields=[],
            value_renames=["only_one"],
        )


def test_pstate_mappings_uncolored_has_sixteen_entries_no_color():
    options = lib.pstate_mappings()[0]["options"]
    assert len(options) == 16
    assert options["0"] == {"text": "P0", "index": 0}
    assert options["15"] == {"text": "P15", "index": 15}
    assert all("color" not in entry for entry in options.values())


def test_pstate_mappings_colored_assigns_a_distinct_hue_per_state():
    # Literal expected colors (not mirrored from lib._PSTATE_COLORS_16) so a
    # transposed/duplicated hue in the source table is caught.
    options = lib.pstate_mappings(colored=True)[0]["options"]
    expected_colors = [
        "dark-green",
        "green",
        "semi-dark-green",
        "light-green",
        "dark-blue",
        "blue",
        "semi-dark-blue",
        "light-blue",
        "dark-purple",
        "purple",
        "semi-dark-purple",
        "light-purple",
        "dark-orange",
        "orange",
        "semi-dark-orange",
        "light-orange",
    ]
    assert [options[str(v)]["color"] for v in range(16)] == expected_colors
    assert len(set(expected_colors)) == 16  # every hue distinct


def test_compute_mode_mappings_covers_all_four_dcgm_values():
    assert lib.compute_mode_mappings() == [
        {
            "type": "value",
            "options": {
                "0": {"text": "Default", "index": 0, "color": "green"},
                "1": {"text": "Exclusive Thread (deprecated)", "index": 1, "color": "yellow"},
                "2": {"text": "Prohibited", "index": 2, "color": "red"},
                "3": {"text": "Exclusive Process", "index": 3, "color": "blue"},
            },
        }
    ]


def test_ecc_mode_mappings():
    assert lib.ecc_mode_mappings() == [
        {
            "type": "value",
            "options": {
                "0": {"text": "Disabled", "index": 0, "color": "gray"},
                "1": {"text": "Enabled", "index": 1, "color": "green"},
            },
        }
    ]


def test_virtual_mode_mappings_has_no_colors():
    options = lib.virtual_mode_mappings()[0]["options"]
    assert list(options.keys()) == ["0", "1", "2", "3", "4"]
    assert options["0"] == {"text": "None (bare-metal)", "index": 0}
    assert options["4"] == {"text": "Host vSGA", "index": 4}
    assert all("color" not in v for v in options.values())


def test_recovery_action_mappings_has_all_eight_nvml_values_with_3_and_4_unswapped():
    # Full literal expectation (text AND color for every index) per nvml.h's
    # nvmlDeviceGpuRecoveryAction_t -- a spot check on 2 colors would miss a
    # mutation on any of the other 6.
    assert lib.recovery_action_mappings() == [
        {
            "type": "value",
            "options": {
                "0": {"text": "None", "index": 0, "color": "green"},
                "1": {"text": "GPU Reset", "index": 1, "color": "yellow"},
                "2": {"text": "Node Reboot", "index": 2, "color": "red"},
                "3": {"text": "Drain P2P", "index": 3, "color": "orange"},
                "4": {"text": "Drain P2P + Reset", "index": 4, "color": "orange"},
                "5": {"text": "Recover IMEX Domain", "index": 5, "color": "orange"},
                "6": {"text": "Bus Reset", "index": 6, "color": "red"},
                "7": {"text": "System Reboot", "index": 7, "color": "red"},
            },
        }
    ]


def test_bool_reliability_mappings():
    assert lib.bool_reliability_mappings() == [
        {
            "type": "value",
            "options": {
                "0": {"text": "No", "index": 0, "color": "green"},
                "1": {"text": "Yes", "index": 1, "color": "red"},
            },
        }
    ]


def test_bool_config_mappings_default_off_on_text():
    assert lib.bool_config_mappings() == [
        {
            "type": "value",
            "options": {
                "0": {"text": "Off", "index": 0, "color": "gray"},
                "1": {"text": "On", "index": 1, "color": "green"},
            },
        }
    ]


def test_bool_config_mappings_custom_text():
    options = lib.bool_config_mappings(off_text="Disabled", on_text="Enabled")[0]["options"]
    assert options["0"]["text"] == "Disabled"
    assert options["1"]["text"] == "Enabled"


def test_health_status_mappings_orders_exact_values_before_range_fallback():
    result = lib.health_status_mappings()
    assert len(result) == 2  # value_mapping()'s one dict, then the range fallback
    options = result[0]["options"]
    assert options["0"] == {"text": "Pass", "index": 0, "color": "green"}
    assert options["10"] == {"text": "Warn", "index": 1, "color": "yellow"}
    assert options["20"] == {"text": "Fail", "index": 2, "color": "red"}
    assert result[1] == lib.range_mapping(None, None, "Unknown", "orange", index=3)


# Literal mirror of dcgm_dashboard.lib.XID_TABLE. Kept as a hardcoded expectation
# (not read off lib.XID_TABLE) so a mutated entry -- wrong code, wrong severity
# color, dropped/duplicated row -- is actually caught rather than trivially
# matching itself.
_EXPECTED_XID_TABLE = [
    (43, "Reset channel (app killed, GPU recovers)", "green"),
    (63, "Row/page retirement event (self-healed)", "green"),
    (94, "Contained ECC error", "green"),
    (13, "Graphics/compute exception (app bug)", "yellow"),
    (31, "GPU memory page fault", "yellow"),
    (61, "PMU breakpoint", "yellow"),
    (68, "NVDEC video-processor exception", "yellow"),
    (69, "Graphics engine class error", "yellow"),
    (92, "Excessive correctable ECC interrupts", "yellow"),
    (32, "PBDMA push-buffer error", "red"),
    (45, "Preemptive context removal (correlate with prior Xid)", "orange"),
    (48, "Double-bit ECC error (uncorrectable)", "red"),
    (62, "PMU halt error", "red"),
    (64, "Row-remap/page-retirement failure", "red"),
    (74, "NVLink error", "red"),
    (79, "GPU has fallen off the bus", "red"),
    (95, "Uncontained ECC error", "red"),
    (109, "Context-switch timeout", "red"),
    (110, "SEC2 fault", "red"),
    (119, "GSP RPC timeout", "red"),
    (120, "GSP firmware error", "red"),
    (121, "C2C error", "red"),
    (140, "Unrecoverable ECC error", "red"),
    (143, "GPU init error", "red"),
]


def test_xid_table_matches_literal_expected_table():
    assert lib.XID_TABLE == _EXPECTED_XID_TABLE


def test_benign_xid_codes_matches_green_entries_of_xid_table_sorted():
    assert sorted(xid for xid, _, color in lib.XID_TABLE if color == "green") == lib.BENIGN_XID_CODES
    assert lib.BENIGN_XID_CODES == [43, 63, 94]


def test_xid_mappings_structure_and_fallbacks():
    result = lib.xid_mappings()
    assert len(result) == 3
    value_block, nvlink_family, fallback = result
    assert value_block["type"] == "value"
    # Full literal options dict built independently of value_mapping()/XID_TABLE,
    # so a bug in either the table or xid_mappings' own construction is caught.
    expected_options = {
        str(xid): {"text": name, "index": i, "color": color} for i, (xid, name, color) in enumerate(_EXPECTED_XID_TABLE)
    }
    assert value_block["options"] == expected_options
    assert nvlink_family == {
        "type": "range",
        "options": {
            "from": 144,
            "to": 150,
            "result": {"text": "NVLink sub-link-layer error", "index": 24, "color": "red"},
        },
    }
    assert fallback == {
        "type": "range",
        "options": {
            "from": None,
            "to": None,
            "result": {
                "text": "Unknown Xid — see docs.nvidia.com/deploy/xid-errors/",
                "index": 25,
                "color": "orange",
            },
        },
    }


def test_real_throttle_bit_sets_match_documented_values():
    assert lib.REAL_THROTTLE_BITS == [4, 8, 32, 64, 128]
    assert lib.REAL_FAULT_THROTTLE_BITS == [8, 32, 64, 128]
    assert lib.CLOCK_EVENT_COLOR == {"benign": "gray", "throttle": "red", "clock_limit_info": "blue"}


def test_clock_event_bits_full_table_bits_and_natures():
    # Full literal table (not a partial partition count) so a mutation that swaps
    # one bit's nature for another of the same class-size is still caught.
    assert lib.CLOCK_EVENT_BITS == {
        "GPU_IDLE": (0x001, "benign"),
        "APP_CLOCKS": (0x002, "benign"),
        "SW_POWER_CAP": (0x004, "throttle"),
        "HW_SLOWDOWN": (0x008, "throttle"),
        "SYNC_BOOST": (0x010, "benign"),
        "SW_THERMAL": (0x020, "throttle"),
        "HW_THERMAL": (0x040, "throttle"),
        "HW_POWER_BRAKE": (0x080, "throttle"),
        "DISPLAY_CLOCKS": (0x100, "benign"),
        "BOARD_LIMIT": (0x200, "clock_limit_info"),
        "RELIABILITY": (0x400, "clock_limit_info"),
    }


def test_clock_event_bit_expr_uses_floor_division_decode():
    assert lib.clock_event_bit_expr("x", 8) == "floor(x/8) % 2"


def test_any_bits_set_expr_joins_terms_with_plus():
    assert lib.any_bits_set_expr("x", [4, 8]) == "( floor(x/4) % 2 + floor(x/8) % 2 ) > 0"


def test_any_bits_set_expr_empty_bits():
    assert lib.any_bits_set_expr("x", []) == "(  ) > 0"


def test_any_real_throttle_expr_uses_all_five_real_throttle_bits():
    expected_terms = " + ".join(lib.clock_event_bit_expr("x", bit) for bit in lib.REAL_THROTTLE_BITS)
    assert lib.any_real_throttle_expr("x") == f"( {expected_terms} ) > 0"


def test_any_real_fault_throttle_expr_excludes_sw_power_cap():
    expected_terms = " + ".join(lib.clock_event_bit_expr("x", bit) for bit in lib.REAL_FAULT_THROTTLE_BITS)
    result = lib.any_real_fault_throttle_expr("x")
    assert result == f"( {expected_terms} ) > 0"
    assert "floor(x/4) % 2" not in result  # SW_POWER_CAP (0x4) deliberately excluded


def test_cuda_compute_capability_major_minor_formula_and_worked_example():
    major, minor = lib.cuda_compute_capability_major_minor("x")
    assert major == "floor(x/65536)"
    assert minor == "(x - floor(x/65536)*65536)"

    major_val, minor_val = lib.cuda_compute_capability_major_minor("786432")
    env = {"floor": math.floor}
    assert eval(major_val, env) == 12  # docstring: 786432 -> major=12, minor=0
    assert eval(minor_val, env) == 0


def test_cuda_driver_version_major_minor_formula_and_worked_example():
    major, minor = lib.cuda_driver_version_major_minor("x")
    assert major == "floor(x/1000)"
    assert minor == "floor((x - floor(x/1000)*1000)/10)"

    major_val, minor_val = lib.cuda_driver_version_major_minor("13040")
    env = {"floor": math.floor}
    assert eval(major_val, env) == 13  # docstring: 13040 -> 13.4
    assert eval(minor_val, env) == 4
