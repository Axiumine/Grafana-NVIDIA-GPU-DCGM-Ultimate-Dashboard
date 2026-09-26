<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Giovanni Manzoni -->

# Row author guide

This document, plus each row module's own docstring in `dcgm_dashboard/rows/`, is the
source of truth for a row's panels/ids/gridPos/targets. Read an existing row module close
to what you're building (row A/B/C are the best-documented) before writing code.
Everything below is about *how* to turn a row's design into a row module using
`dcgm_dashboard/lib.py`.

## The row module contract

Each row lives in `dcgm_dashboard/rows/row_<letter>.py` and exposes exactly one function:

```python
def build(ctx: lib.RowContext) -> tuple[dict | None, list[dict]]:
    ...
    return row_def, panels
```

- `row_def`: `lib.row(id, title, collapsed=<bool>, repeat=None)` (`collapsed` is
  required, no default), or `None` for row A only
  (row A has no row-panel wrapper -- its tiles are top-level panels).
- `panels`: a flat list of panel dicts built with `lib.stat`/`lib.gauge`/`lib.timeseries`/
  etc. **gridPos.y is relative**, not absolute:
  - Row A (`row_def is None`): `y` is absolute-from-0 (row A always starts the dashboard).
  - An expanded row: `y=1` for the first sub-row's panels (one grid unit below the row's
    own header), `y=1+h1` for the second sub-row, etc. -- see row_a/row_b/row_c for the
    pattern.
  - A collapsed row (only row L today): `y` relative to 0 inside that row panel's own
    `panels` array -- see row_l.py for the pattern.
- Never set `gridPos.y` to an absolute dashboard value yourself, and never set the row
  panel's own `gridPos` -- `lib.build_layout()` (called from `build_dashboard.py`) computes
  both by stacking rows top-to-bottom in the order given by `ROW_ORDER`/`--rows`.

## Panel ids and gridPos

Panel ids are fixed, not auto-assigned, so the dashboard is deterministic across rebuilds.
Once a row ships, treat its ids, titles, and `collapsed` flag as fixed unless you have a
specific, documented reason to change them. Ids do not need to be sequential within a row
-- a row's original id block gets extended with the next free id anywhere in the dashboard
when a later addition needs one, rather than being renumbered in. Current id ranges
(verified against `nvidia-dcgm-dashboard.json`):

| Row | Row id | Child ids |
|---|---|---|
| A (top strip) | -- (no row) | 1-10 |
| B (per-GPU) | 11 | 12-19 |
| C (inventory + fleet status) | 20 | 21, 97 |
| D (utilization) | 22 | 23-31 |
| E (memory) | 32 | 33-38 |
| F (clocks) | 39 | 40-46, 98-99 |
| G (power/energy) | 47 | 48-55 |
| H (thermal/fan) | 56 | 57-62 |
| I (PCIe) | 63 | 64-67 |
| J (video engines) | 68 | 69-73 |
| K (reliability + health incidents) | 74 | 75-85, 96, 100 |
| L (datacenter, collapsed) | 86 | 87, 88, 95, 89-94 |

That is 100 panels total. Two ids are worth calling out:

- **95** ("C2C link power status", row L) sits between 88 and 89 in the row's own panel
  order -- its id is simply the next one that was free when it was added, not a rename of
  an existing panel.
- **96-100** were each added after their row's initial id block: 96 (row K, "Health
  incidents (why)"), 97 (row C, "Fleet Live Status"), 98/99 (row F, "Fraction of Range
  Throttled" bargauge / "Time at High Performance" stat), 100 (row K, "Exporter scrape
  status"). Update this table if you add more.

`lib.build_layout()` asserts panel-id uniqueness and gridPos overlap/width across the whole
dashboard (top-level space) and, separately, within each collapsed row's own nested `panels`
array -- run a full `--rows a,b,...,<yours>` build locally and it will raise `ValueError`
with the offending ids/rects if you get a gridPos wrong. `w` per panel must sum to <=24
across any row of tiles.

