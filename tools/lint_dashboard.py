#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Static checks on a built dashboard JSON.

Usage: uv run tools/lint_dashboard.py <json_path> [--csv PATH]

Hard errors (non-zero exit):
  - duplicate panel ids
  - gridPos overlap, or width/x+w > 24, within a coordinate space (the top-level
    dashboard, or a single collapsed row's own nested panels array)
  - a panel or target datasource that isn't exactly {"type":"prometheus",
    "uid":"${DS_PROMETHEUS}"}
  - a panel unit not in dcgm_dashboard.lib.UNITS (the verified-unit-id allowlist)
  - a fieldConfig override with matcher id "byRegexp" whose options string isn't
    slash-delimited (see CONVENTIONS.md's byRegexp gotcha: a bare pattern silently
    becomes a no-op in Grafana 13.2.2 instead of erroring, so this is the only thing
    that catches it before a rendered-dashboard visual check)
  - a target expr containing the literal substring "273.15" (the signature of adding
    273.15 to a Celsius field to force a Kelvin ratio, which produces misleading
    percentages/false alarms at normal operating temperatures; see
    dcgm_dashboard/rows/row_a.py and row_b.py's module docstrings)

Warnings (reported, do not fail the run):
  - CSV fields (from custom-counters.csv) that appear nowhere in the dashboard JSON
    -- every field in the CSV should be used by at least one panel; a warning here
    usually means either a new CSV field a row hasn't picked up yet, or a field that
    should be dropped from the CSV
  - rate()/increase() applied to a field whose CSV type isn't "counter"
"""

import argparse
import json
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from dcgm_dashboard import lib  # noqa: E402

DEFAULT_CSV = PROJECT_ROOT / "custom-counters.csv"
EXPECTED_DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}


def load_csv_fields(csv_path: pathlib.Path) -> dict:
    fields = {}
    with open(csv_path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = line.split(",")  # only parts[0:2] are read below; no maxsplit needed to bound them
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            ftype = parts[1].strip()
            if name:
                fields[name] = ftype
    return fields


def collect_panels(panels, space="top"):
    """Yields (panel, space) for every non-row panel and every row panel itself.
    'space' groups panels into the coordinate system their gridPos is measured in:
    'top' for the dashboard's own top-level panels, or 'row<id>' for a collapsed
    row's nested panels array."""
    out = []
    for p in panels:
        out.append((p, space))
        if p.get("type") == "row" and p.get("collapsed"):
            out.extend(collect_panels(p.get("panels", []), space=f"row{p.get('id')}"))
    return out


rects_overlap = lib.rects_overlap  # reuse the generation-time check instead of a second copy


def check_ids_and_overlap(all_panels, errors):
    seen = {}
    for p, _ in all_panels:
        pid = p.get("id")
        if pid is None:
            errors.append(f"panel {p.get('title')!r} has no id")
            continue
        if pid in seen:
            errors.append(f"duplicate panel id {pid}: {seen[pid]!r} and {p.get('title')!r}")
        else:
            seen[pid] = p.get("title")

    by_space = {}
    for p, space in all_panels:
        by_space.setdefault(space, []).append(p)
    for space, plist in by_space.items():
        for p in plist:
            g = p.get("gridPos")
            if not g:
                continue
            if g["w"] > 24 or g["x"] + g["w"] > 24:
                errors.append(f"[{space}] panel id={p.get('id')} {p.get('title')!r} gridPos width/x+w>24: {g}")
        for i in range(len(plist)):
            gi = plist[i].get("gridPos")
            if not gi:
                continue
            for j in range(i + 1, len(plist)):
                gj = plist[j].get("gridPos")
                if not gj:
                    continue
                if rects_overlap(gi, gj):
                    errors.append(
                        f"[{space}] panels id={plist[i].get('id')} ({plist[i].get('title')!r}) and "
                        f"id={plist[j].get('id')} ({plist[j].get('title')!r}) overlap: {gi} vs {gj}"
                    )


def check_datasources(all_panels, errors):
    for p, _ in all_panels:
        if p.get("type") in ("row", "text"):
            continue
        ds = p.get("datasource")
        if ds is not None and ds != EXPECTED_DS:
            errors.append(f"panel id={p.get('id')} {p.get('title')!r} datasource != {EXPECTED_DS}: {ds}")
        for t in p.get("targets", []):
            tds = t.get("datasource")
            if tds is not None and tds != EXPECTED_DS:
                errors.append(
                    f"panel id={p.get('id')} {p.get('title')!r} target[{t.get('refId')}] "
                    f"datasource != {EXPECTED_DS}: {tds}"
                )


def check_units(all_panels, errors):
    valid = set(lib.UNITS.values())
    for p, _ in all_panels:
        unit = p.get("fieldConfig", {}).get("defaults", {}).get("unit")
        if unit is not None and unit not in valid:
            errors.append(f"panel id={p.get('id')} {p.get('title')!r} unknown/unverified unit: {unit!r}")


def check_byregexp_delimited(all_panels, errors):
    """A `byRegexp` matcher's `options` must be slash-delimited (`/pattern/flags`,
    exactly as Grafana 13.2.2 parses it: lib.GRAFANA_DELIMITED_REGEX) to match anywhere
    in a display name -- a bare pattern silently compiles fully anchored, and a string
    starting with '/' that Grafana can't parse (e.g. bad flags) disables the matcher, so
    either way the override becomes a no-op with no error anywhere except a rendered
    panel (see CONVENTIONS.md's byRegexp gotcha). `lib.override_by_regex()` always
    produces the delimited form, so a failure here means an override was hand-rolled
    instead of going through it."""
    for p, _ in all_panels:
        for ov in p.get("fieldConfig", {}).get("overrides", []):
            matcher = ov.get("matcher", {})
            if matcher.get("id") != "byRegexp":
                continue
            options = matcher.get("options", "")
            if not (isinstance(options, str) and lib.GRAFANA_DELIMITED_REGEX.fullmatch(options)):
                errors.append(
                    f"panel id={p.get('id')} {p.get('title')!r} byRegexp override options not "
                    f"slash-delimited (silently matches nothing in Grafana 13.2.2): {options!r}"
                )


def check_no_kelvin(all_panels, errors):
    """No target expr may contain "273.15" -- the signature of adding 273.15 to a
    Celsius field to force a Kelvin ratio before dividing, which produces misleading
    percentages/false alarms at normal operating temperatures. Celsius-only
    ratios/margins are the correct pattern; see row_a.py panel 10 and row_b.py
    panel 17."""
    for p, _ in all_panels:
        for t in p.get("targets", []):
            expr = t.get("expr")
            if expr and "273.15" in expr:
                errors.append(
                    f"panel id={p.get('id')} {p.get('title')!r} target[{t.get('refId')}] "
                    f"expr contains '273.15' (Kelvin-ratio bug pattern): {expr!r}"
                )


def check_csv_coverage(dashboard_text, csv_fields, warnings):
    unused = [name for name in csv_fields if name not in dashboard_text]
    if unused:
        warnings.append(f"{len(unused)}/{len(csv_fields)} CSV fields not referenced anywhere in the dashboard JSON:")
        for name in unused:
            warnings.append(f"    unused: {name} ({csv_fields[name]})")


def check_rate_over_gauge(all_panels, csv_fields, warnings):
    pattern = re.compile(r"(?:rate|increase)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*[\{\[]")
    for p, _ in all_panels:
        for t in p.get("targets", []):
            expr = t.get("expr")
            if not expr:
                continue
            for m in pattern.finditer(expr):
                field = m.group(1)
                ftype = csv_fields.get(field)
                if ftype is not None and ftype != "counter":
                    warnings.append(
                        f"panel id={p.get('id')} {p.get('title')!r} target[{t.get('refId')}] "
                        f"applies rate()/increase() to {field!r}, CSV type={ftype!r} (expected counter)"
                    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    args = ap.parse_args()

    with open(args.json_path) as f:
        dashboard_text = f.read()
        dashboard = json.loads(dashboard_text)

    csv_fields = load_csv_fields(pathlib.Path(args.csv))
    all_panels = collect_panels(dashboard.get("panels", []))

    errors: list = []
    warnings: list = []

    check_ids_and_overlap(all_panels, errors)
    check_datasources(all_panels, errors)
    check_units(all_panels, errors)
    check_byregexp_delimited(all_panels, errors)
    check_no_kelvin(all_panels, errors)
    check_csv_coverage(dashboard_text, csv_fields, warnings)
    check_rate_over_gauge(all_panels, csv_fields, warnings)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    print(f"\n{len(all_panels)} panels checked, {len(errors)} error(s), {len(warnings)} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
