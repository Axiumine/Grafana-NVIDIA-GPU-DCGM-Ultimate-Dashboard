# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row I -- PCIe.

Row id 63. Child ids 64-67. The PCIe link table (panel 66) needs room for 8
columns without a horizontal scroll, which a narrower panel can't give it, so
this row is laid out across three full-height sub-rows instead of two:
  - Sub-row 1 (rel y=1, h=8): 64 (throughput, w16), 65 (bytes-moved stat, w8).
  - Sub-row 2 (rel y=9, h=8): 66 (link gen/width table), full width (w24).
  - Sub-row 3 (rel y=17, h=8): 67 (replay/correctable-error rate, w24).

Panel 66's "Gen OK" column is range-gated, not an instant check: PCIe ASPM /
dynamic link-speed power management legitimately drops the link to Gen1 at
idle on every modern NVIDIA GPU -- comparing only the CURRENT instant
LINK_GEN to MAX_LINK_GEN flags every idle, perfectly healthy GPU as
"Downgraded", always, not occasionally (confirmed live: idle GPU_UTIL=0,
LINK_GEN=1, MAX_LINK_GEN=5, on every GPU in this environment). The check
instead only flags a link that never reached its max generation anywhere in
the visible range: `max_over_time(LINK_GEN[$__range]) >= bool MAX_LINK_GEN`.
Width is NOT range-gated -- an x8-on-an-x16-card downgrade is a real,
persistent problem and should stay flagged immediately, unlike Gen which
legitimately oscillates with P-state. The un-gated, raw "PCIe Gen (current)"
column is kept alongside (informational, uncolored) so the idle Gen1 value is
still visible without being misread as a fault.

