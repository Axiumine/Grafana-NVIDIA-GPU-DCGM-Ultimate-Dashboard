# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Shared pytest fixtures."""

import pathlib
from collections.abc import Iterator

import pytest
from prometheus_stub import FakePrometheus

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def project_root() -> pathlib.Path:
    return PROJECT_ROOT


@pytest.fixture
def prometheus_stub() -> Iterator[FakePrometheus]:
    """A running local fake Prometheus (see prometheus_stub.py), stopped after the test."""
    with FakePrometheus() as stub:
        yield stub
