"""Slice-0 acceptance: the skeleton is fully wired and importable."""

import importlib

from fastapi import FastAPI


def test_common_surface_imports():
    from common import bus, config, contracts, interfaces

    assert hasattr(contracts, "Situation")
    assert hasattr(interfaces, "BusClient")
    assert hasattr(bus, "RedisBus")
    assert hasattr(config, "get_settings")


def test_all_service_apps_construct():
    for name in ("ingestion", "correlation", "rca", "action", "governance", "feedback"):
        mod = importlib.import_module(f"services.{name}.app")
        assert isinstance(mod.app, FastAPI)