## Helpers in `lib.py` (see its docstrings for full signatures)

- `lib.ds_ref()` -- datasource ref, `{"type":"prometheus","uid":"${DS_PROMETHEUS}"}`.
- `lib.target(expr, *, legend=None, ref_id, instant=False, hide=False, fmt="time_series")`
  -- `ref_id` is required (keyword-only, no default): every call site assigns one
  deliberately, since a target's refId is what a table join or override keys off.
  Use `lib.make_targets([{...}, ...])` instead for auto-lettered refIds on a
  multi-target panel.
- `lib.per_gpu(expr, agg="max", extra=())` -- **every per-GPU range query goes through
  this helper.** See the "Series identity / driver upgrades" gotcha below for what it
  fixes and `lib.GPU_ID_LABELS` for the exact label set; `lib.per_gpu()`'s own
  docstring is the source of truth for which `agg` a given query shape needs.
- `lib.latest_per_gpu(expr, base=None)` -- the raw-query counterpart to `per_gpu()`,
  for the identity/inventory tables that must stay unaggregated (panels 21/87/90, see
  the "Exception" bullet below): keeps only the most-recently-sampled series per GPU
  (`expr and topk by (job, instance, UUID, GPU_I_ID) (1, timestamp(...))`) without
  aggregating any label away. Pass `base` (the raw selector) when `expr` is a
  function/filter of it (e.g. `floor(x/65536)`) so `timestamp()` is taken of the
  selector, not the derived expression. **Rule: an identity/inventory table showing a
  CSV label-type field as a column uses `latest_per_gpu()`; every other per-GPU query
  uses `per_gpu()`.**
- Panel builders: `stat`, `gauge`, `bargauge`, `timeseries`, `state_timeline`,
  `status_history`, `table`, `heatmap`, `histogram`, `text`, `row`. `unit_id` and
  `thresholds_steps` are required (keyword-only, no default) on `stat`/`gauge`/
  `bargauge`/`timeseries` (`gauge` also requires `min_`/`max_`); `heatmap`/`histogram`
  require `unit_id` only (they take no `thresholds_steps`: `histogram` always uses
  `no_thresholds()`, `heatmap` sets none) -- every call site in this codebase already
  computes and passes them explicitly, so a
  panel that forgets one is almost certainly a bug, not a legitimate "use the default"
  case; `table`'s `unit_id` keeps its `"none"` default (few table panels care about a
  unit). `lib.row(panel_id, title, *, collapsed, repeat=None)` -- `collapsed` is
  likewise required: it's a deliberate, documented-per-row choice (see the id table
  above), never an incidental default.
