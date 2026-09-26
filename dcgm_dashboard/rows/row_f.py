# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row F -- Clocks & Throttling.

Row id 39, y:60. Children 40-46, plus two completeness additions: 98
("Fraction of Range Throttled (by reason)") and 99 ("Time at High Performance
(P0-P2)"), a sub-row 4 (relative y=25, h=8).

The clock-event bitmask classification (see CONVENTIONS.md's clock-event-
bitmask gotcha) governs panel 42: bits 0x200 BOARD_LIMIT and 0x400 RELIABILITY
are informational clock limits, not throttling -- this module reuses
`lib.CLOCK_EVENT_BITS`/`lib.CLOCK_EVENT_COLOR` directly so panel 42 stays in
lockstep with row A's "GPUs throttled now" tile (id 9).

Every legend in this row's multi-metric overlay panels (40, 42, 44, 46)
appends `ctx.legend_std` (`{{hostname}} GPU{{gpu}}`) rather than a bare
per-metric/per-bit token (e.g. "SM Clock", "GPU_IDLE") with no host/GPU
identity: with the $gpu variable's default (includeAll, every GPU), a bare
legend collapses every selected GPU's series down to one ambiguous legend
string per metric/bit (confirmed live: 3 GPUs each rendering an identical
"SM Clock" label, or identical bitmask lanes). Panel 42's per-bit
`override_by_regex` (anchored on the bit's label prefix, not `override_by_name`)
follows from this too: once a per-GPU suffix is appended, the legend is no
longer a single fixed string, so each bit needs its own color/mapping matched
by regex rather than by exact name. The same reasoning applies to panel 46's
three fixed-color informational overrides (`FAULT_INFO_ONLY`).

SW_POWER_CAP throttling is the expected behavior of a GPU running at its
configured power limit under full load, not a fault -- unlike the other 4
real-throttle bits (HW_SLOWDOWN, SW_THERMAL, HW_THERMAL, HW_POWER_BRAKE),
which do indicate a real problem (thermal or external power-brake distress).
It still counts toward row A's "GPUs throttled now" tile and
REAL_THROTTLE_BITS (it is still real throttling, just not alarm-worthy), but
panel 42's bit lane and panel 98's bargauge color it yellow/orange instead of
red: `BIT_COLOR_OVERRIDE` below beats `lib.CLOCK_EVENT_COLOR[nature]` for that
one bit in panel 42, and panel 98 gets a dedicated, less alarming threshold
via a field override.

Every per-GPU query in this row (40-45, 46, 99) goes through `lib.per_gpu()`
(see CONVENTIONS.md's series-identity section) so a dcgm-exporter CSV
label-set change (new driver/VBIOS) can't draw two lines/lanes with the same
legend for a while. Panel 98's bargauge and panel 46's *_VIOLATION-based
rate() stay as-is: panel 98 already `sum()`s across the fleet with no
per-GPU legend (a plain fleet total is unaffected by the split, and `sum` is
already the right operator for the range-integrating `increase()` inside it),
and panel 46's rate() targets each get the same lib.per_gpu(agg="max")
treatment as the identical pattern in panels 44/45.
"""
import re
from typing import Any, Dict, List, Tuple

from .. import lib

# Friendly display text for each bit's *asserted* (value=1) state. The two
# "clock limit (informational)" bits are labeled as such right in the text
# (see CONVENTIONS.md's clock-event-bitmask gotcha).
BIT_LABELS: Dict[str, str] = {
    "GPU_IDLE": "GPU Idle",
    "APP_CLOCKS": "App Clocks Setting",
    "SW_POWER_CAP": "SW Power Cap (throttling, expected at full load)",
    "HW_SLOWDOWN": "HW Slowdown (throttling)",
    "SYNC_BOOST": "Multi-GPU Sync Boost",
    "SW_THERMAL": "SW Thermal Slowdown (throttling)",
    "HW_THERMAL": "HW Thermal Slowdown (throttling)",
    "HW_POWER_BRAKE": "HW Power Brake (throttling)",
    "DISPLAY_CLOCKS": "Display Clocks Setting",
    "BOARD_LIMIT": "Board Limit (clock limit, informational)",
    "RELIABILITY": "Reliability (clock limit, informational)",
}

# SW_POWER_CAP is real throttling (it stays in REAL_THROTTLE_BITS) but expected
# whenever a GPU is running at its power limit under full load, not a fault --
# colored yellow here instead of lib.CLOCK_EVENT_COLOR["throttle"]'s red. Every
# other "throttle"-nature bit (HW_SLOWDOWN, SW_THERMAL, HW_THERMAL, HW_POWER_BRAKE)
# keeps the shared red.
BIT_COLOR_OVERRIDE: Dict[str, str] = {"SW_POWER_CAP": "yellow"}

# Short legend tokens for the per-reason NS-counter panel (44) -- these 5 fields
# are the direct twins of the 5 REAL_THROTTLE_BITS, in the same bit order.
THROTTLE_NS_FIELDS = [
    ("DCGM_FI_DEV_CLOCKS_EVENT_REASON_SW_POWER_CAP_NS", "SW Power Cap"),
    ("DCGM_FI_DEV_CLOCKS_EVENT_REASON_SYNC_BOOST_NS", "Sync Boost"),  # pre-rename spelling, see custom-counters.csv's header note
    ("DCGM_FI_DEV_CLOCKS_EVENT_REASON_SW_THERM_SLOWDOWN_NS", "SW Thermal"),
    ("DCGM_FI_DEV_CLOCKS_EVENT_REASON_HW_THERM_SLOWDOWN_NS", "HW Thermal"),
    ("DCGM_FI_DEV_CLOCKS_EVENT_REASON_HW_POWER_BRAKE_SLOWDOWN_NS", "HW Power Brake"),
]
# There is no dedicated *_NS counter for HW_SLOWDOWN (0x008) in this CSV -- Sync Boost
# (a 'benign' bit, not a real-throttle one) fills the 5th slot instead as the only other
# *_NS field available. Maps each of the above legend labels back to its CLOCK_EVENT_BITS
# key so panel 44 can reuse the exact same nature-based color as panel 42's bitmask lane
# for the same reason, instead of an arbitrary classic-palette color by field order.
THROTTLE_NS_BIT_NAME: Dict[str, str] = {
    "SW Power Cap": "SW_POWER_CAP",
    "Sync Boost": "SYNC_BOOST",
    "SW Thermal": "SW_THERMAL",
    "HW Thermal": "HW_THERMAL",
    "HW Power Brake": "HW_POWER_BRAKE",
}

# Short legend tokens for the fault-violation panel (46). Only the first two
# (POWER_VIOLATION, THERMAL_VIOLATION) are real-fault alarm series: BOARD_LIMIT/
# RELIABILITY are excluded from any throttle alarm (informational clock limits,
# not throttling), and SYNC_BOOST is independently classified 'benign/config'
# in lib.CLOCK_EVENT_BITS.
FAULT_VIOLATION_FIELDS = [
    ("DCGM_FI_DEV_POWER_VIOLATION", "Power"),
    ("DCGM_FI_DEV_THERMAL_VIOLATION", "Thermal"),
    ("DCGM_FI_DEV_SYNC_BOOST_VIOLATION", "Sync Boost (benign)"),
    ("DCGM_FI_DEV_BOARD_LIMIT_VIOLATION", "Board Limit (clock limit, informational)"),
    ("DCGM_FI_DEV_RELIABILITY_VIOLATION", "Reliability (clock limit, informational)"),
]
# Legend labels above that must NEVER trip the panel's red threshold.
FAULT_INFO_ONLY = {
    "Sync Boost (benign)": "gray",
    "Board Limit (clock limit, informational)": "blue",
    "Reliability (clock limit, informational)": "blue",
}


def build(ctx: lib.RowContext) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(39, "Clocks & Throttling", collapsed=False)
    panels: List[Dict[str, Any]] = []

    # -- Sub-row 1 (relative y=1, h=8) -------------------------------------
    panels.append(
        lib.timeseries(
            40, "SM / Memory / Video clock", 0, 1, 12, 8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_SM_CLOCK{f}"), legend=f"SM Clock · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_MEM_CLOCK{f}"), legend=f"Mem Clock · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_VIDEO_CLOCK{f}"), legend=f"Video Clock · {std}", ref_id="C"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_MAX_SM_CLOCK{f}"), legend=f"Max SM Clock · {std}", ref_id="D"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_MAX_MEM_CLOCK{f}"), legend=f"Max Mem Clock · {std}", ref_id="E"),
            ],
            unit_id="rotmhz",
            overrides=[
                lib.override_by_regex(r"^Max ", [
                    ("custom.lineStyle", {"fill": "dash", "dash": [10, 10]}),
                    ("custom.fillOpacity", 0),
                ]),
            ],
            description="Direct nvidia-smi/14574 parity: SM, memory and video clock, each with its "
                        "hardware max as a dashed reference. A solid line sitting well below its dashed "
                        "max most of the time is expected on an idle-ish GPU -- see panel 41 for *why* "
                        "clocks are where they are.",
        )
    )
    panels.append(
        lib.state_timeline(
            41, "P-State history", 12, 1, 12, 8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_PSTATE{f}"), legend=std, ref_id="A")],
            mappings=lib.pstate_mappings(colored=True),
            description="Performance state over time, P0 (max performance) to P15 (min performance) -- "
                        "16 distinct colors, one per state, so a state *change* is visible. A GPU that idles "
                        "mostly in a high P-number (e.g. P8) and drops to P0 only under load is healthy; see "
                        "panel 99 for a range-summary 'fraction of time boosted' number.",
        )
    )

    # -- Sub-row 2 (relative y=9, h=8) --------------------------------------
    bit_targets = []
    bit_overrides = []
    for i, (bit_name, (bit_value, nature)) in enumerate(lib.CLOCK_EVENT_BITS.items()):
        ref_id = lib._excel_ref_ids(11)[i]
        expr = lib.clock_event_bit_expr(f"DCGM_FI_DEV_CLOCKS_EVENT_REASONS{f}", bit_value)
        bit_targets.append(lib.target(lib.per_gpu(expr), legend=f"{bit_name} · {std}", ref_id=ref_id))
        bit_color = BIT_COLOR_OVERRIDE.get(bit_name, lib.CLOCK_EVENT_COLOR[nature])
        bit_overrides.append(
            lib.override_by_regex(r"^" + re.escape(bit_name) + r" ", [
                ("mappings", lib.value_mapping([
                    (0, "—", "dark-gray"),
                    (1, BIT_LABELS[bit_name], bit_color),
                ])),
            ])
        )
    panels.append(
        lib.state_timeline(
            42, "Clock-event reasons (bitmask decode)", 0, 9, 16, 8,
            bit_targets,
            overrides=bit_overrides,
            description="All 11 bits of DCGM_FI_DEV_CLOCKS_EVENT_REASONS, decoded one boolean lane per bit "
                        "per GPU via floor(x/BIT) mod 2 (Prometheus has no bitwise-AND). Gray lanes are "
                        "benign/informational. Blue lanes (Board Limit, Reliability) are "
                        "'clock limit, informational' bits -- deliberately NOT red and NOT counted toward row "
                        "A's 'GPUs throttled now' tile. Yellow (SW Power Cap) is real throttling "
                        "(still counted toward the 'throttled now' tile) but expected whenever a GPU runs at "
                        "its configured power limit under full load, not a fault -- deliberately not red. "
                        "Red lanes are the 4 remaining real-throttle reasons that do indicate a problem "
                        "(HW Slowdown, SW/HW Thermal, HW Power Brake). Legend includes host+GPU so multiple "
                        "selected GPUs' lanes for the same bit stay distinct instead of visually merging "
                        "(state-timeline's mergeValues would otherwise blend different GPUs' histories into "
                        "one lane).",
        )
    )
    panels.append(
        lib.stat(
            43, "Auto-boost enabled", 16, 9, 8, 8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_CLOCKS_AUTOBOOST_MODE{f}"), legend=std, ref_id="A", instant=True)],
            unit_id="none",
            mappings=lib.bool_config_mappings(off_text="Disabled", on_text="Enabled"),
            thresholds_steps=lib.no_thresholds(),
            no_value="Not supported on this GPU",
            description="GPU Boost auto-boost setting -- configuration state, not a fault. Not populated on "
                        "GPUs without auto-boost support (confirmed empty on this environment's RTX PRO 4000 "
                        "Blackwell).",
        )
    )

    # -- Sub-row 3 (relative y=17, h=8) --------------------------------------
    throttle_targets = [
        lib.target(
            lib.per_gpu(f"rate({field}{f}[$__rate_interval])/1e9"), legend=f"{legend} · {std}", ref_id=rid,
        )
        for rid, (field, legend) in zip(lib._excel_ref_ids(len(THROTTLE_NS_FIELDS)), THROTTLE_NS_FIELDS)
    ]
    throttle_ns_overrides = []
    for _field, legend in THROTTLE_NS_FIELDS:
        bit_name = THROTTLE_NS_BIT_NAME[legend]
        _, nature = lib.CLOCK_EVENT_BITS[bit_name]
        color = BIT_COLOR_OVERRIDE.get(bit_name, lib.CLOCK_EVENT_COLOR[nature])
        throttle_ns_overrides.append(
            lib.override_by_regex(r"^" + re.escape(legend) + r" ", [("color", lib.fixed_color(color))])
        )
    panels.append(
        lib.timeseries(
            44, "Throttle time by reason (rate)", 0, 17, 12, 8,
            throttle_targets,
            unit_id="percentunit", min_=0, max_=1,
            stacked=False,
            thresholds_steps=lib.no_thresholds("gray"),
            overrides=throttle_ns_overrides,
            legend_mode="table", legend_placement="right", legend_calcs=["lastNotNull"],
            legend_sort_by="Last *", legend_sort_desc=True,
            description="Fraction of the selected window each reason's *_NS counter was active -- "
                        "rate(...)/1e9 converts a cumulative ns counter into a 0-1 'fraction of time' value. "
                        "Not stacked: these reasons can be simultaneously nonzero, and a stacked chart draws "
                        "a zero-valued series' boundary line at the cumulative height of the series below it "
                        "rather than at its own (zero) value, which can visually paint an entirely different "
                        "reason's color across the top of the stack -- unstacked lines show each reason's "
                        "own true value with no such misattribution. Each line's color matches panel 42's "
                        "bitmask lane for the same reason (yellow SW Power Cap, gray Sync Boost/benign, red "
                        "SW/HW Thermal and HW Power Brake) rather than an arbitrary palette-by-order color. "
                        "See panel 98 for the same signal integrated over the whole range instead of shown "
                        "as an instantaneous rate.",
        )
    )
    panels.append(
        lib.timeseries(
            45, "Idle time (LOW_UTIL_VIOLATION)", 12, 17, 6, 8,
            [lib.target(lib.per_gpu(f"rate(DCGM_FI_DEV_LOW_UTIL_VIOLATION{f}[$__rate_interval])/1e9"), legend=std, ref_id="A")],
            unit_id="percentunit", min_=0, max_=1,
            thresholds_steps=lib.no_thresholds("blue"),
            description="Fraction of the window spent in a low-utilization power-saving clock state -- a "
                        "large or near-1.0 value here on an idle GPU is the expected, healthy steady state, "
                        "not a fault.",
        )
    )
    fault_targets = [
        lib.target(
            lib.per_gpu(f"rate({field}{f}[$__rate_interval])/1e9"), legend=f"{legend} · {std}", ref_id=rid,
        )
        for rid, (field, legend) in zip(lib._excel_ref_ids(len(FAULT_VIOLATION_FIELDS)), FAULT_VIOLATION_FIELDS)
    ]
    panels.append(
        lib.timeseries(
            46, "Fault violations (rate)", 18, 17, 6, 8,
            fault_targets,
            unit_id="percentunit", min_=0, max_=1,
            thresholds_steps=lib.thresholds([(0, "green"), (1e-6, "red")]),
            overrides=[
                lib.override_by_regex(r"^" + re.escape(label) + r" ", [("color", lib.fixed_color(color))])
                for label, color in FAULT_INFO_ONLY.items()
            ],
            legend_mode="table", legend_placement="right", legend_calcs=["lastNotNull"],
            legend_sort_by="Last *", legend_sort_desc=True,
            description="Power and Thermal are real-fault alarm series (green@0/red@>0) via the older "
                        "cumulative *_VIOLATION field family (NANOSECONDS, not microseconds). Sync Boost/"
                        "Board Limit/Reliability are shown alongside for context only, fixed-color (gray/"
                        "blue, never red) and excluded from the alarm threshold -- "
                        "trust panel 42's live bitmask over this older counter family for those two reasons.",
        )
    )

    # -- Sub-row 4 (relative y=25, h=8) --
    duty_targets = [
        lib.target(
            f"sum(increase({field}{f}[$__range]))/1e9/$__range_s", legend=legend, ref_id=rid, instant=True,
        )
        for rid, (field, legend) in zip(lib._excel_ref_ids(len(THROTTLE_NS_FIELDS)), THROTTLE_NS_FIELDS)
    ]
    panels.append(
        lib.bargauge(
            98, "Fraction of Range Throttled (by reason)", 0, 25, 18, 8,
            duty_targets,
            unit_id="percentunit", min_=0, max_=1,
            thresholds_steps=lib.thresholds([(0, "green"), (0.05, "red")]),
            color={"mode": "thresholds"},
            display_mode="gradient", orientation="horizontal",
            overrides=[
                lib.override_by_name("Sync Boost", [("color", lib.fixed_color("gray"))]),
                lib.override_by_name("SW Power Cap", [
                    ("thresholds", lib.thresholds([(0, "green"), (0.05, "yellow"), (0.5, "orange")])),
                ]),
            ],
            description="Completeness gap: panels 44/46 only show an instantaneous throttle *rate* -- there "
                        "was no 'what fraction of my selected time range was this GPU actually throttled' "
                        "summary for a capacity-review/postmortem workflow ('was this run power-capped for "
                        "40% of it, or one 30s spike'). Same 5 *_NS counters as panel 44, integrated over "
                        "$__range instead of an instantaneous rate. Sync Boost stays fixed gray "
                        "(informational, per panel 44's own documented rationale), never red. SW Power Cap "
                        "gets its own, less alarming threshold -- yellow above 5% of the "
                        "range, orange above 50% -- since running at the power limit under full load is "
                        "expected GPU behavior, not a fault; the other 3 reasons here (SW Thermal, HW "
                        "Thermal, HW Power Brake) keep the shared green@0/red@>5% threshold, since any "
                        "nonzero time in one of those does indicate a real problem.",
        )
    )
    panels.append(
        lib.stat(
            99, "Time at High Performance (P0-P2)", 18, 25, 6, 8,
            [lib.target(
                lib.per_gpu(f"avg_over_time((DCGM_FI_DEV_PSTATE{f} <= bool 2)[$__range:])", agg="avg"),
                ref_id="A", instant=True,
            )],
            unit_id="percentunit",
            thresholds_steps=lib.thresholds([(0, "blue"), (0.3, "green")]),
            text_mode="value",
            graph_mode="area",
            description="Completeness gap: fraction of the selected range this GPU spent in a "
                        "high-performance P-state (P0-P2) rather than idling in a lower one -- more boost "
                        "time is good (busy GPU), not a fault, hence the blue(idle)->green(busy) activity "
                        "palette rather than red-is-bad. Complements panel 41's P-State timeline with a "
                        "single range-summary number. Wrapped `avg by (identity) (avg_over_time(...))`: if a "
                        "driver/VBIOS upgrade split this GPU's series mid-range, each half's own "
                        "avg_over_time is only a partial-range average, and avg-of-averages weights both "
                        "halves equally regardless of how much of the range each actually covered -- "
                        "approximate in that specific window, exact otherwise (see lib.per_gpu()'s docstring).",
        )
    )

    return row_def, panels
