# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row C -- GPU Inventory + Fleet Live Status. Row id 20.

Two tables: the static inventory table (id 21) and a live per-GPU status table,
"Fleet Live Status" (id 97), so a fleet operator can answer "which GPU(s) need
attention right now" without scrolling past a full per-GPU repeat block (row
B). Panel 97 sits above the static inventory table (relative y=1; the
inventory table follows at y=9); both are h=6 rather than a taller h=8, since
h=8 reads as a tall, mostly-empty panel with only 1-3 GPUs selected (the common
case in this environment and in most single/few-GPU deployments), while h=6
still comfortably fits ~5 rows before an internal scrollbar kicks in for larger
fleets.

GPU Inventory (id 21) intentionally drops DCGM_FI_DEV_BOARD_SERIAL and
DCGM_FI_SYSTEM_NVML_VERSION from the displayed columns (they are in
NOISE_FIELDS, not IDENTITY_FIELDS) -- the two lowest-value, rarely-needed
identity columns, freeing enough width that the remaining ~17 columns fit a
24-wide panel without a forced horizontal scroll for 1-8 GPUs. Every remaining
column gets an explicit `custom.width` so layout doesn't depend on Grafana's
auto-width guess. Both tables build on the shared `lib.table_join()` helper
(the general form of the join-by-UUID + organize recipe used here, in
row_i.py, and in row_l.py) rather than each hand-rolling its own copy of that
logic.

Panel 97 columns: identity (Host/GPU/Model) plus five live per-GPU numbers
(Util, Tensor Active, Temp, Power, VRAM Used) and two decision columns
(Throttled, Health -- reusing row A panel 8's own Pass/Warn/Fail mapping and
real-throttle-bit expression, not reinvented). Default-sorted Health desc, then
Temp desc, so the worst GPU floats to row 1. A field data-link drilldown to
re-scope $gpu to a clicked row's GPU (re-scoping $gpu correctly narrows row B's
repeat to exactly that GPU with zero row-B code changes) was considered but
deferred: Grafana's `${__data.fields.*}` field link templating is not
empirically verified against this environment yet, so it is left as a
follow-up rather than shipped unverified.

