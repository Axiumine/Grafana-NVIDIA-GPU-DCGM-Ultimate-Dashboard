# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row E -- Memory (Framebuffer + BAR1).

Row id 32, y:43 (header), collapsed=False, 6 children ids 33-38 across two
h=8 sub-rows (relative y=1, y=9). Unlike row D's blue->green "activity is
good" semantics, VRAM usage is a resource-pressure metric (like power/temp
vs. limit in rows G/H) -- more used is not inherently good, and running near
capacity risks OOM/allocation failures -- so this row uses the traditional
green->yellow->red convention instead.

Legends on 34/36/37 use the dashboard-wide `ctx.legend_std` token
(`{{hostname}} GPU{{gpu}}`) so multiple selected GPUs stay distinguishable.
Panel 34's "Free" series is recolored from Grafana's default muddy
olive/brown (which reads like a warning block despite being a healthy value)
to blue.

Panels 33-37's raw per-GPU metrics go through `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section) so a dcgm-exporter CSV label-set
change (new driver/VBIOS) can't draw this GPU as two overlapping series for a
while. Panel 38 ("zombie detector") is left as a plain `count(... and
on(UUID) ...)`: it already aggregates across the whole fleet into one number
(no by-clause, no per-GPU legend), so it is unaffected by the split either way
-- see lib.per_gpu()'s own docstring, "already aggregates, don't
double-wrap".
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(32, "Memory (Framebuffer + BAR1)", collapsed=False)
    panels: list[dict[str, Any]] = []

    # --- Sub-row 1 (relative y=1, h=8) --------------------------------------
    panels.append(
        lib.gauge(
            33,
            "VRAM Used %",
            0,
            1,
            6,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_USED_RATIO{f}"), legend=ctx.legend_std, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(0, "green"), (0.85, "yellow"), (0.95, "red")]),
            description="Used/(Total-Reserved), DCGM's own pre-computed ratio (avoids re-deriving the "
            "VRAM% formula by hand in PromQL, and avoids the ~5% error of dividing by raw "
            "Total instead of the allocatable Total-Reserved). Yellow at 85% is 'plan "
            "ahead', red at 95% is 'the next large allocation will likely fail with an "
            "out-of-memory error'.",
        )
    )
    panels.append(
        lib.timeseries(
            34,
            "VRAM Used/Free/Reserved",
            6,
            1,
            12,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_USED{f}"), legend=f"Used · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_FREE{f}"), legend=f"Free · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_RESERVED{f}"), legend=f"Reserved · {std}", ref_id="C"),
            ],
            unit_id="mbytes",
            stacked=True,
            min_=0,
            thresholds_steps=lib.no_thresholds("gray"),
            overrides=[
                # Minor visual fix: Grafana's default palette assigned "Free" a muddy
                # olive/brown that read like a warning block despite free VRAM being a
                # healthy value -- recolored to a neutral blue, consistent with the rest
                # of the dashboard's blue/green "healthy" palette.
                lib.override_by_regex(r"^Free", [("color", lib.fixed_color("blue"))]),
            ],
            description="Stacked so Used+Free+Reserved always sums to the framebuffer total, showing "
            "exactly how much headroom remains and how it is currently split. Unit is "
            "'mbytes' (Grafana's MiB id) because DCGM reports these fields in MiB, never "
            "'decmbytes' (decimal MB) -- that would under-report by about 4.9%. Reserved is "
            "memory the driver/ECC/row-remapping subsystem has set aside and is never "
            "available for allocation regardless of how idle the GPU is.",
        )
    )
    panels.append(
        lib.stat(
            35,
            "VRAM total",
            18,
            1,
            6,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_FB_TOTAL{f}"), legend=ctx.legend_std, ref_id="A", instant=True)],
            unit_id="mbytes",
            thresholds_steps=lib.no_thresholds("blue"),
            description="Informational nameplate capacity, not a live health signal -- this is a "
            "per-GPU-model constant (e.g. 24 GB on the reference RTX PRO 4000 Blackwell) "
            "that only changes if the physical GPU or its MIG partitioning changes, so it "
            "gets a flat, uncolored 'blue' presentation rather than a threshold gradient.",
        )
    )

    # --- Sub-row 2 (relative y=9, h=8) --------------------------------------
    panels.append(
        lib.histogram(
            36,
            "Memory Utilization % distribution",
            0,
            9,
            8,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_MEM_COPY_UTIL{f}"), legend=std, ref_id="A")],
            unit_id="percent",
            description="Native Grafana 'histogram' panel (auto-bucketing on the raw 0-100% "
            "MEM_COPY_UTIL samples) -- distinct from the 'heatmap' panel type used in row "
            "D: a histogram panel buckets and auto-scales natively from a plain scalar "
            "series with no options.calculate flag needed, while a heatmap panel needs "
            "that flag to do the same thing over time. Shows whether the memory controller "
            "spends most of its time near-idle, near-saturated, or spread across the range.",
        )
    )
    panels.append(
        lib.timeseries(
            37,
            "BAR1 aperture used/free/capacity",
            8,
            9,
            8,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_BAR1_USED_BYTES{f}"), legend=f"Used · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_BAR1_FREE_BYTES{f}"), legend=f"Free · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_BAR1_CAPACITY_BYTES{f}"), legend=f"Capacity · {std}", ref_id="C"),
            ],
            unit_id="bytes",
            min_=0,
            thresholds_steps=lib.no_thresholds("gray"),
            description="BAR1 is the PCIe-addressable aperture into VRAM (separate and usually much "
            "smaller than the framebuffer itself) that lets the CPU or peer devices map GPU "
            "memory directly. Only relevant for GPUDirect RDMA/Storage and other large "
            "peer-mapping workloads; a general compute workload can run BAR1 completely "
            "idle with no impact on GPU performance, so a flat line near 0 here is normal, "
            "not a fault.",
        )
    )
    panels.append(
        lib.stat(
            38,
            "Idle-but-allocated VRAM (zombie detector)",
            16,
            9,
            8,
            8,
            [
                lib.target(
                    f"count((DCGM_FI_DEV_FB_USED_RATIO{f} > 0.1) and on(UUID) "
                    f"(avg_over_time(DCGM_FI_DEV_GPU_UTIL{f}[15m]) == 0)) or vector(0)",
                    ref_id="A",
                    instant=True,
                )
            ],
            unit_id="none",
            thresholds_steps=lib.thresholds([(0, "green"), (1, "yellow")]),
            description="Counts GPUs holding more than 10% of VRAM while averaging 0% GPU utilization "
            "over the last 15 minutes -- the classic symptom of a leaked CUDA context, a "
            "crashed-but-not-cleaned-up process, or a forgotten idle notebook kernel still "
            "holding an allocation. No reference dashboard (14574/22424/25526) has this "
            "check. A nonzero reading does not by itself say which process is at fault -- "
            "cross-check with `nvidia-smi` on the affected host to find the offending PID.",
        )
    )

    return row_def, panels
