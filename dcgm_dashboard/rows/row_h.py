# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row H -- Thermals & Fan.

Row id 56, y:110. Child ids 57-62.
Sub-row 1 relative y=1, h=8: 57,58,59,60.
Sub-row 2 relative y=9, h=8: 61,62.

Two local conventions not (yet) promoted into lib.py, documented here instead:

1. Dashed reference lines on a timeseries: lib.timeseries() has no dash-style
   param, so each dashed series is given a distinctive legend prefix and a
   `lib.override_by_regex(r"^<prefix>", [("custom.lineStyle", {...})])`
   override targets it. byRegexp matches the field's *display name*, i.e. the
   fully label-substituted legendFormat string, so an anchored `^prefix`
   reliably selects "that metric, any GPU" regardless of how many GPUs are
   selected. (Legend text is kept free of regex metacharacters -- no literal
   parentheses -- so the prefix needs no escaping.)
2. Second Y axis: same override mechanism, using the per-field
   `custom.axisPlacement: "right"` + `unit` property ids (already used
   elsewhere in lib.py's overrides, e.g. row_c.py's per-column `unit`
   override) -- no dedicated lib helper needed for a single-field axis
   override; lib.py's formerly-dead `second_y_axis` param on
   `lib.timeseries()` was removed during assembly rather than wired up,
   since every dual-axis panel needs a *specific* field's unit/label on
   the right axis anyway, which this override pattern already covers.

