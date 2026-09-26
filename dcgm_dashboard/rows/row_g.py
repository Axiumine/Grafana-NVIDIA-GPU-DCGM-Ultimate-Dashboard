# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row G -- Power, Energy & Cost.

Row id 47, y:85. 8 children, ids 48-55.

Legend convention: every per-metric legend in this row's multi-metric overlay
panels (48, 49, 51) appends the compact `{std}` (`{{hostname}} GPU{{gpu}}`)
token rather than a bare literal ("Power Usage", "Min Limit", ...) -- a bare
literal legend renders IDENTICAL text for every GPU once more than one is
selected (3 GPUs -> 3 indistinguishable "Power Usage" lines). `ctx.legend_std`
is used directly where a single metric is compared across GPUs, and no legend
at all for panels that `sum()`/aggregate the whole fleet into one series
(52-55 all reduce with sum()).

Panel 48 ("Power draw vs. limits"): the 5 limit series (Min/Default/Current/
Max/Enforced) are dashed and fillOpacity 0, not area-filled, so Power Usage
stays visible underneath instead of being hidden behind an opaque block. The
dashed/fillOpacity-0 override is a byRegexp *prefix* match
(`^(Min|Default|Current|Max|Enforced) Limit`), not a suffix match on " Limit$",
since the legend carries a per-GPU suffix and so no longer literally ends in
" Limit". The limit series also get an explicit thin `lineWidth: 1`, and
Min/Max Limit are hidden from the graph by default (`custom.hideFrom`, still
togglable from the legend) since they clutter more than they inform on a
single-workstation-GPU environment. Only Power Usage keeps its area fill.

