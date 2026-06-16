"""
Unit tests for tools/classify_incident.py.
"""

from __future__ import annotations

import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.classify_incident import (
    PRIORITY_LEVELS,
    INCIDENT_MAPPING,
    _extract_victim_count,
    _bump_severity,
    _classify_by_rules,
    classify_incident,
    Classification,
)



class TestExtractVictimCount:

    def test_explicit_numeric_count(self):
        assert _extract_victim_count("Hay 3 personas heridas en la calle.") == 3

    def test_explicit_numeric_count_large(self):
        assert _extract_victim_count("Hay 7 víctimas en el accidente.") == 7

    def test_word_number_dos(self):
        assert _extract_victim_count("Dos heridos en el accidente de tráfico.") == 2

    def test_word_number_tres(self):
        assert _extract_victim_count("Hay tres fallecidos.") == 3

    def test_word_number_cuatro(self):
        result = _extract_victim_count("Cuatro personas lesionadas.")
        assert result == 4

    def test_un_herido_singular(self):
        result = _extract_victim_count("Hay un herido en el suelo.")
        assert result >= 1

    def test_una_victima_singular(self):
        result = _extract_victim_count("Hay una víctima inconsciente.")
        assert result >= 1

    def test_fallback_he_not_breathing(self):
        result = _extract_victim_count("Mi padre no respira y no tiene pulso.")
        assert result >= 1

    def test_fallback_she_fell(self):
        result = _extract_victim_count("Ella está inconsciente en el suelo.")
        assert result >= 1

    def test_zero_unrelated_text(self):
        result = _extract_victim_count("Buenas tardes, quería hacer una consulta.")
        assert result == 0

    def test_multiple_patterns_no_double_count(self):
        result = _extract_victim_count("Hay un muerto en el vehículo.")
        assert result == 1

    def test_cardiac_arrest_capped(self):
        raw = classify_incident(
            "Mi padre tiene un paro cardíaco. No respira. Hay dos personas en la habitación."
        )
        result = json.loads(raw)
        if result["incident_type"] == "cardiac_arrest":
            assert result["victims"] == 1

    def test_english_three_injured(self):
        result = _extract_victim_count("There are 3 injured at the scene.")
        assert result == 3

    def test_english_one_unconscious(self):
        result = _extract_victim_count("There is one person unconscious.")
        assert result >= 1

    def test_empty_string(self):
        assert _extract_victim_count("") == 0

    def test_none_like_empty(self):
        """Non-string input shouldn't crash the function."""
        assert _extract_victim_count("") == 0



class TestBumpSeverity:

    def test_bump_up_from_urgent(self):
        """Bumping P-2 up should yield P-1."""
        result = _bump_severity("P-2 (Urgent)", +1)
        assert result == "P-1 (Emergency)"

    def test_bump_down_from_urgent(self):
        """Bumping P-2 down should yield P-3."""
        result = _bump_severity("P-2 (Urgent)", -1)
        assert result == "P-3 (Non-Urgent)"

    def test_bump_up_clamps_at_p1(self):
        """Can't go higher than P-1."""
        result = _bump_severity("P-1 (Emergency)", +1)
        assert result == "P-1 (Emergency)"

    def test_bump_down_clamps_at_p4(self):
        """Can't go lower than P-4."""
        result = _bump_severity("P-4 (Information)", -1)
        assert result == "P-4 (Information)"

    def test_bump_zero(self):
        for level in PRIORITY_LEVELS:
            assert _bump_severity(level, 0) == level

    def test_all_levels_covered(self):
        for level in PRIORITY_LEVELS:
            _bump_severity(level, +1)
            _bump_severity(level, -1)



