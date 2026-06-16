"""Test extract_location candidate extraction improvements."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.extract_location import _extract_candidates, _normalize_address

tests = [
    ('Hay un incendio en la Calle Resolana numero ocho, Sevilla.', 'Calle Resolana 8'),
    ('Llamo porque huele a gas en la Avenida Menendez Pelayo numero doce, Sevilla.', 'Menendez Pelayo'),
    ('Mi abuela vive en la Calle Gonzalez Cuadrado numero cinco, Sevilla.', 'Gonzalez Cuadrado'),
    ('Hay un accidente en la Calle Feria numero treinta y dos, Sevilla.', '32'),
    ('Una persona se ahoga en el puente de Triana, Sevilla.', 'Triana'),
    ('Hay un accidente en la autovia A-4 kilometro 120.', 'A-4'),
    ('Vivimos en la Plaza del Salvador numero tres.', 'Salvador'),
    ('Hay un accidente de moto en el paseo de la Alameda de Hercules.', 'Alameda'),
]
print('=== Candidate extraction tests ===')
all_ok = True
for transcript, expected_hint in tests:
    candidates = _extract_candidates(transcript)
    normalized = [_normalize_address(c) for c in candidates]
    found = any(expected_hint.lower() in n.lower() for n in normalized)
    status = 'OK' if found else 'WARN'
    if status == 'WARN':
        all_ok = False
    print(f'  {status}: {transcript[:60]}')
    print(f'       Candidates: {candidates[:3]}')
    print(f'       Normalized:  {normalized[:3]}')

print()
print('All OK' if all_ok else 'Some candidates not extracted as expected')