- `lib.thresholds([(None,"green"),(75,"yellow"),(85,"red")])` -- the first step's value
  must be written as `None` (Grafana's base step never carries one); passing a number
  there raises `ValueError` instead of silently discarding it. `lib.no_thresholds(color)`.
- Value mappings: `lib.value_mapping([(val,text,color_or_None), ...])`,
  `lib.range_mapping(frm,to,text,color)`, `lib.special_mapping(match,text,color)`.
- Overrides: `lib.override_by_name(field, [(id, value), ...])`, `lib.override_by_regex(...)`.
- Table transforms: `lib.transform_join_by_field`, `lib.transform_organize`,
  `lib.transform_labels_to_fields`, `lib.transform_merge`, `lib.transform_calculate_field`
  (see the caveat below -- you probably don't want this one), `lib.transform_filter_fields_by_name`.
- The decode library: `pstate_mappings`, `compute_mode_mappings`, `ecc_mode_mappings`,
  `virtual_mode_mappings`, `recovery_action_mappings`, `bool_reliability_mappings`,
  `bool_config_mappings`, `health_status_mappings`, `xid_mappings` (+ `XID_TABLE`),
  `CLOCK_EVENT_BITS` / `REAL_THROTTLE_BITS` / `clock_event_bit_expr` / `any_real_throttle_expr`,
  `cuda_compute_capability_major_minor`, `cuda_driver_version_major_minor`.
- `lib.UNITS` -- the verified-unit-id allowlist; always go through `lib.unit("mbytes")` etc.
  (or pass the string key directly to a builder's `unit_id=` param) so `lint_dashboard.py`'s
  unit check passes. Add a new unit to this dict (with a comment citing where you verified
  it) before using it, never invent a unit string inline.

## Filters

- `ctx.filter_all` = `{job=~"$job", instance=~"$instance", UUID=~"$gpu"}` -- use this
  everywhere except inside row B's repeat.
- `ctx.filter_single` = `{job=~"$job", instance=~"$instance", UUID="$gpu"}` (plain UUID
  equality) -- row B only.
- `ctx.filter_up` = `{job=~"$job", instance=~"$instance"}` -- for Prometheus's own `up`
  metric, which has no UUID/gpu label (it's a per-scrape-target series, not a per-GPU one).
- `ctx.legend_std` = `"{{instance}} · {{modelName}} · GPU {{gpu}} ({{pci_bus_id}})"`.

## Grafana/DCGM facts and gotchas

1. **`on(UUID)` drops every other label.** `A{filter} / on(UUID) B{filter}` keeps *only*
   the `UUID` label in the result -- any `{{modelName}}`/`{{instance}}` legend on that
   target renders blank. Verified live. Fix: `on(UUID) group_left(modelName)` (or just
   `group_left()`) restores the left-hand side's full label set. Only needed when you
   explicitly pin the match to `on(UUID)`; if both sides already share their full label set
   (common for two `DCGM_FI_DEV_*` gauges on the same GPU), a plain `A{filter} / B{filter}`
   with no `on()` at all also works and is simpler -- verify with `check_queries.py` either
   way (it prints `n=` series count; `n=0`/EMPTY where you expected 1 means a label
   mismatch).
2. **Grafana row/panel titles do not do `{{label}}` substitution.** That handlebars syntax
   only works inside a query target's `legendFormat`. A repeated row's title needs Grafana's
   own `$var` syntax instead (e.g. `"$gpu — Overview"`, `repeat="gpu"` -- bare name, no `$`
   in the `repeat=` value itself). Row B's title is `"$gpu — Overview"`, not `"GPU $gpu —
   Overview"` -- `$gpu`'s value is already a `GPU-<uuid>` string, so a literal leading
   "GPU " would double it into "GPU GPU-65bd... — Overview".
3. **`calculateField`'s binary mode has no `floor()`/`mod()`.** If a panel needs a decode
   like compute-capability's `floor(x/65536)`, do the math in PromQL instead (see
   `lib.cuda_compute_capability_major_minor` for the pattern: floor/arithmetic on a single
   vector preserves every label, so this is also simpler than a transform). Reserve
   `lib.transform_calculate_field` for genuine two-field ratios/sums.
4. **A multi-query table join's auto-generated field names are quirky -- verify them live,
   don't guess.** `lib.transform_join_by_field("UUID")` across N targets (refIds A..N)
   produces: `UUID` (bare, the join key), one `Value #<refId>` per query, and for every
   *other* field name that appears in more than one source frame, `"<field> <frame_index>"`
   for **every** occurrence including the first (`frame_index` = 1-based position in your
   `targets` list, not the refId letter) -- there is no unsuffixed "canonical" copy once a
   name collides. This is exactly what happens if every target uses
   `fmt="table", instant=True` on `DCGM_FI_DEV_*` metrics, since every metric carries every
   CSV `label`-type field. `lib.table_join()`'s `identity_fields=`/`noise_fields=`
   parameters exist for this: pick frame 1's copy of each identity column to keep+rename
   (`identity_fields`), and exclude every other frame's copy of it plus every frame's copy
   of the purely-structural fields (`Time`, `job`, `device`, `gpu`, `host`, `__name__`, and
   any CSV `label` field you don't want to show) via `noise_fields`. See row_c.py's calls
   for worked examples, or row_l.py's module-level `_IDENTITY`/`_NOISE` constants for the
   pattern shared across several table panels in one row. A field
   that is legitimately absent on a given GPU (no NVLink, no C2C, ...) contributes an empty
   frame that the join simply skips -- normal and expected -- but this shifts or removes the
   numeric suffix on every *other* frame's identity columns, and if only one frame survives,
   `joinByField` emits its identity fields with **no** numeric suffix at all. `lib.table_join()`
   handles this by renaming/excluding both the suffixed and the bare field name.
   **`joinByField`'s key must be unique within each input frame** -- `lib.table_join()`'s
   `join_field=` parameter defaults to `"UUID"` (one row per physical GPU, correct for every
   table except one), but a table whose rows are finer-grained than one-per-GPU needs a
   join key that is actually unique per row, or rows sharing the default key silently
   re-collide at the join step even though they were distinct series going in. Panel 90
   (MIG, row_l.py) is that one exception: its rows are one per MIG instance, and every
   instance on the same physical GPU shares the same UUID (GPU_I_ID is what actually
   distinguishes them -- see the series-identity section below). Its fix: wrap every target
   in `label_join(expr, "mig_key", "/", "UUID", "GPU_I_ID")` (row_l.py's `_mig_key()`
   helper), pass `join_field="mig_key"` to `table_join()`, and add `"mig_key"` to
   `noise_fields` so the synthetic key doesn't leak through as a column -- the plain
   `"UUID"` column (read off `identity_fields`, unrenamed) still shows the clean physical
   UUID. This is a no-op on a non-MIG GPU (`GPU_I_ID` is empty, so `mig_key` == `"<uuid>/"`
   and is still unique per GPU there).
   **Testing a table join**: import the dashboard, open the panel solo
   (`.../d/<uid>/x?viewPanel=<id>`), resize the browser wide (virtualized table columns
   outside the viewport are not even in the DOM, so a narrow screenshot under-reports
   columns), and read `document.querySelectorAll('[role="columnheader"]')` via
   `browser_evaluate` -- faster and more reliable than scrolling and eyeballing screenshots.
5. **Clock-event bitmask: `0x200` BOARD_LIMIT and `0x400` RELIABILITY are informational
   clock limits, not throttling.** `nvidia-smi -q -d PERFORMANCE` shows `Reliability:
   Active` while idling in P8. They are excluded from `lib.REAL_THROTTLE_BITS` (only 5
   bits: `0x4,0x8,0x20,0x40,0x80`) and from `lib.any_real_throttle_expr()`. Use that helper
   for any throttle tile/alarm; if you build a bitmask state-timeline or a violation-ns rate
   panel, color those two bits blue/yellow ("clock limit, informational"), never red, and
   never fold them into a fault count. `0x4` SW_POWER_CAP is real throttling but expected
   under sustained full load, so this dashboard colors it yellow, not red, while still
   counting it in `REAL_THROTTLE_BITS`; `lib.REAL_FAULT_THROTTLE_BITS` is the 4-bit subset
   that excludes it, for an alarm that should only fire on genuine thermal/power-brake
   distress.
6. **Violation/reason counters are nanoseconds, not microseconds.** `DCGM_FI_DEV_*_VIOLATION`
   and the five `CLOCKS_EVENT_REASON_*_NS` fields are cumulative nanosecond counters --
   `rate(x[$__rate_interval]) / 1e9` gives a fraction-of-time, not `/ 1e6`. Use `lib.unit("ns")`
   for a panel that graphs one of these directly.
7. **PCIe link generation legitimately drops to Gen1 at idle.** ASPM / dynamic link-speed
   power management parks an idle link at Gen1 even though the GPU and slot both support a
   higher max generation -- this is normal power saving, not a fault. An instant "current
   gen == max gen" check therefore false-positives as "Downgraded" on every idle sample.
   Range-gate it instead: `max_over_time(LINK_GEN[$__range]) >= bool MAX_LINK_GEN` ("OK"
   means the link reached its max generation *at some point* in the range). Link width
   should stay an instant check -- a real width downgrade doesn't fluctuate with load the
   way generation does.
8. **`DCGM_EXP_GPU_HEALTH_STATUS` is 0/10/20 (Pass/Warn/Fail), not a boolean**, with a
   `health_watch` label: 10 named subsystems plus `health_watch="ALL"`, DCGM's own separate
   GPU-wide watch that is *not* a rollup/derived value of the other 10 -- it independently
   catches devastating, non-subsystem-specific faults (e.g. Xid 79 fallen off bus, Xid 95
   uncontained ECC). Always aggregate with `max()`/`max by (...)` across watches, including
   `ALL`: `max` is idempotent under duplication, so including `ALL` can only surface a
   failure the other 10 would have missed, never double-count one. `health_error_code` and
   `health_error_severity` are part of this metric's series identity and change value on
   every fault transition, so a query keyed only on `{{instance}}`/`{{health_watch}}` can
   render two non-adjacent lanes for what is logically one continuous watch -- aggregate
   those two labels away (`max by (hostname, instance, gpu, UUID, health_watch)`) before
   feeding a state-timeline.
9. **Grafana value mappings cannot interpolate the matched value into text.** There is no
   `${value}` token, so `health_status_mappings()`/`xid_mappings()` use a literal "Unknown"/
   "Unknown Xid" fallback -- the raw number is still visible in the tooltip.
10. **Currency in units.** A `displayName` override (e.g. `"${currency} Cost"`) interpolates
    a template variable into stat-panel text correctly in this environment's Grafana
    13.2.2 -- empirically verified (imported a throwaway single-panel dashboard, read the
    rendered DOM via Playwright; see row_g.py's module docstring). A Grafana unit `prefix`
    was tried too and does not interpolate as reliably; the `displayName` path is the one
    to use.
11. **`lib.override_by_regex(pattern, ...)` needs `pattern` delimited (Grafana's `byRegexp`,
    Grafana 13.2.2) -- `lib.override_by_regex` does this for you, do not hand-roll a
    `{"matcher": {"id": "byRegexp", ...}}` override yourself.** A bare pattern string is
    compiled fully anchored, as `^<pattern>$`, not as "pattern found in the display name" --
    e.g. `"^Free"` never matches a real series name "Free · gpu-node-01 GPU0" (it would only
    match a display name that is exactly `Free`), so the override silently
    becomes a no-op: no error anywhere, the JSON is schema-valid, `check_queries.py` still
    returns data, and only a rendered panel shows the bug. `lib.override_by_regex` wraps
    every bare pattern in `/…/` itself, so every call site gets this fixed at the one source; do
    not "fix" a symptom by hand-adding slashes to a pattern string passed into it (an
    already-delimited `/pattern/flags` is passed through, for when you need a flag like `i`).
12. **Hot-reloading `custom-counters.csv` can leave two label-set "generations" of the same
    metric in Prometheus for a while.** If you add or remove CSV fields on a live exporter,
    the extra/missing `label`-type fields make Prometheus treat the pre- and post-reload
    series as distinct identities, so a *range* query spanning the reload can return 2
    series for what is logically one GPU until the old series ages out of the window. This
    is expected behavior of a hot-reloaded exporter, not a dashboard bug -- use a short time
    range (e.g. Last 15 minutes) when verifying a change right after a CSV edit.
13. **Series identity / driver upgrades -- this is gotcha #12 above, generalized, and it
    is not merely "wait 15 minutes and it goes away": a live incident (2026-09-26) showed
    every timeseries panel drawing two overlapping lines under one legend (e.g. two
    "gpu-node-01 GPU0" lines) for as long as the old label-set's series stayed queryable.**
    `lib.GPU_ID_LABELS` is the label subset that survives a CSV/driver/VBIOS change
    (job/instance/hostname/gpu/UUID/modelName/pci_bus_id/device, plus MIG's
    GPU_I_ID/GPU_I_PROFILE so a MIG instance is never merged with another on the same
    GPU) -- `job=~"$job"` etc. is a red herring here, since the CSV-driven fields
    (driver/VBIOS/serial/brand/NVML/IMEX/fabric-cluster/CUDA-visible-devices) are not in
    that filter at all, only in the *rest* of the series' label set. **Every per-GPU
    range query goes through `lib.per_gpu(expr, agg=..., extra=...)`** instead of
    querying the raw metric (or a hand-rolled `max by (...)`) directly:
    - `agg="max"` (the default) for a gauge, ratio, bitmask decode, or a
      rate()/irate()/deriv() result -- at any instant only one of the split series is
      alive (the other has gone stale), so `max` is exact.
    - `agg="sum"` for a range-integrating counter (`increase(x[$__range])`,
      `sum_over_time`, an event count or "this range" energy total) -- the old and new
      series each cover only part of the range, so they must be added, not maxed.
    - `agg="avg"` for `avg_over_time(x[$__range:])` -- only approximate across a split
      (each partial series' own average is weighted equally regardless of how much of
      the range it covers); document this at the call site (see row_f.py panel 99).
    - `extra=(...)` for any panel-specific label a legendFormat/override/join relies on
      that isn't already in `lib.GPU_ID_LABELS` (e.g. `health_watch`,
      `health_error_code`/`health_error_severity`, `xid`) -- every `{{label}}` a
      target's legendFormat references must survive the aggregation or it silently
      renders empty.
    - A join (`on(UUID)`/`group_left(...)`) needs *both* operands pre-aggregated with
      `lib.per_gpu()`, not just the panel's final result. Which failure this actually
      prevents depends on the operands: for a join of *plain, non-windowed* gauge
      selectors (row_b.py panels 16/17), Prometheus's own staleness handling means the
      old and new label-set series can never both be live at one evaluation instant, so
      the risk is a duplicate-legend/duplicate-gauge-box render (same class as the
      panel-18 fan fix above), not a hard query error. A join whose operand uses a
      *windowed* function (`rate()`/`increase()`/`*_over_time`) spanning the transition
      is the case that can genuinely hit a "many-to-one matching must be explicit"
      error, since the window can legitimately return both the old and new row for one
      series at a single outer evaluation point -- pre-aggregate those operands too.
    - **Exception**: a table whose whole point is to *display* a CSV label-type field
      (GPU Inventory id 21's driver/VBIOS/serial/brand/NVML columns; Fabric/IMEX/C2C id
      87's Fabric Cluster UUID/IMEX Domain Status; MIG id 90's CUDA Visible Devices)
      leaves its raw target(s) unaggregated -- `lib.per_gpu()` would aggregate away the
      very field the table exists to show. Every *other* frame in those same tables
      (and every frame of a table that promotes no CSV label field, e.g. panels
      66/92/94/97) is still wrapped normally in `lib.per_gpu()`.
      Those raw target(s) instead go through **`lib.latest_per_gpu()`**: on stock
      Prometheus, staleness markers already stop an *instant* query from returning both
      the old and new label-set series for one GPU (verified live), but a backend
      without staleness markers (e.g. VictoriaMetrics, some remote-read setups) can
      keep returning the stale row for up to the lookback (~5 min) after a CSV
      field-list change, rendering as a duplicate row per GPU. `lib.latest_per_gpu(expr,
      base=None)` ANDs `expr` against `topk by (job, instance, UUID, GPU_I_ID) (1,
      timestamp(...))` to keep only the most-recently-sampled series per GPU identity
      (MIG instances stay distinct via `GPU_I_ID`) -- unlike `per_gpu()`, no label is
      aggregated away, so the CSV field the table exists to show survives on the row
      that's kept. **Rule: identity/inventory tables showing label-type fields use
      `lib.latest_per_gpu()`; everything else uses `lib.per_gpu()`.** See
      `lib.latest_per_gpu()`'s own docstring for the `base` param (needed when the raw
      target is a function/filter of the selector, e.g. MIG id 90's
      `DCGM_FI_DEV_MIG_MODE{...} == 1` anchor, or GPU Inventory id 21's CUDA
      compute-capability/driver-version decodes) and `tools/check_series.py` for the
      regression tool (run it over a window spanning a label-set change). This
      series-level distinctness is necessary but not sufficient for panel 90: its table
      join must *also* key on more than bare UUID, or two distinct MIG-instance series
      re-collide at the `joinByField` step -- see gotcha #4 above (`join_field=`) for
      that half of the fix.
    - `tools/check_series.py <json>` is the regression tool for this: it runs every
      target as a *range* query over a window and reports DUPLICATE where two or more
      series render the same rendered legend within one target. Run it after any CSV
      field-list change, over a window spanning the change (its default window covers
      this incident).

## Testing your row

```bash
cd <repo-root>

# Build just row A (always include it -- top strip) + your row, to a scratch path:
uv run build_dashboard.py --rows a,<your-letter> --out /tmp/dcgm-dev.json

# Static checks (ids, gridPos, datasource refs, units, CSV coverage, rate()-on-gauge,
# byRegexp delimiters, no stray Kelvin math):
uv run tools/lint_dashboard.py /tmp/dcgm-dev.json

# Does every target actually return data from a live Prometheus?
# Defaults to http://localhost:9090 -- there is no local Prometheus/Grafana
# in this project; pass --prom URL to point at a different one. import_local.py is
# stale (targeted a local test Grafana that no longer exists) -- do not use it.
uv run tools/check_queries.py /tmp/dcgm-dev.json
uv run tools/check_queries.py /tmp/dcgm-dev.json --panels 40,41,42   # just your ids

# Does any per-GPU query render two series under one legend (the series-identity
# gotcha below)? Only meaningful with a --start/--end window that actually spans a
# label-set change (a driver/VBIOS upgrade or a custom-counters.csv edit) -- on an
# unchanged label set every target simply reports its normal single-series legend.
uv run tools/check_series.py /tmp/dcgm-dev.json
```

Once your row is ready, rebuild the full dashboard (`uv run build_dashboard.py`, no
`--rows`/`--out`) so `nvidia-dcgm-dashboard.json` reflects every row, and re-run
`lint_dashboard.py`/`check_queries.py` (and `check_series.py`, if you touched a
per-GPU query) against that file before committing it. Run `uv run ruff check --fix .` and
`uv run ruff format .` as well: with the repo's pre-commit hook enabled (`git config core.hooksPath
.githooks`, see the README's Development section), a commit that fails ruff, or whose
`nvidia-dcgm-dashboard.json` doesn't match a fresh build of the staged sources, is rejected.

Finally, `uv run pytest` (also run by the hook) must pass at 100% line and branch coverage. For a
row change that means: `tests/test_rows.py`'s `ROW_SPECS` pins every row's id, title, `collapsed`
flag, and child ids (the id-range table above), and `tests/test_build_dashboard.py` pins the total
panel count (`FULL_PANEL_COUNT`) and compares a fresh build to the committed
`nvidia-dcgm-dashboard.json` -- update the first two alongside this table. A new `if`/loop in a row
module needs a test that takes each side of it.

## What's already implemented

Every row, A through L, is fully implemented and ships real panels matching the
100-panel dashboard `tools/lint_dashboard.py` reports. `row_a.py` (top strip), `row_b.py`
(per-GPU repeat), and `row_c.py` (inventory table) are the most useful to read first --
they demonstrate every builder pattern you're likely to need, including the `group_left`
and table-join-naming gotchas above. Read each row's own module docstring for its design
rationale before changing it, and treat any existing row's id, title, or collapsed flag as
fixed unless you have a specific, documented reason to change it.
