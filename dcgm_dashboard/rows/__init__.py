# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Row modules for the NVIDIA GPU DCGM Ultimate Dashboard.

Row order in the built dashboard: a, b, c, d, e, f, g, h, i, j, k, l
(see build_dashboard.py). Every module exposes build(ctx) -> (row_def, panels);
see dcgm_dashboard/CONVENTIONS.md for the contract.
"""
