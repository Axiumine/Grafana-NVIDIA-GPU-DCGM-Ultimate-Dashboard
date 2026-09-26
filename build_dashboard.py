#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Build the "NVIDIA GPU -- DCGM Ultimate Dashboard" Grafana JSON.

Usage:
    python3 build_dashboard.py [--out PATH] [--rows a,b,c,...]

Default output: "nvidia-dcgm-dashboard.json" next to this script.
Row selection lets a row author build a dashboard containing only their row(s)
plus row A (top strip) for fast local iteration -- see dcgm_dashboard/CONVENTIONS.md.

Output is deterministic: stable panel ids (fixed, not auto-assigned -- see
dcgm_dashboard/CONVENTIONS.md's id-range table), stable key order (json.dump
with sort_keys=True), pretty-printed with indent=2.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from dcgm_dashboard import lib  # noqa: E402
from dcgm_dashboard.rows import (  # noqa: E402
    row_a, row_b, row_c, row_d, row_e, row_f, row_g, row_h, row_i, row_j, row_k, row_l,
)

ROW_ORDER = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"]
ROW_MODULES = {
    "a": row_a, "b": row_b, "c": row_c, "d": row_d, "e": row_e, "f": row_f,
    "g": row_g, "h": row_h, "i": row_i, "j": row_j, "k": row_k, "l": row_l,
}

DASHBOARD_UID = "nvidia-dcgm-ultimate"
DASHBOARD_TITLE = "NVIDIA GPU — DCGM Ultimate Dashboard"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent / "nvidia-dcgm-dashboard.json"


def build_templating() -> list:
    return [
        lib.query_variable(
            "job", "Job", 'label_values(DCGM_FI_DEV_GPU_UTIL, job)',
        ),
        lib.query_variable(
            "instance", "Instance",
            'label_values(DCGM_FI_DEV_GPU_UTIL{job=~"$job"}, instance)',
        ),
        lib.query_variable(
            "gpu", "GPU (UUID)",
            'label_values(DCGM_FI_DEV_GPU_UTIL{job=~"$job", instance=~"$instance"}, UUID)',
        ),
        lib.textbox_variable("energy_price", "Energy price ($/kWh)", "0.25"),
        lib.textbox_variable("currency", "Currency symbol", "$"),
        lib.textbox_variable("peak_fp32_tflops", "Peak FP32 TFLOPS (this GPU model)", "40"),
        # A "peak_tensor_tflops" variable is intentionally not defined here. It would
        # be wired into no panel/expr/override/displayName -- a dead control in the
        # toolbar that visibly does nothing when edited -- because NVIDIA publishes no
        # single dense-precision peak number valid across GPU/precision combinations
        # the way FP32/mem-bandwidth do -- unlike those two textboxes, this one has no
        # safe non-empty default. Wiring it up anyway (e.g.
        # PIPE_TENSOR_ACTIVE * $peak_tensor_tflops) would need a real placeholder value
        # to avoid shipping a panel that errors on a syntactically invalid "X * "
        # PromQL expression on every fresh import (the variable's would-be default is
        # ""), which is a worse default experience than simply not shipping the
        # control. See README.md's Variables section for the same note.
        lib.textbox_variable("peak_mem_bw_gbs", "Peak memory bandwidth GB/s (this GPU model)", "672"),
    ]


def build_dashboard(selected_rows: list) -> dict:
    modules = [ROW_MODULES[r] for r in selected_rows]
    panels = lib.build_layout(modules)

    return {
        "__inputs": [
            {
                "name": "DS_PROMETHEUS",
                "label": "Prometheus",
                "description": "",
                "type": "datasource",
                "pluginId": "prometheus",
                "pluginName": "Prometheus",
            }
        ],
        "__requires": [
            {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.4.0"},
            {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"},
        ],
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                # Without these, an Xid fault or a driver reload (which resets the
                # cumulative energy counter row G's "Energy this range" panel depends
                # on) would leave no on-screen trace on any timeseries panel.
                lib.annotation_query(
                    "Xid event",
                    f"DCGM_FI_DEV_XID_ERRORS{lib.FILTER_ALL}",
                    "red",
                    "Xid {{Value}} — {{hostname}} GPU{{gpu}}",
                ),
                lib.annotation_query(
                    "Driver reload (energy counter reset)",
                    f"resets(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION{lib.FILTER_ALL}[$__interval]) > 0",
                    "blue",
                    "Driver reload — {{hostname}} GPU{{gpu}}",
                ),
            ]
        },
        "description": "Full-parity, DCGM-exporter-only successor to grafana.com dashboard 14574, "
                        "plus PROF_* real activity, energy/cost, PCIe/NVLink byte counters, and RAS.",
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [
            lib.dashboard_link("NVIDIA Xid Errors reference", "https://docs.nvidia.com/deploy/xid-errors/"),
            lib.dashboard_link("DCGM User Guide", "https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/"),
        ],
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "tags": ["nvidia", "gpu", "dcgm", "dcgm-exporter"],
        "templating": {"list": build_templating()},
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "",
        "title": DASHBOARD_TITLE,
        "uid": DASHBOARD_UID,
        "version": 1,
        "weekStart": "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output JSON path")
    ap.add_argument(
        "--rows", default=",".join(ROW_ORDER),
        help=f"comma-separated row letters to include, in {ROW_ORDER} order regardless of "
             "the order given (default: all)",
    )
    args = ap.parse_args()

    requested = [r.strip().lower() for r in args.rows.split(",") if r.strip()]
    unknown = sorted(set(requested) - set(ROW_ORDER))
    if unknown:
        print(f"error: unknown row letter(s): {unknown} (valid: {ROW_ORDER})", file=sys.stderr)
        return 2
    selected_rows = [r for r in ROW_ORDER if r in requested]

    dashboard = build_dashboard(selected_rows)

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(dashboard, f, indent=2, sort_keys=True)
        f.write("\n")

    panel_count = sum(
        1 + len(p.get("panels", [])) if p.get("type") == "row" else 1
        for p in dashboard["panels"]
    )
    print(f"wrote {out_path} ({panel_count} panels, rows: {','.join(selected_rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