Every raw per-GPU metric in this row (57-62) goes through `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section) so a dcgm-exporter CSV label-set
change (new driver/VBIOS) can't draw two lines with the same legend for a
while. Panel 58's `X or (GPU_TEMP*0)` no-sensor fallback is wrapped as a
single expression (aggregate the whole `A or B`, not each side separately) --
either branch could independently duplicate during the split, and wrapping
only the outer result still collapses both cases to one series per GPU.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(56, "Thermals & Fan", collapsed=False)

    panels: list[dict[str, Any]] = []

    # -- Sub-row 1 (rel y=1, h=8) --------------------------------------

    panels.append(
        lib.timeseries(
            57,
            "GPU temperature",
            0,
            1,
            8,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_TEMP{f}"), legend=std, ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_SLOWDOWN_TEMP{f}"), legend=f"Slowdown limit · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_SHUTDOWN_TEMP{f}"), legend=f"Shutdown limit · {std}", ref_id="C"),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_GPU_MAX_OP_TEMP_CELSIUS{f}"),
                    legend=f"Max recommended · {std}",
                    ref_id="D",
                ),
            ],
            unit_id="celsius",
            thresholds_steps=lib.no_thresholds("gray"),
            overrides=[
                lib.override_by_regex(r"^Slowdown limit", [("custom.lineStyle", {"fill": "dash", "dash": [10, 10]})]),
                lib.override_by_regex(r"^Shutdown limit", [("custom.lineStyle", {"fill": "dash", "dash": [10, 10]})]),
                lib.override_by_regex(r"^Max recommended", [("custom.lineStyle", {"fill": "dash", "dash": [10, 10]})]),
            ],
            description="GPU die temperature (solid) against its three configured limits (dashed): the "
            "slowdown threshold (clocks throttle above this -- see panel 42/44's HW_THERMAL/"
            "SW_THERMAL bits), the shutdown threshold (hardware protection cutoff, should never "
            "be reached in normal operation), and NVIDIA's maximum recommended operating "
            "temperature. No fixed green/75/85 Celsius threshold here -- several datacenter GPU "
            "models run normally in that band at full load, nowhere near their real slowdown "
            "limit, which the dashed reference lines already show per-GPU.",
        )
    )
    panels.append(
        lib.timeseries(
            58,
            "Memory temperature",
            8,
            1,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_MEMORY_TEMP{f} or (DCGM_FI_DEV_GPU_TEMP{f}*0)"),
                    legend=std,
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_MEMORY_MAX_OP_TEMP_CELSIUS{f} or (DCGM_FI_DEV_GPU_TEMP{f}*0)"),
                    legend=f"Mem max recommended · {std}",
                    ref_id="B",
                ),
            ],
            unit_id="celsius",
            thresholds_steps=lib.no_thresholds("gray"),
            overrides=[
                lib.override_by_regex(
                    r"^Mem max recommended", [("custom.lineStyle", {"fill": "dash", "dash": [10, 10]})]
                ),
            ],
            description="On-die memory (HBM/GDDR) junction temperature, when the GPU exposes that sensor. "
            "A large share of GPUs -- including this environment's RTX PRO 4000 Blackwell -- have "
            "no on-die memory temperature sensor at all and always read 0 via the "
            "`DCGM_FI_DEV_GPU_TEMP*0` fallback used here so the panel degrades gracefully instead "
            "of going blank. On those GPUs, 0 means 'no sensor', never 'very cold' -- deliberately "
            "no threshold on this panel for that reason.",
        )
    )
    panels.append(
        lib.gauge(
            59,
            "Thermal Margin",
            16,
            1,
            4,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_TEMP_MARGIN_CELSIUS{f}"), legend=std, ref_id="A")],
            unit_id="celsius",
            min_=0,
            max_=100,
            thresholds_steps=lib.thresholds([(None, "red"), (10, "yellow"), (20, "green")]),
            description="Degrees Celsius of headroom between the GPU's current temperature and its nearest "
            "slowdown threshold -- lower is worse, the inverse of most gauges on this dashboard. "
            "Red under 10C means the GPU is close to thermal throttling right now; yellow 10-20C "
            "is a warning zone; green above 20C is healthy headroom.",
        )
    )
    panels.append(
        lib.histogram(
            60,
            "Temp Distribution",
            20,
            1,
            4,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_TEMP{f}"), legend=std, ref_id="A")],
            unit_id="celsius",
            description="Distribution of GPU die temperature samples over the dashboard's time range, "
            "native auto-bucketing (no `calculate` flag -- this is the histogram panel type, not "
            "the heatmap-over-time panel type used elsewhere for this). A bimodal shape -- one "
            "cluster near idle temperature and one near a hotter loaded temperature -- reveals two "
            "distinct operating regimes that a single timeseries line tends to hide.",
        )
    )

    # -- Sub-row 2 (rel y=9, h=8) ---------------------------------------

    panels.append(
        lib.timeseries(
            61,
            "Fan speed",
            0,
            9,
            12,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_FAN_SPEED{f}"), legend=std, ref_id="A")],
            unit_id="percent",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Fan duty cycle (%). No `or vector(0)` fallback: PromQL's `or` unions series by "
            "label set rather than coalescing, so applying it directly to this fully-labeled "
            "per-GPU series would produce a second, label-less phantom 0% series alongside the "
            "real one whenever the metric IS reporting. A passively-cooled or blower-less GPU "
            "with no fan sensor correctly renders 'No data' instead (see panel 18's gauge for "
            "the safe-fallback pattern via `max(...) or vector(0)`, which strips labels first).",
        )
    )
    panels.append(
        lib.timeseries(
            62,
            "Fan speed vs. GPU temperature",
            12,
            9,
            12,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_FAN_SPEED{f}"), legend=f"Fan · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_TEMP{f}"), legend=f"Temp · {std}", ref_id="B"),
            ],
            unit_id="percent",
            thresholds_steps=lib.no_thresholds("gray"),
            overrides=[
                lib.override_by_regex(
                    r"^Temp",
                    [
                        ("custom.axisPlacement", "right"),
                        ("custom.axisLabel", "°C"),
                        ("unit", lib.unit("celsius")),
                    ],
                ),
            ],
            description="Dual-axis overlay: fan speed on the left axis (%), GPU temperature on the right "
            "axis (C). Lets a viewer spot 'fan ramped up but temperature kept climbing anyway' -- "
            "the classic failing-fan or blocked-airflow signature -- at a glance. No reference "
            "dashboard (14574/22424/25526) has this overlay.",
        )
    )

    return row_def, panels