Panel 97's per-GPU value columns go through `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section). Panel 21 (GPU Inventory) is left
unaggregated on purpose: it is an identity table that displays CSV
label-type fields (driver/VBIOS/serial/brand/NVML) as real columns, and
`lib.per_gpu()` would aggregate those away -- same exception as row_l.py's
Fabric/IMEX/C2C and MIG tables. Every one of its raw targets instead goes
through `lib.latest_per_gpu()`, which keeps only the most-recently-sampled
series per GPU (defensive against a backend without Prometheus's own
instant-query staleness markers, e.g. VictoriaMetrics -- see CONVENTIONS.md)
without aggregating any label away, so the CSV columns this table exists to
show still render.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    inner = f[1:-1]
    row_def = lib.row(20, "GPU Inventory", collapsed=False)

    panels: list[dict[str, Any]] = []

    # ---- id 97: Fleet Live Status (new, completeness gap #1) --------------
    # Every expr below goes through lib.per_gpu() (see CONVENTIONS.md's
    # series-identity section): unlike the GPU Inventory table below, this
    # table's identity columns (Host/GPU/Model) are all in lib.GPU_ID_LABELS
    # already -- none of them is a CSV label-type field -- so aggregating away
    # the CSV churn costs this table nothing, and fixes the same duplicate-row
    # risk a driver/VBIOS upgrade would otherwise cause here.
    fault_throttle_terms = " + ".join(
        lib.clock_event_bit_expr(f"DCGM_FI_DEV_CLOCKS_EVENT_REASONS{f}", bit) for bit in lib.REAL_FAULT_THROTTLE_BITS
    )
    throttled_expr = lib.per_gpu(f"(({fault_throttle_terms}) > bool 0)")
    power_capped_expr = lib.per_gpu(
        f"({lib.clock_event_bit_expr(f'DCGM_FI_DEV_CLOCKS_EVENT_REASONS{f}', 0x004)} > bool 0)"
    )
    # UUID alone is sufficient here (the join key, and this frame contributes no
    # display identity column -- those come from frame 1 below), so this is left
    # as its own pre-existing `max by (UUID)`, already immune to the CSV split.
    health_expr = f"max by (UUID) (DCGM_EXP_GPU_HEALTH_STATUS{{{inner}}})"

    fleet_panel = lib.table_join(
        97,
        "Fleet Live Status",
        0,
        1,
        24,
        8,
        exprs=[
            lib.per_gpu(f"DCGM_FI_DEV_GPU_UTIL{f}"),
            f"100*{lib.per_gpu(f'DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{f}')}",
            lib.per_gpu(f"DCGM_FI_DEV_GPU_TEMP{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_POWER_USAGE{f}"),
            f"100*{lib.per_gpu(f'DCGM_FI_DEV_FB_USED_RATIO{f}')}",
            throttled_expr,
            power_capped_expr,
            health_expr,
        ],
        identity_fields={"hostname": "Host", "gpu": "GPU", "modelName": "Model"},
        noise_fields=[
            "Time",
            "job",
            "device",
            "host",
            "__name__",
            "instance",
            "pci_bus_id",
            "DCGM_FI_DEV_GPU_BRAND",
            "DCGM_FI_DEV_BOARD_SERIAL",
            "DCGM_FI_DRIVER_VERSION",
            "DCGM_FI_DEV_VBIOS_VERSION",
            "DCGM_FI_SYSTEM_NVML_VERSION",
            "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES",
            "DCGM_FI_DEV_FABRIC_CLUSTER_UUID",
            "DCGM_FI_IMEX_DOMAIN_STATUS",
        ],
        value_renames=[
            "GPU Util (%)",
            "Tensor Active (%)",
            "Temp (C)",
            "Power (W)",
            "VRAM Used (%)",
            "Throttled",
            "Power Capped",
            "Health",
        ],
        overrides=[
            lib.override_by_name(
                "Health",
                [
                    ("mappings", lib.health_status_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 90),
                ],
            ),
            lib.override_by_name(
                "Throttled",
                [
                    ("mappings", lib.value_mapping([(0, "OK", "green"), (1, "Throttled", "orange")])),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 110),
                ],
            ),
            lib.override_by_name(
                "Power Capped",
                [
                    ("mappings", lib.value_mapping([(0, "OK", "green"), (1, "At Cap", "blue")])),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 110),
                ],
            ),
            lib.override_by_name(
                "Temp (C)",
                [
                    ("unit", lib.unit("celsius")),
                    ("thresholds", lib.no_thresholds("gray")),
                    ("custom.width", 100),
                ],
            ),
            lib.override_by_name("GPU Util (%)", [("unit", lib.unit("percent")), ("custom.width", 100)]),
            lib.override_by_name("Tensor Active (%)", [("unit", lib.unit("percent")), ("custom.width", 130)]),
            lib.override_by_name("Power (W)", [("unit", lib.unit("watt")), ("custom.width", 100)]),
            lib.override_by_name("VRAM Used (%)", [("unit", lib.unit("percent")), ("custom.width", 120)]),
            lib.override_by_name("Host", [("custom.width", 110)]),
            lib.override_by_name("GPU", [("custom.width", 60)]),
            lib.override_by_name("Model", [("custom.width", 220)]),
        ],
        description="Live per-GPU fleet status, one row per GPU: at-a-glance Util/Tensor-Active/Temp/"
        "Power/VRAM plus three decision columns -- Throttled (any of the 4 real-fault throttle "
        "bits, same as row A's 'Fault-throttled' value), Power Capped (SW_POWER_CAP alone, "
        "shown separately and never colored as a fault since hitting the configured power limit "
        "under full load is expected), and Health (Pass/Warn/Fail, same mapping as row A panel "
        "8 and the Reliability row's incident table). Temp (C) is informational only (gray, no "
        "fixed threshold) -- a fixed Celsius band is not portable across GPU models with "
        "different slowdown points; see panel 5/17 for the portable percent-of-slowdown "
        "version. Answers 'which GPU(s) need attention right now' without scrolling past a full "
        "per-GPU repeat block (row B) on a multi-host/multi-GPU fleet. Sorted Health desc, then "
        "Temp desc by default so the worst GPU floats to the top.",
    )
    fleet_panel["options"]["sortBy"] = [
        {"displayName": "Health", "desc": True},
        {"displayName": "Temp (C)", "desc": True},
    ]
    panels.append(fleet_panel)

    # ---- id 21: GPU Inventory (static identity), relative y=9, h=6 --------
    cc_major, cc_minor = lib.cuda_compute_capability_major_minor(f"DCGM_FI_CUDA_GPU_COMPUTE_CAPABILITY{f}")
    cuda_major, cuda_minor = lib.cuda_driver_version_major_minor(f"DCGM_FI_CUDA_DRIVER_VERSION{f}")

    cc_field = f"DCGM_FI_CUDA_GPU_COMPUTE_CAPABILITY{f}"
    cuda_field = f"DCGM_FI_CUDA_DRIVER_VERSION{f}"
    inventory_panel = lib.table_join(
        21,
        "GPU Inventory",
        0,
        9,
        24,
        6,
        exprs=[
            # Every target here is raw/unaggregated on purpose (see the module
            # docstring above) -- lib.per_gpu() would aggregate away the CSV
            # label-type identity columns this table exists to show. Each one goes
            # through lib.latest_per_gpu() instead: it keeps only the
            # most-recently-sampled series per GPU (defensive against a backend
            # without Prometheus's own instant-query staleness markers -- see
            # CONVENTIONS.md's series-identity section) without collapsing any label.
            lib.latest_per_gpu(
                f"DCGM_FI_DEV_GPU_UTIL{f}"
            ),  # base: carries the full label set (identity columns), not displayed
            lib.latest_per_gpu(f"DCGM_FI_DEV_FB_TOTAL{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_ECC_MODE{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_MIG_MODE{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_GPU_VIRTUAL_MODE{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_COMPUTE_MODE{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_PERSISTENCE_MODE{f}"),
            lib.latest_per_gpu(f"DCGM_FI_DEV_CC_MODE{f}"),
            lib.latest_per_gpu(cc_major, base=cc_field),
            lib.latest_per_gpu(cc_minor, base=cc_field),
            lib.latest_per_gpu(cuda_major, base=cuda_field),
            lib.latest_per_gpu(cuda_minor, base=cuda_field),
        ],
        identity_fields={
            "modelName": "Model",
            "pci_bus_id": "PCI Bus ID",
            "hostname": "Hostname",
            "instance": "Instance",
            "DCGM_FI_DEV_GPU_BRAND": "Brand",
            "DCGM_FI_DRIVER_VERSION": "Driver Version",
            "DCGM_FI_DEV_VBIOS_VERSION": "VBIOS Version",
        },
        # Board Serial / NVML Version are dropped from the default view (lowest-value,
        # rarely-needed identity columns) -- freed width is why the remaining ~17 columns
        # fit a 24-wide panel without a forced horizontal scroll for 1-8 GPUs.
        noise_fields=[
            "Time",
            "job",
            "device",
            "gpu",
            "host",
            "__name__",
            "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES",
            "DCGM_FI_DEV_FABRIC_CLUSTER_UUID",
            "DCGM_FI_IMEX_DOMAIN_STATUS",
            "DCGM_FI_DEV_BOARD_SERIAL",
            "DCGM_FI_SYSTEM_NVML_VERSION",
        ],
        value_renames=[
            None,  # GPU_UTIL was only the join's anchor query, not a displayed column
            "FB Total (MiB)",
            "ECC Mode",
            "MIG Mode",
            "Virtualization Mode",
            "Compute Mode",
            "Persistence Mode",
            "CC Mode",
            "CUDA Compute Capability Major",
            "CUDA Compute Capability Minor",
            "CUDA Driver Major",
            "CUDA Driver Minor",
        ],
        overrides=[
            lib.override_by_name(
                "ECC Mode",
                [
                    ("mappings", lib.ecc_mode_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 90),
                ],
            ),
            lib.override_by_name(
                "MIG Mode",
                [
                    ("mappings", lib.bool_config_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 90),
                ],
            ),
            lib.override_by_name(
                "Virtualization Mode",
                [
                    ("mappings", lib.virtual_mode_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 150),
                ],
            ),
            lib.override_by_name(
                "Compute Mode",
                [
                    ("mappings", lib.compute_mode_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 150),
                ],
            ),
            lib.override_by_name(
                "Persistence Mode",
                [
                    ("mappings", lib.bool_config_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 130),
                ],
            ),
            lib.override_by_name(
                "CC Mode",
                [
                    ("mappings", lib.bool_config_mappings()),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 90),
                ],
            ),
            lib.override_by_name("FB Total (MiB)", [("unit", lib.unit("mbytes")), ("custom.width", 110)]),
            lib.override_by_name("Model", [("custom.width", 210)]),
            lib.override_by_name("Hostname", [("custom.width", 110)]),
            lib.override_by_name("Instance", [("custom.width", 120)]),
            lib.override_by_name("PCI Bus ID", [("custom.width", 130)]),
            lib.override_by_name("Brand", [("custom.width", 90)]),
            lib.override_by_name("Driver Version", [("custom.width", 110)]),
            lib.override_by_name("VBIOS Version", [("custom.width", 110)]),
            lib.override_by_name("CUDA Compute Capability Major", [("custom.width", 70)]),
            lib.override_by_name("CUDA Compute Capability Minor", [("custom.width", 70)]),
            lib.override_by_name("CUDA Driver Major", [("custom.width", 90)]),
            lib.override_by_name("CUDA Driver Minor", [("custom.width", 90)]),
        ],
        description="One row per GPU: identity, driver/VBIOS versions, decoded CUDA compute capability and "
        "driver version, FB total, and mode flags (cell-colored). Board Serial and NVML Version "
        "are intentionally not shown here (lowest-value, rarely-needed columns, dropped to keep "
        "this table scrollbar-free) -- both remain queryable directly via DCGM_FI_DEV_BOARD_SERIAL "
        "/ DCGM_FI_SYSTEM_NVML_VERSION in Explore if actually needed.",
    )
    panels.append(inventory_panel)

    return row_def, panels
