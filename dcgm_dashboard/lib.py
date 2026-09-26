# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""
dcgm_dashboard.lib -- reusable builders for the "NVIDIA GPU - DCGM Ultimate Dashboard".

Everything a row module needs lives here: the datasource ref, target/panel builders,
threshold/value-mapping/override helpers, the shared label filters, the DCGM enum and
bitmask decode library (see the CLOCK_EVENT_BITS section below for the clock-event
bitmask's throttle-vs-informational classification), verified Grafana unit ids, and
the layout engine that stacks row modules top-to-bottom.

Two helpers dedup a per-GPU query across a dcgm-exporter CSV label-set change
(CONVENTIONS.md's series-identity section) -- pick per query shape, not by habit:
`per_gpu()` aggregates (`<agg> by (GPU_ID_LABELS)`) and is the default for every
per-GPU query; `latest_per_gpu()` instead keeps the single most-recently-sampled raw
series per GPU (`expr and topk by (...) (1, timestamp(...))`) without collapsing any
label, for the few identity/inventory tables (GPU Inventory id 21, Fabric/IMEX/C2C id
87, MIG id 90) that must stay raw to display a CSV label-type field as a column.

See dcgm_dashboard/CONVENTIONS.md for how to write a row module.
"""

import copy
import dataclasses
import re
import string
from collections.abc import Sequence
from typing import Any

# ---------------------------------------------------------------------------
# Datasource, filters, legend
# ---------------------------------------------------------------------------

DS_VAR = "${DS_PROMETHEUS}"


def ds_ref() -> dict[str, str]:
    """A fresh datasource reference dict -- never share/mutate one instance."""
    return {"type": "prometheus", "uid": DS_VAR}


# Standard label filter. Everywhere except the repeated per-GPU
# row (row B), $gpu is a multi-select variable so UUID uses regex match.
FILTER_ALL = '{job=~"$job", instance=~"$instance", UUID=~"$gpu"}'

# Inside row B's repeat, $gpu is a single value per repetition -> plain equality.
FILTER_SINGLE = '{job=~"$job", instance=~"$instance", UUID="$gpu"}'

# Prometheus's own `up` metric has no UUID label (it is a per-scrape-target series,
# not a per-GPU one) -- $gpu is deliberately left out of this filter, unlike
# FILTER_ALL/FILTER_SINGLE above.
FILTER_UP = '{job=~"$job", instance=~"$instance"}'

LEGEND_STD = "{{hostname}} GPU{{gpu}}"

# ---------------------------------------------------------------------------
# GPU series identity -- see CONVENTIONS.md's "series identity / driver
# upgrades" section for the incident this fixes.
#
# dcgm-exporter attaches its custom-counters.csv "label"-type fields (currently
# DCGM_FI_DRIVER_VERSION, DCGM_FI_DEV_VBIOS_VERSION, DCGM_FI_DEV_BOARD_SERIAL,
# DCGM_FI_DEV_GPU_BRAND, DCGM_FI_SYSTEM_NVML_VERSION, DCGM_FI_IMEX_DOMAIN_STATUS,
# DCGM_FI_DEV_FABRIC_CLUSTER_UUID, DCGM_FI_CUDA_GPU_VISIBLE_DEVICES) to EVERY
# series it exports. Prometheus identifies a series by its FULL label set, so
# any time that CSV's field list changes on a live exporter (a driver/VBIOS
# upgrade, or just editing the CSV) the exact same physical GPU starts a brand
# new series -- the old one goes stale, but stays queryable until Prometheus
# ages it out, so a query whose range/lookback spans the change sees the same
# GPU twice with two different label sets. GPU_ID_LABELS is the label subset
# that is stable across that kind of change -- the actual hardware identity --
# and every per-GPU query should aggregate on exactly this set (see per_gpu()
# below) instead of relying on the raw metric's full (unstable) label set.
#
# "host" (the Prometheus-added, optional label mirroring "hostname") is
# deliberately NOT included here -- it is not used by any panel's legend or
# join today. If a future panel's legendFormat needs {{host}}, add it via
# per_gpu()'s `extra` param for that one call site rather than promoting it
# here for everyone. GPU_I_ID/GPU_I_PROFILE (MIG instance identity) ARE
# included so `by(...)` never merges two distinct MIG instances on the same
# physical GPU into one series -- a label a series doesn't have (e.g. these two
# on a non-MIG GPU) simply doesn't contribute to the grouping key, so including
# them is harmless everywhere else.
GPU_ID_LABELS: tuple[str, ...] = (
    "job",
    "instance",
    "hostname",
    "gpu",
    "UUID",
    "modelName",
    "pci_bus_id",
    "device",
    "GPU_I_ID",
    "GPU_I_PROFILE",
)


def per_gpu(expr: str, agg: str = "max", extra: Sequence[str] = ()) -> str:
    """Wrap a per-GPU PromQL expr in `<agg> by (<GPU_ID_LABELS + extra>) (<expr>)` so
    a dcgm-exporter CSV label-set change (see GPU_ID_LABELS' docstring above) can
    never render one physical GPU as two series/legend rows. Every per-GPU range
    query in this codebase should go through this helper (CONVENTIONS.md).

    `extra`: any panel-specific label a legendFormat/override/join relies on that
    isn't already in GPU_ID_LABELS (e.g. health_watch, the XID/err_code family,
    window_size_in_ms, an NVLink/link id) -- every `{{label}}` a target's
    legendFormat references must survive the aggregation, or it silently renders
    empty.

    `agg` -- pick per the *shape* of `expr`, not by habit:
      - "max" (default): a gauge, ratio, bitmask decode, or a rate()/irate()/
        deriv() result. At any given instant only one of the split series is
        ever alive (the other has gone stale), so max is exact -- never an
        average/blend of two real values.
      - "sum": a range-integrating counter -- increase(x[$__range]),
        sum_over_time, an event count or "this range" energy total. The old and
        new series each cover only PART of the range, so they must be added,
        not maxed (max would silently undercount).
      - "avg": avg_over_time(x[$__range:]) (or similar) across a window that
        spans the split is only approximate -- each partial series' own average
        is weighted equally regardless of how much of the range it actually
        covers. Document this at the call site when used; there is no exact fix
        short of a sub-range-aware query.
    """
    labels = ", ".join(list(GPU_ID_LABELS) + list(extra))
    return f"{agg} by ({labels}) ({expr})"


# Identity used by latest_per_gpu() -- deliberately narrower than GPU_ID_LABELS. These
# three (four with job) are the only labels a raw, unaggregated identity-table query
# needs to pick "the one current series per physical GPU": instance/UUID identify the
# GPU itself, GPU_I_ID keeps each MIG instance on that GPU distinct (never merged with
# a sibling instance), and job guards against two distinct scrape jobs happening to
# reuse the same instance string. modelName/pci_bus_id/hostname/device are left out on
# purpose -- unlike per_gpu()'s aggregation, latest_per_gpu() does not collapse labels
# away, so including more identity labels here would only narrow (and could break) the
# topk grouping for no benefit.
LATEST_GPU_ID_LABELS: tuple[str, ...] = ("job", "instance", "UUID", "GPU_I_ID")


def latest_per_gpu(expr: str, base: str | None = None) -> str:
    """Dedup helper for the small set of *identity/inventory* tables (GPU Inventory id
    21, Fabric Manager/IMEX/C2C id 87, MIG id 90) that intentionally keep a raw,
    unaggregated per-series query so they can display a CSV `label`-type field
    (driver/VBIOS/serial/brand/NVML version, fabric cluster UUID, IMEX domain status,
    CUDA visible devices) as a real table column -- `lib.per_gpu()` would aggregate
    those fields away, which is exactly why these tables don't use it (see
    CONVENTIONS.md's series-identity section).

    On stock Prometheus, staleness markers already stop an *instant* query from
    returning both the old and new label-set series for one GPU across a
    custom-counters.csv field-list change (verified live) -- but a backend without
    staleness markers (e.g. VictoriaMetrics, some remote-read setups) can keep
    returning the stale row for up to the lookback (~5 min) after the change, which
    would render as a duplicate row per GPU in one of these tables. latest_per_gpu()
    is the defensive fix, verified live on production: it ANDs `expr` against a
    `topk by (<identity>) (1, timestamp(...))` selecting only the most-recently-sampled
    series per GPU identity (LATEST_GPU_ID_LABELS -- MIG instances stay distinct via
    GPU_I_ID), while leaving every label on the surviving series intact. Unlike
    per_gpu(), nothing is aggregated/collapsed: this only filters out the older
    duplicate series, so the CSV label-type fields the table exists to show are still
    there on the one row that survives.

    Rule of thumb (see CONVENTIONS.md): an identity/inventory table showing a CSV
    label-type field as a column uses latest_per_gpu(); every other per-GPU query
    uses per_gpu().

    `base`: the raw selector `timestamp()` should be taken of, when `expr` is not
    itself a bare selector but a function/filter of one (arithmetic, `==`, etc. all
    preserve the selector's full label set, so timing the original selector is
    equivalent and keeps the generated PromQL simpler to read). Example:
        latest_per_gpu(f"floor({x}/65536)", base=x)
        -> "floor(x/65536) and topk by (job, instance, UUID, GPU_I_ID) (1, timestamp(x))"
    Leave `base` unset when `expr` already *is* the bare selector.
    """
    ts_of = base if base is not None else expr
    labels = ", ".join(LATEST_GPU_ID_LABELS)
    return f"{expr} and topk by ({labels}) (1, timestamp({ts_of}))"


def with_filter(metric: str, single: bool = False) -> str:
    """metric name + the standard label filter, e.g. with_filter('DCGM_FI_DEV_GPU_UTIL')."""
    return metric + (FILTER_SINGLE if single else FILTER_ALL)


# ---------------------------------------------------------------------------
# Verified Grafana unit ids. Never hand-roll a unit string outside this table --
# tools/lint_dashboard.py checks every panel's unit against this list.
# ---------------------------------------------------------------------------

UNITS = {
    "none": "none",
    "short": "short",  # generic compact number (event/op counts with no physical unit)
    "percent": "percent",  # already-0-100 fields (GPU_UTIL, MEM_COPY_UTIL, ENC/DEC_UTIL, FAN_SPEED)
    "percentunit": "percentunit",  # native 0.0-1.0 ratios (PROF_* family, FB_USED_RATIO)
    "celsius": "celsius",
    "watt": "watt",  # single-GPU/summed power
    "kwatth": "kwatth",  # energy-this-range panels (mJ -> /3.6e9)
    "mbytes": "mbytes",  # MiB -- FB_* fields (never decmbytes, that's decimal MB, ~4.9% off)
    "bytes": "bytes",  # BAR1 aperture fields
    "rotmhz": "rotmhz",  # clock fields, raw MHz value, no x1e6 gymnastics
    "Bps": "Bps",  # PCIe/NVLink/C2C byte-rate fields (decimal bytes/sec)
    "ns": "ns",  # raw violation/reason counters graphed directly
}


def unit(name: str) -> str:
    if name not in UNITS:
        raise ValueError(f"unverified Grafana unit id: {name!r} -- add it to lib.UNITS first")
    return UNITS[name]


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def _excel_ref_ids(n: int) -> list[str]:
    """A, B, ..., Z, AA, AB, ... -- Grafana refIds are strings, any length is fine."""
    letters = string.ascii_uppercase
    out = []
    i = 0
    while len(out) < n:
        s = ""
        x = i
        while True:
            s = letters[x % 26] + s
            x = x // 26 - 1
            if x < 0:
                break
        out.append(s)
        i += 1
    return out


def target(
    expr: str,
    legend: str | None = None,
    ref_id: str = "A",
    instant: bool = False,
    hide: bool = False,
    fmt: str = "time_series",
) -> dict[str, Any]:
    """No per-target "datasource" key: this dashboard is single-datasource throughout,
    and Grafana falls back to the panel-level datasource for any target that omits
    its own, so repeating a byte-identical datasource ref on every target would only
    add dead weight to the JSON with zero functional difference. Verified live:
    importing a dashboard with this field omitted from every target renders
    identically, including the 12-target GPU Inventory join.
    tools/lint_dashboard.py's datasource check treats a missing target datasource
    as fine."""
    t: dict[str, Any] = {
        "expr": expr,
        "refId": ref_id,
        "instant": instant,
        "range": not instant,
        "hide": hide,
        "format": fmt,
    }
    if legend is not None:
        t["legendFormat"] = legend
    return t


def make_targets(specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """specs: [{'expr':..., 'legend':..., 'instant':bool, 'hide':bool, 'format':...}, ...]
    refIds are auto-assigned A, B, C, ... in order."""
    ref_ids = _excel_ref_ids(len(specs))
    out = []
    for rid, spec in zip(ref_ids, specs, strict=True):
        out.append(
            target(
                spec["expr"],
                legend=spec.get("legend"),
                ref_id=rid,
                instant=spec.get("instant", False),
                hide=spec.get("hide", False),
                fmt=spec.get("format", "time_series"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Thresholds / mappings / overrides
# ---------------------------------------------------------------------------


def thresholds(steps: Sequence[tuple[float | None, str]], mode: str = "absolute") -> dict[str, Any]:
    """steps: [(None, 'green'), (75, 'yellow'), (85, 'red')] -- first value forced to None
    (Grafana's base step) regardless of what's passed."""
    out_steps = []
    for i, (value, color) in enumerate(steps):
        out_steps.append({"color": color, "value": None if i == 0 else value})
    return {"mode": mode, "steps": out_steps}


def no_thresholds(color: str = "gray") -> dict[str, Any]:
    """A single flat "informational, no real threshold" step. Every panel in this
    dashboard should have either a real threshold or an explicit informational-only
    one, rather than leaving fieldConfig.defaults.thresholds unset."""
    return {"mode": "absolute", "steps": [{"color": color, "value": None}]}


def value_mapping(entries: Sequence[tuple[Any, str, str | None]]) -> list[dict[str, Any]]:
    """entries: [(raw_value, text, color_or_None), ...] -> one Grafana 'value'-type mapping
    covering all entries (this is how the Grafana UI itself emits multi-value mappings)."""
    options: dict[str, Any] = {}
    for i, (value, text, color) in enumerate(entries):
        entry: dict[str, Any] = {"text": text, "index": i}
        if color is not None:
            entry["color"] = color
        options[str(value)] = entry
    return [{"type": "value", "options": options}]


def range_mapping(
    frm: float | None, to: float | None, text: str, color: str | None = None, index: int = 0
) -> dict[str, Any]:
    result: dict[str, Any] = {"text": text, "index": index}
    if color is not None:
        result["color"] = color
    return {"type": "range", "options": {"from": frm, "to": to, "result": result}}


def override_by_name(field_name: str, properties: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    return {
        "matcher": {"id": "byName", "options": field_name},
        "properties": [{"id": pid, "value": pval} for pid, pval in properties],
    }


# Grafana's own test for a slash-delimited regex string: stringToJsRegex() (Grafana 13.2.2)
# requires '^/(.*?)/(g?i?m?y?s?)$' of any string starting with '/', and one that fails it
# makes a byRegexp matcher a silent no-op. JS `.` excludes all four line terminators, not
# just \n. Use with fullmatch().
GRAFANA_DELIMITED_REGEX = re.compile(r"/([^\n\r\u2028\u2029]*?)/(g?i?m?y?s?)")


def override_by_regex(pattern: str, properties: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """Field override matching a display-name regex.

    Grafana's `byRegexp` matcher (`stringToJsRegex`, Grafana 13.2.2) only treats the
    `options` string as an unanchored regular expression when it is delimited with
    slashes (`/pattern/flags`); a bare pattern like `^Free` is instead compiled fully
    anchored, as `^` + `^Free` + `$`, which only matches a display name that is exactly
    "Free" and silently makes the override a no-op (found live: panel 34's "Free" series
    stayed Grafana's default yellow instead of the intended fixed blue). Every caller in
    this codebase passes a bare Python regex string, so the delimiters are added here,
    once, instead of at each of the ~15 call sites.

    A pattern already in Grafana's delimited form, flags included (`/^free/i`, see
    GRAFANA_DELIMITED_REGEX), is passed through unchanged. Anything else is a bare regex
    and gets wrapped -- including one that merely starts or ends with a slash: `/dev`
    becomes `//dev/`, which Grafana reads back as exactly the regex `/dev`. (A bare regex
    that itself looks delimited, like `/dev/`, is ambiguous and taken as delimited;
    write its first slash as `\\/` to have it wrapped.)
    """
    wrapped = pattern if GRAFANA_DELIMITED_REGEX.fullmatch(pattern) else f"/{pattern}/"
    return {
        "matcher": {"id": "byRegexp", "options": wrapped},
        "properties": [{"id": pid, "value": pval} for pid, pval in properties],
    }


def fixed_color(color: str) -> dict[str, Any]:
    return {"mode": "fixed", "fixedColor": color}


# ---------------------------------------------------------------------------
# Transformation helpers (table panels)
# ---------------------------------------------------------------------------


def transformation(tid: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": tid, "options": options or {}}


def transform_join_by_field(field: str, mode: str = "outer") -> dict[str, Any]:
    return transformation("joinByField", {"byField": field, "mode": mode})


def transform_organize(
    exclude: Sequence[str] | None = None,
    rename: dict[str, str] | None = None,
    order: Sequence[str] | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if exclude:
        opts["excludeByName"] = dict.fromkeys(exclude, True)
    if rename:
        opts["renameByName"] = rename
    if order:
        opts["indexByName"] = {name: i for i, name in enumerate(order)}
    return transformation("organize", opts)


def table_join(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    exprs: Sequence[str],
    identity_fields: dict[str, str],
    noise_fields: Sequence[str],
    value_renames: Sequence[str | None],
    join_mode: str = "outer",
    join_field: str = "UUID",
    overrides: list[dict[str, Any]] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Multi-target table join, generalizing the join-by-UUID + organize recipe used
    by row_c.py (GPU Inventory), row_i.py (PCIe link table) and row_l.py (Fabric/MIG/
    power-profile/NVLink tables): N instant format=table targets, each sharing the
    same identity label set on the join field, -> joinByField(join_field) -> organize
    (rename each identity column's frame-1 copy to a friendly name, rename each
    query's "Value #<refId>" column, drop every duplicate-frame copy and structural
    noise field). See CONVENTIONS.md gotcha #4 for why every repeated field name
    gets a " <frame_index>" suffix (1-based, including frame 1) once it collides
    across >=2 source frames.

    `join_field` defaults to "UUID" (one row per physical GPU -- correct for every
    caller except row_l.py's MIG table). `joinByField` assumes its key is unique
    *within* each input frame: a plain "UUID" join is wrong for a table whose rows
    are finer-grained than one-per-GPU (MIG id 90's rows are one per MIG instance,
    and every instance on the same physical GPU shares the same UUID -- GPU_I_ID is
    what actually distinguishes them, per GPU_ID_LABELS/LATEST_GPU_ID_LABELS, but it
    is not part of the join key by default). For that case pass a `join_field` that
    is unique per row instead -- e.g. a composite key built with PromQL's
    `label_join(expr, "mig_key", "/", "UUID", "GPU_I_ID")` on every target -- and add
    that synthetic field to `noise_fields` so it doesn't leak through as a visible
    column (the plain "UUID" column, read off `identity_fields`/left unrenamed as
    today, still shows the clean physical UUID).

    `value_renames[i]` is the friendly column name for `exprs[i]`'s "Value #<ref>"
    column, or None to drop that column entirely (e.g. it was only a join anchor/gate,
    as in row_l.py's MIG panel).

    Live-verified gotcha (Grafana 13.2.2): joinByField only suffixes a repeated
    identity-field name with " <1-based index of surviving frames>" when *more than
    one* of the N input frames actually comes back non-empty. Any DCGM field that is
    legitimately absent on a given GPU (no NVLink, no C2C, FM error code unset, ...)
    contributes an empty frame and is simply skipped by the join -- normal and
    expected (see check_queries.py's EMPTY classification), but it shifts/removes the
    numeric suffix on every OTHER frame's identity columns. In the extreme case where
    only one target frame survives (e.g. panel 94 "NVLink health" on a GPU with no
    NVLink hardware: everything empty except the P2P-status target), joinByField
    emits identity fields with NO numeric suffix at all (there is nothing left to
    disambiguate), so a rename/exclude map built only from "<field> 1".."<field> N"
    silently matches nothing and the raw field names leak through untouched. Fix:
    also rename/exclude the bare (unsuffixed) field name -- Grafana's organize
    transform no-ops on a map key that is not present in a given render, so adding
    these extra keys is safe for every frame-count in between too."""
    ref_ids = _excel_ref_ids(len(exprs))
    targets = [target(expr, ref_id=rid, instant=True, fmt="table") for rid, expr in zip(ref_ids, exprs, strict=True)]
    n = len(exprs)

    exclude: list[str] = []
    rename: dict[str, str] = {}
    for field in noise_fields:
        exclude.append(field)
        exclude += [f"{field} {i}" for i in range(1, n + 1)]
    for field, friendly in identity_fields.items():
        rename[field] = friendly
        rename[f"{field} 1"] = friendly
        exclude += [f"{field} {i}" for i in range(2, n + 1)]
    for rid, friendly in zip(ref_ids, value_renames, strict=True):
        if friendly is None:
            exclude.append(f"Value #{rid}")
        else:
            rename[f"Value #{rid}"] = friendly

    transformations = [
        transform_join_by_field(join_field, mode=join_mode),
        transform_organize(exclude=exclude, rename=rename),
    ]
    return table(
        panel_id,
        title,
        x,
        y,
        w,
        h,
        targets,
        transformations=transformations,
        overrides=overrides or [],
        description=description,
    )


# ---------------------------------------------------------------------------
# Panel base + specific builders
# ---------------------------------------------------------------------------


def _grid(x: int, y: int, w: int, h: int) -> dict[str, int]:
    if w > 24:
        raise ValueError(f"panel width {w} > 24")
    if x + w > 24:
        raise ValueError(f"panel x({x})+w({w}) > 24")
    return {"x": x, "y": y, "w": w, "h": h}


def _base_panel(
    panel_id: int,
    title: str,
    ptype: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]] = (),
    description: str = "",
    unit_id: str = "none",
    thresholds_steps: dict[str, Any] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    min_: float | None = None,
    max_: float | None = None,
    custom: dict[str, Any] | None = None,
    color: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    links: list[dict[str, Any]] | None = None,
    display_name: str | None = None,
    no_value: str | None = None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "unit": unit(unit_id) if unit_id in UNITS else unit_id,
        "thresholds": thresholds_steps if thresholds_steps is not None else no_thresholds(),
    }
    if mappings:
        defaults["mappings"] = mappings
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    if custom:
        defaults["custom"] = custom
    if color:
        defaults["color"] = color
    if display_name:
        defaults["displayName"] = display_name
    if no_value:
        defaults["noValue"] = no_value

    panel: dict[str, Any] = {
        "id": panel_id,
        "title": title,
        "type": ptype,
        "datasource": ds_ref(),
        "gridPos": _grid(x, y, w, h),
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "targets": list(targets),
    }
    if description:
        panel["description"] = description
    if options is not None:
        panel["options"] = options
    if links:
        panel["links"] = links
    return panel


def stat(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    unit_id: str = "none",
    thresholds_steps: dict[str, Any] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    graph_mode: str = "none",
    color_mode: str = "value",
    reduce_calc: str = "lastNotNull",
    description: str = "",
    overrides: list[dict[str, Any]] | None = None,
    no_value: str | None = None,
    display_name: str | None = None,
    links: list[dict[str, Any]] | None = None,
    text_mode: str = "auto",
) -> dict[str, Any]:
    options = {
        "reduceOptions": {"calcs": [reduce_calc], "fields": "", "values": False},
        "orientation": "auto",
        "textMode": text_mode,
        "colorMode": color_mode,
        "graphMode": graph_mode,
        "justifyMode": "auto",
    }
    return _base_panel(
        panel_id,
        title,
        "stat",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        thresholds_steps=thresholds_steps,
        mappings=mappings,
        overrides=overrides,
        options=options,
        no_value=no_value,
        display_name=display_name,
        links=links,
    )


def gauge(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    unit_id: str = "percent",
    min_: float = 0,
    max_: float = 100,
    thresholds_steps: dict[str, Any] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    description: str = "",
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    options = {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "orientation": "auto",
        "showThresholdLabels": False,
        "showThresholdMarkers": True,
    }
    return _base_panel(
        panel_id,
        title,
        "gauge",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        thresholds_steps=thresholds_steps,
        mappings=mappings,
        overrides=overrides,
        min_=min_,
        max_=max_,
        options=options,
    )


def bargauge(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    unit_id: str = "none",
    min_: float | None = None,
    max_: float | None = None,
    thresholds_steps: dict[str, Any] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    description: str = "",
    display_mode: str = "gradient",
    orientation: str = "horizontal",
    overrides: list[dict[str, Any]] | None = None,
    color: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "orientation": orientation,
        "displayMode": display_mode,
        "showUnfilled": True,
    }
    return _base_panel(
        panel_id,
        title,
        "bargauge",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        thresholds_steps=thresholds_steps,
        mappings=mappings,
        overrides=overrides,
        min_=min_,
        max_=max_,
        options=options,
        color=color,
    )


def timeseries(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    unit_id: str = "none",
    min_: float | None = None,
    max_: float | None = None,
    thresholds_steps: dict[str, Any] | None = None,
    stacked: bool = False,
    description: str = "",
    overrides: list[dict[str, Any]] | None = None,
    legend_mode: str = "list",
    legend_placement: str = "bottom",
    legend_calcs: list[str] | None = None,
    legend_sort_by: str | None = None,
    legend_sort_desc: bool = False,
) -> dict[str, Any]:
    custom: dict[str, Any] = {
        "drawStyle": "line",
        "lineWidth": 1,
        "fillOpacity": 15 if stacked else 5,
        "gradientMode": "none",
        "spanNulls": False,
        "pointSize": 5,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
        "axisPlacement": "auto",
        "axisCenteredZero": False,
        "showPoints": "never",
    }
    legend: dict[str, Any] = {
        "displayMode": legend_mode,
        "placement": legend_placement,
        "calcs": legend_calcs or [],
    }
    if legend_sort_by is not None:
        # Spec section 5's "legend density" rule (panels 26, 44, 46, 70, 71): a
        # table-mode legend on a >=4-series-family panel is sorted by last value so
        # the busiest/most-recent series float to the top instead of staying in
        # query order. `legend_sort_by` is the exact calc display label Grafana's UI
        # uses in its own "sort by" dropdown for a given calcs[] entry, e.g.
        # "Last *" for calc "lastNotNull" -- pass legend_calcs=["lastNotNull"]
        # alongside this so the column actually exists to sort by.
        legend["sortBy"] = legend_sort_by
        legend["sortDesc"] = legend_sort_desc
    options = {
        "legend": legend,
        "tooltip": {"mode": "multi", "sort": "none"},
    }
    return _base_panel(
        panel_id,
        title,
        "timeseries",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        thresholds_steps=thresholds_steps,
        overrides=overrides,
        min_=min_,
        max_=max_,
        custom=custom,
        options=options,
    )


def state_timeline(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    mappings: list[dict[str, Any]] | None = None,
    description: str = "",
    overrides: list[dict[str, Any]] | None = None,
    merge_values: bool = True,
) -> dict[str, Any]:
    options = {
        "mergeValues": merge_values,
        "showValue": "auto",
        "rowHeight": 0.9,
        "legend": {"displayMode": "list", "placement": "bottom"},
    }
    return _base_panel(
        panel_id,
        title,
        "state-timeline",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id="none",
        mappings=mappings,
        overrides=overrides,
        options=options,
        thresholds_steps=no_thresholds(),
    )


def table(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    transformations: list[dict[str, Any]] | None = None,
    description: str = "",
    overrides: list[dict[str, Any]] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    unit_id: str = "none",
    no_value: str | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    options = {"showHeader": True, "cellHeight": "sm"}
    panel = _base_panel(
        panel_id,
        title,
        "table",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        mappings=mappings,
        overrides=overrides,
        options=options,
        thresholds_steps=no_thresholds(),
        custom={"align": "auto", "cellOptions": {"type": "auto"}},
        no_value=no_value,
        links=links,
    )
    if transformations:
        panel["transformations"] = transformations
    return panel


def heatmap(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    bucket_size: float,
    unit_id: str = "none",
    description: str = "",
    min_: float | None = None,
    max_: float | None = None,
) -> dict[str, Any]:
    """calculate:true heatmap over a raw scalar gauge -- see row_d.py panels 29/30 for
    an example. min_/max_ pin the underlying value field's range so a zero-variance (idle GPU)
    sample set buckets into a thin band at its true value instead of Grafana's
    client-side bucketer spreading a single repeated value into one fully-saturated
    band across the *entire* auto-detected range -- visually indistinguishable from
    'constantly at the max' at a glance. Pass the metric's real domain (e.g. 0-100 for
    a percent field, 0-1 for percentunit)."""
    options = {
        "calculate": True,
        "calculation": {"xBuckets": {"mode": "size"}, "yBuckets": {"mode": "size", "value": str(bucket_size)}},
        "color": {"mode": "scheme", "scheme": "Turbo", "steps": 64},
        "yAxis": {"unit": unit(unit_id) if unit_id in UNITS else unit_id},
        "cellGap": 1,
        "tooltip": {"show": True, "yHistogram": True},
        "legend": {"show": True},
    }
    defaults: dict[str, Any] = {}
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    panel: dict[str, Any] = {
        "id": panel_id,
        "title": title,
        "type": "heatmap",
        "datasource": ds_ref(),
        "gridPos": _grid(x, y, w, h),
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "targets": list(targets),
        "options": options,
    }
    if description:
        panel["description"] = description
    return panel


def histogram(
    panel_id: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: Sequence[dict[str, Any]],
    unit_id: str = "none",
    description: str = "",
) -> dict[str, Any]:
    """Native 'histogram' panel type -- auto-bucketing, not the heatmap panel."""
    options = {"bucketOffset": 0, "legend": {"showLegend": True}}
    return _base_panel(
        panel_id,
        title,
        "histogram",
        x,
        y,
        w,
        h,
        targets,
        description=description,
        unit_id=unit_id,
        options=options,
        thresholds_steps=no_thresholds(),
    )


def text(
    panel_id: int, title: str, x: int, y: int, w: int, h: int, content: str, mode: str = "markdown"
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "title": title,
        "type": "text",
        "gridPos": _grid(x, y, w, h),
        "options": {"mode": mode, "content": content},
        "fieldConfig": {"defaults": {}, "overrides": []},
        "targets": [],
    }


def query_variable(
    name: str,
    label: str,
    query: str,
    multi: bool = True,
    include_all: bool = True,
    all_value: str = ".*",
    refresh: int = 2,
    sort: int = 1,
) -> dict[str, Any]:
    """A Prometheus label_values(...) template variable. multi+includeAll+a real
    allValue (never the literal string 'All' reaching a =~ matcher) are always set
    together: an includeAll variable whose allValue is left at Grafana's default
    ('All') would substitute the literal text "All" into a =~ regex filter and match
    nothing, so allValue is always given a real regex like ".*" instead."""
    return {
        "current": {},
        "datasource": ds_ref(),
        "definition": query,
        "hide": 0,
        "includeAll": include_all,
        "multi": multi,
        "name": name,
        "label": label,
        "options": [],
        "query": {"query": query, "refId": "StandardVariableQuery"},
        "refresh": refresh,
        "regex": "",
        "skipUrlSync": False,
        "sort": sort,
        "type": "query",
        "allValue": all_value,
    }


def textbox_variable(name: str, label: str, default: str) -> dict[str, Any]:
    return {
        "current": {"value": default, "text": default},
        "hide": 0,
        "name": name,
        "label": label,
        "options": [],
        "query": default,
        "skipUrlSync": False,
        "type": "textbox",
    }


def annotation_query(name: str, expr: str, icon_color: str, title_format: str) -> dict[str, Any]:
    """A Prometheus-backed annotation query (dashboard-level, not a panel) -- draws a
    vertical marker on every timeseries panel wherever `expr` returns a sample.
    Completeness gap: the dashboard previously had zero automatic annotations (only
    the built-in manual layer), so a driver reload (which resets the cumulative
    energy counter panel 52 depends on) or an actual Xid fault left no on-screen trace."""
    return {
        "datasource": ds_ref(),
        "enable": True,
        "expr": expr,
        "iconColor": icon_color,
        "name": name,
        "titleFormat": title_format,
    }


def dashboard_link(title: str, url: str) -> dict[str, Any]:
    return {
        "asDropdown": False,
        "icon": "external link",
        "includeVars": False,
        "keepTime": False,
        "tags": [],
        "targetBlank": True,
        "title": title,
        "tooltip": "",
        "type": "link",
        "url": url,
    }


def row(
    panel_id: int,
    title: str,
    collapsed: bool = False,
    repeat: str | None = None,
) -> dict[str, Any]:
    """A row panel skeleton. gridPos/panels are filled in by the layout engine
    (see build_layout below) -- row modules never set gridPos.y themselves."""
    r: dict[str, Any] = {
        "id": panel_id,
        "title": title,
        "type": "row",
        "collapsed": collapsed,
        "panels": [],
    }
    if repeat:
        r["repeat"] = repeat
    return r


# ---------------------------------------------------------------------------
# Decode library: NVIDIA/DCGM enum and bitmask mappings shared by every row.
# ---------------------------------------------------------------------------

_PSTATE_COLORS_16 = [
    "dark-green",
    "green",
    "semi-dark-green",
    "light-green",
    "dark-blue",
    "blue",
    "semi-dark-blue",
    "light-blue",
    "dark-purple",
    "purple",
    "semi-dark-purple",
    "light-purple",
    "dark-orange",
    "orange",
    "semi-dark-orange",
    "light-orange",
]


def pstate_mappings(colored: bool = False) -> list[dict[str, Any]]:
    """DCGM_FI_DEV_PSTATE, 0-15, format 'P${value}'. colored=False for the compact
    per-GPU stat (id 19: no color, since P0 isn't inherently good/bad); colored=True
    for the P-State history state-timeline (id 41, needs a distinct hue per state)."""
    entries = []
    for v in range(16):
        color = _PSTATE_COLORS_16[v] if colored else None
        entries.append((v, f"P{v}", color))
    return value_mapping(entries)


def compute_mode_mappings() -> list[dict[str, Any]]:
    return value_mapping(
        [
            (0, "Default", "green"),
            (1, "Exclusive Thread (deprecated)", "yellow"),
            (2, "Prohibited", "red"),
            (3, "Exclusive Process", "blue"),
        ]
    )


def ecc_mode_mappings() -> list[dict[str, Any]]:
    return value_mapping(
        [
            (0, "Disabled", "gray"),
            (1, "Enabled", "green"),
        ]
    )


def virtual_mode_mappings() -> list[dict[str, Any]]:
    return value_mapping(
        [
            (0, "None (bare-metal)", None),
            (1, "Passthrough", None),
            (2, "vGPU", None),
            (3, "Host vGPU", None),
            (4, "Host vSGA", None),
        ]
    )


def recovery_action_mappings() -> list[dict[str, Any]]:
    """DCGM_FI_DEV_GPU_RECOVERY_ACTION -> nvmlDeviceGpuRecoveryAction_t. VERIFIED against
    the installed /usr/local/cuda-13.4/targets/x86_64-linux/include/nvml.h (line 2430),
    which defines all 8 values -- the original 5-entry mapping had 3/4 backwards
    ("Drain + Reset"/"Drain P2P + Reset", swapped vs. the header's actual 3=Drain P2P
    (no reset)/4=Drain P2P AND Reset) and was missing 5/6/7 entirely (would have
    rendered as a bare unmapped number with no text/color on any GPU new enough to
    report them)."""
    return value_mapping(
        [
            (0, "None", "green"),
            (1, "GPU Reset", "yellow"),
            (2, "Node Reboot", "red"),
            (3, "Drain P2P", "orange"),
            (4, "Drain P2P + Reset", "orange"),
            (5, "Recover IMEX Domain", "orange"),
            (6, "Bus Reset", "red"),
            (7, "System Reboot", "red"),
        ]
    )


def bool_reliability_mappings() -> list[dict[str, Any]]:
    """ROW_REMAP_FAILURE, ROW_REMAP_PENDING, SRAM_EXCEEDED, MEMORY_UNREPAIRABLE,
    RETIRED_PENDING: 0->No (green), 1->Yes (red)."""
    return value_mapping(
        [
            (0, "No", "green"),
            (1, "Yes", "red"),
        ]
    )


def bool_config_mappings(off_text: str = "Off", on_text: str = "On") -> list[dict[str, Any]]:
    """FABRIC_MANAGER_STATUS, C2C_LINK_STATUS, VGPU_LICENSE_STATUS, MIG_MODE,
    CLOCKS_AUTOBOOST_MODE, PERSISTENCE_MODE, CC_MODE: 0->Off/No (gray), 1->On/Yes (green)."""
    return value_mapping(
        [
            (0, off_text, "gray"),
            (1, on_text, "green"),
        ]
    )


def health_status_mappings() -> list[dict[str, Any]]:
    """DCGM_EXP_GPU_HEALTH_STATUS: 0 Pass, 10 Warn, 20 Fail, else Unknown.
    Grafana evaluates mappings in array order and stops at the first match, so the
    exact-value mappings must precede the catch-all range. NOTE: Grafana value
    mappings cannot interpolate the matched value into text (no ${value} token) --
    the spec's "Unknown (${value})" is approximated here as literal "Unknown"; the
    raw numeric value is still visible in the tooltip/legend for anyone who needs it."""
    exact = value_mapping(
        [
            (0, "Pass", "green"),
            (10, "Warn", "yellow"),
            (20, "Fail", "red"),
        ]
    )
    fallback = range_mapping(None, None, "Unknown", "orange", index=3)
    return [*exact, fallback]


# NVIDIA Xid-errors reference table (release 615 documentation).
XID_TABLE: list[tuple[int, str, str]] = [
    (43, "Reset channel (app killed, GPU recovers)", "green"),
    (63, "Row/page retirement event (self-healed)", "green"),
    (94, "Contained ECC error", "green"),
    (13, "Graphics/compute exception (app bug)", "yellow"),
    (31, "GPU memory page fault", "yellow"),
    (61, "PMU breakpoint", "yellow"),
    (68, "NVDEC video-processor exception", "yellow"),
    (69, "Graphics engine class error", "yellow"),
    (92, "Excessive correctable ECC interrupts", "yellow"),
    (32, "PBDMA push-buffer error", "red"),
    (45, "Preemptive context removal (correlate with prior Xid)", "orange"),
    (48, "Double-bit ECC error (uncorrectable)", "red"),
    (62, "PMU halt error", "red"),
    (64, "Row-remap/page-retirement failure", "red"),
    (74, "NVLink error", "red"),
    (79, "GPU has fallen off the bus", "red"),
    (95, "Uncontained ECC error", "red"),
    (109, "Context-switch timeout", "red"),
    (110, "SEC2 fault", "red"),
    (119, "GSP RPC timeout", "red"),
    (120, "GSP firmware error", "red"),
    (121, "C2C error", "red"),
    (140, "Unrecoverable ECC error", "red"),
    (143, "GPU init error", "red"),
]
# 144-150: NVLink sub-link-layer error family, all red -- expand as a range below.

# The subset of XID_TABLE that is self-healing/informational (green), not operator-
# actionable -- derived from XID_TABLE itself so it can never drift out of sync with
# the drill-down table's own severity classification. Used to keep a top-strip/alarm
# tile from painting solid red for e.g. Xid 43 (an app crash the GPU already recovered
# from) exactly the same as a genuine Xid 79 (fallen off the bus).
BENIGN_XID_CODES: list[int] = sorted(xid for xid, _, color in XID_TABLE if color == "green")


def xid_mappings() -> list[dict[str, Any]]:
    """See health_status_mappings() docstring re: no ${value} interpolation in
    Grafana value mappings -- 'Unknown Xid' is used instead of 'Unknown Xid ${value}'."""
    exact = value_mapping([(xid, name, color) for xid, name, color in XID_TABLE])
    nvlink_family = range_mapping(144, 150, "NVLink sub-link-layer error", "red", index=len(XID_TABLE))
    fallback = range_mapping(
        None, None, "Unknown Xid — see docs.nvidia.com/deploy/xid-errors/", "orange", index=len(XID_TABLE) + 1
    )
    return [*exact, nvlink_family, fallback]


# Clock-event-reason bitmask (DCGM_FI_DEV_CLOCKS_EVENT_REASONS), all 11 bits.
# 'nature' values: 'benign' (informational/expected), 'throttle' (real throttle,
# counts toward the "GPUs throttled now" tile and any throttle alarm), or
# 'clock_limit_info': bits 0x200 BOARD_LIMIT and 0x400 RELIABILITY are
# informational clock limits, NOT throttling (nvidia-smi -q -d PERFORMANCE shows
# "Reliability: Active" while idling in P8) -- excluded from any throttle
# tile/alarm, colored blue/yellow (not red) in the bitmask timeline.
CLOCK_EVENT_BITS: dict[str, tuple[int, str]] = {
    "GPU_IDLE": (0x001, "benign"),
    "APP_CLOCKS": (0x002, "benign"),
    "SW_POWER_CAP": (0x004, "throttle"),
    "HW_SLOWDOWN": (0x008, "throttle"),
    "SYNC_BOOST": (0x010, "benign"),
    "SW_THERMAL": (0x020, "throttle"),
    "HW_THERMAL": (0x040, "throttle"),
    "HW_POWER_BRAKE": (0x080, "throttle"),
    "DISPLAY_CLOCKS": (0x100, "benign"),
    "BOARD_LIMIT": (0x200, "clock_limit_info"),
    "RELIABILITY": (0x400, "clock_limit_info"),
}

# Only these 5 bits count as "real throttle now". BOARD_LIMIT/RELIABILITY are
# informational clock limits, not throttling, and are deliberately excluded --
# summing all 7 non-benign bits would wrongly count them as throttling too.
REAL_THROTTLE_BITS: list[int] = sorted(
    bit for bit, nature in CLOCK_EVENT_BITS.values() if nature == "throttle"
)  # [4, 8, 32, 64, 128]

# SW_POWER_CAP (0x4) is real throttling but expected any time a GPU is running at
# its configured power limit under full load -- not a fault the way the other 4
# real-throttle bits are (those indicate thermal or external power-brake distress).
# Anything that alarms on "is this GPU actually in trouble" should use this set
# instead of REAL_THROTTLE_BITS; anything that just wants "is this GPU throttled
# at all, for any reason" (including the benign power-cap case) keeps using
# REAL_THROTTLE_BITS/any_real_throttle_expr.
REAL_FAULT_THROTTLE_BITS: list[int] = [b for b in REAL_THROTTLE_BITS if b != 0x004]  # [8, 32, 64, 128]

CLOCK_EVENT_COLOR = {"benign": "gray", "throttle": "red", "clock_limit_info": "blue"}


def clock_event_bit_expr(field_expr: str, bit: int) -> str:
    """Prometheus has no bitwise-AND (as of 3.14 / any released version) -- the sole
    decode method is floor-division: floor(x/bit) % 2 -- yields exactly 0 or 1."""
    return f"floor({field_expr}/{bit}) % 2"


def any_bits_set_expr(field_expr: str, bits: list[int]) -> str:
    """'Is any of these decode bits set' -- the general form of any_real_throttle_expr
    for an arbitrary bit subset (e.g. REAL_FAULT_THROTTLE_BITS)."""
    terms = " + ".join(clock_event_bit_expr(field_expr, bit) for bit in bits)
    return f"( {terms} ) > 0"


def any_real_throttle_expr(field_expr: str) -> str:
    """'Is any real-throttle bit set' (5 bits, excludes the two informational
    clock-limit bits 0x200/0x400). Used by row A's 'GPUs throttled now' tile and
    any throttle alarm rule."""
    return any_bits_set_expr(field_expr, REAL_THROTTLE_BITS)


def any_real_fault_throttle_expr(field_expr: str) -> str:
    """'Is any *fault* real-throttle bit set' -- REAL_FAULT_THROTTLE_BITS (the 4 real-
    throttle bits that indicate an actual problem, excluding the merely-expected
    SW_POWER_CAP). Use this for an alarm; use any_real_throttle_expr for a plain
    'throttled for any reason, including the benign one' count."""
    return any_bits_set_expr(field_expr, REAL_FAULT_THROTTLE_BITS)


def cuda_compute_capability_major_minor(field_expr: str) -> tuple[str, str]:
    """(major << 16 | minor), NOT <<32. Verified live: 786432 -> major=12, minor=0."""
    major = f"floor({field_expr}/65536)"
    minor = f"({field_expr} - floor({field_expr}/65536)*65536)"
    return major, minor


def cuda_driver_version_major_minor(field_expr: str) -> tuple[str, str]:
    """major*1000 + minor*10. Verified live: 13040 -> 13.4."""
    major = f"floor({field_expr}/1000)"
    minor = f"floor(({field_expr} - floor({field_expr}/1000)*1000)/10)"
    return major, minor


# ---------------------------------------------------------------------------
# Row context + layout engine
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RowContext:
    """Passed to every row module's build(ctx). Currently just the shared constants
    (kept as an object, not bare module globals, so a future multi-dashboard build
    or a test harness can substitute a different filter/legend without editing every
    row module)."""

    filter_all: str = FILTER_ALL
    filter_single: str = FILTER_SINGLE
    filter_up: str = FILTER_UP
    legend_std: str = LEGEND_STD


def rects_overlap(a: dict[str, int], b: dict[str, int]) -> bool:
    """Public so tools/lint_dashboard.py can import this exact algorithm instead of
    keeping its own duplicate copy, which would risk the two silently drifting
    apart."""
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


_rects_overlap = rects_overlap  # internal alias, kept so call sites below read unchanged


def _check_no_overlap(panels: list[dict[str, Any]], context: str) -> None:
    for i in range(len(panels)):
        gi = panels[i]["gridPos"]
        if gi["w"] > 24 or gi["x"] + gi["w"] > 24:
            raise ValueError(
                f"{context}: panel id={panels[i].get('id')} title={panels[i].get('title')!r} "
                f"has gridPos width/x+w > 24: {gi}"
            )
        for j in range(i + 1, len(panels)):
            gj = panels[j]["gridPos"]
            if _rects_overlap(gi, gj):
                raise ValueError(
                    f"{context}: panels id={panels[i].get('id')} ({panels[i].get('title')!r}) "
                    f"and id={panels[j].get('id')} ({panels[j].get('title')!r}) overlap: {gi} vs {gj}"
                )


def build_layout(row_modules: Sequence[Any], ctx: RowContext | None = None) -> list[dict[str, Any]]:
    """Stacks row modules top-to-bottom and returns the flat list of top-level
    dashboard panels (row panels + their expanded children as siblings; collapsed
    rows carry their children inside row['panels']).

    Each module must expose build(ctx) -> (row_def_or_None, panels). panels use a
    *relative* gridPos.y:
      - row_def is None (row A, no wrapper): y is absolute-from-0 within that
        pseudo-row (i.e. row A starts the whole dashboard at y=0).
      - row_def given, collapsed=False (expanded row): y is relative to the row's
        own gridPos.y (e.g. first sub-row's children start at y=1, one unit below
        the row header).
      - row_def given, collapsed=True: y is relative to 0 inside the row panel's
        own panels array (Grafana's convention for a collapsed row's children).
    """
    ctx = ctx or RowContext()
    cursor = 0
    top_level: list[dict[str, Any]] = []
    seen_ids: dict[int, str] = {}

    def _register_id(pid: int, label: str) -> None:
        if pid in seen_ids:
            raise ValueError(f"duplicate panel id {pid}: {seen_ids[pid]!r} and {label!r}")
        seen_ids[pid] = label

    for mod in row_modules:
        row_def, panels = mod.build(ctx)
        panels = [copy.deepcopy(p) for p in panels]

        if row_def is None:
            # Row A: no row wrapper, panels already absolute-from-0.
            for p in panels:
                p["gridPos"]["y"] += cursor
                _register_id(p["id"], p.get("title", ""))
            _check_no_overlap(panels, "row A (top strip)")
            top_level.extend(panels)
            row_height = max((p["gridPos"]["y"] - cursor) + p["gridPos"]["h"] for p in panels) if panels else 0
            cursor += row_height
            continue

        row_def = copy.deepcopy(row_def)
        _register_id(row_def["id"], row_def.get("title", ""))
        row_def["gridPos"] = {"x": 0, "y": cursor, "w": 24, "h": 1}

        if row_def.get("collapsed"):
            for p in panels:
                _register_id(p["id"], p.get("title", ""))
            _check_no_overlap(panels, f"row {row_def['title']!r} (collapsed children)")
            row_def["panels"] = panels
            top_level.append(row_def)
            cursor += 1  # only the header occupies space while collapsed
        else:
            for p in panels:
                p["gridPos"]["y"] += cursor
                _register_id(p["id"], p.get("title", ""))
            row_def["panels"] = []
            top_level.append(row_def)
            top_level.extend(panels)
            if panels:
                _check_no_overlap(panels, f"row {row_def['title']!r}")
                row_height = max((p["gridPos"]["y"] - cursor) + p["gridPos"]["h"] for p in panels)
            else:
                row_height = 1
            cursor += row_height

    return top_level
