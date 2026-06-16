from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from typing import Optional

from smolagents import tool

TYPOLOGIES = [
    "Sanitary",
    "Police",
    "Extinction and Rescue",
    "Protection/Civil Services"
]

PRIORITY_LEVELS = (
    "P-1 (Emergency)",
    "P-2 (Urgent)",
    "P-3 (Non-Urgent)",
    "P-4 (Information)"
)

INCIDENT_MAPPING = {
    "traffic_accident": "Sanitary",
    "traffic_disruption": "Police",
    "fire": "Extinction and Rescue",
    "cardiac_arrest": "Sanitary",
    "stroke": "Sanitary",
    "assault": "Police",
    "domestic_violence": "Police",
    "robbery": "Police",
    "drowning": "Sanitary",
    "fall_injury": "Sanitary",
    "overdose": "Sanitary",
    "gas_leak": "Extinction and Rescue",
    "explosion": "Extinction and Rescue",
    "missing_person": "Police",
    "mental_health": "Sanitary",
    "mental_health_crisis": "Sanitary",
    "choking": "Sanitary",
    "flooding": "Protection/Civil Services",
    "infrastructure_collapse": "Protection/Civil Services",
    "chemical_spill": "Extinction and Rescue",
    "utility_failure": "Protection/Civil Services",
    "other_medical": "Sanitary",
    "other_police": "Police",
    "other": "Protection/Civil Services",
}

