#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Detect the "two series, one legend" bug: run every panel target as a RANGE query
over a window and check whether it renders two or more identically-labeled series.

Usage:
    uv run tools/check_series.py <json_path> [--prom URL] [--start RFC3339|epoch]
                                  [--end RFC3339|epoch] [--step 30s] [--panels 1,7,21]

Why a RANGE query, not check_queries.py's instant one: Prometheus identifies a
series by its full label set. When dcgm-exporter's custom-counters.csv "label"-type
field list changes (a driver/VBIOS upgrade, or any hot CSV edit -- see
CONVENTIONS.md's series-identity section), the exact same physical GPU starts a
brand-new series and the old one goes stale but stays queryable until it ages out.
A query whose window spans that change sees the same GPU twice, under two
different label sets -- an *instant* query at "now" (long after the change) will
usually miss this entirely, but a range query covering the transition reproduces
exactly what a timeseries panel drew on-screen at the time: two lines under one
legend.

For every target of every panel (including a collapsed row's nested panels), this
substitutes the same variables check_queries.py does ($job/$instance/$gpu -> .*,
row B's plain UUID="$gpu" equality -> a real UUID, every textbox's own default),
plus $__rate_interval -> 2m and $__range/$__range_s -> the window length, runs a
RANGE query over [--start, --end] at --step resolution, and renders each returned
series' legend the way Grafana would: the target's legendFormat with every
{{label}} substituted (missing label -> empty string), or -- if the target has no
legendFormat (every table/instant-only target in this dashboard) -- a raw
sorted-label-set string, which two series from a genuine label-set split will
almost never collide on (their CSV label fields differ), so such targets simply
cannot produce a false DUPLICATE here.

Reports, per target: ERROR (the query itself failed), series count, DUPLICATE
rendered legends (the bug this tool exists to catch), and any rendered legend
that still contains an unresolved/empty {{label}} substitution (informational --
compare a baseline run against an after-the-fix run to see whether a fix
introduced a *new* empty label; a legend that was already empty before is not
this tool's concern).

Exit code is non-zero iff at least one target ERRORed or produced a DUPLICATE.
Default window: 2026-09-26T03:30:00+02:00 (shortly before the dcgm-exporter CSV
label-set change) to now -- covers the restart described in this project's
change-log.
"""

import argparse
import datetime
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PROM_URL = "http://localhost:9090"
DEFAULT_START = "2026-09-26T03:30:00+02:00"

BUILTIN_SUBS = {
    "$__rate_interval": "2m",
    # $__range_s / $__range are filled in per-invocation once the window length is
    # known (main()) -- $__range_s must substitute before $__range, same ordering
    # requirement as check_queries.py.
}
# $job/$instance are resolved per-run by fetch_dcgm_job_instance_regex() (scoped to
# DCGM_FI_DEV_GPU_UTIL, matching Grafana's own "All" for these two template
# variables) rather than a module-level constant -- see that function's docstring
# for why a blanket ".*" is wrong on a shared production Prometheus. $gpu (a UUID
# string) has no such collision risk with unrelated jobs, so it stays ".*" here.

# `safe` for urllib.parse.quote() on a label name: "" percent-encodes every character
# outside [A-Za-z0-9_.~-], "/" included. Module level on purpose: mutmut only mutates
# code inside functions, and its one mutation of this literal ("XXXX") is equivalent --
# quote() never escapes letters, whatever `safe` says.
_LABEL_SAFE_CHARS = ""

LEGEND_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def parse_time(value: str) -> float:
    """Accepts an epoch number (int/float, seconds) or an RFC3339 timestamp."""
    try:
        return float(value)
    except ValueError:
        pass
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.timestamp()


def parse_step_seconds(step: str) -> int:
    m = re.fullmatch(r"(\d+)([smh])", step.strip())
    if not m:
        raise ValueError(f"--step must look like '30s'/'5m'/'1h', got {step!r}")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def load_textbox_defaults(dashboard: dict) -> dict:
    out = {}
    for v in dashboard.get("templating", {}).get("list", []):
        if v.get("type") == "textbox":
            out[v["name"]] = v.get("current", {}).get("value", v.get("query", ""))
    return out


def fetch_a_real_gpu_uuid(prom_url: str) -> str:
    """Same rationale as check_queries.py's helper of the same name: row B's
    repeated panels use plain equality (UUID="$gpu") rather than every other
    panel's regex match, so ".*" would look for the literal string ".*" as a UUID
    and always read EMPTY even for a perfectly correct panel."""
    url = prom_url.rstrip("/") + "/api/v1/label/UUID/values"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        values = body.get("data") or []
        if values:
            return values[0]
    except urllib.error.HTTPError as e:
        e.close()  # see check_queries.py's classify()
    except Exception:
        pass
    return ".*"


def label_values(prom_url: str, label: str, match_expr: str) -> list:
    """GET /api/v1/label/<label>/values?match[]=<match_expr> -- the real
    Prometheus API behind Grafana's `label_values(<selector>, <label>)` template
    query. `urllib` has no query-string helper that avoids POST-only quirks here,
    so the URL is built by hand rather than reusing check_series.py's POST-shaped
    urlopen(Request(...)) pattern used elsewhere."""
    quoted_label = urllib.parse.quote(label, safe=_LABEL_SAFE_CHARS)
    url = (
        prom_url.rstrip("/")
        + "/api/v1/label/"
        + quoted_label
        + "/values?"
        + urllib.parse.urlencode({"match[]": match_expr})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        return body.get("data") or []
    except urllib.error.HTTPError as e:
        e.close()  # see check_queries.py's classify()
    except Exception:
        pass
    return []


def fetch_dcgm_job_instance_regex(prom_url: str) -> tuple:
    """This dashboard's own $job/$instance template variables are `label_values`
    queries scoped to DCGM_FI_DEV_GPU_UTIL (see build_dashboard.py's
    build_templating()), so Grafana's own "All" selection for either one is never
    literally ".*" -- it is an alternation of just the values that actually emit
    that metric. Substituting a blanket ".*" instead, on a Prometheus instance
    that also carries unrelated jobs (this is a shared production Prometheus, not
    a dashboard-dedicated one), makes `up{job=~".*", instance=~".*"}` -- panel
    100's Exporter-scrape-status query, the one target in this dashboard that
    queries a non-DCGM metric -- match every scrape target on the whole
    Prometheus, including e.g. unrelated Postgres exporters that happen to share
    an `instance` value, which renders as a false DUPLICATE that has nothing to
    do with this dashboard or dcgm-exporter. Returns (job_regex, instance_regex),
    each an alternation of the real values (or ".*" as a last-resort fallback if
    the lookup itself fails, e.g. DCGM_FI_DEV_GPU_UTIL has no data at all)."""
    jobs = label_values(prom_url, "job", "DCGM_FI_DEV_GPU_UTIL")
    job_regex = "|".join(jobs) if jobs else ".*"
    instances = label_values(prom_url, "instance", f'DCGM_FI_DEV_GPU_UTIL{{job=~"{job_regex}"}}')
    instance_regex = "|".join(instances) if instances else ".*"
    return job_regex, instance_regex


def substitute(
    expr: str, textbox_defaults: dict, gpu_equals_value: str, builtin_subs: dict, query_var_subs: dict
) -> str:
    expr = re.sub(r'UUID="\$\{?gpu\}?"', f'UUID="{gpu_equals_value}"', expr)
    for token, value in builtin_subs.items():
        expr = expr.replace(token, value)
    all_vars = dict(query_var_subs)
    all_vars.update(textbox_defaults)
    for name, value in all_vars.items():
        expr = re.sub(r"\$\{" + re.escape(name) + r"\}", value, expr)
        expr = re.sub(r"\$" + re.escape(name) + r"(?![A-Za-z0-9_])", value, expr)
    return expr


def iter_panels_with_context(panels, row_title=None):
    for p in panels:
        if p.get("type") == "row":
            yield from iter_panels_with_context(p.get("panels", []), row_title=p.get("title"))
        else:
            yield p, row_title


def render_legend(legend_format, metric: dict) -> str:
    """Grafana's {{label}} substitution (missing label -> empty string). With no
    legendFormat at all, falls back to a raw sorted-label-set string -- this is
    NOT what Grafana itself would render (it has its own default-name algorithm),
    but it is a stable, label-set-sensitive stand-in that serves this tool's one
    purpose: two series only collide on it if their full label sets are
    byte-identical, which two series from a genuine CSV label-set split never
    are."""
    if not legend_format:
        return "{" + ",".join(f'{k}="{metric[k]}"' for k in sorted(metric)) + "}"
    return LEGEND_TOKEN_RE.sub(lambda m: str(metric.get(m.group(1), "")), legend_format)


def unresolved_labels(legend_format, metric: dict):
    """Names of every {{label}} token in legend_format that substituted to empty
    (either the label is absent on this series, or present but an empty string)."""
    if not legend_format:
        return []
    return [name for name in LEGEND_TOKEN_RE.findall(legend_format) if not metric.get(name)]


def query_range(prom_url: str, expr: str, start: float, end: float, step: int) -> tuple:
    """Returns (status, detail_or_series) where status in {"OK", "EMPTY", "ERROR"}
    and detail_or_series is an error message (ERROR) or a list of metric label
    dicts, one per returned series (OK/EMPTY)."""
    params = {
        "query": expr,
        "start": f"{start:.3f}",
        "end": f"{end:.3f}",
        "step": str(step),
    }
    url = prom_url.rstrip("/") + "/api/v1/query_range?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # `with e`: see check_queries.py's classify().
        with e:
            try:
                msg = json.loads(e.read().decode()).get("error", str(e))
            except Exception:
                msg = str(e)
        return "ERROR", msg
    except Exception as e:
        return "ERROR", str(e)

    if body.get("status") != "success":
        return "ERROR", body.get("error", str(body))

    data = body.get("data", {})
    # No default: when "result" is absent, data.get("result") reads None, and the
    # very next line already returns a hardcoded [] for any falsy result (None or
    # an empty list alike), so an explicit [] default here would never surface on
    # its own -- it would just be a second spelling of the same fallback.
    result = data.get("result")
    if not result:
        return "EMPTY", []
    return "OK", [r.get("metric", {}) for r in result]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument(
        # dest: omitted -- argparse already derives "prom" from this first long
        # option string, so an explicit dest="prom" here would be redundant.
        "--prom",
        "--prom-url",
        default=DEFAULT_PROM_URL,
        help=f"Prometheus base URL (default: {DEFAULT_PROM_URL})",
    )
    ap.add_argument("--start", default=DEFAULT_START, help=f"RFC3339 or epoch (default: {DEFAULT_START})")
    ap.add_argument("--end", default="now", help="RFC3339, epoch, or 'now' (default: now)")
    ap.add_argument("--step", default="30s", help="range-query step, e.g. 30s/2m (default: 30s)")
    # default: omitted -- argparse's own built-in default is already None.
    ap.add_argument("--panels", help="comma-separated panel ids to restrict to")
    args = ap.parse_args()

    with open(args.json_path) as f:
        dashboard = json.load(f)

    start = parse_time(args.start)
    end = datetime.datetime.now(datetime.UTC).timestamp() if args.end == "now" else parse_time(args.end)
    step = parse_step_seconds(args.step)
    if end <= start:
        print(f"error: --end ({end}) <= --start ({start})", file=sys.stderr)
        return 2
    range_seconds = end - start

    builtin_subs = dict(BUILTIN_SUBS)
    builtin_subs["$__range_s"] = str(int(range_seconds))  # must precede $__range below
    builtin_subs["$__range"] = f"{int(range_seconds)}s"
    builtin_subs["$__interval"] = args.step

    wanted_ids = None
    if args.panels:
        wanted_ids = {int(x) for x in args.panels.split(",") if x.strip()}

    textbox_defaults = load_textbox_defaults(dashboard)
    gpu_equals_value = fetch_a_real_gpu_uuid(args.prom)
    job_regex, instance_regex = fetch_dcgm_job_instance_regex(args.prom)
    query_var_subs = {"job": job_regex, "instance": instance_regex, "gpu": ".*"}

    print(
        f"# window: {datetime.datetime.fromtimestamp(start, datetime.UTC).isoformat()} .. "
        f"{datetime.datetime.fromtimestamp(end, datetime.UTC).isoformat()} "
        f"({range_seconds:.0f}s), step={step}s, prom={args.prom}\n"
        f"# $job -> '{job_regex}', $instance -> '{instance_regex}' (scoped to DCGM_FI_DEV_GPU_UTIL, "
        f"not a blanket '.*' -- see fetch_dcgm_job_instance_regex()'s docstring)",
        file=sys.stderr,
    )

    # Lists rather than two booleans flipped to True on occurrence: a boolean
    # starting False is only ever read in boolean context below (if/or/the
    # summary ternary), so a False-vs-None initial value would be
    # indistinguishable by any observable outcome -- but an empty-vs-None *list*
    # is not: any(errors)/any(duplicates) below run unconditionally on every
    # call to main(), and any() raises on None regardless of whether the loop
    # ever appended to it, so a mutant that starts either list at None instead
    # of [] crashes on every run, including one with zero errors/duplicates.
    errors = []
    duplicates = []
    checked = 0
    empty_label_lines = 0

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
            legend_format = t.get("legendFormat")
            checked += 1
            sub_expr = substitute(expr, textbox_defaults, gpu_equals_value, builtin_subs, query_var_subs)
            status, detail = query_range(args.prom, sub_expr, start, end, step)

            if status == "ERROR":
                errors.append(True)
                print(f"{label} [{ref_id}]: ERROR({detail})")
                print(f"    expr: {sub_expr}", file=sys.stderr)
                continue
            if status == "EMPTY":
                print(f"{label} [{ref_id}]: EMPTY")
                continue

            metrics = detail
            legends = [render_legend(legend_format, m) for m in metrics]
            seen: dict = {}
            dup_texts = []
            for legend in legends:
                seen[legend] = seen.get(legend, 0) + 1
            for legend, count in seen.items():
                if count > 1:
                    dup_texts.append(f"{legend!r} x{count}")

            empty_lines = []
            for m in metrics:
                missing = unresolved_labels(legend_format, m)
                if missing:
                    empty_lines.append((render_legend(legend_format, m), missing))

            status_str = f"OK(n={len(metrics)})"
            if dup_texts:
                duplicates.append(True)
                status_str += f" DUPLICATE: {'; '.join(dup_texts)}"
            print(f"{label} [{ref_id}]: {status_str}")
            if empty_lines:
                empty_label_lines += len(empty_lines)
                for legend, missing in empty_lines:
                    print(f"    empty-label: {legend!r} missing {missing}")

    had_error = any(errors)
    had_duplicate = any(duplicates)
    print(
        f"\n{checked} target(s) checked, "
        f"{'ERRORs present' if had_error else 'no ERROR'}, "
        f"{'DUPLICATEs present' if had_duplicate else 'no DUPLICATE'}, "
        f"{empty_label_lines} legend(s) with an unresolved/empty label.",
        file=sys.stderr,
    )
    return 1 if (had_error or had_duplicate) else 0


if __name__ == "__main__":
    raise SystemExit(main())
