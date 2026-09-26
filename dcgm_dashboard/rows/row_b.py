# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row B -- Per-GPU Overview (repeats per selected GPU).

Row id 11, repeat over $gpu, 8 children ids 12-19, relative y=1 (one unit below
the row header), h=4, filtered with FILTER_SINGLE (plain UUID="$gpu" equality
-- single value per repetition).

The row title uses Grafana's own template-variable syntax ($gpu), not the
{{gpu}} Prometheus-legend handlebars notation: a Grafana row/panel *title* has
no per-series label context to substitute {{gpu}} from (that syntax only works
inside a query target's legendFormat), so a literal "{{gpu}}" in a title would
render as the literal string, not the GPU's value. The title is `"$gpu —
Overview"`, not `"GPU $gpu — Overview"` -- $gpu's value is already a
`GPU-<uuid>` string (DCGM_FI_DEV_UUID's own format), so a leading literal
"GPU " would double into "GPU GPU-65bd... — Overview".

Panels 16/17 use `on(UUID) group_left(modelName)`, not plain `on(UUID)`:
dividing two metrics with `on(UUID)` alone drops every other label from the
result (verified live against this environment's Prometheus), which would make
the {{modelName}} legend render blank. `group_left(modelName)` restores the
full label set from the left-hand series (verified live) while still matching
defensively on UUID alone.

Panel 17 is a plain Celsius ratio, GPU_TEMP / SLOWDOWN_TEMP -- "what percent of
the slowdown temperature (in C) is this GPU currently at" -- which is the
semantic every viewer actually reads a percent-style gauge as, and needs no
Kelvin caveat in the tooltip. An earlier version computed
(GPU_TEMP+273.15)/(SLOWDOWN_TEMP+273.15), a Kelvin ratio that is
thermodynamically "correct" in the abstract but produces a misleadingly high
percentage at normal operating temperatures (0.9 in Kelvin is only ~55C on a
91C-slowdown GPU), causing this gauge to sit in its own yellow/red zone while
idling at 37C. See row A's panel 10 (top strip "Near Limit") for the matching
fleet-wide alarm.

Every raw metric in this row is wrapped in `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section): a dcgm-exporter CSV label-set
change (new driver/VBIOS) would otherwise render this GPU's single repeated
row as two overlapping gauges/stats for a while. Panels 16/17's division now
aggregates *each side* before dividing -- both operands are plain,
non-windowed gauge selectors, so Prometheus's own staleness handling already
guarantees only one of the split series is live at any instant the query
evaluates against; pre-aggregating still matters because without it this
gauge would briefly render as two overlapping boxes under one legend, the
same duplicate-rendering class as panel 18's fan fix just below (not a hard
join error -- see CONVENTIONS.md gotcha #13 for which joins actually risk a
"many-to-one matching must be explicit" error). Panel 18's fallback also
switched from a label-dropping bare `max(...)` to `lib.per_gpu(...)` --
max(...) alone strips every label, including modelName, which the panel's
own `{{modelName}}` legend needed (a preexisting, now-fixed rendering gap,
not something the label-set split introduced).
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_single
    legend = "{{modelName}}"
    row_def = lib.row(11, "$gpu — Overview", collapsed=False, repeat="gpu")

    panels: list[dict[str, Any]] = []

    panels.append(
        lib.gauge(
            12,
            "GPU Util",
            0,
            1,
            3,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_UTIL{f}"), legend=legend, ref_id="A")],
            unit_id="percent",
            min_=0,
            max_=100,
            thresholds_steps=lib.thresholds([(None, "blue"), (50, "green")]),
        )
    )
    panels.append(
        lib.gauge(
            13,
            "SM Active",
            3,
            1,
            3,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_PROF_SM_ACTIVE{f}"), legend=legend, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.5, "green")]),
            description="NVIDIA guidance: >=0.8 is genuinely good; 0.5 is the 'worth a look' floor, not a target.",
        )
    )
    panels.append(
        lib.gauge(
            14,
            "Tensor Active",
            6,
            1,
            3,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{f}"), legend=legend, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.3, "green")]),
        )
    )
    panels.append(
        lib.gauge(
            15,
            "VRAM Used",
            9,
            1,
            3,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_USED_RATIO{f}"), legend=legend, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "green"), (0.85, "yellow"), (0.95, "red")]),
        )
    )
    panels.append(
        lib.gauge(
            16,
            "Power %",
            12,
            1,
            3,
            4,
            [
                lib.target(
                    f"{lib.per_gpu(f'DCGM_FI_DEV_POWER_USAGE{f}')} / on(UUID) group_left(modelName) "
                    f"{lib.per_gpu(f'DCGM_FI_DEV_ENFORCED_POWER_LIMIT{f}')}",
                    legend=legend,
                    ref_id="A",
                )
            ],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "green"), (0.85, "yellow"), (0.95, "red")]),
            description="Ratio, not an absolute gauge with a dynamic max: Grafana's gauge max is a single "
            "static number, so the ratio is computed in PromQL instead. Absolute Watts in panel 48. "
            "Each side of the division is pre-aggregated via lib.per_gpu() before the on(UUID) "
            "join. Both operands are plain gauge selectors, so a driver/VBIOS upgrade's old and "
            "new label-set series can never both be live at the same evaluation instant here "
            "(Prometheus staleness handling) -- pre-aggregating instead stops this gauge from "
            "briefly rendering as two overlapping boxes under one legend, same class as panel "
            "18's fan fix. A hard 'many-to-one matching must be explicit' join error is the real "
            "risk only when a join operand uses rate()/increase()/*_over_time spanning the "
            "transition; see CONVENTIONS.md gotcha #13.",
        )
    )
    panels.append(
        lib.gauge(
            17,
            "Temp %",
            15,
            1,
            3,
            4,
            [
                lib.target(
                    f"{lib.per_gpu(f'DCGM_FI_DEV_GPU_TEMP{f}')} / on(UUID) group_left(modelName) "
                    f"{lib.per_gpu(f'DCGM_FI_DEV_SLOWDOWN_TEMP{f}')}",
                    legend=legend,
                    ref_id="A",
                )
            ],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "green"), (0.85, "yellow"), (0.95, "red")]),
            description="What percent of this GPU's own slowdown threshold its current temperature is, "
            "both in Celsius (an earlier +273.15 Kelvin-ratio version made "
            "this gauge read a misleadingly high percentage at normal temperatures -- e.g. a "
            "37C idle GPU with a 91C slowdown threshold read ~85% and sat in this gauge's own "
            "yellow zone; the plain Celsius ratio instead reads ~41%, in the green zone where "
            "an idle GPU belongs). Thresholds are percent-of-slowdown-in-C: green below 85% "
            "(e.g. under 77C of a 91C limit), yellow 85-95% (77-86C), red at/above 95% (86C+). "
            "Absolute Celsius in panel 57, unit-agnostic absolute margin in panel 59.",
        )
    )
    panels.append(
        lib.gauge(
            18,
            "Fan",
            18,
            1,
            3,
            4,
            [
                lib.target(
                    f"{lib.per_gpu(f'DCGM_FI_DEV_FAN_SPEED{f}')} or ({lib.per_gpu(f'DCGM_FI_DEV_GPU_UTIL{f}')}*0)",
                    legend=legend,
                    ref_id="A",
                )
            ],
            unit_id="percent",
            min_=0,
            max_=100,
            thresholds_steps=lib.no_thresholds(),
            description="Passively-cooled GPUs read 0 -- that is the healthy steady state, not a fault. "
            "Wrapped in lib.per_gpu() (max by GPU identity) on both sides of the fallback: a bare "
            "`max(...) or vector(0)` (the previous version) drops every label on the left "
            "(including modelName, which this panel's own legend needs) and unions it with "
            "vector(0)'s label-less result -- fine for a single instant, but over a time RANGE "
            "(this panel's gauge is queried as a range and reduced to lastNotNull) any step where "
            "FAN_SPEED has no sample gets a second, label-less phantom-0 series alongside the "
            "labeled one, rendered as an empty legend next to the real 'NVIDIA RTX PRO 4000 "
            "Blackwell' one. Falling back to `lib.per_gpu(GPU_UTIL)*0` instead of `vector(0)` "
            "(the same trick as panel 58's memory-temp fallback) gives the fallback branch the "
            "*same* identity label set as the real branch, so `or` coalesces them into one "
            "continuous, correctly-labeled series instead of two.",
        )
    )
    panels.append(
        lib.stat(
            19,
            "P-State",
            21,
            1,
            3,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_PSTATE{f}"), legend=legend, ref_id="A", instant=True)],
            unit_id="none",
            mappings=lib.pstate_mappings(),
            color_mode="none",
            thresholds_steps=lib.no_thresholds(),
            description="No color: P0 is not inherently 'good' or 'bad' in isolation.",
        )
    )

    return row_def, panels