Panels 52-55 ("Energy/Cost/Idle-power" stats) are a 2x2 grid of h=4 stats
beside panel 51 (which stays h=8), not 4 separate h=8 panels: at h=8 with
`text_mode="value_and_name"`, the name renders large with the value pushed
below it and a big block of empty space above both, worst on two panels with
no neighboring taller panel in their own sub-row where more than half the
panel was blank. The 2x2 grid at h=4 eliminates that wasted vertical space.
Panels with a real Grafana unit (52: kwatth, 55: watt) use
`text_mode="value"` (the unit renders attached to the number, and the title
already says what it is). The 2 currency panels (53, 54) have no static
numeric unit -- $currency is a runtime textbox, and only a panel's
`displayName` is empirically verified (see CONVENTIONS.md's currency-in-units
gotcha) to interpolate it, so they keep `value_and_name` but with the name
shortened from a full sentence ("${currency} Cost") to just the bare symbol
("${currency}"), which at h=4 no longer dominates the panel the way a longer
phrase did.
"""
from typing import Any, Dict, List, Tuple

from .. import lib


def build(ctx: lib.RowContext) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(47, "Power, Energy & Cost", collapsed=False)
    panels: List[Dict[str, Any]] = []

    # -- Sub-row 1 (relative y=1, h=8) ---------------------------------------
    hide_from_viz = {"tooltip": False, "viz": True, "legend": False}
    panels.append(
        lib.timeseries(
            48, "Power draw vs. limits", 0, 1, 12, 8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE{f}"), legend=f"Power Usage · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_BOARD_POWER_LIMIT_MIN_WATTS{f}"), legend=f"Min Limit · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_BOARD_POWER_LIMIT_DEFAULT_WATTS{f}"), legend=f"Default Limit · {std}", ref_id="C"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_MGMT_LIMIT{f}"), legend=f"Current Limit · {std}", ref_id="D"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_MGMT_LIMIT_MAX{f}"), legend=f"Max Limit · {std}", ref_id="E"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_ENFORCED_POWER_LIMIT{f}"), legend=f"Enforced Limit · {std}", ref_id="F"),
            ],
            unit_id="watt",
            overrides=[
                lib.override_by_regex(r"^(Min|Default|Current|Max|Enforced) Limit", [
                    ("custom.lineStyle", {"fill": "dash", "dash": [10, 10]}),
                    ("custom.fillOpacity", 0),
                    ("custom.lineWidth", 1),
                ]),
                lib.override_by_regex(r"^Min Limit", [("custom.hideFrom", hide_from_viz)]),
                lib.override_by_regex(r"^Max Limit", [("custom.hideFrom", hide_from_viz)]),
            ],
            description="Full min/default/current/max/enforced power-limit stack (thin dashed lines, "
                        "fillOpacity 0 -- only Power Usage itself is area-filled) against actual draw "
                        "(solid). Min/Max Limit start hidden (togglable from the legend) since they clutter "
                        "more than they inform on a typical single-model fleet. 'Enforced' is the limit that "
                        "actually applies after every other limiter (board, driver, external); 'Current' "
                        "(POWER_MGMT_LIMIT) is the active management limit before those other limiters are "
                        "considered. Power draw riding at or above the Enforced line is expected -- DCGM/NVML "
                        "samples very close to the limit under sustained load, that is the power cap doing "
                        "its job, not a bug.",
        )
    )
    panels.append(
        lib.timeseries(
            49, "Power (1s avg) vs. instantaneous", 12, 1, 6, 8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE{f}"), legend=f"1s Avg · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE_INSTANT{f}"), legend=f"Instantaneous · {std}", ref_id="B"),
            ],
            unit_id="watt",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Sanity-check overlay of NVML's 1-second-averaged power reading against its "
                        "instantaneous sample. The two should track closely; a persistent, large gap "
                        "between them points at a very spiky power-draw pattern (short bursts averaged "
                        "out) rather than a sensor fault. Informational -- neither line is inherently good "
                        "or bad on its own.",
        )
    )
    panels.append(
        lib.histogram(
            50, "Power distribution", 18, 1, 6, 8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE{f}"), legend=std, ref_id="A")],
            unit_id="watt",
            description="Native auto-bucketed histogram of power-draw samples over the dashboard's time "
                        "range -- shows the *shape* of power usage that a single averaged number cannot. "
                        "Deliberately informational: an absolute-Watts threshold cannot be correct across GPU "
                        "models with very different power envelopes -- see panel 16 (row B) for the "
                        "model-normalized ratio-based version with real thresholds, and panel 48 above for "
                        "this same raw signal against this GPU's own limit stack.",
        )
    )

    # -- Sub-row 2 (relative y=9, h=8) ---------------------------------------
    panels.append(
        lib.timeseries(
            51, "Energy counter vs. instantaneous power", 0, 9, 12, 8,
            [
                lib.target(lib.per_gpu(f"rate(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{f}[$__rate_interval])/1e3"), legend=f"Energy Rate (derived) · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE{f}"), legend=f"Power Usage (measured) · {std}", ref_id="B"),
            ],
            unit_id="watt",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Cross-checks two independent ways DCGM reports the same quantity: TOTAL_ENERGY_"
                        "CONSUMPTION is a cumulative millijoule counter, so rate(...) yields mJ/s; dividing "
                        "by 1e3 (NOT 1e6) converts that to Watts. The two lines should track closely; a "
                        "persistent gap suggests one of the two sensors/counters is stale.",
        )
    )
    panels.append(
        lib.stat(
            52, "Energy this range", 12, 9, 6, 4,
            [lib.target(f"sum(increase(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{f}[$__range]))/3.6e9", ref_id="A", instant=True)],
            unit_id="kwatth",
            text_mode="value",
            graph_mode="area",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Total energy consumed by the selected GPU(s) over the dashboard's current time "
                        "range. /3.6e9 converts millijoules to kWh. Informational -- there is no universal "
                        "'good'/'bad' total, only trend-over-time and cost (see the two cost panels).",
        )
    )
    panels.append(
        lib.stat(
            53, "Estimated cost this range", 18, 9, 6, 4,
            [lib.target(
                f"(sum(increase(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{f}[$__range]))/3.6e9) * $energy_price",
                ref_id="A", instant=True,
            )],
            unit_id="none",
            display_name="${currency}",
            text_mode="value_and_name",
            graph_mode="area",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Panel 52's energy (kWh) x the $energy_price textbox (price per kWh). The currency "
                        "symbol comes from the $currency textbox via a panel displayName override -- "
                        "confirmed interpolating correctly in this environment's Grafana 13.2.2. "
                        "Informational: a 'high' cost is only meaningful relative to the user's own budget.",
            # Panels 48-51's raw/derived per-GPU metrics go through lib.per_gpu() (see
            # CONVENTIONS.md's series-identity section) so a driver/VBIOS upgrade's CSV
            # label-set change can't draw two lines with the same legend for a while.
            # Panels 52-55 are left as plain sum()/no-by-clause fleet totals with no
            # per-GPU legend -- already fully aggregated, so the split cannot duplicate
            # them (see lib.per_gpu()'s docstring, "already aggregates, don't
            # double-wrap"); 52/53's sum(increase(...[$__range])) in particular is
            # exactly the "sum, not max" range-integrating-counter case, just already
            # summed across every GPU rather than per-GPU.
        )
    )
    panels.append(
        lib.stat(
            54, "Running cost per hour (current draw)", 12, 13, 6, 4,
            [lib.target(
                f"(sum(DCGM_FI_DEV_POWER_USAGE{f})/1000) * $energy_price",
                ref_id="A", instant=True,
            )],
            unit_id="none",
            display_name="${currency}/h",
            text_mode="value_and_name",
            graph_mode="area",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Instantaneous cost-per-hour AT the current power draw (kW x $/kWh), not an "
                        "energy-integral like panel 53 -- if draw changes, this number changes immediately "
                        "with it, it does not accumulate.",
        )
    )
    panels.append(
        lib.stat(
            55, "Idle-power waste", 18, 13, 6, 4,
            [lib.target(
                f"sum(DCGM_FI_DEV_POWER_USAGE{f} and on(UUID) DCGM_FI_DEV_GPU_UTIL{f} == 0) or vector(0)",
                ref_id="A", instant=True,
            )],
            unit_id="watt",
            text_mode="value",
            graph_mode="area",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Total power currently being drawn by GPUs sitting at exactly 0% utilization. On a "
                        "single idle workstation GPU a nonzero reading here is the normal idle baseline, not "
                        "a fault -- the panel earns its keep at fleet scale, where it quantifies how much "
                        "power is going to GPUs that could be power-capped or consolidated.",
        )
    )

    return row_def, panels
