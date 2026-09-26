# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row J -- Video Engines (ENC/DEC/NVDEC/NVJPG/OFA).

Row id 68, y:144. Child ids 69-73.
Sub-row 1 relative y=1, h=8: 69,70,71.
Sub-row 2 relative y=9, h=8: 72,73.

Panels 70/71 use a "legend density" treatment (short per-series token instead
of the full {{std}} legend, table-mode legend on the right, sorted by last
value) -- 8 series families x N selected GPUs would otherwise produce up to
8xN near-duplicate rows with the full legend string.

Panels 70/71's per-instance legend uses `ctx.legend_std`
(`{{hostname}} GPU{{gpu}}`), which includes the GPU index, rather than
`NVDEC{i} · {{instance}}`: two GPUs on the same host share one `instance`
label, so an instance-only legend renders identical text (e.g.
"NVDEC0 · simhost:9400") for two physically different GPUs.

Every metric in this row goes through `lib.per_gpu()` (see CONVENTIONS.md's
series-identity section) so a dcgm-exporter CSV label-set change (new driver/
VBIOS) can't draw two lines with the same legend for a while.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(68, "Video Engines (ENC/DEC/NVDEC/NVJPG/OFA)", collapsed=False)

    panels: list[dict[str, Any]] = []

    # -- Sub-row 1 (rel y=1, h=8) --------------------------------------

    panels.append(
        lib.timeseries(
            69,
            "Encoder/Decoder utilization (coarse)",
            0,
            1,
            8,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_ENC_UTIL{f}"), legend=f"Encoder · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_DEC_UTIL{f}"), legend=f"Decoder · {std}", ref_id="B"),
            ],
            unit_id="percent",
            thresholds_steps=lib.no_thresholds("blue"),
            description="Coarse encoder/decoder utilization (0-100%), aggregated across all NVENC/NVDEC "
            "instances on the GPU -- direct feature parity with nvidia-smi's "
            "utilization.encoder/decoder. See panels 70/71 for the fine per-instance breakdown "
            "only DCGM can provide. Activity metric: busier is normal, no red threshold.",
        )
    )

    nvdec_targets = [
        lib.target(lib.per_gpu(f"DCGM_FI_PROF_NVDEC_UTIL_{i}_RATIO{f}"), legend=f"NVDEC{i} · {std}", ref_id=rid)
        for i, rid in enumerate(lib._excel_ref_ids(8))
    ]
    panels.append(
        lib.timeseries(
            70,
            "NVDEC per-instance activity",
            8,
            1,
            8,
            8,
            nvdec_targets,
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.no_thresholds("blue"),
            legend_mode="table",
            legend_placement="right",
            legend_calcs=["lastNotNull"],
            legend_sort_by="Last *",
            legend_sort_desc=True,
            description="Per-instance NVDEC (hardware video decoder) activity ratio, one line per physical "
            "NVDEC engine on the GPU. Only physically-present instances report a value -- a GPU "
            "with 2 NVDEC engines shows 2 lines, not 8 empty ones; DCGM_FI_DEV_DEC_UTIL (panel 69) "
            "is the coarse aggregate of these. No nvidia-smi equivalent exists at any granularity.",
        )
    )

    nvjpg_targets = [
        lib.target(lib.per_gpu(f"DCGM_FI_PROF_NVJPG_UTIL_{i}_RATIO{f}"), legend=f"NVJPG{i} · {std}", ref_id=rid)
        for i, rid in enumerate(lib._excel_ref_ids(8))
    ]
    panels.append(
        lib.timeseries(
            71,
            "NVJPG per-instance activity",
            16,
            1,
            8,
            8,
            nvjpg_targets,
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.no_thresholds("blue"),
            legend_mode="table",
            legend_placement="right",
            legend_calcs=["lastNotNull"],
            legend_sort_by="Last *",
            legend_sort_desc=True,
            description="Per-instance NVJPG (hardware JPEG decoder) activity ratio, one line per physical "
            "NVJPG engine on the GPU. Only physically-present instances report a value. Relevant "
            "to image-heavy inference/preprocessing pipelines; nvidia-smi has no equivalent field "
            "at all (not even a coarse aggregate).",
        )
    )

    # -- Sub-row 2 (rel y=9, h=8) ---------------------------------------

    panels.append(
        lib.timeseries(
            72,
            "NVOFA (optical flow) activity",
            0,
            9,
            12,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_NVOFA_UTIL_0_RATIO{f}"), legend=f"OFA0 · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_NVOFA_UTIL_1_RATIO{f}"), legend=f"OFA1 · {std}", ref_id="B"),
            ],
            unit_id="percentunit",
            min_=0,
            max_=1,
            thresholds_steps=lib.no_thresholds("blue"),
            description="Per-instance NVOFA (Optical Flow Accelerator) activity -- the hardware block used by "
            "frame-interpolation/motion-estimation workloads and some video-analytics pipelines. "
            "Absent/0 on GPUs without an OFA engine or when nothing is using it. Niche but "
            "genuinely new versus every reference dashboard (14574/22424/25526).",
        )
    )
    panels.append(
        lib.timeseries(
            73,
            "Host/Peer memory cache hit-rate",
            12,
            9,
            12,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_HOSTMEM_CACHE_HIT{f}"), legend=f"Host hit · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_HOSTMEM_CACHE_MISS{f}"), legend=f"Host miss · {std}", ref_id="B"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PEERMEM_CACHE_HIT{f}"), legend=f"Peer hit · {std}", ref_id="C"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PEERMEM_CACHE_MISS{f}"), legend=f"Peer miss · {std}", ref_id="D"),
            ],
            unit_id="percent",
            thresholds_steps=lib.no_thresholds(),
            description="Host-memory and peer-GPU-memory cache hit/miss rates from DCGM's profiling counters. "
            "Only meaningful on Grace-Hopper/Grace-Blackwell C2C superchips or multi-GPU "
            "peer-access workloads -- reads 0/absent on a plain PCIe-attached single GPU like this "
            "environment's RTX PRO 4000; that is expected, not a fault.",
        )
    )

    return row_def, panels
