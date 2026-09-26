# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row K -- Reliability (ECC / Remap / Retired / XID / Recovery / Health).

Row id 74, expanded, child ids 75-85 plus id 96 ("Health incidents (why)",
sub-row 5, rel y=43, h=6) and id 100 ("Exporter scrape status", sub-row 6, rel
y=49, h=6).

All targets use ctx.filter_all (the standard multi-select $job/$instance/$gpu
regex filter) -- this row is not repeated per GPU, so a fleet with >1 GPU
selected overlays one series/legend entry per GPU on every panel. Legends use
`ctx.legend_std` (`{{hostname}} GPU{{gpu}}`) instead of bare `{{modelName}}`,
which cannot disambiguate multiple identically-modelled GPUs across hosts.

Panel 85 ("Health check by subsystem"):
1. Its legend includes a GPU token (`{{hostname}} GPU{{gpu}} · {{health_watch}}`)
   rather than `{{instance}} · {{health_watch}}`: an instance-only legend has no
   GPU index, so two GPUs sharing one `instance` label (any multi-GPU host)
   render two complete, visually identical lane blocks with no way to tell
   which GPU was which (confirmed live on a 2-GPU-per-host environment).
2. Its query aggregates `max by (hostname, instance, gpu, UUID, health_watch)`
   instead of querying the raw metric directly. DCGM_EXP_GPU_HEALTH_STATUS's
   `health_error_code`/`health_error_severity` labels are part of the series
   identity and change value on every fault transition -- Prometheus therefore
   starts a brand new series (and lets the old one go stale) whenever a
   watch's error code changes, which a state-timeline keyed only on
   `{{instance}}`/`{{health_watch}}` renders as TWO separate, non-adjacent
   lanes for the same logical watch (confirmed live: one host's POWER watch
   showed up as two separate rows of an 11-row full-screen view, with
   different fill patterns, for the exact same watch). Aggregating those two
   labels away up front collapses every watch back down to exactly one lane
   per GPU.
3. It is h=18, not a shorter h=8: a fleet of just 2-3 GPUs already puts 22-33
   lanes (GPUs x 11 watches, including 'ALL') into this one state-timeline,
   and at a shorter height those lanes' y-axis labels overlap into an
   unreadable smear once more than ~1 GPU/instance is selected (confirmed
   live at instance=All/gpu=All on a 3-GPU fleet). Sub-rows 5/6 (ids 96/100)
   are shifted down to stay below it.