_RULES: list[tuple[str, str, str, int]] = [
    (r"\baccidente\b.*?\btr[aá]fico\b|\baccidente\s+de\s+(coche|carro|tr[aá]fico|moto|camion)\b|\bchoque\b|\bcolisi[oó]n\b",
     "traffic_accident", "P-2 (Urgent)", 4),
    (r"\batropello\b|\batropellad[oa]\b|\bpeatón\b.*\bgolpe\b|\bpedestrian\s+hit\b|\brun\s+over\b",
     "traffic_accident", "P-1 (Emergency)", 5),
    (r"\bveh[ií]culo\b.*\b(volcad[oa]|volcado|chocado|accidente)\b|\bcoche\b.*\b(volcad|caído|accidente|choc)\b",
     "traffic_accident", "P-1 (Emergency)", 4),
    (r"\bha[n]?\s+chocado\b|\bchoc[oó]\b|\bcolision[oó]\b",
     "traffic_accident", "P-1 (Emergency)", 4),

    (r"\baccidente\b.*?\bherido[s]?\b|\bherido[s]?\b.*?\baccidente\b|\baccidente\b.*?\bcalzada\b",
     "traffic_accident", "P-1 (Emergency)", 4),
    (r"\b(coche|carro|moto|camión|autobús|bus|vehículo|conductor|piloto)\b",
     "traffic_accident", "P-3 (Non-Urgent)", 2),

    (r"\bincendio\b|\bfuego\b|\bllamas\b|\bfire\b|\bburning\b",
     "fire", "P-1 (Emergency)", 5),
    (r"\bhumo\b.*?\b(edificio|casa|piso|local|coche|cochera|garage)\b|\bsmoke\b.*?\b(building|house|car)\b",
     "fire", "P-1 (Emergency)", 4),
    (r"\bolía\s+a\s+quemado\b|\bhuele\s+a\s+quemado\b|\bsmells?\s+like\s+smoke\b",
     "fire", "P-2 (Urgent)", 3),

    (r"\bfuga\s+de\s+gas\b|\bgas\s+leak\b|\bhuele\s+a\s+gas\b|\bsmell.*?gas\b|\bolor\b.*?\bgas\b",
     "gas_leak", "P-1 (Emergency)", 5),
    (r"\bgas\b.*?\bfuga\b|\bescapa\b.*?\bgas\b",
     "gas_leak", "P-1 (Emergency)", 4),
    (r"(?:huele|huelen|olor)\b.*?\bgas\b.*?\bexplosi[oó]n\b|\bfuga.*?\bgas\b.*?\bexplosi[oó]n\b",
     "gas_leak", "P-1 (Emergency)", 7),

    (r"\bha\s+explotado\b|\bexplosion\b|\bblast\b|\bdetonaci[oó]n\b|\bha\s+habido\s+una\s+(?:gran\s+)?explosi[oó]n\b",
     "explosion", "P-1 (Emergency)", 6),
    (r"\bexplosi[oó]n\b",
     "explosion", "P-1 (Emergency)", 4),

    (r"\bderrumb[eo]\b|\bcolapso\b|\bedificio\s+ca[ií]do\b|\bcollapse\b|\bse\s+ha\s+derrumbado\b|\bhundimiento\b",
     "infrastructure_collapse", "P-1 (Emergency)", 6),
    (r"\bse\s+ha\s+hundido\b|\bhundido\b.*?\b(techo|suelo|piso|edificio|muro|pared)\b",
     "infrastructure_collapse", "P-1 (Emergency)", 6),
    (r"\bescombros\b|\batrapado[s]?\b.*?\b(escombro|derrumbe|techo|muro)\b",
     "infrastructure_collapse", "P-1 (Emergency)", 5),

    (r"\bderrame\s+qu[ií]mico\b|\bchemical\s+spill\b|\bproductos?\s+qu[ií]micos?\b.*?\bvertido\b",
     "chemical_spill", "P-2 (Urgent)", 5),

    (r"\bparo\s+card[ií]aco\b|\binfarto\b|\bcardiac\s+arrest\b|\bheart\s+attack\b",
     "cardiac_arrest", "P-1 (Emergency)", 6),
    (r"\bno\s+respira\b|\bnot\s+breathing\b|\bsin\s+pulso\b|\bno\s+pulse\b|\bha\s+dejado\s+de\s+respirar\b",
     "cardiac_arrest", "P-1 (Emergency)", 5),
    (r"\binconsciente\b|\bunconsciou\b|\bno\s+responde\b|\bnot\s+responding\b",
     "cardiac_arrest", "P-1 (Emergency)", 3),

    (r"\bictus\b|\bapoplej[ií]a\b|\bstroke\b",
     "stroke", "P-1 (Emergency)", 6),
    (r"\bderrame\s+cerebral\b|\bpar[aá]lisis\s+facial\b|\bface\s+drooping\b|\bcara\s+ca[ií]da\b",
     "stroke", "P-1 (Emergency)", 6),
    (r"\bboca\s+torcida\b|\bcara\s+torcida\b|\bno\s+puede\s+hablar\b|\bhabla[r]?\s+raro\b|\bno\s+mueve\b.*?\bbraz[oa]\b|\barm\b.*?\bweak\b",
     "stroke", "P-1 (Emergency)", 5),
    (r"\bpalabras\s+sin\s+sentido\b|\bno\s+articula\b|\bbalbuce[ao]\b|\bno\s+puede\s+levantar\b.*?\bbraz[oa]\b|\bbraz[oa]\b.*?\bno\s+(?:puede|lo\s+puede)\s+levantar\b",
     "stroke", "P-1 (Emergency)", 5),

    (r"\bse\s+ha\s+ca[ií]do\b|\bca[ií]da\b|\bfell\b|\bfell\s+down\b|\bfractura\b|\bfracture\b|\bhueso\s+roto\b|\bbroken\s+bone\b",
     "fall_injury", "P-2 (Urgent)", 4),
    (r"\bescalera\b.*?\bca[ií]d\b|\bca[ií]d.*?\bescalera\b|\bbalc[oó]n\b.*?\bca[ií]d\b",
     "fall_injury", "P-1 (Emergency)", 5),
    (r"\bse\s+ha\s+tirad[oa]\b.*?\b(?:ventana|balc[oó]n|tejado|azotea|terraza|piso|planta)\b|\b(?:ventana|balc[oó]n|tejado|azotea)\b.*?\bse\s+ha\s+tirad[oa]\b",
     "fall_injury", "P-1 (Emergency)", 6),
    (r"\b(?:pierna[s]?|brazo[s]?|costilla[s]?|tobillo[s]?|cadera[s]?)\b.*?\brot[ao][s]?\b",
     "fall_injury", "P-1 (Emergency)", 4),

    (r"\bahog[aá]ndose\b|\bahog[aá]ndo\b|\bdrownin\b|\bse\s+ahoga\b|\bse\s+est[aá]\s+ahogando\b",
     "drowning", "P-1 (Emergency)", 6),
    (r"\bpiscina\b.*?\b(ni[ñn]o|ni[ñn]a|beb[eé])\b|\bpool\b.*?\bchild\b|\bplaya\b.*?\bni[ñn]o\b.*?\bagua\b",
     "drowning", "P-1 (Emergency)", 5),
    (r"\bmar\b.*?\bayuda\b|\bswimming\b.*?\bhelp\b",
     "drowning", "P-1 (Emergency)", 4),

    (r"\bsobredosis\b|\boverdose\b|\bpinchado\b.*?\bperdido\b|\bdroga\b.*?\binconsciente\b|\bheroin[a]\b|\bcocaína\b.*?\bmal\b",
     "overdose", "P-1 (Emergency)", 5),
    (r"\b(?:tomado|tomó|tomando)\b.*?\bpastilla[s]?\b|\bpastilla[s]?\b.*?\b(?:no\s+(?:despierta|responde|puedo\s+despertar)|inconsciente|desmayado)\b",
     "overdose", "P-1 (Emergency)", 5),
    (r"\b(?:drogas?|sustancia[s]?|medicament[oa]s?)\b.*?\b(?:no\s+(?:despierta|responde)|respira\s+(?:muy\s+)?despacio|inconsciente)\b",
     "overdose", "P-1 (Emergency)", 4),

    (r"\bagresi[oó]n\b|\bassault\b|\battack\b|\bpelea\b|\bpeli[eé]a\b|\bfight\b",
     "assault", "P-2 (Urgent)", 4),
    (r"\bme\s+est[aá]n?\s+pegando\b|\ble\s+est[aá]n?\s+pegando\b|\bme\s+golpea\b|\bme\s+hit\b|\bpuñetazo\b|\bnavaja\b|\barmado\b",
     "assault", "P-1 (Emergency)", 5),
    (r"\bviolencia\s+dom[eé]stica\b|\bme\s+pega\s+(mi|el|la)\b|\bme\s+est[aá]\s+pegando\b|\bmi\s+(marido|pareja|novio|exnovio|ex|mujer|esposa)\b.*?\b(peg|agred|golpe)\b|\ble\s+pega\s+a\b.*?\bmujer\b|\bpareja\b.*?\bpeg\b|\bdomestic\s+violence\b",
     "domestic_violence", "P-1 (Emergency)", 6),

    (r"\brobo\b|\batraco\b|\brobber\b|\btheft\b|\bstolen\b|\bburglary\b|\bme\s+han\s+robado\b|\bme\s+están\s+robando\b",
     "robbery", "P-2 (Urgent)", 4),
    (r"\bme\s+están\s+atracando\b|\bpistola\b|\barticulado\b|\barma\b.*?\brobo\b",
     "robbery", "P-1 (Emergency)", 5),

    (r"\bdesaparecid[oa]\b|\bmissing\b|\bno\s+aparece\b|\bno\s+lo\s+encuentro\b|\bno\s+le\s+encuentro\b|\bcan.t\s+find\b",
     "missing_person", "P-3 (Non-Urgent)", 4),
    (r"\bni[ñn]o\b.*?\bdesaparecid\b|\bni[ñn]a\b.*?\bdesaparecid\b|\bchild\b.*?\bmissing\b",
     "missing_person", "P-2 (Urgent)", 5),

    (r"\bsuicid\b|\bse\s+va\s+a\s+tirar\b|\bjumping\b|\bself.harm\b|\bautolesi[oó]n\b",
     "mental_health_crisis", "P-1 (Emergency)", 6),
    (r"\bquiere?\s+tirar[s]?e\b|\bva\s+a\s+tirar[s]?e\b|\bintenta\s+tirar[s]?e\b|\bse\s+(?:quiere|va\s+a)\s+lanzar\b",
     "mental_health_crisis", "P-1 (Emergency)", 6),
    (r"\b(?:azotea|terraza|tejado|balc[oó]n|puente|ventana)\b.*?\b(?:tirar[s]?e|saltar|lanzar[s]?e|caer[s]?e)\b|\bameaza\s+con\s+tirar[s]?e\b",
     "mental_health_crisis", "P-1 (Emergency)", 6),
    (r"\b(?:lanzar[s]?e|tirar[s]?e|saltar)\b.*?\bvac[ií]o\b|\bvac[ií]o\b.*?\b(?:lanzar|tirar|saltar)\b",
     "mental_health_crisis", "P-1 (Emergency)", 6),
    (r"\bcrisis\s+(nerviosa|ansiedad|p[aá]nico)\b|\bpanic\s+attack\b|\bataque\s+de\s+p[aá]nico\b|\bmuy\s+alterado\b",
     "mental_health_crisis", "P-2 (Urgent)", 4),
    (r"\bbrote\s+psic[oó]tico\b|\bpsych[o]tic\b|\bdelira\b|\balucinaciones\b",
     "mental_health_crisis", "P-1 (Emergency)", 5),

    (r"\batragantad[oa]\b|\batragantándose\b|\bse\s+ha\s+atragantado\b|\bchoking\b|\bairway\b",
     "choking", "P-1 (Emergency)", 6),
    (r"\bno\s+puede\s+respirar\b.*?\b(?:ni[ñn][oa]|beb[eé]|obst[aá]culo|juguete|comida|hueso)\b|\bobstrucci[oó]n\b.*?\bv[ií]a[s]?\s+a[eé]rea[s]?\b",
     "choking", "P-1 (Emergency)", 5),
    (r"\bponi[eé]ndo[s]?e\s+(?:azul|morado[a]?)\b|\best[aá]\s+(?:azul|morado[a]?)\b",
     "choking", "P-1 (Emergency)", 4),

    (r"\binundaci[oó]n\b|\bagua\b.*?\bcalle\b|\bflood\b|\bsubida\s+del\s+agua\b|\bagua\s+entrando\b",
     "flooding", "P-2 (Urgent)", 4),

    (r"\bveh[ií]culo\s+(averiado|parado|abandonado|bloqueando)\b|\bobst[aá]culo\b.*?\bcalzada\b|\bcarretera\s+cortada\b|\bcorte\s+de\s+tr[aá]fico\b",
     "traffic_disruption", "P-3 (Non-Urgent)", 4),
    (r"\bcontramarcha\b|\bsentido\s+contrario\b|\bcontram[aá]no\b|\bconductor\s+en\s+sentido\s+contrario\b",
     "traffic_disruption", "P-2 (Urgent)", 5),
    (r"\bveh[ií]culo\b.*?\bmed[ia]ana\b|\bcoche\b.*?\bcuneta\b|\bcami[oó]n\b.*?\bvuelco\b.*?\bsin\b.*?\bheridos?\b",
     "traffic_disruption", "P-2 (Urgent)", 4),

    (r"\bcorte\s+de\s+luz\b|\bsin\s+luz\b|\bapag[oó]n\b|\bfallo\s+el[eé]ctrico\b|\bavería\s+el[eé]ctrica\b",
     "utility_failure", "P-3 (Non-Urgent)", 4),
    (r"\bcorte\s+de\s+agua\b|\bsin\s+agua\b|\brotura\s+de\s+tubería\b|\bperdida\s+de\s+agua\b|\bfuga\s+de\s+agua\b",
     "utility_failure", "P-3 (Non-Urgent)", 4),
    (r"\btransformador\b.*?\b(humo|fuego|chispas)\b|\bcable\s+(ca[ií]do|roto|tendido)\b|\btendido\s+el[eé]ctrico\b.*?\bca[ií]do\b",
     "utility_failure", "P-2 (Urgent)", 5),
]

