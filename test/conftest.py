"""
Pytest configuration shared by all IMERS test modules.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if sys.platform == "win32":
    try:
        sys.stdout.fileno()
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation, ValueError):
        pass
    try:
        sys.stderr.fileno()
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation, ValueError):
        pass



@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the absolute path to the project root."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory) -> Path:
    """Return a session-scoped temp directory for test data."""
    return tmp_path_factory.mktemp("imers_test_data")


@pytest.fixture
def sample_transcript_es() -> str:
    return (
        "Hola, ha habido un accidente de tráfico muy grave en la Avenida de la "
        "Constitución esquina con Calle Sierpes en Sevilla. Hay tres vehículos "
        "implicados, uno de los conductores está inconsciente y atrapado en el "
        "coche, otro tiene heridas visibles en la cabeza, y hay humo saliendo "
        "del motor del tercer vehículo. Hay al menos cuatro personas heridas."
    )


@pytest.fixture
def sample_transcript_en() -> str:
    return (
        "Hello, there has been a serious traffic accident at the intersection of "
        "Main Street and Oak Avenue. There are three vehicles involved, one driver "
        "is unconscious and trapped inside the car, another has visible head wounds, "
        "and smoke is coming from the engine of the third vehicle. "
        "There are at least four people injured."
    )


@pytest.fixture
def cardiac_transcript() -> str:
    return (
        "My father is not breathing and has no pulse. "
        "We are at 22 Calle Betis, third floor, Seville. "
        "Please send help immediately."
    )


@pytest.fixture
def fire_transcript() -> str:
    return (
        "There's a fire in the apartment building on Calle Resolana number 8. "
        "I can see flames coming from the third floor windows. "
        "There are people shouting upstairs, I think they are trapped."
    )


@pytest.fixture
def stub_classification() -> dict:
    return {
        "incident_type": "traffic_accident",
        "typology": "Sanitary",
        "priority": "P-1 (Emergency)",
        "victims": 3,
        "confidence": "high",
        "matched_keywords": ["traffic_accident"],
        "reasoning": None,
    }


@pytest.fixture
def stub_location() -> dict:
    return {
        "found": True,
        "address": "Avenida de la Constitución, Sevilla",
        "latitude": 37.3861,
        "longitude": -5.9926,
        "confidence": "high",
        "candidates": ["Avenida de la Constitución", "Calle Sierpes"],
        "error": None,
    }


@pytest.fixture
def stub_dispatch_result() -> dict:
    return {
        "dispatched": [
            {"id": "AMB-SVA-01", "type": "ambulance_sva", "eta_minutes": 7,
             "base_location": "Hospital Virgen del Rocío", "destination": "Sevilla"},
            {"id": "POL-01",     "type": "police",        "eta_minutes": 3,
             "base_location": "Comisaría Central",         "destination": "Sevilla"},
        ],
        "unavailable": [],
        "warnings": [],
        "total_units": 2,
        "estimated_first_arrival": 3,
    }