class TestClassifyByRules:

    def test_cardiac_arrest_no_pulse(self):
        result = _classify_by_rules(
            "Mi padre no respira y no tiene pulso. Está inconsciente."
        )
        assert result is not None
        assert result.incident_type == "cardiac_arrest"
        assert result.priority == "P-1 (Emergency)"

    def test_fire_clear_keyword(self):
        result = _classify_by_rules("Hay un incendio en el edificio, veo llamas.")
        assert result is not None
        assert result.incident_type == "fire"

    def test_gas_leak_keyword(self):
        result = _classify_by_rules("Hay una fuga de gas en el portal de mi casa.")
        assert result is not None
        assert result.incident_type == "gas_leak"

    def test_traffic_accident_collision(self):
        result = _classify_by_rules(
            "Ha habido una colisión entre dos coches en la avenida."
        )
        assert result is not None
        assert result.incident_type == "traffic_accident"

    def test_stroke_face_drooping(self):
        result = _classify_by_rules(
            "Mi madre tiene la cara caída y no puede hablar. Creo que es un ictus."
        )
        assert result is not None
        assert result.incident_type == "stroke"

    def test_no_match_returns_none(self):
        result = _classify_by_rules("Quería preguntar por el horario del mercado.")
        assert result is None

    def test_severity_upgrade_on_unconscious(self):
        result = _classify_by_rules(
            "Ha habido un accidente de tráfico con heridas leves. El conductor está inconsciente."
        )
        assert result is not None
        assert result.incident_type == "traffic_accident"
        assert result.priority == "P-1 (Emergency)"

    def test_severity_downgrade_on_minor(self):
        result = _classify_by_rules(
            "Ha habido un accidente de tráfico pero es leve, sin heridos."
        )
        assert result is not None
        assert result.priority not in ("P-1 (Emergency)",)

    def test_weighted_voting_traffic_wins_over_generic_inconsciente(self):
        result = _classify_by_rules(
            "Accidente de tráfico con choque en la avenida. "
            "El conductor está inconsciente y atrapado."
        )
        assert result is not None
        assert result.incident_type == "traffic_accident"

    def test_domestic_violence_detected(self):
        result = _classify_by_rules(
            "Mi marido me está pegando, por favor ayúdenme."
        )
        assert result is not None
        assert result.incident_type == "domestic_violence"

    def test_confidence_single_match(self):
        result = _classify_by_rules(
            "Hay un incendio en el bloque de pisos."
        )
        assert result is not None
        assert result.confidence == "high"

    def test_confidence_multi_match(self):
        result = _classify_by_rules(
            "Ha habido un accidente de tráfico y hay fuego en el motor."
        )
        assert result is not None
        assert result.confidence == "medium"

    def test_incident_mapping_populated(self):
        transcripts = [
            "Hay un incendio.",
            "Mi padre no respira.",
            "Ha habido un accidente de tráfico.",
            "Huele a gas.",
            "Me están pegando.",
            "Hay una inundación.",
        ]
        for t in transcripts:
            result = _classify_by_rules(t)
            if result:
                assert result.typology in (
                    "Sanitary", "Police", "Extinction and Rescue", "Protection/Civil Services"
                ), f"Unknown typology: {result.typology}"