_COMPILED_RULES = [(re.compile(pat, re.IGNORECASE), itype, sev, w) for pat, itype, sev, w in _RULES]

_SEVERITY_UP = re.compile(
    r"\binconsciente\b|\bunconsciou\b|\bno\s+respira\b|\bnot\s+breathing\b"
    r"|\bsangre\b.*?\bmucho\b|\bmucha\s+sangre\b|\blot\s+of\s+blood\b|\bmuerto\b|\bdead\b"
    r"|\batrapado\b|\btrapped\b|\bni[ñn]o\b|\bni[ñn]a\b|\bchild\b|\bembara\b|\bpregnant\b"
    r"|\bno\s+responde\b|\bnot\s+responding\b|\bvarios\s+heridos\b|\bmuchos\s+heridos\b"
    r"|\bse\s+muere\b|\bagonizando\b|\bdying\b",
    re.IGNORECASE,
)

_SEVERITY_DOWN = re.compile(
    r"\bleve\b|\bminor\b|\bpeque[ñn]o\b|\bsmall\b|\bno\s+herido\b|\bunhurt\b"
    r"|\bno\s+injuries\b|\bsin\s+heridos\b|\bbien\b.*?\bestado\b|\bestoy\s+bien\b"
    r"|\bno\s+es\s+grave\b|\bnot\s+serious\b",
    re.IGNORECASE,
)

