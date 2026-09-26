#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Execute every panel target's PromQL expression against Prometheus and report
whether it returns data.

Usage: uv run tools/check_queries.py <json_path> [--panels 1,7,21] [--prom URL]

Walks every panel, including nested panels of a collapsed row and the (already
top-level, per our layout engine) children of a repeated row. For each target:
  - $job / $instance / $gpu -> .*
  - $__rate_interval -> 1m, $__range -> 1h, $__interval -> 15s
  - every textbox template variable -> its "current.value" from the dashboard's own
    templating list (so this stays in sync with build_dashboard.py automatically)
  - ${DS_PROMETHEUS} is a datasource reference, never part of an expr string; if one
    somehow appears in an expr it is left as-is (Prometheus will just 400 on it,
    which is reported like any other query error, not silently fixed up)
then queries <prom>/api/v1/query (instant, at "now").

Prints one line per target: "panel <id> <title> [<refId>]: OK(n=<series>, sample=<v>)"
/ "EMPTY" / "ERROR(<msg>)". Exit code is non-zero iff at least one target ERRORed
(EMPTY is not a failure -- many panels are legitimately sparse/empty on a
single-GPU, no-NVLink/MIG/vGPU environment).

--prom (alias --prom-url, kept for backward compatibility) defaults to:
http://localhost:9090 -- there is no local Prometheus/Grafana in this
project anymore (see README.md). Override it to point at any other Prometheus.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PROM_URL = "http://localhost:9090"

BUILTIN_SUBS = {
    "$__rate_interval": "1m",
    "$__range_s": "3600",  # matches $__range: "1h" below -- must substitute before $__range
    "$__range": "1h",
    "$__interval": "15s",
}
# $job/$instance are resolved per-run (see fetch_dcgm_job_instance_regex()) rather
# than a module-level constant: this dashboard's own $job/$instance variables are
# label_values(...) queries scoped to DCGM_FI_DEV_GPU_UTIL (see build_dashboard.py's
# build_templating()), so on a shared production Prometheus with unrelated jobs, a
# blanket ".*" would query far more than this dashboard's own "All" ever would
# (harmless for this script's OK/EMPTY/ERROR check, but wasteful and misleading in
# its own right -- see tools/check_series.py, which hits an actual false positive
# from this same looseness on panel 100's `up{...}` query). $gpu (a UUID string) has
# no such collision risk with unrelated jobs, so it stays ".*".
QUERY_VAR_SUBS = {"gpu": ".*"}


def load_textbox_defaults(dashboard: dict) -> dict:
    out = {}
    for v in dashboard.get("templating", {}).get("list", []):
        if v.get("type") == "textbox":
            out[v["name"]] = v.get("current", {}).get("value", v.get("query", ""))
    return out


def fetch_a_real_gpu_uuid(prom_url: str) -> str:
    """Row B's repeated panels use plain equality (UUID="$gpu", a single value per
    repetition) rather than the regex match every other panel uses -- substituting
    ".*" there would look for the literal string ".*" as a UUID and always come back
    EMPTY even for a perfectly correct panel. Fetch one real UUID from Prometheus to
    substitute into that equality context instead; regex contexts still get ".*"."""
    url = prom_url.rstrip("/") + "/api/v1/label/UUID/values"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        values = body.get("data") or []
        if values:
            return values[0]
    except Exception:
        pass
    return ".*"  # best-effort fallback; equality checks may then read EMPTY honestly


