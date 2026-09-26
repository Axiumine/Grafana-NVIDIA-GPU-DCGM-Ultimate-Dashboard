<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Giovanni Manzoni -->

# Third-party notices

This project is licensed under the GNU General Public License v3.0 or later (see
[LICENSE](LICENSE)). It also contains a small amount of third-party material that
keeps its original license, listed below. Full license texts are in
[`LICENSES/`](LICENSES/).

## NVIDIA dcgm-exporter (Apache-2.0)

- **Source:** `etc/default-counters.csv` and `etc/dcp-metrics-included.csv` from
  <https://github.com/NVIDIA/dcgm-exporter>
- **Copyright:** Copyright (c) 2021 NVIDIA CORPORATION
- **License:** Apache License 2.0 ([`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt)).
  Upstream ships no `NOTICE` file.
- **Used in:** `custom-counters.csv` (SPDX: `GPL-3.0-or-later AND Apache-2.0`)

The CSV format and some help-column descriptions come from the upstream files.
Giovanni Manzoni modified the material in 2026: the field list was expanded to 168
entries, and most descriptions were rewritten or expanded with verification notes and
PromQL usage guidance. The following 19 rows still use help text that is identical or
close to the upstream wording:

`DCGM_FI_DEV_SM_CLOCK`, `DCGM_FI_DEV_MEM_CLOCK`, `DCGM_FI_DEV_GPU_TEMP`,
`DCGM_FI_DEV_POWER_USAGE`, `DCGM_FI_DEV_POWER_MGMT_LIMIT`,
`DCGM_FI_DEV_ENFORCED_POWER_LIMIT`, `DCGM_FI_DEV_MEM_COPY_UTIL`, `DCGM_FI_DEV_FB_USED`,
`DCGM_FI_DEV_FB_FREE`, `DCGM_FI_DRIVER_VERSION`, `DCGM_FI_DEV_VBIOS_VERSION`,
`DCGM_FI_PROF_PIPE_FP16_ACTIVE`, `DCGM_FI_PROF_PIPE_FP32_ACTIVE`,
`DCGM_FI_PROF_PIPE_FP64_ACTIVE`, `DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_TOTAL`,
`DCGM_FI_DEV_NVLINK_CRC_DATA_ERROR_TOTAL`, `DCGM_FI_DEV_NVLINK_REPLAY_ERROR_TOTAL`,
`DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_TOTAL`, `DCGM_FI_DEV_FABRIC_HEALTH_MASK`.

## Facts and identifiers (no license obligation)

`dcgm_dashboard/lib.py` uses DCGM field names, enum values (for example health result
0/10/20), clock-event reason bit names and short Xid error labels (for example Xid 79,
"GPU has fallen off the bus"). These are functional identifiers and facts taken from
NVIDIA's DCGM Python bindings (Apache-2.0) and the public Xid catalog at
<https://docs.nvidia.com/deploy/xid-errors/>. No descriptive prose from either source is
reproduced. The sources are named here for transparency only.

## Reference dashboards (nothing copied)

These grafana.com dashboards were used for feature-parity and quality comparison only
(see the README tables). No panel descriptions, value-mapping text, queries or layout
were copied from them.

- [14574](https://grafana.com/grafana/dashboards/14574), "Nvidia GPU Metrics" by Utku
  Özdemir (nvidia_gpu_exporter project, MIT)
- [22424](https://grafana.com/grafana/dashboards/22424) by hughyw (no license stated)
- [25526](https://grafana.com/grafana/dashboards/25526) by orangeoctopus1069 (no license
  stated)
- [12239](https://grafana.com/grafana/dashboards/12239), NVIDIA DCGM Exporter Dashboard
  (NVIDIA, dcgm-exporter repository is Apache-2.0)
