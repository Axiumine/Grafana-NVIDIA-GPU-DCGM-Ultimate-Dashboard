# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row A -- Top strip (fleet at-a-glance). No row wrapper; always visible.

Panel ids 1-10, two sub-rows of 5 (y=0 and y=4, h=4 each). A single row of 10
narrow (w=2/3) tiles leaves most titles ellipsis-truncated at a glance, which
defeats the point of a KPI strip; 2x5 at w=4/5 gives every title 300-390px of
room, comfortably fitting even the longest ("Tensor Active") -- confirmed live
pixel-for-pixel via scrollWidth/clientWidth on every tile.

Panel 9 ("GPUs throttled now") uses lib.any_real_throttle_expr, which sums only
the 5 real-throttle bits (0x4/0x8/0x20/0x40/0x80) and excludes the two
informational clock-limit bits 0x200 BOARD_LIMIT / 0x400 RELIABILITY (see
CONVENTIONS.md's clock-event-bitmask gotcha).

Panel 8 ("Health") is a `max(...)` worst-status value colored via
lib.health_status_mappings() (Pass/Warn/Fail), not a `count(... > 0)` + red@1
threshold: a count-based formula would paint this tile solid red for a purely
informational MONITOR-severity health watch (e.g. the SW power cap engaging
under sustained load) exactly like a real fault, and would count (GPU, watch)
pairs rather than distinct GPUs. The "why" detail (which watch/GPU/error code)
lives in the Reliability row's "Health incidents (why)" table (id 96), which
also defines "0 GPUs unhealthy" via its own noValue text. Panel 5 ("Avg Temp")
is neutral/informational rather than thresholded for the same portability
reason as panels 10/16/17: a fixed Celsius threshold is wrong for datacenter
GPU models that run hotter by design.

Panel 10's temperature leg is a plain Celsius margin,
`(SLOWDOWN_TEMP - GPU_TEMP) < 10` -- 10C of headroom, portable across GPU
models regardless of each one's own slowdown point. An earlier version computed
(GPU_TEMP+273.15)/(SLOWDOWN_TEMP+273.15) > 0.9, which is thermodynamically
"correct" for a *ratio* but made the tile trip at a misleadingly low absolute
temperature (0.9 in Kelvin is ~55C when SLOWDOWN_TEMP=91C, since 328.15K vs.
364.15K crosses 0.9 well before either side gets anywhere near its actual
limit) -- false "Near Limit" alarms at completely normal operating
temperatures (confirmed live: a 37C idle GPU with a 91C slowdown threshold
tripped this tile). The Celsius margin has none of that ratio's arbitrary-zero
distortion. See panel 17 (row B) and its module docstring for the matching
per-GPU percent-of-slowdown gauge.

Panel 1 ("GPUs") carries a second stat value, "Exporters down"
(`count(up{job=~"$job", instance=~"$instance"} == 0)`), with its own
green@0/red@>=1 threshold via a field override -- this dashboard's first
liveness signal that is independent of the `DCGM_FI_*`/`DCGM_EXP_*` series (see
panel 1's own description for the remaining known gap, and row K's "Exporter
scrape status" panel, id 100, for the per-instance detail).
"""
from typing import Any, Dict, List, Optional, Tuple

from .. import lib


def build(ctx: lib.RowContext) -> Tuple[None, List[Dict[str, Any]]]:
    f = ctx.filter_all
    panels: List[Dict[str, Any]] = []

    panels.append(
        lib.stat(
            1, "GPUs", 0, 0, 5, 4,
            [
                lib.target(f"count(DCGM_FI_DEV_GPU_UTIL{f})", legend="GPUs", ref_id="A", instant=True),
                lib.target(
                    f"count(up{ctx.filter_up} == 0) or vector(0)",
                    legend="Exporters down", ref_id="B", instant=True,
                ),
                lib.target(
                    f"sum(max by (instance) (DCGM_FI_SYSTEM_GPU_QUANTITY{f})) or vector(0)",
                    legend="GPUs known to DCGM", ref_id="C", instant=True,
                ),
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("blue"),
            overrides=[
                lib.override_by_name("Exporters down", [("thresholds", lib.thresholds([(0, "green"), (1, "red")]))]),
            ],
            description="GPUs: number of GPU series matching the current job/instance/gpu selection. "
                        "Exporters down: count of `up{job=~\"$job\", instance=~\"$instance\"} == 0` "
                        "scrape targets -- green@0/red@>=1, this dashboard's first exporter/host-liveness "
                        "signal. GPUs known to DCGM: DCGM_FI_SYSTEM_GPU_QUANTITY is a per-node device count "
                        "(dcgm-exporter attaches the same value to every GPU series on that node), so it is "
                        "de-duplicated per instance with `max by (instance)` before being summed across the "
                        "selected fleet. Comparing this to the plain 'GPUs' value answers 'is DCGM still "
                        "reporting telemetry for every GPU it knows this node has' -- a GPU that fell off the "
                        "bus (Xid 79) typically stops producing DCGM_FI_DEV_GPU_UTIL samples first, so 'GPUs' "
                        "reads lower than 'GPUs known to DCGM' while the mismatch persists. REMAINING KNOWN "
                        "LIMITATION: every other fleet tile (Busy, Avg Util/Temp, Total Power, Health, ...) is "
                        "still built entirely from `DCGM_FI_*`/`DCGM_EXP_*` series, which simply stop existing "
                        "(rather than turning red) when a GPU falls off the bus or a scrape target dies "
                        "without `up` itself going to 0 (e.g. the whole host is unreachable and the target is "
                        "absent, not merely down) -- those tiles can still only get quieter, never redder, on "
                        "their own. Cross-check against this tile's 'Exporters down'/'GPUs known to DCGM' "
                        "values, or row K's 'Exporter scrape status' panel (id 100) for the per-instance "
                        "detail, if a fleet count looks lower than expected.",
        )
    )
    panels.append(
        lib.stat(
            2, "Busy", 5, 0, 5, 4,
            [lib.target(f"count(DCGM_FI_DEV_GPU_UTIL{f} > 0) or vector(0)", ref_id="A", instant=True)],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("blue"),
        )
    )
    panels.append(
        lib.stat(
            3, "Avg Util", 10, 0, 5, 4,
            [lib.target(f"avg(DCGM_FI_DEV_GPU_UTIL{f})", ref_id="A", instant=False)],
            unit_id="percent",
            thresholds_steps=lib.thresholds([(0, "blue"), (50, "green")]),
            graph_mode="area",
            reduce_calc="lastNotNull",
        )
    )
    panels.append(
        lib.stat(
            4, "Tensor Active", 15, 0, 5, 4,
            [lib.target(f"avg(DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{f})", ref_id="A", instant=False)],
            unit_id="percentunit",
            thresholds_steps=lib.thresholds([(0, "blue"), (0.3, "green")]),
            graph_mode="area",
            reduce_calc="lastNotNull",
        )
    )
    panels.append(
        lib.stat(
            5, "Avg Temp", 20, 0, 4, 4,
            [lib.target(f"avg(DCGM_FI_DEV_GPU_TEMP{f})", ref_id="A", instant=False)],
            unit_id="celsius",
            thresholds_steps=lib.no_thresholds("gray"),
            graph_mode="area",
            reduce_calc="lastNotNull",
            description="Neutral/informational rather than thresholded: a fixed absolute-Celsius threshold "
                        "(e.g. green/75/85) is not portable across GPU models on a mixed fleet -- several "
                        "datacenter GPUs run normally in the 75-85C+ band at full load, nowhere near their "
                        "real slowdown limit. See panel 17 (row B) for the per-GPU ratio against each GPU's "
                        "own slowdown threshold, which is the portable version of this same signal.",
        )
    )
    panels.append(
        lib.stat(
            6, "Total Power", 0, 4, 5, 4,
            [lib.target(f"sum(DCGM_FI_DEV_POWER_USAGE{f})", ref_id="A", instant=False)],
            unit_id="watt",
            thresholds_steps=lib.no_thresholds("gray"),
            graph_mode="area",
            reduce_calc="lastNotNull",
            description="Neutral gray -- informational sum, not a fault signal by itself.",
        )
    )
    xid_benign_pattern = "|".join(str(code) for code in lib.BENIGN_XID_CODES)
    panels.append(
        lib.stat(
            7, "Xid (5m)", 5, 4, 5, 4,
            [lib.target(
                f'sum(DCGM_EXP_XID_ERRORS_COUNT{{xid!~"{xid_benign_pattern}", {f[1:-1]}}})',
                ref_id="A", instant=True,
            )],
            unit_id="none",
            thresholds_steps=lib.thresholds([(0, "green"), (1, "red")]),
            description="Count of distinct XID events in dcgm-exporter's trailing 5-minute window, summed "
                        "across every selected GPU, excluding the self-healing/informational codes "
                        f"({xid_benign_pattern} -- see the Reliability row's XID reference table) that the "
                        "dashboard's own severity classification colors green -- an app crash harmlessly "
                        "triggering Xid 43 should not paint this top-strip tile solid red the same as a "
                        "genuine Xid 79 (fallen off bus).",
        )
    )
    panels.append(
        lib.stat(
            8, "Health", 10, 4, 5, 4,
            [lib.target(
                f'max(DCGM_EXP_GPU_HEALTH_STATUS{{{f[1:-1]}}})',
                ref_id="A", instant=True,
            )],
            unit_id="none",
            mappings=lib.health_status_mappings(),
            thresholds_steps=lib.no_thresholds(),
            description="Worst status (max) across every DCGM health-check watch, including the 'ALL' "
                        "watch -- DCGM's own separate GPU-wide check, not a rollup of the other 10, that "
                        "catches devastating XIDs (e.g. Xid 79 fallen off bus, Xid 95 uncontained ECC) with "
                        "no single named subsystem; max() is idempotent, so including it cannot double-"
                        "count, it can only surface a failure the other 10 watches would have missed. Pass "
                        "(green) / Warn (yellow) / Fail (red) -- a plain max() over status codes rather than "
                        "a count()-with-red-threshold formula, which would paint this tile solid red for a "
                        "purely informational MONITOR-severity condition (e.g. the SW power cap engaging "
                        "under sustained full load) exactly the same as a real fault (e.g. a thermal "
                        "shutdown or an uncorrectable ECC error), and which would count (GPU, watch) pairs "
                        "rather than GPUs -- 2 watches WARNing on the same GPU would misleadingly read as 2 "
                        "unhealthy GPUs. See the Reliability row's 'Health incidents (why)' table for which "
                        "watch/GPU/error-code is responsible; its own noValue text reads '0 GPUs unhealthy' "
                        "when nothing is Warn or Fail.",
        )
    )
    panels.append(
        lib.stat(
            9, "Throttled", 15, 4, 5, 4,
            [
                lib.target(
                    f"count({lib.any_real_fault_throttle_expr('DCGM_FI_DEV_CLOCKS_EVENT_REASONS' + f)}) or vector(0)",
                    legend="Fault-throttled", ref_id="A", instant=True,
                ),
                lib.target(
                    f"count({lib.clock_event_bit_expr('DCGM_FI_DEV_CLOCKS_EVENT_REASONS' + f, 0x004)} > 0) or vector(0)",
                    legend="At power cap", ref_id="B", instant=True,
                ),
            ],
            unit_id="none",
            thresholds_steps=lib.thresholds([(0, "green"), (1, "yellow"), (2, "red")]),
            overrides=[
                lib.override_by_name("At power cap", [("thresholds", lib.no_thresholds("blue"))]),
            ],
            description="Fault-throttled: GPUs with any of the 4 real-fault throttle bits set (HW_SLOWDOWN, "
                        "SW_THERMAL, HW_THERMAL, HW_POWER_BRAKE) -- a real problem, alarm-colored. At power "
                        "cap: GPUs hitting SW_POWER_CAP (0x4), shown separately and always blue -- running at "
                        "the configured power limit under full load is expected GPU behavior, not a fault, "
                        "so it no longer drives this tile's red threshold the way it used to when both were "
                        "summed together. BOARD_LIMIT (0x200) and RELIABILITY (0x400) are informational clock "
                        "limits (nvidia-smi -q -d PERFORMANCE shows \"Reliability: Active\" while idling in "
                        "P8) and are excluded from both counts. See panel 97's Throttled/Power Capped columns "
                        "for the same split per GPU.",
        )
    )
    panels.append(
        lib.stat(
            10, "Near Limit", 20, 4, 4, 4,
            [lib.target(
                f"count((DCGM_FI_DEV_POWER_USAGE{f} / on(UUID) DCGM_FI_DEV_ENFORCED_POWER_LIMIT{f} > 0.9) "
                f"or ((DCGM_FI_DEV_SLOWDOWN_TEMP{f} - on(UUID) DCGM_FI_DEV_GPU_TEMP{f}) < 10)) or vector(0)",
                ref_id="A", instant=True,
            )],
            unit_id="none",
            thresholds_steps=lib.thresholds([(0, "green"), (1, "yellow"), (2, "red")]),
            description="Power: fraction of enforced limit (a true ratio -- Watts is a ratio scale). "
                        "Temp: plain Celsius margin, SLOWDOWN_TEMP - GPU_TEMP < 10C -- "
                        "an earlier formula divided (GPU_TEMP+273.15) by (SLOWDOWN_TEMP+273.15) to get a "
                        "Kelvin-based ratio, which is thermodynamically defensible for a *ratio* but made "
                        "this tile trip at a misleadingly low absolute temperature: with a 91C slowdown "
                        "GPU, 0.9 in Kelvin is reached at only ~55C, so a GPU idling at 37C could still fire "
                        "a false 'Near Limit' alarm on a warm day. A raw Celsius margin has no such "
                        "distortion and needs no unit-conversion caveat. See panel 17 (row B) for the "
                        "matching per-GPU percent-of-slowdown gauge, and panel 59 for the same absolute-"
                        "margin signal as a per-GPU gauge.",
        )
    )

    return None, panels