def label_values(prom_url: str, label: str, match_expr: str) -> list:
    """GET /api/v1/label/<label>/values?match[]=<match_expr> -- the real Prometheus
    API behind Grafana's `label_values(<selector>, <label>)` template query."""
    url = (
        prom_url.rstrip("/")
        + "/api/v1/label/"
        + urllib.parse.quote(label, safe="")
        + "/values?"
        + urllib.parse.urlencode({"match[]": match_expr})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("data") or []
    except Exception:
        return []


def fetch_dcgm_job_instance_regex(prom_url: str) -> tuple:
    """See QUERY_VAR_SUBS' comment: this dashboard's $job/$instance are label_values
    queries scoped to DCGM_FI_DEV_GPU_UTIL, so that metric's real values (alternated
    into a regex) are the faithful stand-in for Grafana's "All", not a blanket ".*"."""
    jobs = label_values(prom_url, "job", "DCGM_FI_DEV_GPU_UTIL")
    job_regex = "|".join(jobs) if jobs else ".*"
    instances = label_values(prom_url, "instance", f'DCGM_FI_DEV_GPU_UTIL{{job=~"{job_regex}"}}')
    instance_regex = "|".join(instances) if instances else ".*"
    return job_regex, instance_regex


def substitute(expr: str, textbox_defaults: dict, gpu_equals_value: str, query_var_subs: dict) -> str:
    # Fix the one equality (non-regex) use of $gpu *before* the generic pass, so the
    # generic ".*" substitution below only ever lands inside a regex (=~) context.
    expr = re.sub(r'UUID="\$\{?gpu\}?"', f'UUID="{gpu_equals_value}"', expr)

    for token, value in BUILTIN_SUBS.items():
        expr = expr.replace(token, value)
    all_vars = dict(query_var_subs)
    all_vars.update(textbox_defaults)
    for name, value in all_vars.items():
        # ${name} or $name, not followed by another identifier char (so $job doesn't
        # eat into some future $jobxyz).
        expr = re.sub(r"\$\{" + re.escape(name) + r"\}", value, expr)
        expr = re.sub(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", value, expr)
    return expr


def iter_panels_with_context(panels, row_title=None):
    """Yields (panel, row_title_or_None) for every non-row panel, recursing into a
    collapsed row's nested panels array."""
    for p in panels:
        if p.get("type") == "row":
            yield from iter_panels_with_context(p.get("panels", []), row_title=p.get("title"))
        else:
            yield p, row_title


def classify(prom_url: str, expr: str) -> tuple:
    """Returns (status, detail) where status in {"OK","EMPTY","ERROR"}."""
    url = prom_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", str(e))
        except Exception:
            msg = str(e)
        return "ERROR", msg
    except Exception as e:  # network error, timeout, bad JSON, ...
        return "ERROR", str(e)

    if body.get("status") != "success":
        return "ERROR", body.get("error", str(body))

    result = body.get("data", {}).get("result", [])
    if not result:
        return "EMPTY", ""
    n = len(result)
    sample = None
    r0 = result[0]
    if "value" in r0:
        sample = r0["value"][1]
    elif r0.get("values"):
        sample = r0["values"][-1][1]
    return "OK", f"n={n}, sample={sample}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument("--panels", default=None, help="comma-separated panel ids to restrict to")
    ap.add_argument(
        "--prom",
        "--prom-url",
        dest="prom",
        default=DEFAULT_PROM_URL,
        help=f"Prometheus base URL (default: {DEFAULT_PROM_URL})",
    )
    args = ap.parse_args()
    args.prom_url = args.prom  # keep the rest of this module's existing attribute name

    with open(args.json_path) as f:
        dashboard = json.load(f)

    wanted_ids = None
    if args.panels:
        wanted_ids = {int(x) for x in args.panels.split(",") if x.strip()}

    textbox_defaults = load_textbox_defaults(dashboard)
    gpu_equals_value = fetch_a_real_gpu_uuid(args.prom_url)
    job_regex, instance_regex = fetch_dcgm_job_instance_regex(args.prom_url)
    query_var_subs = dict(QUERY_VAR_SUBS)
    query_var_subs["job"] = job_regex
    query_var_subs["instance"] = instance_regex

    had_error = False
    checked = 0
    for panel, row_title in iter_panels_with_context(dashboard.get("panels", [])):
        pid = panel.get("id")
        if wanted_ids is not None and pid not in wanted_ids:
            continue
        title = panel.get("title", "")
        label = f"panel {pid} {title!r}" + (f" (row {row_title!r})" if row_title else "")
        for t in panel.get("targets", []):
            expr = t.get("expr")
            if not expr:
                continue
            ref_id = t.get("refId", "?")
            checked += 1
            sub_expr = substitute(expr, textbox_defaults, gpu_equals_value, query_var_subs)
            status, detail = classify(args.prom_url, sub_expr)
            line = f"{label} [{ref_id}]: {status}" + (f"({detail})" if detail else "")
            print(line)
            if status == "ERROR":
                had_error = True
                print(f"    expr: {sub_expr}", file=sys.stderr)

    print(f"\n{checked} target(s) checked.", file=sys.stderr)
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
