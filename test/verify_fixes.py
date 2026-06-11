"""Quick verification of bug fixes."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

print("=== BUG-001: Spanish number words in addresses ===")
from tools.extract_location import _normalize_address, _words_to_int

tests = [
    ("Calle Feria numero treinta y dos", "32"),
    ("Avenida Menendez Pelayo numero doce", "12"),
    ("Calle Resolana numero ocho", "8"),
    ("Plaza del Salvador numero tres", "3"),
    ("Calle Sierpes numero 14", "14"),
    ("Calle Feria numero cuarenta y cinco", "45"),
    ("Avenida numero ciento veinte", "120"),
]
for addr, expected in tests:
    result = _normalize_address(addr)
    ok = expected in result
    print(f"  {'OK' if ok else 'FAIL'}: {addr!r} -> {result!r} (expected '{expected}')")

print()
print("=== BUG-002: Gas leak vs explosion classification ===")
from tools.classify_incident import classify_incident

t11 = "Llamo porque huele a gas en toda mi escalera. El olor es muy fuerte y tengo miedo de que haya una explosion."
r = json.loads(classify_incident(t11))
ok = r["incident_type"] == "gas_leak"
print(f"  {'OK' if ok else 'FAIL'}: Gas leak fear-of-explosion -> type={r['incident_type']} (expected gas_leak)")

t23 = "Ha habido una explosion muy fuerte en el bar de la Calle Cervantes. Hay cristales rotos."
r = json.loads(classify_incident(t23))
ok = r["incident_type"] == "explosion"
print(f"  {'OK' if ok else 'FAIL'}: Confirmed explosion -> type={r['incident_type']} (expected explosion)")

t_gas_simple = "Hay una fuga de gas en mi edificio en la Calle Torneo."
r = json.loads(classify_incident(t_gas_simple))
ok = r["incident_type"] == "gas_leak"
print(f"  {'OK' if ok else 'FAIL'}: Simple gas leak -> type={r['incident_type']} (expected gas_leak)")

print()
print("=== BUG-004: Stroke lay-language symptoms ===")

t14 = "Mi marido ha empezado a hablar raro, dice palabras sin sentido y se ha caido."
r = json.loads(classify_incident(t14))
ok = r["incident_type"] == "stroke"
print(f"  {'OK' if ok else 'FAIL'}: Stroke (hablar raro + palabras sin sentido) -> type={r['incident_type']} (expected stroke)")

t14b = "Mi madre no puede levantar el brazo izquierdo y tiene la cara torcida."
r = json.loads(classify_incident(t14b))
ok = r["incident_type"] == "stroke"
print(f"  {'OK' if ok else 'FAIL'}: Stroke (cara torcida + brazo no puede levantar) -> type={r['incident_type']} (expected stroke)")

t_stroke_clear = "Mi madre ha tenido un ictus, creo. Tiene la cara torcida, no puede hablar bien."
r = json.loads(classify_incident(t_stroke_clear))
ok = r["incident_type"] == "stroke"
print(f"  {'OK' if ok else 'FAIL'}: Stroke (ictus + cara torcida) -> type={r['incident_type']} (expected stroke)")

print()
print("=== Regression checks: should not break existing correct classifications ===")

regressions = [
    ("Hay un accidente de trafico en la Avenida de la Constitucion, hay tres heridos.", "traffic_accident"),
    ("Mi padre ha tenido un paro cardiaco, no respira.", "cardiac_arrest"),
    ("Hay un incendio en el edificio de apartamentos, llamas por las ventanas.", "fire"),
    ("Me estan atracando en el cajero, tienen una pistola.", "robbery"),
    ("Hay un derrumbe en el edificio de la Calle Catalanes.", "infrastructure_collapse"),
]
for transcript, expected_type in regressions:
    r = json.loads(classify_incident(transcript))
    ok = r["incident_type"] == expected_type
    print(f"  {'OK' if ok else 'FAIL'}: {expected_type} -> got {r['incident_type']}")
