# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row L -- Datacenter / hardware-specific. Row id 86, collapsed=True by default.

Child `y` is relative to 0 inside this row panel's own `panels` array (0-based
local coordinates -- no offset needed, unlike an expanded row).

Every panel here reads 0/absent on this test environment (one workstation GPU, no
NVLink/NVSwitch fabric, no C2C/superchip, no MIG, no vGPU, no workload power
profiles) and becomes real on the matching datacenter/superchip hardware -- that is
the whole point of collapsing this row by default. EMPTY here is the correct,
expected result for every target in this row.

Id **95** ("C2C link power status") is intentionally out of numeric sequence
between 88 and 89: its original id designator was not a valid Grafana integer
panel id, so it was assigned the next free id instead (see CONVENTIONS.md's
id-range table).

Multi-target table panels (87 Fabric/IMEX/C2C, 90 MIG, 92 Power profiles, 94 NVLink
health) all follow row_c.py's join-by-UUID + organize pattern: every DCGM_FI_DEV_*
target shares the same identity label set (job/instance/host/device/gpu/UUID/
modelName/pci_bus_id/__name__), so after `transform_join_by_field("UUID")` every one
of those names is suffixed "<field> <1-based frame index>" for every occurrence
(see CONVENTIONS.md's table-join-naming gotcha) -- `lib.table_join()` is the
shared, generalized version of that pattern. **Panel 90 is the one exception**: its
rows are one per *MIG instance*, not one per GPU, and two instances on the same
physical GPU share the same UUID -- a bare "UUID" join key would silently re-collide
them (Grafana's joinByField assumes its key is unique per input frame). Every one of
panel 90's 5 targets is instead wrapped in `_mig_key()` (a PromQL `label_join(...,
"mig_key", "/", "UUID", "GPU_I_ID")`) and `table_join(..., join_field="mig_key")`
joins on that composite key -- see `lib.table_join()`'s `join_field` docstring. CSV
`label`-type fields
(DCGM_FI_DEV_FABRIC_CLUSTER_UUID, DCGM_FI_IMEX_DOMAIN_STATUS,
DCGM_FI_CUDA_GPU_VISIBLE_DEVICES) are not separate metrics -- dcgm-exporter
attaches them as extra Prometheus labels on every *other* metric for that GPU
(confirmed against dcgm-exporter 4.8.4 source), so they are read off frame 1 of
whichever numeric targets are already being queried, exactly like row_c.py
reads DCGM_FI_DEV_GPU_BRAND off its base identity query.

Legends on panels 88/89/93 use `ctx.legend_std` (`{{hostname}} GPU{{gpu}}`)
rather than bare `{{modelName}}`: modelName alone cannot disambiguate multiple
identically-modelled GPUs on a fleet with more than one of the same model
(confirmed live: several such GPUs all returning non-absent 0-valued series,
not truly absent, so the collision is real even without exotic hardware).
Panels 87 and 94 (the two other wide, easily-empty tables in this row) get
explicit column widths and h reduced from 8 to 6; panels 91 and 95
(single-value stats) get h reduced from 8 to 4 to shrink the empty space
around a one-word status tile.

Panel 90 (MIG) is implemented as a single `table` panel, not a combined
table+stat display (Grafana's core table panel has no such mode). Gating
visibility on MIG_MODE==1 is done at the PromQL level (the anchor target is
`DCGM_FI_DEV_MIG_MODE{...} == 1`, joined with `mode="inner"`) rather than a
client-side table filter transform: with MIG disabled/absent (this
environment), the anchor query itself returns no series, the inner join
yields zero rows, and the panel correctly renders empty rather than a row of
mostly-N/A MIG fields.

Every non-anchor per-GPU query in this row goes through `lib.per_gpu()` (see
CONVENTIONS.md's series-identity section), same as every other row, so a
dcgm-exporter CSV label-set change (new driver/VBIOS) can't render one
GPU/link/instance as two lines or two table rows for a while. The two
multi-target tables whose FIRST frame promotes a CSV label-type field to a
real display column -- 87 (DCGM_FI_DEV_FABRIC_CLUSTER_UUID/DCGM_FI_IMEX_
DOMAIN_STATUS) and 90 (DCGM_FI_CUDA_GPU_VISIBLE_DEVICES) -- leave that one
anchor frame raw/unaggregated, exactly like row_c.py's GPU Inventory table:
per_gpu() would aggregate away the very CSV field the table exists to show.
That anchor frame goes through `lib.latest_per_gpu()` instead (keeps only the
most-recently-sampled series per GPU, defensive against a backend without
Prometheus's own instant-query staleness markers, without collapsing any
label -- see CONVENTIONS.md). Every *other* frame in those two tables, and
every frame of 92/94 (which promote no CSV label field), is wrapped normally
in `lib.per_gpu()`.
"""

from typing import Any

from .. import lib

_table_join = lib.table_join  # local alias, kept so the calls below read unchanged

_NOISE = [
    "Time",
    "job",
    "device",
    "gpu",
    "host",
    "__name__",
    # dcgm-exporter labels attached to every metric (not separate value columns) --
    # confirmed live leaking through as stray unexcluded columns on panel 94
    # (mirrors the same fix in row_i.py). NOTE: DCGM_FI_CUDA_GPU_VISIBLE_DEVICES /
    # DCGM_FI_DEV_FABRIC_CLUSTER_UUID / DCGM_FI_IMEX_DOMAIN_STATUS are deliberately
    # NOT in this shared list -- panels 87/90 promote them to real identity columns
    # via their own per-call `identity_fields` dict, so blanket-excluding them here
    # would silently defeat that rename for those two panels. Panels 92/94 (which
    # don't need them) still get them via their own extra noise_fields below.
    "DCGM_FI_DEV_GPU_BRAND",
    "DCGM_FI_DEV_BOARD_SERIAL",
    "DCGM_FI_DRIVER_VERSION",
    "DCGM_FI_DEV_VBIOS_VERSION",
    "DCGM_FI_SYSTEM_NVML_VERSION",
]
_LABEL_NOISE = ["DCGM_FI_CUDA_GPU_VISIBLE_DEVICES", "DCGM_FI_DEV_FABRIC_CLUSTER_UUID", "DCGM_FI_IMEX_DOMAIN_STATUS"]
_IDENTITY = {
    "modelName": "Model",
    "instance": "Instance",
    "pci_bus_id": "PCI Bus ID",
    # Missing here previously: "hostname" fell through unrenamed/unexcluded and
    # leaked into every table_join panel in this row as a stray "hostname 1"
    # (or "hostname 2".."hostname N") column, confirmed live on panel 87.
    "hostname": "Hostname",
}


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    std = ctx.legend_std
    row_def = lib.row(86, "Datacenter / hardware-specific", collapsed=True)
    panels: list[dict[str, Any]] = []

    # ---- id 87: Fabric Manager / IMEX / C2C (table, x0 y0 w24 h6) --------
    # h reduced 8->6 (tall/mostly-empty on a small fleet); identity columns
    # get explicit widths so the table doesn't need a horizontal scroll.
    fabric_identity = dict(_IDENTITY)
    fabric_identity["DCGM_FI_DEV_FABRIC_CLUSTER_UUID"] = "Fabric Cluster UUID"
    fabric_identity["DCGM_FI_IMEX_DOMAIN_STATUS"] = "IMEX Domain Status"
    fabric_panel = _table_join(
        87,
        "Fabric Manager / IMEX / C2C",
        0,
        0,
        24,
        6,
        exprs=[
            # Frame 1 (the identity anchor) is left as a raw selector, NOT wrapped in
            # lib.per_gpu(): fabric_identity below reads DCGM_FI_DEV_FABRIC_
            # CLUSTER_UUID/DCGM_FI_IMEX_DOMAIN_STATUS off *this* frame's own CSV
            # labels, and per_gpu()'s aggregation would aggregate those two away
            # (they aren't in lib.GPU_ID_LABELS) -- same "inventory table keeps its
            # label-type fields" rule as row_c.py's GPU Inventory table. It goes
            # through lib.latest_per_gpu() instead, to keep only the
            # most-recently-sampled series per GPU (defensive against a backend
            # without Prometheus's own instant-query staleness markers -- see
            # CONVENTIONS.md's series-identity section) without collapsing any
            # label. Every other frame below has no such conflict and is wrapped
            # normally in lib.per_gpu().
            lib.latest_per_gpu(f"DCGM_FI_DEV_FABRIC_MANAGER_STATUS{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_FABRIC_MANAGER_ERROR{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_FABRIC_CLIQUE_ID{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_FABRIC_HEALTH_MASK{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_FABRIC_HEALTH_SUMMARY{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_C2C_LINK_QUANTITY{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_C2C_LINK_STATUS{f}"),
            lib.per_gpu(f"DCGM_FI_IMEX_DAEMON_STATUS{f}"),
        ],
        identity_fields=fabric_identity,
        noise_fields=_NOISE + [n for n in _LABEL_NOISE if n not in fabric_identity],
        value_renames=[
            "Fabric Manager Status",
            "Fabric Manager Error",
            "Fabric Clique ID",
            "Fabric Health Mask",
            "Fabric Health Summary",
            "C2C Link Quantity",
            "C2C Link Status",
            "IMEX Daemon Status",
        ],
        overrides=[
            lib.override_by_name(
                "C2C Link Status",
                [
                    ("mappings", lib.bool_config_mappings(off_text="Inactive", on_text="Active")),
                    ("custom.cellOptions", {"type": "color-background"}),
                ],
            ),
            lib.override_by_name("Hostname", [("custom.width", 110)]),
            lib.override_by_name("Instance", [("custom.width", 120)]),
            lib.override_by_name("PCI Bus ID", [("custom.width", 130)]),
            lib.override_by_name("Fabric Cluster UUID", [("custom.width", 260)]),
        ],
        description="NVSwitch fabric (Fabric Manager) and Grace-Blackwell superchip (C2C, IMEX) inventory "
        "and status, consolidated into one row-per-GPU table. Fabric Manager fields are 0/absent "
        "without an NVSwitch fabric; C2C fields are 0/absent without a chip-to-chip superchip "
        "link; IMEX (multi-node memory export) fields read -1/absent when nvidia-imex is not "
        "running. All of the above is the expected, healthy state on this single workstation "
        "GPU -- every field here becomes meaningful on the matching NVSwitch/Grace-Blackwell "
        "datacenter hardware. C2C Link Status is the one enum decoded with a real mapping "
        "(0=Inactive, 1=Active); the rest are raw status/error codes without a published "
        "public decode table.",
    )
    panels.append(fabric_panel)

    # ---- id 88: C2C error counters (timeseries, x0 y8 w16 h8) -------------
    panels.append(
        lib.timeseries(
            88,
            "C2C error counters",
            0,
            8,
            16,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_C2C_LINK_ERROR_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · Errors",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_C2C_LINK_REPLAY_ERROR_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · Replay errors",
                    ref_id="B",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_C2C_LINK_REPLAY_ERROR_B2B_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · Replay errors (back-to-back)",
                    ref_id="C",
                ),
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Chip-to-chip (Grace-Blackwell superchip) link error counters. 0/no-data on this "
            "discrete workstation GPU -- expected. Back-to-back replay errors are broken out "
            "separately from isolated ones because a burst pattern (many replays in immediate "
            "succession) is a stronger link-degradation signal than the occasional isolated "
            "replay and is worth alerting on independently.",
        )
    )

    # ---- id 95 (spec "88b"): C2C link power status (stat, x16 y8 w8 h4) ---
    # Minor layout fix: h reduced 8->4 -- a single centered word ("Unlicensed"/status)
    # doesn't need a full-height panel; leaves the freed grid space blank rather than
    # stretching a one-line stat, addressing the collapsed row's empty-space imbalance.
    panels.append(
        lib.stat(
            95,
            "C2C link power status",
            16,
            8,
            8,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_C2C_LINK_POWER_STATUS{f}"), ref_id="A", instant=True)],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("gray"),
            description="Chip-to-chip link power state -- informational (no NVIDIA-published good/bad enum "
            "to map against, hence no color threshold). 0/absent without a C2C superchip link. "
            "This panel's id (95) is intentionally out of numeric sequence between 88 and 89 -- "
            "see CONVENTIONS.md's id-range table.",
        )
    )

    # ---- id 89: C2C throughput (timeseries, x0 y16 w24 h8) ----------------
    panels.append(
        lib.timeseries(
            89,
            "C2C throughput",
            0,
            16,
            24,
            8,
            [
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_C2C_TX_ALL_BYTES{f}"), legend=f"{std} · TX (all)", ref_id="A"),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_C2C_TX_DATA_BYTES{f}"), legend=f"{std} · TX (data only)", ref_id="B"
                ),
                lib.target(lib.per_gpu(f"DCGM_FI_PROF_C2C_RX_ALL_BYTES{f}"), legend=f"{std} · RX (all)", ref_id="C"),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_C2C_RX_DATA_BYTES{f}"), legend=f"{std} · RX (data only)", ref_id="D"
                ),
            ],
            unit_id="Bps",
            thresholds_steps=lib.no_thresholds("blue"),
            description="Chip-to-chip (Grace-Blackwell superchip) bandwidth, already expressed as an "
            "instantaneous byte rate by DCGM (no rate() needed -- these are PROF gauges, not "
            "counters). 'All' includes protocol overhead; 'data only' is the useful payload "
            "rate. Blue/informational: this is a throughput metric, not a fault axis (this "
            "dashboard's usual activity-metric color convention) -- 0/no-data is expected on "
            "this discrete workstation GPU.",
        )
    )

    # ---- id 90: MIG (table, x0 y24 w18 h8) --------------------------------
    mig_identity = dict(_IDENTITY)
    mig_identity["DCGM_FI_CUDA_GPU_VISIBLE_DEVICES"] = "CUDA Visible Devices"

    def _mig_key(expr: str) -> str:
        # lib.table_join()'s default join field is bare "UUID", which is IDENTICAL
        # across every MIG instance on the same physical GPU -- topk/per_gpu keep
        # instances distinct as separate raw series (GPU_I_ID), but joinByField
        # would still key only on UUID and collide them back together into one
        # ambiguous multi-row-per-key join. Every one of this panel's 5 targets
        # gets a synthetic composite key instead (harmless on a non-MIG GPU, where
        # GPU_I_ID is empty and "mig_key" == "<uuid>/"): join_field="mig_key" below
        # makes joinByField key on the actual per-row identity, while the plain
        # "UUID" column (read off mig_identity/frame 1, untouched) still shows the
        # clean physical UUID. "mig_key" itself is dropped via noise_fields.
        return f'label_join({expr}, "mig_key", "/", "UUID", "GPU_I_ID")'

    mig_panel = _table_join(
        90,
        "MIG",
        0,
        24,
        18,
        8,
        exprs=[
            # Frame 1 (anchor + gate) is left raw, same reason as panel 87's frame 1:
            # mig_identity below reads DCGM_FI_CUDA_GPU_VISIBLE_DEVICES off this
            # frame's own CSV labels. It goes through lib.latest_per_gpu() (base=
            # the un-filtered MIG_MODE selector, since `== 1` is a filter on it, not
            # a bare selector) to keep only the most-recently-sampled series per GPU
            # -- MIG instances stay distinct via GPU_I_ID -- without collapsing any
            # label, same defensive fix as panels 21/87 (see CONVENTIONS.md's
            # series-identity section). The other frames are wrapped normally in
            # lib.per_gpu() -- lib.GPU_ID_LABELS already includes GPU_I_ID/
            # GPU_I_PROFILE, so GI_INFO/CI_INFO (per-MIG-instance fields) keep each
            # instance on the same physical GPU as its own row instead of being
            # merged together. That per-series distinctness only reaches the
            # rendered table because every frame is also keyed by _mig_key() below
            # (see lib.table_join()'s join_field docstring) -- a bare "UUID" join
            # would silently re-collide two instances on one GPU at the join step.
            _mig_key(
                lib.latest_per_gpu(f"DCGM_FI_DEV_MIG_MODE{f} == 1", base=f"DCGM_FI_DEV_MIG_MODE{f}")
            ),  # anchor + gate: no row unless MIG is actually enabled
            _mig_key(lib.per_gpu(f"DCGM_FI_DEV_MIG_MAX_SLICES{f}")),
            _mig_key(lib.per_gpu(f"DCGM_FI_DEV_MIG_ATTRIBUTES{f}")),
            _mig_key(lib.per_gpu(f"DCGM_FI_DEV_MIG_GI_INFO{f}")),
            _mig_key(lib.per_gpu(f"DCGM_FI_DEV_MIG_CI_INFO{f}")),
        ],
        identity_fields=mig_identity,
        noise_fields=_NOISE + [n for n in _LABEL_NOISE if n not in mig_identity] + ["mig_key"],
        value_renames=[None, "Max Slices", "Attributes", "GI Info", "CI Info"],
        join_mode="inner",  # inner join: a GPU missing from the gated anchor frame is dropped entirely
        join_field="mig_key",  # composite UUID/GPU_I_ID key -- see _mig_key() above
        overrides=[],
        description="MIG (Multi-Instance GPU) inventory, gated on MIG mode actually being enabled: the "
        "anchor query is `DCGM_FI_DEV_MIG_MODE == 1` inner-joined with the rest, so a GPU with "
        "MIG off (this workstation GPU has no MIG capability at all -- MIG_MAX_SLICES=0) simply "
        "produces no row, rather than a row full of not-applicable fields. IMPORTANT per NVIDIA's "
        "own guidance: on a MIG-sliced fleet, a 'whole-GPU utilization' rollup must weight each "
        "instance's utilization by its slice size -- never plain-average the per-instance ratios, "
        "or a fleet with mixed slice sizes will be silently misreported. This panel is inventory "
        "only (no utilization rollup); expect 'No data' here on any non-MIG-capable or MIG-"
        "disabled GPU.",
    )
    panels.append(mig_panel)

    # ---- id 91: vGPU (stat, x18 y24 w6 h4) --------------------------------
    # Minor layout fix: h reduced 8->4, same rationale as panel 95.
    panels.append(
        lib.stat(
            91,
            "vGPU",
            18,
            24,
            6,
            4,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_VGPU_LICENSE_STATUS{f}"), ref_id="A", instant=True)],
            unit_id="none",
            mappings=lib.bool_config_mappings(off_text="Unlicensed", on_text="Licensed"),
            thresholds_steps=lib.no_thresholds(),
            description="vGPU software license status. 0=Unlicensed (gray -- neutral: a bare-metal host, "
            "including this workstation GPU, is expected to read this way, not a fault). "
            "1=Licensed (green). 0/absent on any bare-metal host with no vGPU hypervisor layer.",
        )
    )

    # ---- id 92: Power profiles / power smoothing (table, x0 y32 w24 h8) ---
    power_panel = _table_join(
        92,
        "Power profiles / power smoothing (Blackwell fleets)",
        0,
        32,
        24,
        8,
        exprs=[
            # No CSV label-type field is promoted to a display column here (unlike
            # 87/90), so every frame -- including frame 1 -- is safe to wrap.
            lib.per_gpu(f"DCGM_FI_DEV_BOARD_POWER_PROFILE_REQUESTED_MASK{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_BOARD_POWER_PROFILE_ENFORCED_MASK{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_BOARD_POWER_PROFILE_SUPPORTED_MASK{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_PWR_SMOOTHING_ENABLED{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_SYSIO_POWER_WATTS{f}"),
            lib.per_gpu(f"DCGM_FI_DEV_MODULE_POWER_WATTS{f}"),
        ],
        identity_fields=_IDENTITY,
        noise_fields=_NOISE + _LABEL_NOISE,
        value_renames=[
            "Requested Profile Mask",
            "Enforced Profile Mask",
            "Supported Profile Mask",
            "Power Smoothing Enabled",
            "SysIO Power (W)",
            "Module Power (W)",
        ],
        overrides=[
            lib.override_by_name(
                "Power Smoothing Enabled",
                [
                    ("mappings", lib.bool_config_mappings(off_text="Disabled", on_text="Enabled")),
                    ("custom.cellOptions", {"type": "color-background"}),
                ],
            ),
            lib.override_by_name("SysIO Power (W)", [("unit", lib.unit("watt"))]),
            lib.override_by_name("Module Power (W)", [("unit", lib.unit("watt"))]),
        ],
        description="Workload power-profile bitmasks and power-smoothing state -- Blackwell large-fleet "
        "features with no public per-bit decode table as of this driver release (shown raw, for "
        "operators who already know their fleet's specific profile IDs), plus the superchip-only "
        "SysIO/Module power rails. Lives only here, next to the rest of the datacenter/superchip-"
        "specific fields, not in the general-purpose Power row. Expect every column empty/0 on a "
        "workstation GPU -- these fields are gated to specific datacenter SKUs.",
    )
    panels.append(power_panel)

    # ---- id 93: NVLink aggregate bandwidth (timeseries, x0 y40 w12 h8) ----
    panels.append(
        lib.timeseries(
            93,
            "NVLink aggregate bandwidth",
            0,
            40,
            12,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_NVLINK_TX_BYTES{f}"), legend=f"{std} · TX (profiling)", ref_id="A"
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_PROF_NVLINK_RX_BYTES{f}"), legend=f"{std} · RX (profiling)", ref_id="B"
                ),
                lib.target(
                    lib.per_gpu(f"rate(DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · Total (cumulative counter rate)",
                    ref_id="C",
                ),
            ],
            unit_id="Bps",
            thresholds_steps=lib.no_thresholds("blue"),
            description="Aggregate NVLink bandwidth across all links: the PROF TX/RX series are already "
            "instantaneous rates; the third series derives a rate from the cumulative "
            "DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL counter as a cross-check. 0/no-data on this "
            "single GPU with no NVLink/NVSwitch interconnect -- expected. Moved here from the "
            "always-visible PCIe row per spec: with NVLink absent on the large majority of DCGM "
            "installs, it does not belong next to universally-relevant PCIe panels.",
        )
    )

    # ---- id 94: NVLink health (table, x12 y40 w12 h6) ---------------------
    # h reduced 8->6; identity + value columns get explicit widths.
    nvlink_panel = _table_join(
        94,
        "NVLink health",
        12,
        40,
        12,
        6,
        exprs=[
            # No CSV label-type field is promoted to a display column here, so every
            # frame is safe to wrap. The four increase(...[$__range]) frames use
            # agg="sum" (a range-integrating counter -- the old and new label-set
            # series each cover only part of the range, see lib.per_gpu()'s
            # docstring); P2P_STATUS/NVLINK_ERROR are gauges, so the default "max".
            lib.per_gpu(f"DCGM_FI_DEV_NVLINK_P2P_STATUS{f}"),
            lib.per_gpu(f"increase(DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_TOTAL{f}[$__range])", agg="sum"),
            lib.per_gpu(f"increase(DCGM_FI_DEV_NVLINK_CRC_DATA_ERROR_TOTAL{f}[$__range])", agg="sum"),
            lib.per_gpu(f"increase(DCGM_FI_DEV_NVLINK_REPLAY_ERROR_TOTAL{f}[$__range])", agg="sum"),
            lib.per_gpu(f"increase(DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_TOTAL{f}[$__range])", agg="sum"),
            lib.per_gpu(f"DCGM_FI_DEV_NVLINK_ERROR{f}"),
        ],
        identity_fields=_IDENTITY,
        noise_fields=_NOISE + _LABEL_NOISE,
        value_renames=[
            "P2P Status",
            "CRC Flit Errors (range)",
            "CRC Data Errors (range)",
            "Replay Errors (range)",
            "Recovery Errors (range)",
            "Error State",
        ],
        overrides=[
            lib.override_by_name(
                name,
                [
                    ("thresholds", lib.thresholds([(0, "green"), (0.001, "red")])),
                    ("custom.cellOptions", {"type": "color-background"}),
                ],
            )
            for name in [
                "CRC Flit Errors (range)",
                "CRC Data Errors (range)",
                "Replay Errors (range)",
                "Recovery Errors (range)",
                "Error State",
            ]
        ]
        + [
            lib.override_by_name("Hostname", [("custom.width", 100)]),
            lib.override_by_name("Instance", [("custom.width", 110)]),
            lib.override_by_name("PCI Bus ID", [("custom.width", 120)]),
            lib.override_by_name("Model", [("custom.width", 180)]),
            lib.override_by_name("P2P Status", [("custom.width", 100)]),
        ],
        description="Per-GPU NVLink peer-to-peer status bitmap and cumulative error counters (summed over "
        "the dashboard's current time range via `increase(...[$__range])`, so the window "
        "changes with whatever the operator has selected -- unlike every rate() panel elsewhere "
        "in this dashboard, which intentionally stays on $__rate_interval). Every error column "
        "is green@0 / red@>0 -- any NVLink CRC, replay, or recovery error is worth investigating "
        "regardless of magnitude, since a healthy link should log none. 0/no-data throughout on "
        "this single GPU with no NVLink hardware -- real signal on NVLink/NVSwitch hosts.",
    )
    panels.append(nvlink_panel)

    return row_def, panels