Id 100, "Exporter scrape status", is a state-timeline of Prometheus's own
`up{job=~"$job", instance=~"$instance"}` per scrape target, 1=Up (green) /
0=Down (red). It is deliberately keyed on `instance` (always present, per
CONVENTIONS.md/README's own "instance always exists" note) rather than
`hostname`/`UUID` -- `up` is a per-scrape-target series with no GPU-level
labels at all, unlike every other panel in this row.

Panels 85/97/96's queries do not filter out `health_watch="ALL"`. 'ALL' is not
a rollup/derived value of the other 10 watches -- DCGM reports it as its own
independent GPU-wide watch that catches devastating, non-subsystem-specific
faults (e.g. Xid 79 fallen off bus, Xid 95 uncontained ECC) the other 10 named
subsystems never see (confirmed via dcgm-exporter source: gpuHealthChecks
lists DCGM_HEALTH_WATCH_ALL as its own entry, defaulted PASS like every other
watch). Every aggregation here uses `max()`/`max by (...)`, which is
idempotent under duplication, so including 'ALL' alongside the other 10
cannot double-count a failure -- it can only surface one the other 10 would
have missed.

Every per-GPU query in this row (75-85, 96) now goes through `lib.per_gpu()`
(see CONVENTIONS.md's series-identity section) instead of a hand-rolled `max
by (...)`/raw selector, so a dcgm-exporter CSV label-set change (new driver/
VBIOS) can't render one incident/GPU as two rows/lines for a while. Panel 85's
and 96's own health_watch/health_error_code/health_error_severity aggregation
(gotcha #8 above) is preserved via `per_gpu(..., extra=(...))` -- those three
labels are kept, only the CSV label-set churn is aggregated away. Panel 82's
lifetime xid-count table uses `agg="max"` (the default): DCGM_EXP_XID_ERRORS_
TOTAL is the exporter's own counter, not reset by a CSV-only reload, so the
post-split series only ever continues upward -- max is exact. Panels 83/100
are left unchanged: 83 is already a fleet-wide `sum()` with no per-GPU legend
(unaffected by the split -- see lib.per_gpu()'s docstring), and 100 queries
Prometheus's own `up`, which carries no GPU-identity labels at all.
"""

from typing import Any

from .. import lib


def build(ctx: lib.RowContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f = ctx.filter_all
    inner = f[1:-1]
    std = ctx.legend_std
    xid_benign_pattern = "|".join(str(code) for code in lib.BENIGN_XID_CODES)
    row_def = lib.row(74, "Reliability (ECC / Remap / Retired / XID / Recovery / Health)", collapsed=False)
    panels: list[dict[str, Any]] = []

    # ---- Sub-row 1 (rel y=1, h=8) --------------------------------------
    panels.append(
        lib.stat(
            75,
            "ECC mode",
            0,
            1,
            6,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_ECC_MODE{f}"), legend=f"{std} · Current", ref_id="A", instant=True
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_ECC_PENDING{f}"), legend=f"{std} · Pending", ref_id="B", instant=True
                ),
            ],
            unit_id="none",
            mappings=lib.ecc_mode_mappings(),
            thresholds_steps=lib.no_thresholds(),
            description="Current vs. pending ECC (error-correcting code) mode for this GPU's framebuffer "
            "memory. 0=Disabled (gray -- many workstation SKUs, including this RTX PRO Blackwell "
            "card, ship with ECC off by default; that is a configuration choice, not a fault). "
            "1=Enabled (green). 'Pending' differs from 'Current' only right after `nvidia-smi -e` "
            "toggles the mode, until the next GPU reset/reboot actually applies it -- if the two "
            "disagree, a reset is needed.",
        )
    )
    panels.append(
        lib.timeseries(
            76,
            "ECC errors: volatile vs. aggregate",
            6,
            1,
            12,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_ECC_SBE_VOL_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · SBE (volatile)",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_ECC_DBE_VOL_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · DBE (volatile)",
                    ref_id="B",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_ECC_SBE_AGG_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · SBE (aggregate)",
                    ref_id="C",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_ECC_DBE_AGG_TOTAL{f}[$__rate_interval])"),
                    legend=f"{std} · DBE (aggregate)",
                    ref_id="D",
                ),
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("gray"),
            description="SBE = single-bit/correctable (the ECC engine fixed it transparently -- occasional "
            "nonzero is normal). DBE = double-bit/uncorrectable (any nonzero deserves attention). "
            "'Volatile' resets to 0 on every driver reload; 'aggregate' is the lifetime count.",
        )
    )
    panels.append(
        lib.stat(
            77,
            "Row-remap failure / pending",
            18,
            1,
            6,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_ROW_REMAP_FAILURE{f}"),
                    legend=f"{std} · Failure",
                    ref_id="A",
                    instant=True,
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_ROW_REMAP_PENDING{f}"),
                    legend=f"{std} · Pending",
                    ref_id="B",
                    instant=True,
                ),
            ],
            unit_id="none",
            mappings=lib.bool_reliability_mappings(),
            thresholds_steps=lib.no_thresholds(),
            description="Failure=Yes (red) means a row-remap operation itself failed -- this GPU can no "
            "longer self-heal that ECC error and is an RMA candidate (NVIDIA Xid 64). "
            "Pending=Yes means a remap is queued but needs a GPU reset to apply.",
        )
    )

    # ---- Sub-row 2 (rel y=9, h=8) ---------------------------------------
    panels.append(
        lib.timeseries(
            78,
            "Row-remap events",
            0,
            9,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS{f}[$__rate_interval])"),
                    legend=f"{std} · Uncorrectable",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS{f}[$__rate_interval])"),
                    legend=f"{std} · Correctable",
                    ref_id="B",
                ),
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("gray"),
            description="The modern Ampere+ reliability signal: DCGM remaps a failing DRAM row to a spare "
            "row instead of retiring the whole page. Occasional 'Correctable' events are expected "
            "and self-healing. Any 'Uncorrectable' event is worth investigating -- spare capacity "
            "is finite (see panel 79).",
        )
    )
    panels.append(
        lib.bargauge(
            79,
            "Row-remap spare capacity",
            8,
            9,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_BANK_REMAP_AVAIL_MAX{f}"),
                    legend="Historical max (spare rows/bank)",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_BANK_REMAP_AVAIL_HIGH{f}"), legend="Banks: high availability", ref_id="B"
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_BANK_REMAP_AVAIL_PARTIAL{f}"),
                    legend="Banks: partial availability",
                    ref_id="C",
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_BANK_REMAP_AVAIL_LOW{f}"), legend="Banks: low availability", ref_id="D"
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_BANK_REMAP_AVAIL_NONE{f}"),
                    legend="Banks: none left (critical)",
                    ref_id="E",
                ),
            ],
            unit_id="none",
            min_=0,
            thresholds_steps=lib.no_thresholds("blue"),
            color={"mode": "thresholds"},
            overrides=[
                lib.override_by_name(
                    "Banks: none left (critical)", [("thresholds", lib.thresholds([(0, "green"), (1, "red")]))]
                ),
            ],
            description="Counts of memory *banks* falling into each spare-row-availability bucket. 'High'/"
            "'Partial'/'Low' nonzero counts are informational (blue). 'Banks: none left' > 0 "
            "(red) means that bank can no longer self-heal a further ECC error via row-remap.",
        )
    )
    panels.append(
        lib.stat(
            80,
            "SRAM exceeded / Memory unrepairable",
            16,
            9,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_SRAM_EXCEEDED{f}"),
                    legend=f"{std} · SRAM exceeded",
                    ref_id="A",
                    instant=True,
                ),
                lib.target(
                    lib.per_gpu(f"DCGM_FI_DEV_MEMORY_UNREPAIRABLE{f}"),
                    legend=f"{std} · Unrepairable",
                    ref_id="B",
                    instant=True,
                ),
            ],
            unit_id="none",
            mappings=lib.bool_reliability_mappings(),
            thresholds_steps=lib.no_thresholds(),
            description="Blackwell+ reliability signals. SRAM Exceeded=Yes: the on-die SRAM ECC error rate "
            "crossed NVIDIA's threshold. Unrepairable=Yes: the memory subsystem has an error it "
            "cannot repair. Either =Yes (red) warrants an immediate look and is a strong RMA "
            "signal; 'No data' is equally healthy to a confirmed 0/No on driver/GPU variants "
            "that don't populate a given field.",
        )
    )

    # ---- Sub-row 3 (rel y=17, h=8) ---------------------------------------
    panels.append(
        lib.timeseries(
            81,
            "Retired pages (legacy, pre-Ampere)",
            0,
            17,
            8,
            8,
            [
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_RETIRED_SBE{f}[$__rate_interval])"),
                    legend=f"{std} · Retired (SBE)",
                    ref_id="A",
                ),
                lib.target(
                    lib.per_gpu(f"increase(DCGM_FI_DEV_RETIRED_DBE{f}[$__rate_interval])"),
                    legend=f"{std} · Retired (DBE)",
                    ref_id="B",
                ),
                lib.target(lib.per_gpu(f"DCGM_FI_DEV_RETIRED_PENDING{f}"), legend=f"{std} · Pending", ref_id="C"),
            ],
            unit_id="none",
            thresholds_steps=lib.no_thresholds("gray"),
            description="The pre-Ampere page-retirement mechanism, superseded by row-remap (panels 78-80) "
            "on Ampere and newer. Expect 0 or 'No data' on modern hardware -- that is healthy, "
            "not a monitoring gap.",
        )
    )

    # max by (identity, xid): DCGM_EXP_XID_ERRORS_TOTAL is the exporter's own
    # lifetime counter (not a GPU/driver counter -- a CSV-only reload does not
    # reset it, unlike the ECC volatile counters), so on a label-set split the new
    # series' value only ever continues upward from the old one -- max is exact,
    # never an undercounting sum. Without this, the same GPU's lifetime total for
    # a given xid would render as two rows (identical hostname+xid, since gpu/UUID
    # are already excluded from this table's display) for a while after a
    # driver/VBIOS upgrade.
    xid_targets = [
        lib.target(
            lib.per_gpu(f"DCGM_EXP_XID_ERRORS_TOTAL{f}", extra=("xid",)),
            ref_id="A",
            instant=True,
            fmt="table",
        )
    ]
    # Same leak as panel 96 (see its comment): the 8 CSV "label"-type OPTIONAL fields attach to
    # every DCGM_EXP_* metric too (confirmed live on the sibling DCGM_EXP_XID_ERRORS_COUNT/
    # DCGM_EXP_GPU_HEALTH_STATUS series), so they need the same exclusion here.
    xid_exclude = [
        "Time",
        "job",
        "device",
        "gpu",
        "host",
        "__name__",
        "UUID",
        "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES",
        "DCGM_FI_DEV_BOARD_SERIAL",
        "DCGM_FI_DEV_FABRIC_CLUSTER_UUID",
        "DCGM_FI_DEV_GPU_BRAND",
        "DCGM_FI_DEV_VBIOS_VERSION",
        "DCGM_FI_DRIVER_VERSION",
        "DCGM_FI_IMEX_DOMAIN_STATUS",
        "DCGM_FI_SYSTEM_NVML_VERSION",
    ]
    xid_rename = {"Value": "Total events (lifetime)"}
    panels.append(
        lib.table(
            82,
            "XID events by code",
            8,
            17,
            8,
            8,
            xid_targets,
            transformations=[lib.transform_organize(exclude=xid_exclude, rename=xid_rename)],
            overrides=[
                lib.override_by_name(
                    "xid", [("mappings", lib.xid_mappings()), ("custom.cellOptions", {"type": "color-background"})]
                ),
            ],
            no_value="No Xid events",
            links=[
                {
                    "title": "NVIDIA Xid Errors reference",
                    "url": "https://docs.nvidia.com/deploy/xid-errors/",
                    "targetBlank": True,
                }
            ],
            description="One row per distinct XID error code ever seen since dcgm-exporter started. The "
            "'xid' column is colored/named from NVIDIA's Xid-errors reference: green = "
            "informational, yellow/orange = worth a look, red = fatal/RMA-relevant. No Xid logged "
            "in this test window -- 'No Xid events' is the expected, healthy state.",
        )
    )

    panels.append(
        lib.stat(
            83,
            "XID events (5m)",
            16,
            17,
            4,
            8,
            [
                lib.target(
                    f'sum(DCGM_EXP_XID_ERRORS_COUNT{{xid!~"{xid_benign_pattern}", {inner}}})',
                    ref_id="A",
                    instant=True,
                )
            ],
            unit_id="none",
            thresholds_steps=lib.thresholds([(0, "green"), (1, "red")]),
            description="Count of distinct XID events in dcgm-exporter's trailing 5-minute window, summed "
            "across every selected GPU, excluding the self-healing/informational codes "
            f"({xid_benign_pattern}) the XID reference table below colors green. Dense (always "
            "emits, including an explicit 0) -- safe to wire up as a real alert rule.",
        )
    )
    panels.append(
        lib.stat(
            84,
            "GPU recovery action",
            20,
            17,
            4,
            8,
            [lib.target(lib.per_gpu(f"DCGM_FI_DEV_GPU_RECOVERY_ACTION{f}"), ref_id="A", instant=True)],
            unit_id="none",
            mappings=lib.recovery_action_mappings(),
            thresholds_steps=lib.no_thresholds(),
            description="DCGM's own recommended recovery action after a fault: None (green) / GPU Reset "
            "(yellow) / Node Reboot (red) / Drain P2P (orange) / Drain P2P + Reset (orange) / "
            "Recover IMEX Domain (orange) / Bus Reset (red) / System Reboot (red).",
        )
    )

    # ---- Sub-row 4 (rel y=25, h=18) ---------------------------------------
    # h=18 (not 8): a fleet of just 2-3 GPUs already puts 22-33 lanes (GPUs x 11
    # watches, including ALL) into this one state-timeline -- at h=8 those lanes'
    # y-axis labels overlap into an unreadable smear once more than ~1 GPU/instance
    # is selected (confirmed live at instance=All/gpu=All on this environment's
    # 3-GPU fleet). h=18 keeps every label legible up to a moderate fleet size;
    # a very large fleet should filter $instance/$gpu rather than view "All" here.
    panels.append(
        lib.state_timeline(
            85,
            "Health check by subsystem",
            0,
            25,
            24,
            18,
            [
                lib.target(
                    lib.per_gpu(f"DCGM_EXP_GPU_HEALTH_STATUS{f}", extra=("health_watch",)),
                    legend=f"{std} · {{{{health_watch}}}}",
                    ref_id="A",
                )
            ],
            mappings=lib.health_status_mappings(),
            description="One lane per GPU per DCGM health-check watch: the 10 named subsystems (PCIe/"
            "NVLink/PMU/MCU/Mem/SM/InfoROM/Thermal/Power/Driver) plus 'ALL', DCGM's own "
            "separate GPU-wide watch -- not a rollup/derived value of the other 10 -- that "
            "independently reports devastating, non-subsystem-specific faults such as Xid 79 "
            "(fallen off bus) or Xid 95 (uncontained ECC). Pass (green) / Warn (yellow) / Fail "
            "(red). Legend includes the GPU (not just the host instance) so multi-GPU hosts are "
            "distinguishable, and the query aggregates away DCGM_EXP_GPU_HEALTH_STATUS's "
            "health_error_code/severity labels (`max by (...)`) so a watch's fault code changing "
            "mid-range no longer spawns a second, orphaned lane for what is logically one "
            "continuous per-GPU-per-watch signal. See panel 96 below for the per-incident 'why' "
            "(error code + severity).",
        )
    )

    # ---- Sub-row 5 (rel y=43, h=6): Health incidents drill-down -------
    # per_gpu(..., extra=(health_watch, error_code, severity)) before the >0 filter:
    # these three labels are this metric's own series identity (see CONVENTIONS.md
    # gotcha #8), and aggregating away only the *CSV* label-set split (job/instance/
    # hostname/gpu/UUID/modelName/pci_bus_id/device) -- while keeping all three --
    # stops a driver/VBIOS upgrade from showing the same incident as two rows.
    health_series = lib.per_gpu(
        f"DCGM_EXP_GPU_HEALTH_STATUS{f}", extra=("health_watch", "health_error_code", "health_error_severity")
    )
    health_incidents_expr = f"({health_series}) > 0"
    panels.append(
        lib.table(
            96,
            "Health incidents (why)",
            0,
            43,
            24,
            6,
            [lib.target(health_incidents_expr, ref_id="A", instant=True, fmt="table")],
            transformations=[
                lib.transform_organize(
                    exclude=[
                        "Time",
                        "job",
                        "device",
                        "host",
                        "__name__",
                        "UUID",
                        "modelName",
                        "pci_bus_id",
                        "health_error_category",
                        # The 8 CSV "label"-type OPTIONAL fields dcgm-exporter attaches to *every*
                        # metric of a GPU (see custom-counters.csv's header), including this derived
                        # DCGM_EXP_* one -- verified live: without this exclusion they leak in as 8
                        # raw, unrenamed columns ahead of the actually useful Host/GPU/Watch/Error
                        # Code/Severity ones, pushing the "why" off the visible table entirely.
                        "DCGM_FI_CUDA_GPU_VISIBLE_DEVICES",
                        "DCGM_FI_DEV_BOARD_SERIAL",
                        "DCGM_FI_DEV_FABRIC_CLUSTER_UUID",
                        "DCGM_FI_DEV_GPU_BRAND",
                        "DCGM_FI_DEV_VBIOS_VERSION",
                        "DCGM_FI_DRIVER_VERSION",
                        "DCGM_FI_IMEX_DOMAIN_STATUS",
                        "DCGM_FI_SYSTEM_NVML_VERSION",
                    ],
                    rename={
                        "hostname": "Host",
                        "gpu": "GPU",
                        "health_watch": "Watch",
                        "health_error_code": "Error Code",
                        "health_error_severity": "Severity",
                        "Value": "Status",
                    },
                )
            ],
            overrides=[
                lib.override_by_name(
                    "Status",
                    [("mappings", lib.health_status_mappings()), ("custom.cellOptions", {"type": "color-background"})],
                ),
                lib.override_by_name("Host", [("custom.width", 110)]),
                lib.override_by_name("GPU", [("custom.width", 60)]),
                lib.override_by_name("Watch", [("custom.width", 110)]),
                lib.override_by_name("Error Code", [("custom.width", 260)]),
                lib.override_by_name("Severity", [("custom.width", 110)]),
            ],
            no_value="0 GPUs unhealthy",
            description="Every currently-nonzero (Warn or Fail) DCGM health watch, one row per GPU per "
            "affected subsystem (including a bare 'ALL' row for a GPU-wide fault that doesn't "
            "map to any of the 10 named subsystems), with DCGM's own error code and severity -- "
            "the detail behind the top-strip Health tile (id 8) and the timeline above. Instant "
            "query (current state only), deliberately: health_error_code/severity are part of "
            "this metric's series identity and change on every fault transition, which would "
            "create duplicate/orphaned rows in a range-based grouping (see panel 85's own fix "
            "for the same issue). A MONITOR-severity Warn (e.g. the SW power cap engaging under "
            "load) is expected/informational, not a failure -- this table shows severity "
            "precisely so that distinction is visible. '0 GPUs unhealthy' (noValue) means no "
            "watch on any selected GPU is currently Warn or Fail.",
        )
    )

    # ---- Sub-row 6 (rel y=49, h=6): exporter liveness ------
    panels.append(
        lib.state_timeline(
            100,
            "Exporter scrape status",
            0,
            49,
            24,
            6,
            [lib.target(f"up{ctx.filter_up}", legend="{{instance}}", ref_id="A")],
            mappings=lib.value_mapping([(0, "Down", "red"), (1, "Up", "green")]),
            description="One lane per scrape target (job/instance, not per-GPU -- `up` carries no "
            "UUID/gpu label) showing Prometheus's own scrape-liveness signal, 1=Up (green) / "
            "0=Down (red). Closes this dashboard's biggest completeness gap: every other panel "
            "is built from DCGM_FI_*/DCGM_EXP_* series, which simply stop existing rather than "
            "turning red when an exporter or host dies (see panel 1's 'Exporters down' count in "
            "the top strip for the same signal as a single fleet-wide number). A gap in a lane "
            "(rather than a red segment) means Prometheus itself never got a sample in that "
            "window -- distinct from a confirmed 'Down' (a scrape that was attempted and failed).",
        )
    )

    return row_def, panels