The table's identity columns are a compact Host/GPU pair, not
Instance/Model/PCI Bus ID: Model and PCI Bus ID already live in the GPU
Inventory table, and Instance alone cannot disambiguate 2 GPUs on the same
host (an identity set that silently relies on PCI Bus ID differing per GPU
happens to work but isn't a real disambiguator). Every column gets an
explicit `custom.width` so 8 columns comfortably fit the full-width (w24)
panel with no forced horizontal scroll.

Built on the shared `lib.table_join()` helper rather than a row-local copy of
the join-by-UUID + organize recipe.

Every metric in this row (64-67) goes through `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section) so a dcgm-exporter CSV label-set
change (new driver/VBIOS) can't draw two lines, or two rows for the same GPU,
for a while. Panel 66's table gets the same treatment as 64/65/67, unlike the
CSV-label-displaying inventory tables (21/87/90): its identity columns are
just Host/GPU, so aggregating away the CSV label fields costs it nothing, and
it fixes the same duplicate-row risk. Panel 65's increase(...[$__range]) is
aggregated with `agg="sum"` (a range-integrating counter -- see
lib.per_gpu()'s docstring), not the default "max", since the old and new
series each cover only part of the range.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(63, "PCIe", collapsed=False)

    panels: list[dict[str, Any]] = []

    # -- Sub-row 1 (rel y=1, h=8) --------------------------------------

    panels.append(
        lib.timeseries(
            64,
            "PCIe throughput (real, PROF)",
            0,
            1,
            16,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PCIE_TX_BYTES{f}"), legend=f"TX · {std}", ref_id="A"),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_PCIE_RX_BYTES{f}"), legend=f"RX · {std}", ref_id="B"),
            ],
            unit_id="Bps",
            thresholds_steps=lib.no_thresholds("blue"),
            description="True PCIe TX/RX byte throughput measured by DCGM's profiling counters, including "
            "protocol overhead -- both fields are already a rate (bytes/s), never wrap them in "
            "rate()/increase(). Superior to nvidia-smi-based dashboards, which can only estimate "
            "PCIe throughput from link-speed x utilization%. Informational activity metric: "
            "busier is normal, not a fault, hence no red threshold.",
        )
    )
    panels.append(
        lib.stat(
            65,
            "PCIe total bytes moved (this range)",
            16,
            1,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_PROF_PCIE_TX_BYTES_TOTAL{f}[$__range])", agg="sum"),
                    legend=f"TX · {std}",
                    ref_id="A",
                    instant=True,
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_PROF_PCIE_RX_BYTES_TOTAL{f}[$__range])", agg="sum"),
                    legend=f"RX · {std}",
                    ref_id="B",
                    instant=True,
                ),
            ],
            unit_id="bytes",
            thresholds_steps=lib.no_thresholds(),
            description="Cumulative PCIe bytes moved over the dashboard's selected time range, computed with "
            "increase() over DCGM's ever-growing PCIe TX/RX byte counters -- a counter-based "
            "cross-check of panel 64's instantaneous rate (the two should agree: this stat "
            "roughly equals panel 64's average rate times the range length).",
        )
    )

    # -- Sub-row 2 (rel y=9, h=8): PCIe link generation/width table ----------

    # Every column here is wrapped in lib.per_gpu() (see CONVENTIONS.md's
    # series-identity section): unlike the CSV-label-displaying inventory tables
    # (21/87/90), this table's identity is just Host/GPU, so aggregating away the
    # CSV label fields loses nothing it needs -- and it also collapses the
    # gen/width comparisons below to one row per GPU instead of briefly showing
    # duplicate rows across a driver/VBIOS upgrade.
    link_gen = lib.per_gpu(f"DCGM_FI_DEV_PCIE_LINK_GEN{f}")
    max_link_gen = lib.per_gpu(f"DCGM_FI_DEV_PCIE_MAX_LINK_GEN{f}")
    link_width = lib.per_gpu(f"DCGM_FI_DEV_PCIE_LINK_WIDTH{f}")
    max_link_width = lib.per_gpu(f"DCGM_FI_DEV_PCIE_MAX_LINK_WIDTH{f}")
    # max_over_time(...) itself stays over the raw selector (an exact range-vector
    # function call, not a subquery) -- lib.per_gpu() wraps the WHOLE
    # max_over_time(...) result (agg="max", the matching aggregator for a
    # max_over_time per CONVENTIONS.md/lib.per_gpu()'s own docstring), not its
    # operand, so this stays one plain range-vector selector, not a subquery.
    gen_max_over_time = lib.per_gpu(f"max_over_time(DCGM_FI_DEV_PCIE_LINK_GEN{f}[$__range])")
    gen_ok_expr = f"({gen_max_over_time}) >= bool ({max_link_gen})"
    width_ok_expr = f"({link_width}) >= bool ({max_link_width})"
    ok_map_gen = lib.value_mapping([(0, "Downgraded", "red"), (1, "OK", "green")])
    ok_map_width = lib.value_mapping([(0, "Downgraded", "red"), (1, "OK", "green")])

    panel_66 = lib.table_join(
        66,
        "PCIe link generation/width",
        0,
        9,
        24,
        8,
        exprs=[
            link_gen,
            max_link_gen,
            link_width,
            max_link_width,
            gen_ok_expr,
            width_ok_expr,
        ],
        identity_fields={"hostname": "Host", "gpu": "GPU"},
        noise_fields=[
            "Time",
            "job",
            "device",
            "host",
            "__name__",
            "instance",
            "modelName",
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
            "PCIe Gen (current)",
            "PCIe Gen (max)",
            "Link Width (current)",
            "Link Width (max)",
            "Gen OK (range-checked)",
            "Width OK",
        ],
        overrides=[
            lib.override_by_name(
                "Gen OK (range-checked)",
                [
                    ("mappings", ok_map_gen),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 170),
                ],
            ),
            lib.override_by_name(
                "Width OK",
                [
                    ("mappings", ok_map_width),
                    ("custom.cellOptions", {"type": "color-background"}),
                    ("custom.width", 110),
                ],
            ),
            lib.override_by_name("Host", [("custom.width", 120)]),
            lib.override_by_name("GPU", [("custom.width", 60)]),
            lib.override_by_name("PCIe Gen (current)", [("custom.width", 150)]),
            lib.override_by_name("PCIe Gen (max)", [("custom.width", 130)]),
            lib.override_by_name("Link Width (current)", [("custom.width", 160)]),
            lib.override_by_name("Link Width (max)", [("custom.width", 140)]),
        ],
        description="Current negotiated PCIe generation/width against the maximum this GPU and slot both "
        "support. 'Gen OK' is range-gated (`max_over_time(current[$__range]) >= max`) "
        "rather than an instant comparison -- PCIe ASPM/dynamic link-speed power management "
        "legitimately drops the link to Gen1 at idle on every modern NVIDIA GPU (confirmed live "
        "on this environment's idle GPUs), so an instant check flagged every healthy idle GPU as "
        "'Downgraded' permanently, not occasionally. The raw, un-gated 'PCIe Gen (current)' "
        "column is kept alongside so the idle Gen1 reading is still visible, just not colored as "
        "a fault. 'Width OK' stays an instant check on purpose: a real x8-on-an-x16-slot "
        "downgrade does not fluctuate with P-state the way Gen does, and should stay flagged "
        "immediately.",
    )
    panels.append(panel_66)

    # -- Sub-row 3 (rel y=17, h=8) ---------------------------------------

    panels.append(
        lib.timeseries(
            67,
            "PCIe replay & correctable-error rate",
            0,
            17,
            24,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_PCIE_REPLAY_COUNTER{f}[$__rate_interval])"),
                    legend=f"Replays · {std}",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_PCIE_CORRECTABLE_ERROR_TOTAL{f}[$__rate_interval])"),
                    legend=f"Correctable errors · {std}",
                    ref_id="B",
                ),
            ],
            unit_id="short",
            thresholds_steps=lib.thresholds([(None, "green"), (1, "red")]),
            description="increase() of the cumulative PCIe link-layer replay (retry) counter and AER "
            "correctable-error counter over each windowed interval -- never the raw ever-growing "
            "counter with a static threshold, which could only turn red once and then stay red "
            "forever. Any nonzero rate here is worth investigating: marginal signal integrity, a "
            "loose riser/cable, or a failing slot. Distinct signals: replays are link-layer "
            "retries, AER correctable errors are a separate PCIe-spec error-reporting mechanism.",
        )
    )

    return row_def, panels
