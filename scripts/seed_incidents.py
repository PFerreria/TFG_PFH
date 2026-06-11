"""
scripts/seed_incidents.py
═════════════════════════
IMERS — Comprehensive Mock Incident Seeder
==========================================
Populates data/imers.db with a rich, realistic dataset covering:
  • All 20 incident types across the 4 typologies
  • 90 days of historical data  (for trend forecasting & KPIs)
  • ~350 historical resolved/closed incidents
  • 12 currently-active incidents (active / en_route)
  • Realistic geographic spread across Seville neighbourhoods
  • Realistic hourly / weekday distribution
  • Proper dispatch sets per incident type
  • Varied severity and response-time profiles

Run from the project root:
    python scripts/seed_incidents.py [--reset]

  --reset   Drop and recreate the incidents table before seeding
            (use when you want a clean slate).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "imers.db"

AREAS: list[dict] = [
    {"name": "Centro Histórico",      "lat": 37.3886, "lon": -5.9823, "w": 18},
    {"name": "Triana",                "lat": 37.3818, "lon": -5.9965, "w": 14},
    {"name": "Nervión",               "lat": 37.3849, "lon": -5.9714, "w": 12},
    {"name": "Macarena",              "lat": 37.4023, "lon": -5.9856, "w": 11},
    {"name": "Los Remedios",          "lat": 37.3736, "lon": -5.9913, "w": 9},
    {"name": "San Pablo - Santa Justa","lat": 37.4068, "lon": -5.9628, "w": 8},
    {"name": "Este - Alcosa",         "lat": 37.3783, "lon": -5.9432, "w": 7},
    {"name": "Sur - Heliópolis",      "lat": 37.3572, "lon": -5.9836, "w": 6},
    {"name": "Palmera - Bellavista",  "lat": 37.3627, "lon": -5.9757, "w": 5},
    {"name": "Torreblanca",           "lat": 37.3718, "lon": -5.8962, "w": 4},
    {"name": "Casco Norte",           "lat": 37.4213, "lon": -5.9782, "w": 4},
    {"name": "Polígono Sur",          "lat": 37.3480, "lon": -5.9810, "w": 3},
]

_AREA_WEIGHTS  = [a["w"] for a in AREAS]
_AREA_NAMES    = [a["name"] for a in AREAS]

ADDRESSES: dict[str, list[str]] = {
    "Centro Histórico": [
        "Avenida de la Constitución 1, Sevilla",
        "Calle Sierpes 45, Sevilla",
        "Calle San Fernando 14, Sevilla",
        "Calle Parras 8, Sevilla",
        "Plaza Nueva 4, Sevilla",
        "Calle Tetuán 21, Sevilla",
        "Calle Albareda 3, Sevilla",
        "Plaza del Salvador 7, Sevilla",
        "Calle Imagen 10, Sevilla",
        "Calle Génova 5, Sevilla",
    ],
    "Triana": [
        "Calle Betis 23, Triana, Sevilla",
        "Calle San Jacinto 80, Triana, Sevilla",
        "Calle Castilla 12, Triana, Sevilla",
        "Calle Tarifa 9, Triana, Sevilla",
        "Avenida República Argentina 7, Sevilla",
        "Calle Pagés del Corro 45, Triana, Sevilla",
        "Calle Evangelista 6, Triana, Sevilla",
        "Plaza del Altozano 2, Triana, Sevilla",
    ],
    "Nervión": [
        "Avenida de Kansas City 4, Sevilla",
        "Calle Luis Montoto 3, Sevilla",
        "Avenida Eduardo Dato 22, Sevilla",
        "Calle Muñoz León 8, Sevilla",
        "Avenida de Sánchez Pizjuán 1, Sevilla",
        "Calle Arjona 12, Sevilla",
        "Calle Almirante Lobo 3, Sevilla",
    ],
    "Macarena": [
        "Calle Feria 43, Sevilla",
        "Calle Resolana 12, Sevilla",
        "Calle Jesús del Gran Poder 22, Sevilla",
        "Calle Peris Mencheta 8, Sevilla",
        "Avenida de la Barzola 5, Sevilla",
        "Calle Torneo 15, Sevilla",
        "Calle Ancha 34, Sevilla",
    ],
    "Los Remedios": [
        "Calle Asunción 41, Sevilla",
        "Calle Virgen de Luján 12, Sevilla",
        "Avenida de la Palmera 45, Sevilla",
        "Calle Virgen del Valle 9, Sevilla",
        "Calle Ronda de Triana 4, Sevilla",
        "Avenida República Argentina 21, Sevilla",
    ],
    "San Pablo - Santa Justa": [
        "Avenida de Kansas City 18, Sevilla",
        "Calle Bami 7, Sevilla",
        "Polígono Industrial Norte, Sevilla",
        "Avenida de Málaga 3, Sevilla",
        "Calle Juan Pablo II 10, Sevilla",
    ],
    "Este - Alcosa": [
        "Calle Poeta Muñoz Rojas 4, Sevilla",
        "Calle Barriada Alcosa 12, Sevilla",
        "Avenida de Miraflores 20, Sevilla",
        "Avenida Doctor Fedriani 8, Sevilla",
    ],
    "Sur - Heliópolis": [
        "Avenida de la Palmera 85, Sevilla",
        "Calle Virgen de la Salud 3, Sevilla",
        "Avenida de Heliópolis 12, Sevilla",
        "Calle Blas Infante 7, Sevilla",
    ],
    "Palmera - Bellavista": [
        "Avenida Reina Mercedes 12, Sevilla",
        "Calle Ingeniero La Cierva 3, Sevilla",
        "Avenida Andalucía 45, Sevilla",
    ],
    "Torreblanca": [
        "Calle Torreblanca 5, Sevilla",
        "Polígono Industrial Calonge, Sevilla",
        "Calle Héroes de Toledo 8, Sevilla",
    ],
    "Casco Norte": [
        "Avenida de la Paz 4, Sevilla",
        "Calle Doctor Marañón 10, Sevilla",
        "Calle Enramadilla 3, Sevilla",
        "Avenida San Lázaro 17, Sevilla",
    ],
    "Polígono Sur": [
        "Calle Torreón 6, Sevilla",
        "Avenida de Portugal 20, Sevilla",
        "Calle Padre Damián 3, Sevilla",
    ],
}

INCIDENT_TYPES: list[dict] = [
    {"type": "traffic_accident",    "typology": "Sanitary",             "sev_w": [0.15, 0.40, 0.35, 0.10], "freq": 70},
    {"type": "cardiac_arrest",      "typology": "Sanitary",             "sev_w": [0.60, 0.30, 0.08, 0.02], "freq": 35},
    {"type": "other_medical",       "typology": "Sanitary",             "sev_w": [0.05, 0.20, 0.50, 0.25], "freq": 30},
    {"type": "fall_injury",         "typology": "Sanitary",             "sev_w": [0.08, 0.25, 0.45, 0.22], "freq": 25},
    {"type": "stroke",              "typology": "Sanitary",             "sev_w": [0.45, 0.40, 0.12, 0.03], "freq": 20},
    {"type": "mental_health_crisis","typology": "Sanitary",             "sev_w": [0.12, 0.35, 0.40, 0.13], "freq": 12},
    {"type": "overdose",            "typology": "Sanitary",             "sev_w": [0.30, 0.40, 0.25, 0.05], "freq": 8},
    {"type": "drowning",            "typology": "Sanitary",             "sev_w": [0.50, 0.35, 0.12, 0.03], "freq": 5},
    {"type": "assault",             "typology": "Police",               "sev_w": [0.10, 0.40, 0.38, 0.12], "freq": 35},
    {"type": "robbery",             "typology": "Police",               "sev_w": [0.08, 0.35, 0.42, 0.15], "freq": 15},
    {"type": "domestic_violence",   "typology": "Police",               "sev_w": [0.20, 0.45, 0.28, 0.07], "freq": 12},
    {"type": "missing_person",      "typology": "Police",               "sev_w": [0.05, 0.28, 0.47, 0.20], "freq": 10},
    {"type": "other_police",        "typology": "Police",               "sev_w": [0.03, 0.20, 0.52, 0.25], "freq": 6},
    {"type": "fire",                "typology": "Extinction & Rescue",  "sev_w": [0.20, 0.45, 0.28, 0.07], "freq": 20},
    {"type": "gas_leak",            "typology": "Extinction & Rescue",  "sev_w": [0.15, 0.40, 0.35, 0.10], "freq": 15},
    {"type": "explosion",           "typology": "Extinction & Rescue",  "sev_w": [0.45, 0.40, 0.12, 0.03], "freq": 5},
    {"type": "chemical_spill",      "typology": "Extinction & Rescue",  "sev_w": [0.25, 0.45, 0.25, 0.05], "freq": 4},
    {"type": "flooding",            "typology": "Protection/Civil",     "sev_w": [0.10, 0.30, 0.42, 0.18], "freq": 8},
    {"type": "infrastructure_collapse","typology":"Protection/Civil",   "sev_w": [0.40, 0.40, 0.18, 0.02], "freq": 3},
    {"type": "other",               "typology": "Protection/Civil",     "sev_w": [0.02, 0.10, 0.48, 0.40], "freq": 5},
]

_INC_TYPE_KEYS    = [t["type"] for t in INCIDENT_TYPES]
_INC_TYPE_FREQS   = [t["freq"] for t in INCIDENT_TYPES]
_INC_TYPE_MAP     = {t["type"]: t for t in INCIDENT_TYPES}

SEVERITIES = ["critical", "high", "medium", "low"]

DISPATCH_SETS: dict[str, list[list[dict]]] = {
    "traffic_accident": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":9},
         {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},
         {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "cardiac_arrest": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},
         {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":5},
         {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
    ],
    "stroke": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-05","type":"ambulance_sva","subtype":"SVA","eta_minutes":9}],
        [{"id":"SVA-07","type":"ambulance_sva","subtype":"SVA","eta_minutes":6}],
    ],
    "drowning": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":5},
         {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":9}],
        [{"id":"VIR-01","type":"ambulance_sva","subtype":"VIR","eta_minutes":6},
         {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":10}],
    ],
    "fall_injury": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
    ],
    "overdose": [
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "mental_health_crisis": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "other_medical": [
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"SVB-07","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
    ],
    "assault": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4},
         {"id":"SVB-02","type":"ambulance_svb","subtype":"SVB","eta_minutes":5}],
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "domestic_violence": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
    ],
    "robbery": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
        [{"id":"MOTO-01","type":"police","subtype":"MOTO","eta_minutes":2},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-06","type":"police","subtype":"ZETA","eta_minutes":8},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "missing_person": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},
         {"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "other_police": [
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"ZETA-07","type":"police","subtype":"ZETA","eta_minutes":4}],
    ],
    "fire": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUL-01","type":"fire","subtype":"BUL","eta_minutes":5},
         {"id":"UMES-01","type":"fire","subtype":"UMES","eta_minutes":6},
         {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7}],
    ],
    "gas_leak": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
        [{"id":"BUL-01","type":"fire","subtype":"BUL","eta_minutes":5},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8}],
    ],
    "explosion": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9}],
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "chemical_spill": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},
         {"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5}],
    ],
    "flooding": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"BUL-02","type":"fire","subtype":"BUL","eta_minutes":7},
         {"id":"FSV-02","type":"rescue","subtype":"FSV","eta_minutes":9}],
    ],
    "infrastructure_collapse": [
        [{"id":"BUP-01","type":"fire","subtype":"BUP","eta_minutes":6},
         {"id":"BUP-02","type":"fire","subtype":"BUP","eta_minutes":8},
         {"id":"BUP-05","type":"fire","subtype":"BUP","eta_minutes":10},
         {"id":"FSV-01","type":"rescue","subtype":"FSV","eta_minutes":9},
         {"id":"SVA-03","type":"ambulance_sva","subtype":"SVA","eta_minutes":7},
         {"id":"SVA-01","type":"ambulance_sva","subtype":"SVA","eta_minutes":8},
         {"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
    ],
    "other": [
        [{"id":"ZETA-01","type":"police","subtype":"ZETA","eta_minutes":3}],
        [{"id":"SVB-01","type":"ambulance_svb","subtype":"SVB","eta_minutes":4}],
        [{"id":"ZETA-03","type":"police","subtype":"ZETA","eta_minutes":5},
         {"id":"SVB-04","type":"ambulance_svb","subtype":"SVB","eta_minutes":6}],
    ],
}

PROTOCOLS: dict[str, str] = {
    "traffic_accident":      "PRT-2024-015 · ATT",
    "cardiac_arrest":        "PRT-2024-001 · PCR",
    "stroke":                "PRT-2024-019 · ICTUS",
    "drowning":              "PRT-2024-017 · AHO",
    "fall_injury":           "PRT-2024-011 · TRA",
    "overdose":              "PRT-2024-024 · OVD",
    "mental_health_crisis":  "PRT-2024-033 · PSI",
    "other_medical":         "PRT-2024-050 · MED",
    "assault":               "PRT-2024-031 · AGR",
    "domestic_violence":     "PRT-2024-032 · VDG",
    "robbery":               "PRT-2024-036 · ROB",
    "missing_person":        "PRT-2024-038 · DES",
    "other_police":          "PRT-2024-040 · POL",
    "fire":                  "PRT-2024-008 · INC",
    "gas_leak":              "PRT-2024-022 · GAS",
    "explosion":             "PRT-2024-009 · EXP",
    "chemical_spill":        "PRT-2024-045 · MAT",
    "flooding":              "PRT-2024-027 · INE",
    "infrastructure_collapse":"PRT-2024-048 · COL",
    "other":                 "PRT-2024-099 · GEN",
}

NOTES: dict[str, list[str]] = {
    "traffic_accident": [
        "Colisión frontal. Dos vehículos implicados. Una víctima atrapada.",
        "Atropello en paso de cebra. Peatón consciente.",
        "Accidente en rotonda. Sin víctimas graves aparentes.",
        "Vuelco de vehículo. Conductor inconsciente.",
        "Choque trasero en semáforo. Cuatro personas afectadas.",
        "Accidente de moto. Motorista con fractura de pierna.",
        "Colisión multiple en SE-30. Tres vehículos. PMA activado.",
        "Camión de reparto accidentado. Derrame de carga menor.",
    ],
    "cardiac_arrest": [
        "Paciente encontrado inconsciente. Testigos hacen RCP.",
        "Paro cardiorrespiratoria en domicilio. Familiar al teléfono realizando compresiones.",
        "PCR presenciada en vía pública. DEA disponible en comercio cercano.",
        "Anciano de 78 años sin pulso. Hija proporciona información.",
        "PCR en instalación deportiva. Personal entrenado iniciando DESA.",
        "Infarto masivo. Paciente no responde. Esposa realizando ventilaciones.",
    ],
    "stroke": [
        "Paciente con desviación facial y debilidad en brazo derecho. Activado código ictus.",
        "Dificultad para hablar. Familiar refiere inicio súbito hace 45 minutos.",
        "Hemiplejia izquierda. Paciente consciente pero desorientado.",
        "Mujer de 65 años con cefalea intensa y pérdida de visión. Posible ACV.",
        "Afasia motora súbita. Tiempo de evolución desconocido.",
    ],
    "drowning": [
        "Persona en el río Guadalquivir. Testigos intentan auxilio desde orilla.",
        "Niño accidentado en piscina. Socorrista realizando RCP.",
        "Bañista en dificultades. Salvavidas en camino.",
        "Ahogamiento en canal de riego. Víctima rescatada pero inconsciente.",
    ],
    "fall_injury": [
        "Anciana caída en escalera. Posible fractura de cadera.",
        "Obrero caído desde andamio a 4 metros de altura.",
        "Ciclista caído. Traumatismo cráneo-encefálico leve. Casco puesto.",
        "Niño caído de árbol. Consciente. Dolor en muñeca.",
        "Caída en domicilio. Paciente de 85 años en el suelo desde hace horas.",
        "Patinador con fractura abierta de tibia.",
    ],
    "overdose": [
        "Persona inconsciente con jeringuilla en brazo. Posible sobredosis de heroína.",
        "Joven encontrado sin respuesta en aseos públicos. Olor a disolvente.",
        "Sobredosis de benzodiacepinas. Familiar administró naloxona.",
        "Intoxicación etílica grave. Joven de 19 años inconsciente en vía pública.",
    ],
    "mental_health_crisis": [
        "Persona en azotea amenazando con lanzarse. Negociador solicitado.",
        "Paciente psiquiátrico agitado. Agresividad hacia familiares.",
        "Intento autolítico con objeto cortante. Herida superficial. Consciente.",
        "Ataque de pánico severo. Persona hiperventilando en lugar público.",
        "Crisis disociativa. Persona en la calle, desorientada y sin documentación.",
    ],
    "other_medical": [
        "Dificultad respiratoria aguda. Paciente asmático sin inhalador.",
        "Hipoglucemia severa. Diabético inconsciente. Familiar presente.",
        "Dolor abdominal intenso. Paciente no puede moverse.",
        "Reacción alérgica grave. Hinchazón facial. Posible anafilaxia.",
        "Convulsiones. Primera vez. Paciente recuperando consciencia.",
        "Epistaxis severa que no cede. Paciente anticoagulado.",
    ],
    "assault": [
        "Agresión con arma blanca. Víctima con herida en abdomen. Agresor huido.",
        "Pelea multitudinaria. Varios heridos de consideración.",
        "Agresión a vigilante de seguridad. Traumatismo facial.",
        "Hombre golpeado por grupo. Consciente pero confuso.",
        "Agresión racista. Víctima con fractura nasal y contusiones.",
    ],
    "domestic_violence": [
        "Mujer maltratada por pareja. Hematomas visibles. Niños presentes.",
        "Vecinos alertan por gritos. Mujer con marcas en cuello.",
        "Agresor en domicilio. Víctima pide ayuda desde exterior.",
        "Violencia de género. Activado protocolo VIOGEN.",
    ],
    "robbery": [
        "Atraco a mano armada en gasolinera. Delincuente huyó en moto.",
        "Robo con violencia en joyería. Cliente herido en la refriega.",
        "Carterista ha empujado a víctima. Anciana con muñeca dolorida.",
        "Robo de vehículo con conductor dentro. Conductor expulsado a la vía.",
    ],
    "missing_person": [
        "Menor de 12 años desaparecido desde las 17:00. Última vez visto en parque.",
        "Anciano con Alzheimer extraviado. No lleva documentación ni teléfono.",
        "Adolescente de 15 años no vuelve del instituto. Padres muy preocupados.",
        "Mujer desaparecida en contexto de violencia de género.",
    ],
    "other_police": [
        "Acto vandálico en instalación pública. Grupo numeroso.",
        "Alteración del orden en establecimiento. Clientes amenazados.",
        "Ocupación ilegal. Propietario solicita intervención.",
        "Denuncia de acoso continuado. Persona en estado de nervios.",
    ],
    "fire": [
        "Incendio en planta baja de edificio de 5 plantas. Evacuación en curso.",
        "Fuego en vehículo estacionado junto a gasolinera. Riesgo de expansión.",
        "Incendio forestal en zona periurbana. Viento de componente este.",
        "Llamaradas en cocina industrial. Personal evacuado.",
        "Incendio en local comercial cerrado. Sin víctimas declaradas.",
        "Fuego en cubierta de nave industrial. Riesgo de colapso estructural.",
    ],
    "gas_leak": [
        "Olor a gas en planta tercera. Edificio de 8 plantas. Evacuación preventiva.",
        "Tubería de gas rota durante obras. Calle cortada.",
        "Fuga en instalación doméstica. Vecina mayor no puede desalojar sola.",
        "Gas natural en parking subterráneo. Concentración creciente.",
    ],
    "explosion": [
        "Explosión en fábrica de pinturas. Múltiples heridos. Estructura dañada.",
        "Deflagración en caldera de edificio. Daños materiales. Un herido leve.",
        "Explosión de bombona de butano en restaurante. Incendio posterior.",
    ],
    "chemical_spill": [
        "Camión cisterna derramó producto desconocido en vía pública.",
        "Vertido de ácido en laboratorio. Tres trabajadores con irritación.",
        "Derrame de cloro en instalación de tratamiento de agua.",
    ],
    "flooding": [
        "Socavón en calzada. Agua filtrándose en garaje. Riesgo de personas atrapadas.",
        "Desbordamiento de arroyo. Vehículos atrapados en carretera.",
        "Inundación de semisótano. Familia con dos menores solicita evacuación.",
        "Paso inferior anegado. Conductor con vehículo inmovilizado.",
    ],
    "infrastructure_collapse": [
        "Derrumbe parcial de cornisa. Zona acordonada. Un peatón herido.",
        "Hundimiento de zanja en obras. Operario atrapado bajo escombros.",
        "Colapso de marquesina de autobús. Varios heridos sentados bajo ella.",
    ],
    "other": [
        "Árbol caído sobre vehículo. Conductor ileso. Vía cortada.",
        "Animal herido en calzada. Peligro para la circulación.",
        "Fuente pública averiada. Agua invadiendo calzada.",
        "Ascensor averiado con personas dentro. Sin atrapados.",
    ],
}

HOURLY_WEIGHTS = [
    4, 3, 2, 2, 3, 5,
    8, 12, 15, 16, 14, 13,
    12, 14, 15, 14, 16, 18,
    17, 16, 14, 12, 10, 7,
]
DAY_WEIGHTS = [1.0, 1.0, 1.1, 1.2, 1.4, 1.6, 1.1]

RESP_PROFILES: dict[str, dict[str, tuple[float, float, float]]] = {
    "cardiac_arrest":   {"critical":(4.5,0.8,2.0),"high":(5.5,1.0,3.0),"medium":(7.0,1.5,4.0),"low":(9.0,2.0,5.0)},
    "stroke":           {"critical":(4.8,0.9,2.5),"high":(5.8,1.1,3.0),"medium":(7.2,1.5,4.0),"low":(9.5,2.0,5.0)},
    "traffic_accident": {"critical":(5.0,1.0,2.5),"high":(6.5,1.5,3.5),"medium":(8.0,2.0,4.0),"low":(11.0,2.5,6.0)},
    "fire":             {"critical":(5.5,1.0,3.0),"high":(6.5,1.5,3.5),"medium":(8.5,2.0,5.0),"low":(12.0,2.5,7.0)},
    "explosion":        {"critical":(5.0,0.8,3.0),"high":(6.0,1.2,3.5),"medium":(8.0,2.0,5.0),"low":(11.0,2.5,6.0)},
    "assault":          {"critical":(3.5,0.7,2.0),"high":(4.5,1.0,2.5),"medium":(6.5,1.5,3.5),"low":(9.0,2.0,5.0)},
    "domestic_violence":{"critical":(3.5,0.7,2.0),"high":(4.5,1.0,2.5),"medium":(6.0,1.5,3.0),"low":(8.5,2.0,4.5)},
    "gas_leak":         {"critical":(5.0,1.0,3.0),"high":(6.5,1.5,3.5),"medium":(8.5,2.0,5.0),"low":(12.0,2.5,7.0)},
    "flooding":         {"critical":(6.0,1.5,3.0),"high":(8.0,2.0,4.0),"medium":(11.0,3.0,6.0),"low":(15.0,3.5,8.0)},
    "infrastructure_collapse":{"critical":(5.5,1.0,3.0),"high":(7.0,1.5,4.0),"medium":(9.5,2.0,5.0),"low":(13.0,3.0,7.0)},
    "_default":         {"critical":(5.0,1.2,2.5),"high":(7.0,1.8,3.5),"medium":(9.0,2.5,4.5),"low":(13.0,3.0,6.0)},
}


def _rand_response_time(rng: random.Random, itype: str, severity: str) -> float:
    profile = RESP_PROFILES.get(itype, RESP_PROFILES["_default"])
    mean, std, minimum = profile.get(severity, profile.get("medium", (8.0, 2.0, 4.0)))
    return round(max(minimum, rng.gauss(mean, std)), 1)


def _rand_area(rng: random.Random) -> dict:
    return rng.choices(AREAS, weights=_AREA_WEIGHTS)[0]


def _rand_address(rng: random.Random, area_name: str) -> str:
    options = ADDRESSES.get(area_name, ["Calle Desconocida, Sevilla"])
    return rng.choice(options)


def _jitter(rng: random.Random, lat: float, lon: float,
            sigma_lat: float = 0.0018, sigma_lon: float = 0.0025) -> tuple[float, float]:
    """Add small random offset to keep incidents within the neighbourhood."""
    new_lat = lat + rng.gauss(0, sigma_lat)
    new_lon = lon + rng.gauss(0, sigma_lon)
    new_lat = max(37.25, min(37.52, new_lat))
    new_lon = max(-6.12, min(-5.82, new_lon))
    return round(new_lat, 5), round(new_lon, 5)


def _rand_timestamp(rng: random.Random, days_back_max: float, days_back_min: float = 0.0) -> datetime:
    """Return a realistic UTC timestamp weighted toward daytime hours."""
    frac_day = rng.uniform(days_back_min, days_back_max)
    base = datetime.now(timezone.utc) - timedelta(days=frac_day)
    hour = rng.choices(range(24), weights=HOURLY_WEIGHTS)[0]
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _build_incident(
    rng: random.Random,
    idx: int,
    itype: str,
    severity: str,
    status: str,
    days_back_min: float,
    days_back_max: float,
) -> dict:
    area = _rand_area(rng)
    lat, lon = _jitter(rng, area["lat"], area["lon"])
    address = _rand_address(rng, area["name"])
    ts = _rand_timestamp(rng, days_back_max, days_back_min)

    units_pool = DISPATCH_SETS.get(itype, DISPATCH_SETS["other"])
    units = rng.choice(units_pool)
    first_arrival = min(u["eta_minutes"] for u in units)
    resp_time = _rand_response_time(rng, itype, severity)
    victims = _pick_victims(rng, itype, severity)
    note = rng.choice(NOTES.get(itype, ["Incidente registrado."]))
    confidence = rng.randint(78, 99)

    inc_id = f"INC-{idx:05d}"
    return {
        "id":            inc_id,
        "incident_id":   inc_id,
        "incident_type": itype,
        "typology":      _INC_TYPE_MAP[itype]["typology"],
        "severity":      severity,
        "latitude":      lat,
        "longitude":     lon,
        "address":       address,
        "area":          area["name"],
        "timestamp":     ts.isoformat(),
        "status":        status,
        "dispatch": {
            "units":                 units,
            "total_units":           len(units),
            "first_arrival_minutes": first_arrival,
            "decision":              "",
        },
        "units_dispatched":  len(units),
        "response_time_min": resp_time,
        "confidence_score":  confidence,
        "victims":           victims,
        "protocol":          PROTOCOLS.get(itype, "PRT-2024-099 · GEN"),
        "note":              note,
        "decision":          "",
    }


def _pick_victims(rng: random.Random, itype: str, severity: str) -> int:
    """Return a realistic victim count based on incident type and severity."""
    if itype in ("explosion", "infrastructure_collapse"):
        if severity == "critical":
            return rng.randint(3, 12)
        return rng.randint(1, 5)
    if itype in ("traffic_accident",):
        if severity == "critical":
            return rng.randint(2, 6)
        if severity == "high":
            return rng.randint(1, 4)
        return rng.randint(0, 2)
    if itype in ("flooding", "fire"):
        return rng.randint(0, 4)
    if itype in ("cardiac_arrest", "stroke", "drowning", "overdose"):
        return 1
    if itype in ("assault", "domestic_violence"):
        return rng.randint(1, 2)
    return rng.randint(0, 1)


ACTIVE_INCIDENTS: list[dict] = [
    {
        "itype": "traffic_accident", "severity": "critical",
        "area_idx": 0,
        "address": "Avenida de la Constitución 12, Sevilla",
        "lat": 37.38842, "lon": -5.98374,
        "note": "Colisión frontal con vuelco. Dos personas atrapadas. Triaje START activado.",
        "units": [
            {"id":"AMB-SVA-01","type":"ambulance_sva","eta_minutes":6},
            {"id":"AMB-SVB-01","type":"ambulance_svb","eta_minutes":4},
            {"id":"POL-01","type":"police","eta_minutes":3},
            {"id":"BOM-01","type":"fire","eta_minutes":7},
        ],
    },
    {
        "itype": "cardiac_arrest", "severity": "critical",
        "area_idx": 2,
        "address": "Calle Luis Montoto 40, Sevilla",
        "lat": 37.38500, "lon": -5.97050,
        "note": "Paciente de 67 años inconsciente. Testigo realizando RCP. SVA en camino.",
        "units": [
            {"id":"AMB-SVA-03","type":"ambulance_sva","eta_minutes":5},
            {"id":"AMB-SVB-04","type":"ambulance_svb","eta_minutes":3},
        ],
    },
    {
        "itype": "fire", "severity": "high",
        "area_idx": 3,
        "address": "Calle Feria 67, Sevilla",
        "lat": 37.40285, "lon": -5.98621,
        "note": "Incendio en local comercial. Humo visible desde calle. Evacuación en curso.",
        "units": [
            {"id":"BOM-01","type":"fire","eta_minutes":5},
            {"id":"BOM-02","type":"fire","eta_minutes":7},
            {"id":"AMB-SVB-02","type":"ambulance_svb","eta_minutes":5},
            {"id":"POL-01","type":"police","eta_minutes":3},
        ],
    },
    {
        "itype": "stroke", "severity": "critical",
        "area_idx": 4,
        "address": "Calle Asunción 55, Sevilla",
        "lat": 37.37401, "lon": -5.99045,
        "note": "Mujer 72 años. Desviación facial izquierda. Afasia. Código ictus activado.",
        "units": [
            {"id":"AMB-SVA-01","type":"ambulance_sva","eta_minutes":6},
            {"id":"AMB-SVB-01","type":"ambulance_svb","eta_minutes":4},
        ],
    },
    {
        "itype": "assault", "severity": "high",
        "area_idx": 1,
        "address": "Calle Betis 45, Triana, Sevilla",
        "lat": 37.38124, "lon": -5.99718,
        "note": "Agresión con arma blanca en zona de bares. Agresor contenido por testigos. Herida en costado.",
        "units": [
            {"id":"POL-04","type":"police","eta_minutes":4},
            {"id":"POL-01","type":"police","eta_minutes":5},
            {"id":"AMB-SVA-01","type":"ambulance_sva","eta_minutes":7},
        ],
    },
    {
        "itype": "gas_leak", "severity": "high",
        "area_idx": 3,
        "address": "Calle Resolana 18, Sevilla",
        "lat": 37.40198, "lon": -5.98520,
        "note": "Fuerte olor a gas en bloque de 6 plantas. Edificio evacuado. Empresa de gas avisada.",
        "units": [
            {"id":"BOM-02","type":"fire","eta_minutes":6},
            {"id":"POL-02","type":"police","eta_minutes":4},
            {"id":"AMB-SVB-02","type":"ambulance_svb","eta_minutes":5},
        ],
    },
    {
        "itype": "domestic_violence", "severity": "critical",
        "area_idx": 9,
        "address": "Calle Torreblanca 22, Sevilla",
        "lat": 37.37245, "lon": -5.89650,
        "note": "Mujer llama desde baño. Pareja la ha agredido. Dos hijos menores presentes. Protocolo VIOGEN activado.",
        "units": [
            {"id":"POL-01","type":"police","eta_minutes":5},
            {"id":"POL-03","type":"police","eta_minutes":8},
            {"id":"AMB-SVB-03","type":"ambulance_svb","eta_minutes":6},
        ],
    },
    {
        "itype": "flooding", "severity": "medium",
        "area_idx": 11,
        "address": "Avenida de Portugal 30, Sevilla",
        "lat": 37.34856, "lon": -5.98054,
        "note": "Socavón abierto tras lluvias. Garaje inundado. Familia solicita evacuación.",
        "units": [
            {"id":"BOM-01","type":"fire","eta_minutes":8},
            {"id":"POL-02","type":"police","eta_minutes":5},
        ],
    },
    {
        "itype": "robbery", "severity": "high",
        "area_idx": 0,
        "address": "Calle Sierpes 72, Sevilla",
        "lat": 37.38941, "lon": -5.99140,
        "note": "Atraco con navaja a turistas. Delincuente huyó en dirección Plaza Nueva. Descripción tomada.",
        "units": [
            {"id":"POL-01","type":"police","eta_minutes":2},
            {"id":"POL-04","type":"police","eta_minutes":3},
        ],
    },
    {
        "itype": "mental_health_crisis", "severity": "high",
        "area_idx": 2,
        "address": "Avenida Eduardo Dato 42, Sevilla",
        "lat": 37.38524, "lon": -5.97205,
        "note": "Persona en azotea de 5ª planta. Negociador en camino. Unidad policial asegura perímetro.",
        "units": [
            {"id":"POL-01","type":"police","eta_minutes":4},
            {"id":"POL-02","type":"police","eta_minutes":6},
            {"id":"AMB-SVA-03","type":"ambulance_sva","eta_minutes":8},
        ],
    },
    {
        "itype": "fall_injury", "severity": "high",
        "area_idx": 5,
        "address": "Polígono Industrial Norte, Nave 14, Sevilla",
        "lat": 37.40710, "lon": -5.96320,
        "note": "Trabajador caído desde andamio a 5 metros. Consciente. Sospecha de fractura de columna.",
        "units": [
            {"id":"AMB-SVA-02","type":"ambulance_sva","eta_minutes":7},
            {"id":"AMB-SVB-02","type":"ambulance_svb","eta_minutes":5},
            {"id":"POL-03","type":"police","eta_minutes":8},
        ],
    },
    {
        "itype": "overdose", "severity": "critical",
        "area_idx": 11,
        "address": "Calle Padre Damián 8, Sevilla",
        "lat": 37.34920, "lon": -5.97980,
        "note": "Varón hallado inconsciente. Posible sobredosis de heroína. Cianosis visible. Naloxona administrada por testigo.",
        "units": [
            {"id":"AMB-SVA-01","type":"ambulance_sva","eta_minutes":7},
            {"id":"AMB-SVB-01","type":"ambulance_svb","eta_minutes":5},
            {"id":"POL-02","type":"police","eta_minutes":4},
        ],
    },
]



def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _create_schema(conn: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        conn.execute("DROP TABLE IF EXISTS incidents")
        print("  [reset] Dropped existing incidents table."  )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id            TEXT PRIMARY KEY,
            status        TEXT NOT NULL DEFAULT 'active',
            timestamp     TEXT NOT NULL,
            incident_type TEXT,
            severity      TEXT,
            data          TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status    ON incidents (status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON incidents (timestamp DESC)")
    conn.commit()


def _insert_batch(conn: sqlite3.Connection, incidents: list[dict]) -> int:
    rows = [
        (
            inc["id"],
            inc["status"],
            inc["timestamp"],
            inc["incident_type"],
            inc["severity"],
            json.dumps(inc),
        )
        for inc in incidents
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO incidents (id, status, timestamp, incident_type, severity, data) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)



def seed_historical(conn: sqlite3.Connection, rng: random.Random, start_idx: int = 1) -> int:
    """Generate ~350 historical resolved incidents spread over 90 days."""
    incidents: list[dict] = []
    idx = start_idx

    total_hist = 350
    type_counts: dict[str, int] = {}
    leftovers = []
    total_freq = sum(t["freq"] for t in INCIDENT_TYPES)
    for t in INCIDENT_TYPES:
        exact = t["freq"] / total_freq * total_hist
        n = int(exact)
        type_counts[t["type"]] = n
        leftovers.append((exact - n, t["type"]))
    leftovers.sort(reverse=True)
    remainder = total_hist - sum(type_counts.values())
    for _, tkey in leftovers[:remainder]:
        type_counts[tkey] += 1

    for itype, count in type_counts.items():
        type_info = _INC_TYPE_MAP[itype]
        sev_weights = type_info["sev_w"]
        for _ in range(count):
            severity = rng.choices(SEVERITIES, weights=sev_weights)[0]
            status = rng.choices(
                ["resolved", "closed", "resolved"],
                weights=[0.70, 0.15, 0.15]
            )[0]
            inc = _build_incident(
                rng=rng,
                idx=idx,
                itype=itype,
                severity=severity,
                status=status,
                days_back_min=0.25,
                days_back_max=90.0,
            )
            incidents.append(inc)
            idx += 1

    rng.shuffle(incidents)
    inserted = _insert_batch(conn, incidents)
    return idx


def seed_active(conn: sqlite3.Connection, rng: random.Random, start_idx: int = 4001) -> int:
    """Insert the 12 hard-coded active incidents."""
    now = datetime.now(timezone.utc)
    idx = start_idx
    inserted = 0

    for tmpl in ACTIVE_INCIDENTS:
        area = AREAS[tmpl["area_idx"]]
        lat, lon = _jitter(rng, tmpl["lat"], tmpl["lon"], 0.0002, 0.0002)
        units = tmpl["units"]
        first_arrival = min(u["eta_minutes"] for u in units)
        minutes_ago = rng.uniform(2, 18)
        ts = now - timedelta(minutes=minutes_ago)
        itype = tmpl["itype"]
        severity = tmpl["severity"]

        inc_id = f"INC-{idx:05d}"
        inc = {
            "id":            inc_id,
            "incident_id":   inc_id,
            "incident_type": itype,
            "typology":      _INC_TYPE_MAP[itype]["typology"],
            "severity":      severity,
            "latitude":      lat,
            "longitude":     lon,
            "address":       tmpl["address"],
            "area":          area["name"],
            "timestamp":     ts.isoformat(),
            "status":        "active",
            "dispatch": {
                "units":                 units,
                "total_units":           len(units),
                "first_arrival_minutes": first_arrival,
                "decision":              "",
            },
            "units_dispatched":  len(units),
            "response_time_min": round(first_arrival + rng.uniform(0.5, 2.5), 1),
            "confidence_score":  rng.randint(88, 99),
            "victims":           _pick_victims(rng, itype, severity),
            "protocol":          PROTOCOLS.get(itype, "PRT-2024-099 · GEN"),
            "note":              tmpl["note"],
            "decision":          "",
        }
        _insert_batch(conn, [inc])
        idx += 1
        inserted += 1

    return idx



def main():
    parser = argparse.ArgumentParser(description="IMERS comprehensive incident seeder")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate the incidents table before seeding")
    args = parser.parse_args()

    print(f"IMERS Seed Script")
    print(f"DB path : {DB_PATH}")
    print(f"Reset   : {args.reset}")
    print()

    rng = random.Random(42)

    with _get_conn() as conn:
        _create_schema(conn, reset=args.reset)

        count_before = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        if count_before > 0 and not args.reset:
            print(f"  Database already has {count_before} incidents. Use --reset to overwrite.")
            return

        print("Seeding historical incidents …")
        next_idx = seed_historical(conn, rng, start_idx=1)
        hist_count = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE status NOT IN ('active','en_route')"
        ).fetchone()[0]
        print(f"  > {hist_count} historical incidents inserted.")

        print("Seeding active incidents …")
        seed_active(conn, rng, start_idx=4001)
        active_count = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE status IN ('active','en_route')"
        ).fetchone()[0]
        print(f"  > {active_count} active incidents inserted.")

        total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        print()
        print("-" * 60)
        print(f"  Total incidents : {total}")
        print(f"  Historical      : {hist_count}")
        print(f"  Active          : {active_count}")
        print()

        print("Distribution by type:")
        rows = conn.execute(
            "SELECT incident_type, COUNT(*) as n FROM incidents GROUP BY incident_type ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            bar = "#" * (r[1] // 2)
            print(f"  {r[0]:28s} {r[1]:4d}  {bar}")

        print()
        print("Distribution by severity:")
        rows = conn.execute(
            "SELECT severity, COUNT(*) as n FROM incidents GROUP BY severity ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            bar = "#" * (r[1] // 5)
            print(f"  {r[0]:12s} {r[1]:4d}  {bar}")

        print()
        print("Date range:")
        row = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM incidents WHERE status NOT IN ('active','en_route')"
        ).fetchone()
        print(f"  Oldest : {row[0]}")
        print(f"  Newest : {row[1]}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