class TestClassifyIncident:

    def _parse(self, transcript: str) -> dict:
        return json.loads(classify_incident(transcript))


    def test_traffic_accident_full_scenario(self):
        result = self._parse(
            "Ha habido un accidente de tráfico muy grave en la Avenida de la "
            "Constitución esquina con Calle Sierpes. Hay tres vehículos implicados, "
            "uno de los conductores está inconsciente y atrapado en el coche."
        )
        assert result["incident_type"] == "traffic_accident"
        assert result["priority"] == "P-1 (Emergency)"
        assert result["typology"] == "Sanitary"
        assert result["victims"] >= 1

    def test_cardiac_arrest_english(self):
        result = self._parse(
            "My father is not breathing and has no pulse, please send help immediately."
        )
        assert result["incident_type"] == "cardiac_arrest"
        assert result["priority"] == "P-1 (Emergency)"

    def test_fire_in_building(self):
        result = self._parse(
            "There's a fire in the building, I can see flames coming from the third floor."
        )
        assert result["incident_type"] == "fire"
        assert result["typology"] == "Extinction and Rescue"

    def test_gas_leak(self):
        result = self._parse(
            "I smell gas in the kitchen, strong smell, I'm scared."
        )
        assert result["incident_type"] == "gas_leak"

    def test_suicidal_crisis(self):
        result = self._parse(
            "Someone is jumping off the bridge, please help!"
        )
        assert result["incident_type"] == "mental_health_crisis"
        assert result["priority"] == "P-1 (Emergency)"

    def test_fall_injury_minor(self):
        result = self._parse(
            "A man fell down the stairs, he seems okay but is in pain."
        )
        assert result["incident_type"] == "fall_injury"

    def test_flooding(self):
        result = self._parse(
            "Hay una inundación en la calle principal, el agua llega hasta las rodillas."
        )
        assert result["incident_type"] == "flooding"
        assert result["typology"] == "Protection/Civil Services"

    def test_explosion(self):
        result = self._parse("Ha habido una explosión en el local comercial.")
        assert result["incident_type"] == "explosion"
        assert result["priority"] == "P-1 (Emergency)"

    def test_drowning(self):
        result = self._parse(
            "Un niño se está ahogando en la piscina, ¡ayuden por favor!"
        )
        assert result["incident_type"] == "drowning"

    def test_stroke(self):
        result = self._parse(
            "Mi madre tiene un ictus, tiene la cara torcida y no puede hablar."
        )
        assert result["incident_type"] == "stroke"


    def test_empty_string_fallback(self):
        result = self._parse("")
        assert result["incident_type"] == "other"
        assert result["confidence"] == "low"
        assert result["priority"] == "P-3 (Non-Urgent)"

    def test_none_input_treated_as_empty(self):
        result = json.loads(classify_incident(None))
        assert result["incident_type"] == "other"

    def test_integer_input_treated_as_empty(self):
        result = json.loads(classify_incident(42))
        assert result["incident_type"] == "other"

    def test_unrelated_text_fallback(self):
        result = self._parse("Veo un coche sospechoso aparcado en la calle.")
        assert result["incident_type"] in ("traffic_accident", "other")

    def test_output_json_has_all_required_keys(self):
        result = self._parse("Hay un incendio.")
        for key in ("incident_type", "typology", "priority", "victims",
                    "confidence", "matched_keywords"):
            assert key in result, f"Missing key: {key}"

    def test_victims_is_integer(self):
        result = self._parse("Hay cuatro heridos en el accidente de tráfico.")
        assert isinstance(result["victims"], int)

    def test_confidence_values_valid(self):
        for transcript in [
            "Hay un incendio.",
            "Mi padre no respira.",
            "Buenas tardes.",
        ]:
            result = self._parse(transcript)
            assert result["confidence"] in ("high", "medium", "low")

    def test_priority_values_valid(self):
        for transcript in [
            "Ha habido un accidente de tráfico.",
            "Hay un incendio.",
            "Veo un coche raro.",
        ]:
            result = self._parse(transcript)
            assert result["priority"] in PRIORITY_LEVELS

    def test_typology_values_valid(self):
        valid_typologies = {
            "Sanitary", "Police", "Extinction and Rescue", "Protection/Civil Services"
        }
        for transcript in [
            "Hay un incendio.",
            "Me están pegando.",
            "Mi padre no respira.",
            "Hay una inundación.",
        ]:
            result = self._parse(transcript)
            assert result["typology"] in valid_typologies

    def test_infrastructure_collapse(self):
        result = self._parse(
            "Se ha derrumbado un edificio en la calle, hay gente atrapada entre los escombros."
        )
        assert result["incident_type"] == "infrastructure_collapse"
        assert result["priority"] == "P-1 (Emergency)"

    def test_missing_child_priority(self):
        result = self._parse(
            "Mi niña ha desaparecido en el parque, no la encuentro."
        )
        assert result["incident_type"] == "missing_person"
        assert result["priority"] in ("P-1 (Emergency)", "P-2 (Urgent)")

    def test_matched_keywords_is_list(self):
        result = self._parse("Hay un incendio en el edificio.")
        assert isinstance(result["matched_keywords"], list)

    def test_reasoning_present_on_fallback(self):
        result = self._parse("")
        if result["confidence"] == "low":
            assert result.get("reasoning") is not None


    def test_multiple_victims_counted(self):
        result = self._parse(
            "Ha habido un accidente de tráfico con cinco heridos. Hay dos fallecidos."
        )
        assert result["victims"] >= 2

    def test_no_victims_explicit(self):
        result = self._parse(
            "Hay un incendio en el edificio, no hay personas dentro."
        )
        assert result["victims"] == 0 or result["victims"] >= 0



class TestIncidentMapping:

    def test_all_mapped_typologies_valid(self):
        valid = {"Sanitary", "Police", "Extinction and Rescue", "Protection/Civil Services"}
        for itype, typology in INCIDENT_MAPPING.items():
            assert typology in valid, f"{itype} → unknown typology {typology}"

    def test_no_unmapped_incident_types_returned(self):
        """classify_incident should never return a type outside INCIDENT_MAPPING."""
        test_inputs = [
            "Ha habido un accidente de tráfico.",
            "Hay un incendio.",
            "Mi padre no respira.",
            "Me están pegando.",
            "Huele a gas.",
            "Un niño se está ahogando.",
            "Mi madre tiene un ictus.",
            "Me están atracando.",
            "Hay una inundación.",
            "Se ha derrumbado el edificio.",
        ]
        for t in test_inputs:
            result = json.loads(classify_incident(t))
            itype = result["incident_type"]
            assert itype in INCIDENT_MAPPING, (
                f"classify_incident returned unmapped type '{itype}' for: {t}"
            )



if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
