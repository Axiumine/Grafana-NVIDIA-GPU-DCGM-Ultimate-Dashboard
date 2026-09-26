# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row D -- Utilization & Real Activity (Profiling).

Row id 22, y:18 (header), collapsed=False, 9 children ids 23-31 across three
h=8 sub-rows (relative y=1, y=9, y=17). Color semantics for the whole row:
blue (idle) -> green (busy), never red -- high activity here is the goal, not
a fault, so every threshold/legend choice below follows that convention
instead of the green/yellow/red fault palette used elsewhere in the dashboard.

Panel 27's " TFLOPS" unit suffix is baked into its legendFormat string rather
than set via a fieldConfig.defaults.displayName override or a new lib.UNITS
entry: lib.timeseries() has no display_name param, and a timeseries field's
rendered legend/tooltip label already comes straight from the target's
legendFormat when no displayName override is present -- i.e. for a timeseries
with no override, "set displayName" and "set legendFormat" produce the exact
same rendered label. This gets the desired text unit-suffix next to the values
without inventing a new custom "suffix:" unit id in lib.UNITS (see
CONVENTIONS.md's "never invent a unit string inline" rule) and without the
un-interpolated literal-string risk a static displayName override would carry
across multiple GPUs. A real "suffix:" unit in lib.py would be a cleaner
long-term fix if more panels need this pattern.

Every legend in this row's multi-metric overlay panels (23, 26, 27, 28, 31)
appends/prefixes `ctx.legend_std` (`{{hostname}} GPU{{gpu}}`) rather than a
bare per-metric literal ("GPU Util", "FP64", ...): with the $gpu variable's
default (every GPU), a bare literal legend collapses each metric family to one
ambiguous legend string across the whole fleet, indistinguishable GPU from
GPU.

Every raw/derived per-GPU metric in this row (23-31) is wrapped in
`lib.per_gpu()` (see CONVENTIONS.md's series-identity section) -- otherwise a
dcgm-exporter CSV label-set change (new driver/VBIOS) draws two overlapping
lines with the same legend for a while. Panels 29/30's heatmaps and 31's
distribution-adjacent panels get the same treatment: a heatmap's
`options.calculate:true` bucketing does not know or care about series
identity, so a raw scalar-gauge query left un-aggregated would double-count
every sample into its bucket during the transition, not just misrender a
legend.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(22, "Utilization & Real Activity (Profiling)", collapsed=False)
    panels: list[dict[str, Any]] = []

    # --- Sub-row 1 (relative y=1, h=8) --------------------------------------
    panels.append(
        lib.timeseries(
            23,
            "GPU Util vs. SM/Tensor/GR-Engine Active",
            0,
            1,
            12,
            8,
            [
                lib.target(f"{lib.per_gpu(f'DCGM_FI_DEV_GPU_UTIL{f}')}/100", legend=f"GPU Util · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_SM_ACTIVE{f}"), legend=f"SM Active · {std}", ref_id="B"),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{f}"), legend=f"Tensor Active · {std}", ref_id="C"
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_GR_ENGINE_ACTIVE{f}"), legend=f"GR Engine Active · {std}", ref_id="D"
                ),
            ],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.5, "green")]),
            description="The signature 'AI efficiency' panel. GPU_UTIL (DCGM's copy of nvidia-smi's "
            "utilization%, /100'd here to share the 0-1 axis) only means 'some kernel is "
            "running' -- it can read 100% while the GPU is almost idle underneath. "
            "SM Active (>=0.8 needed, not sufficient, for effective use; <0.5 usually means "
            "real underuse) and Tensor Active are DCGM's *measured* hardware activity ratios, "
            "so a wide gap below GPU_UTIL (GPU_UTIL high, SM/Tensor low) is the classic "
            "'kernel launched but barely doing anything' pattern that nvidia-smi-only "
            "dashboards cannot show at all. GR Engine Active is DCGM's closest measured "
            "analogue to GPU_UTIL itself (bound-context + busy graphics/compute engine), "
            "useful as a sanity cross-check against the DCGM-reported GPU_UTIL line.",
        )
    )
    panels.append(
        lib.timeseries(
            24,
            "SM Occupancy",
            12,
            1,
            6,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_PROF_SM_OCCUPANCY{f}"), legend=ctx.legend_std, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.5, "green")]),
            description="Resident warps / theoretical max warps per SM, averaged over the whole GPU. "
            "Higher is better ONLY for memory- or latency-bound kernels (more resident warps "
            "gives the scheduler more chances to hide a stall behind another warp); for a "
            "compute-bound kernel this ratio is not correlated with performance at all, so "
            "do not read a low value here as automatically bad -- check SM Active/Tensor "
            "Active alongside it before concluding anything is underutilized.",
        )
    )
    panels.append(
        lib.timeseries(
            25,
            "DRAM Active",
            18,
            1,
            6,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_PROF_DRAM_ACTIVE{f}"), legend=ctx.legend_std, ref_id="A")],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.5, "green")]),
            description="Fraction of cycles the memory interface was sending/receiving data. Achieved "
            "bandwidth is approximately this ratio times the GPU's peak memory bandwidth "
            "(see panel 28). Practical achievable peak is roughly 0.8, not 1.0 -- a "
            "memory-bound kernel pegged near 0.8 is already close to the hardware ceiling, "
            "not underperforming.",
        )
    )

    # --- Sub-row 2 (relative y=9, h=8) --------------------------------------
    panels.append(
        lib.timeseries(
            26,
            "Precision mix (FP64/FP32/FP16/INT/Tensor)",
            0,
            9,
            12,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PIPE_FP64_ACTIVE{f}"), legend=f"FP64 · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PIPE_FP32_ACTIVE{f}"), legend=f"FP32 · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PIPE_FP16_ACTIVE{f}"), legend=f"FP16 · {std}", ref_id="C"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_INT_UTIL_RATIO{f}"), legend=f"INT · {std}", ref_id="D"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_IMMA_UTIL_RATIO{f}"), legend=f"IMMA · {std}", ref_id="E"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_HMMA_UTIL_RATIO{f}"), legend=f"HMMA · {std}", ref_id="F"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_DFMA_UTIL_RATIO{f}"), legend=f"DFMA · {std}", ref_id="G"),
            ],
            unit_id="percentunit",
            min_=0,
            max_=1,
            stacked=True,
            legend_mode="table",
            legend_placement="right",
            legend_calcs=["lastNotNull"],
            legend_sort_by="Last *",
            legend_sort_desc=True,
            thresholds_steps=lib.thresholds([(None, "blue"), (0.5, "green")]),
            description="Which execution pipe a workload actually hits, stacked so the mix over time is "
            "visible at a glance -- no nvidia-smi-based dashboard can show this at all (it has "
            "no per-pipe activity counters). FP64/FP32/FP16 are the non-tensor pipes; IMMA "
            "(int8 tensor), HMMA (fp16/bf16/fp8 tensor) and DFMA (fp64 tensor) are the three "
            "tensor-pipe sub-components of PIPE_TENSOR_ACTIVE (panel 23) and roughly sum back "
            "to it. Short per-series tokens + table legend (sorted by last value), since with "
            ">=4 series families and multiple GPUs selected, the standard {{instance}}-style "
            "legend would produce series-count x GPU-count near-duplicate rows.",
        )
    )

    panels.append(
        lib.timeseries(
            27,
            "Achieved TFLOPS (FP32, estimated)",
            12,
            9,
            6,
            8,
            [
                lib.target(
                    f"{lib.per_gpu(f'DCGM_FI_PROF_PIPE_FP32_ACTIVE{f}')} * $peak_fp32_tflops",
                    legend=f"{std} — FP32 TFLOPS",
                    ref_id="A",
                )
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("blue"),
            description="ESTIMATE, not a measured value: active-cycle ratio x this GPU's published "
            "peak FP32 TFLOPS ($peak_fp32_tflops, a textbox variable -- edit it per GPU "
            "model if this dashboard is reused on different hardware; default 40 matches "
            "the RTX PRO 4000 Blackwell). NVIDIA's own dcgmproftester validation example "
            "found this active-ratio x peak-FLOPs formula accurate to measured throughput "
            "on A100 (100% tensor-active measured ≈253 TFLOPS FP16, matching published "
            "peak), so treat this as a good approximation, not an exact hardware counter.",
        )
    )
    panels.append(
        lib.timeseries(
            28,
            "Achieved Bandwidth (estimated)",
            18,
            9,
            6,
            8,
            [
                lib.target(
                    f"{lib.per_gpu(f'DCGM_FI_PROF_DRAM_ACTIVE{f}')} * $peak_mem_bw_gbs * 1e9",
                    legend=f"{std} — Bandwidth",
                    ref_id="A",
                )
            ],
            unit_id="Bps",
            thresholds_steps=lib.no_thresholds("blue"),
            description="ESTIMATE: DRAM_ACTIVE ratio x this GPU's published peak memory bandwidth "
            "($peak_mem_bw_gbs, GB/s -- default 672 matches the RTX PRO 4000 Blackwell; "
            "edit per GPU model). No /8 here: $peak_mem_bw_gbs is already a byte bandwidth "
            "(GB/s, not Gb/s), so ratio x GBps x 1e9 is already bytes/sec -- an extra /8 "
            "would wrongly re-apply a bit-to-byte conversion and under-report by 8x. "
            "Practical peak DRAM_ACTIVE tops out near 0.8, so expect this line to plateau "
            "around 80% of $peak_mem_bw_gbs even at full memory saturation.",
        )
    )

    # --- Sub-row 3 (relative y=17, h=8) --------------------------------------
    panels.append(
        lib.heatmap(
            29,
            "Utilization distribution",
            0,
            17,
            8,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_UTIL{f}"), ref_id="A")],
            bucket_size=5,
            unit_id="percent",
            min_=0,
            max_=100,
            description="Distribution of GPU_UTIL samples over time, bucketed in 5-percentage-point "
            "bands (Turbo color scale = sample density per bucket). Needs "
            "options.calculate:true because GPU_UTIL is a plain scalar gauge, not a "
            "pre-bucketed histogram series -- without it this heatmap renders empty. Useful "
            "for spotting bimodal behavior (e.g. a GPU that is either near-idle or near-100%, "
            "rarely in between) that a single averaged line would hide. min/max pinned to the "
            "field's real 0-100 domain: without it, a zero-variance idle GPU's samples "
            "auto-range to a single fully-saturated band spanning whatever y-range Grafana "
            "happened to pick, which reads as 'constant high activity' at a glance even though "
            "the value is a healthy flat 0%.",
        )
    )
    panels.append(
        lib.heatmap(
            30,
            "Tensor-Active distribution",
            8,
            17,
            8,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{f}"), ref_id="A")],
            bucket_size=0.05,
            unit_id="percentunit",
            min_=0,
            max_=1,
            description="Distribution of PIPE_TENSOR_ACTIVE samples, bucketed in 0.05 (5-percentage-"
            "point) bands. options.calculate:true for the same reason as panel 29. A "
            "training/inference workload that keeps tensor cores consistently saturated "
            "shows a tight band near 1.0; one with frequent host-side stalls or "
            "non-tensor-eligible ops shows samples smeared across the low end too. min/max "
            "pinned to 0-1 for the same idle-degenerate-range reason as panel 29.",
        )
    )
    panels.append(
        lib.timeseries(
            31,
            "Mem/Enc/Dec Util (legacy)",
            16,
            17,
            8,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_MEM_COPY_UTIL{f}"), legend=f"Mem Copy Util · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_ENC_UTIL{f}"), legend=f"Encoder Util · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_DEC_UTIL{f}"), legend=f"Decoder Util · {std}", ref_id="C"),
            ],
            unit_id="percent",
            min_=0,
            max_=100,
            thresholds_steps=lib.thresholds([(None, "blue"), (50, "green")]),
            description="Direct dashboard-14574-parity panel: the same three 0-100% utilization "
            "gauges nvidia-smi itself reports (memory-controller, NVENC, NVDEC), for "
            "anyone cross-checking against older nvidia-smi-based tooling or dashboards. "
            "See row J for per-instance video-engine detail (ENC_UTIL/DEC_UTIL here are "
            "already aggregated across all encoder/decoder instances).",
        )
    )

    return row_def, panels