_WORD_TO_NUM = {"un": 1, "una": 1, "uno": 1, "otro": 1, "otra": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
                "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

def _extract_victim_count(transcript: str) -> int:
    """Counts victim mentions using explicit numeric patterns first, then singular adjective patterns for non-overlapping hits."""
    total_victims = 0
    p1_spans = [] 

    pattern1 = re.compile(
        r"\b(\d+|una?|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|one|two|three|four|five)"
        r"\s*(?:de\s+las?\s+)?"
        r"(?:personas?|herid[oa]s?|v[ií]ctimas?|muertos?|fallecid[oa]s?|lesionad[oa]s?|injured|dead|casualties)",
        re.IGNORECASE,
    )
    for m in pattern1.finditer(transcript):
        raw = m.group(1).lower()
        val = _WORD_TO_NUM.get(raw, int(raw) if raw.isdigit() else 1)
        total_victims += val
        p1_spans.append((m.start(), m.end()))

    pattern2 = re.compile(
        r"\b(un|una|otro|otra|one)\b\s+(?:\w+\s+){0,2}"   
        r"\b(herido|herida|muerto|muerta|fallecido|fallecida|lesionado|lesionada|"
        r"inconsciente|atrapado|atrapada|injured|dead|unconscious|trapped)\b",
        re.IGNORECASE,
    )
    for m in pattern2.finditer(transcript):
        overlaps = any(m.start() < end and m.end() > start for start, end in p1_spans)
        if not overlaps:
            total_victims += 1

    if total_victims == 0:
        if re.search(r"\b([eé]l|ella|mi\s+\w+|he|she|my\s+\w+|alguien|someone)\b", transcript, re.IGNORECASE):
            if re.search(
                r"\b(no\s+respira|not\s+breathing|sangr|blood|duele|pain|"
                r"ca[ií]d|fell|atraco|herid|unconscious|inconsciente|hurt|dead|muerto|fallecido)\b",
                transcript, re.IGNORECASE,
            ):
                total_victims = 1

    return total_victims

def _bump_severity(current: str, direction: int) -> str:
    """Shift priority level by direction steps (+1 = more urgent, -1 = less urgent)."""
    idx = list(PRIORITY_LEVELS).index(current)
    new_idx = max(0, min(len(PRIORITY_LEVELS) - 1, idx - direction))
    return PRIORITY_LEVELS[new_idx]

@dataclass
class Classification:
    incident_type: str
    typology: str
    priority: str
    victims: int
    confidence: str
    matched_keywords: list
    reasoning: Optional[str] = None

def _classify_by_rules(transcript: str) -> Optional[Classification]:
    """Votes across all matching rules by weight; the type with the highest total score wins."""
    scores: dict[str, list] = {}
    priority_order = {s: i for i, s in enumerate(PRIORITY_LEVELS)}
    matched_types: list[str] = []

    for pattern, itype, sev, weight in _COMPILED_RULES:
        if pattern.search(transcript):
            pi = priority_order[sev]
            if itype not in scores:
                scores[itype] = [0, pi]  
            scores[itype][0] += weight
            scores[itype][1] = min(scores[itype][1], pi)  
            if itype not in matched_types:
                matched_types.append(itype)

    if not scores:
        return None

    best_type = max(scores, key=lambda t: (scores[t][0], -scores[t][1]))
    best_priority_idx = scores[best_type][1]
    priority = PRIORITY_LEVELS[best_priority_idx]

    if _SEVERITY_UP.search(transcript):
        priority = _bump_severity(priority, +1)
    if _SEVERITY_DOWN.search(transcript):
        priority = _bump_severity(priority, -1)

    victims = _extract_victim_count(transcript)

    if best_type == "cardiac_arrest" and victims > 1:
        victims = 1

    confidence = "high" if len(matched_types) == 1 else "medium"
    typology = INCIDENT_MAPPING.get(best_type, "Protection/Civil Services")

    return Classification(
        incident_type=best_type,
        typology=typology,
        priority=priority,
        victims=victims,
        confidence=confidence,
        matched_keywords=matched_types,
    )

@tool
def classify_incident(transcript: str) -> str:
    """Classifies an emergency call transcript into a structured incident type, typology and priority level.

    Args:
        transcript: Raw text of the transcribed emergency call.

    Returns:
        A JSON string with keys:
          - incident_type (str): inferred specific incident type.
          - typology (str): one of "Sanitary", "Police", "Extinction and Rescue", "Protection/Civil Services".
          - priority (str): one of "P-1 (Emergency)", "P-2 (Urgent)", "P-3 (Non-Urgent)", "P-4 (Information)".
          - victims (int): estimated number of people directly affected.
          - confidence (str): 'high' (single strong match), 'medium' (multiple rules agreed),
            or 'low' (no keyword match — defaulted to 'other').
          - matched_keywords (list[str]): incident types that triggered keyword rules.
          - reasoning (str | null): explanation when fallback is used.
    """
    if not transcript or not isinstance(transcript, str):
        transcript = ""

    classification = _classify_by_rules(transcript)

    if classification is None:
        victims = _extract_victim_count(transcript)
        classification = Classification(
            incident_type="other",
            typology="Protection/Civil Services",
            priority="P-3 (Non-Urgent)",
            victims=victims,
            confidence="low",
            matched_keywords=[],
            reasoning=(
                "No keyword patterns matched. Defaulting to 'other' in 'P-3 (Non-Urgent)'. "
                "The Procedure Agent should re-evaluate this transcript directly."
            ),
        )

    return json.dumps(asdict(classification), ensure_ascii=False)

if __name__ == "__main__":
    tests = [
        "Ha habido un accidente de tráfico, hay un conductor inconsciente y uno con heridas leves.",
        "My father is not breathing and has no pulse, please send help immediately.",
        "There's a fire in the building, I can see flames coming from the third floor.",
        "I smell gas in the kitchen, strong smell, I'm scared.",
        "Someone is acting strangely on the bridge, I think they might jump.",
        "A man fell down the stairs, he seems okay but is in pain.",
        "Veo un coche sospechoso.",
        "Hay una inundación en la calle principal, el agua llega hasta las rodillas.",
    ]
    print("classify_incident - test results\n" + "=" * 50)
    for t in tests:
        result_json = classify_incident(t)
        result = json.loads(result_json)
        print(f"\nTranscript : {t[:65]}…")
        print(f"Type       : {result['incident_type']}")
        print(f"Typology   : {result['typology']}")
        print(f"Priority   : {result['priority']}")
        print(f"Victims    : {result['victims']}")
        print(f"Confidence : {result['confidence']}")
