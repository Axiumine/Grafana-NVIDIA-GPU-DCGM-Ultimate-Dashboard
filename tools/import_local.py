#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Import a dashboard JSON into the local test Grafana.

Usage: python3 tools/import_local.py <json_path> [--uid-suffix X] [--grafana-url URL]
                                      [--user admin] [--password admin]

Prints the dashboard URL on success. Non-zero exit on any failure (network error,
non-2xx response, or an "success" flag missing from the response body).

Uses POST /api/dashboards/import with inputs=[{name: DS_PROMETHEUS, ...}] mapped to
the datasource uid "prom-local" (this environment's provisioned Prometheus datasource).
--uid-suffix appends "-<suffix>" to the dashboard's own "uid" field before importing,
so a scaffold/dev import never collides with (or overwrites) a "real" import of the
same dashboard under its bare uid.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

DEFAULT_GRAFANA_URL = "http://127.0.0.1:3300"
DEFAULT_DATASOURCE_UID = "prom-local"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path")
    ap.add_argument("--uid-suffix", default=None, help="append -<suffix> to the dashboard uid before importing")
    ap.add_argument("--grafana-url", default=DEFAULT_GRAFANA_URL)
    ap.add_argument("--datasource-uid", default=DEFAULT_DATASOURCE_UID)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--folder-id", type=int, default=0)
    args = ap.parse_args()

    with open(args.json_path) as f:
        dashboard = json.load(f)

    if args.uid_suffix:
        base_uid = dashboard.get("uid") or "dashboard"
        dashboard["uid"] = f"{base_uid}-{args.uid_suffix}"
    dashboard["id"] = None  # never re-target an existing numeric id from a prior export

    payload = {
        "dashboard": dashboard,
        "overwrite": True,
        "folderId": args.folder_id,
        "inputs": [
            {
                "name": "DS_PROMETHEUS",
                "type": "datasource",
                "pluginId": "prometheus",
                "value": args.datasource_uid,
            }
        ],
    }

    url = args.grafana_url.rstrip("/") + "/api/dashboards/import"
    body = json.dumps(payload).encode("utf-8")
    auth = base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} importing {args.json_path}: {e.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"ERROR: could not reach {url}: {e}", file=sys.stderr)
        return 1

    if not resp_body.get("uid") and not resp_body.get("importedUri"):
        print(f"ERROR: import response missing uid/importedUri: {resp_body}", file=sys.stderr)
        return 1

    dash_uid = resp_body.get("uid") or dashboard["uid"]
    dash_url = args.grafana_url.rstrip("/") + (resp_body.get("importedUrl") or f"/d/{dash_uid}")
    print(f"imported OK: uid={dash_uid} slug={resp_body.get('slug')}")
    print(f"URL: {dash_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
