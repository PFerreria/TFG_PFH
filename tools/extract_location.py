from __future__ import annotations

import json
import re
import time
from typing import Optional

from smolagents import tool

from tools.get_route import SEVILLA_BOUNDS, _in_sevilla_bounds
from tools.street_gazetteer import match_street, scan_transcript, street_center

_NLP_MODELS: dict = {}

def _get_nlp(lang: str = "es"):
    """Lazily load and cache the spaCy model for the given language."""
    if lang not in _NLP_MODELS:
        import spacy

        model_name = "es_core_news_lg" if lang == "es" else "en_core_web_lg"
        try:
            _NLP_MODELS[lang] = spacy.load(model_name)
        except OSError:
            small = model_name.replace("_lg", "_sm")
            try:
                _NLP_MODELS[lang] = spacy.load(small)
            except OSError as e:
                raise RuntimeError(
                    f"spaCy model not found. Run: python -m spacy download {model_name}"
                ) from e
    return _NLP_MODELS[lang]

_STREET_TYPES = (
    r"calle|avenida|avda|av\.?|plaza|pza\.?|paseo|pasaje|ronda|carretera|"
    r"camino|callejón|glorieta|rambla|travesía|cuesta|carrer|avinguda|plaça|"
    r"puente|puerta|parque|polígono|jardines?|bulevar|boulevard|alameda|"
    r"autovía|autovia|autopista|carretera|rotonda"
)

_NARRATION = (
    r"en|hay|está|estoy|donde|que|y|pero|con|sin|por|para|se|un|una|"
    r"nervioso|asustado|ayuda|rápido|urgente|fuego|humo|herido|salien|"
    r"ventana|coche|accidente|incendio|llamar|tercer|piso|planta|"
    r"escalera|puerta|casa|vivo|hogar|madre|padre|padre|hijo|hija|"
    r"viviendo|vive|vivimos|edificio|portal|mano|izquierda|derecha|"
    r"frente|cerca|lejos|al lado|junto|encima|debajo"
)

_NAME_WORD = (
    rf"(?:(?:de\s+(?:la|el|los|las)|del|de)\s+)?"
    rf"(?!(?:{_NARRATION})\b)"
    rf"[A-Za-zÁáÉéÍíÓóÚúÜüÑñ][A-Za-zÁáÉéÍíÓóÚúÜüÑñ]*"
)

_ES_NUM_WORDS = (
    r"uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
    r"trece|catorce|quince|diecis[eé]is|diecisiete|dieciocho|diecinueve|"
    r"veinte|veintiuno|veintid[oó]s|veintitr[eé]s|veinticuatro|veinticinco|"
    r"veintis[eé]is|veintisiete|veintiocho|veintinueve|"
    r"treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien(?:to)?"
)
_NUM_SUFFIX = (
    rf"(?:\s*,?\s*"
    rf"(?:\d+[A-Za-z]?|n[úu]?m\.?(?:ero)?\s+(?:\d+|(?:{_ES_NUM_WORDS})(?:\s+y\s+(?:{_ES_NUM_WORDS}))?)))"
    rf"?"
)
_CORE_ADDR = (
    rf"(?:{_STREET_TYPES})\s+"
    rf"(?:{_NAME_WORD}\s+){{0,2}}{_NAME_WORD}"
    rf"{_NUM_SUFFIX}"
)

_CORNER_RE = re.compile(
    rf"(?P<addr1>{_CORE_ADDR})\s+esquina\s+(?:con\s+)(?P<addr2>{_CORE_ADDR})",
    re.IGNORECASE,
)

_NUM_STREET_RE = re.compile(
    rf"n[úu]m(?:ero)?\.?\s*(\d+)\s+de\s+(?:la\s+|el\s+)?({_CORE_ADDR})",
    re.IGNORECASE,
)

_NUM_FIRST_RE = re.compile(
    rf"(\d+)\s+({_CORE_ADDR})",
    re.IGNORECASE,
)

_ADDR_RE = re.compile(_CORE_ADDR, re.IGNORECASE)

_NUMERO_WORDS_ADDR_RE = re.compile(
    rf"(?:{_STREET_TYPES})\s+"
    rf"(?:(?!n[úu]?m)(?:{_NAME_WORD})\s+){{0,2}}(?!n[úu]?m){_NAME_WORD}"
    rf"\s+n[úu]?m\.?(?:ero)?\s+"
    rf"(?:{_ES_NUM_WORDS})(?:\s+y\s+(?:{_ES_NUM_WORDS}))?",
    re.IGNORECASE,
)

_LEADING_FILLER = re.compile(
    r"^\s*(?:en\s+|el\s+|la\s+|los\s+|las\s+|del\s+|de\s+|un\s+|una\s+|"
    r"hay\s+|se\s+)+",
    re.IGNORECASE,
)

_HOUSE_NUMBER_RE = re.compile(r'\b\d+[A-Za-z]?\s*$')


def _has_house_number(address: str) -> bool:
    """Return True if *address* contains a house number after normalization."""
    return bool(_HOUSE_NUMBER_RE.search(_normalize_address(address)))

_TRAILING_CONTEXT = re.compile(
    r"\s+(?:hay\s|está|estoy|donde|porque|que\s|y\s|pero\s|en\s+el\s|"
    r"en\s+mi?\s|en\s+sevilla|en\s+madrid|en\s+granada|tercer\s+piso|"
    r"planta\b|piso\b|escalera|puerta|apartamento|casa\b|vivo|"
    r"nervioso|asustado|ayuda|rápido|urgente|fuego|humo|herido|salien|"
    r"ventana|coche|accidente|incendio|llamar|llamada|teléfono).*$",
    re.IGNORECASE,
)

_STREET_TYPE_WORDS = {
    "calle", "avenida", "avda", "av", "plaza", "pza", "paseo", "pasaje",
    "ronda", "carretera", "camino", "callejón", "glorieta", "rambla",
    "travesía", "cuesta", "carrer", "avinguda", "plaça",
    "puente", "puerta", "parque", "polígono", "jardines", "jardín",
    "bulevar", "boulevard", "alameda", "autovía", "autovia", "autopista",
    "rotonda",
}

_POST_TYPE_FILLER = re.compile(rf"\b((?:{_STREET_TYPES}))\s+es\s+", re.IGNORECASE)


def _clean(raw: str) -> str:
    """Remove leading filler and trailing narrative context from *raw*."""
    text = raw.strip()
    text = _TRAILING_CONTEXT.sub("", text)
    text = _POST_TYPE_FILLER.sub(r"\1 ", text)
    stripped = _LEADING_FILLER.sub("", text)
    first = stripped.split()[0].lower().rstrip(".") if stripped else ""
    if first in _STREET_TYPE_WORDS or first.isdigit():
        text = stripped
    text = re.sub(r"\s+", " ", text).strip(" .,;:")
    return text

_HIGHWAY_RE = re.compile(
    r"\b([ANM]-?\d{1,3})\b.*?\bkil[oó]metro\b\s*(\d+)",
    re.IGNORECASE,
)
_HIGHWAY_WORDED_RE = re.compile(
    r"\b(?:autov[ií]a|autopista)\s+(\d{1,3})\b.*?\bkil[oó]metro\b\s*(\d+)",
    re.IGNORECASE,
)
_LANDMARK_KIND = (
    r"puente|t[uú]nel|parque|estadio|hospital|iglesia|catedral|mercado|"
    r"estaci[oó]n(?:\s+de\s+(?:tren(?:es)?|autob[uú]s(?:es)?|metro|ferrocarril))?|"
    r"aeropuerto|pol[ií]gono\s+industrial|pol[ií]gono"
)
_BRIDGE_LANDMARK_RE = re.compile(
    rf"\b(?P<kind>{_LANDMARK_KIND})\b\s+(?:de\s+|del\s+|en\s+)?(?:la\s+|el\s+|los\s+|las\s+)?"
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}})",
    re.IGNORECASE,
)


def _extract_candidates(transcript: str) -> list[str]:
    """Return cleaned location candidate strings."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        cleaned = _clean(text)
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            candidates.append(cleaned)

    for m in _CORNER_RE.finditer(transcript):
        _add(m.group("addr1"))
        _add(m.group("addr2"))

    for m in _HIGHWAY_RE.finditer(transcript):
        _add(f"autovía {m.group(1)} kilómetro {m.group(2)}, Sevilla")
    for m in _HIGHWAY_WORDED_RE.finditer(transcript):
        _add(f"autovía A-{m.group(1)} kilómetro {m.group(2)}, Sevilla")

    for m in _BRIDGE_LANDMARK_RE.finditer(transcript):
        _add(m.group(0))
        kind_base = m.group("kind").split()[0].lower()
        if kind_base in ("estación", "estacion", "aeropuerto"):
            _add(m.group("name"))
        else:
            _add(f"{kind_base} {m.group('name')}")

    for m in _NUM_STREET_RE.finditer(transcript):
        number, street = m.group(1), m.group(2)
        _add(f"{street} {number}")

    for m in _NUM_FIRST_RE.finditer(transcript):
        _add(m.group(0))

    for m in _NUMERO_WORDS_ADDR_RE.finditer(transcript):
        _add(_normalize_address(m.group(0)))

    for m in _ADDR_RE.finditer(transcript):
        _add(m.group(0))

    lang = "es" if any(
        w in transcript.lower()
        for w in ["calle", "avenida", "plaza", "esquina", "hay"]
    ) else "en"
    try:
        nlp = _get_nlp(lang)
        doc = nlp(transcript)
        for ent in doc.ents:
            if ent.label_ in {"LOC", "GPE", "FAC"}:
                _add(ent.text)
    except Exception:
        pass

    return candidates

def _get_geolocator():
    """Return the module-level Nominatim singleton, creating it on first call."""
    global _geolocator_instance
    if _geolocator_instance is None:
        from geopy.geocoders import Nominatim
        _geolocator_instance = Nominatim(user_agent="imers_emergency_dispatch/1.0")
    return _geolocator_instance

_geolocator_instance = None

_NUMERO_RE = re.compile(r"\s+n[úu]?m\.?(?:ero)?\s+(\d+)", re.IGNORECASE)

_ES_UNITS = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintiuna": 21, "veintidós": 22, "veintidos": 22,
    "veintitrés": 23, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiséis": 26, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100, "ciento": 100,
}

_NUMERO_WORDS_RE = re.compile(
    r"\bn[úu]?m\.?(?:ero)?\s+"                    
    r"((?:[a-záéíóúüñ]+\s+)*[a-záéíóúüñ]+)",  
    re.IGNORECASE,
)


def _words_to_int(words_str: str) -> Optional[int]:
    """Convert a Spanish number word sequence to an integer (e.g. 'treinta y dos' → 32)."""
    tokens = words_str.lower().split()
    total = 0
    current = 0
    for tok in tokens:
        if tok == "y":
            continue
        val = _ES_UNITS.get(tok)
        if val is None:
            return None   
        if val >= 100:
            current = current * val if current else val
        elif val >= 10:
            current += val
        else:
            current += val
    total += current
    return total if total > 0 else None


def _replace_numero_words(m: re.Match) -> str:
    """Regex substitution: replace 'número <words>' with the digit string if parseable."""
    words = m.group(1)
    if re.fullmatch(r"\d+[A-Za-z]?", words.strip()):
        return " " + words.strip()
    n = _words_to_int(words)
    return f" {n}" if n is not None else " " + words.strip()


def _normalize_address(address: str) -> str:
    """Normalise address for Nominatim: convert 'número <words>' to digits."""
    out = _NUMERO_RE.sub(r" \1", address)
    out = _NUMERO_WORDS_RE.sub(_replace_numero_words, out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()

_geocode_success_cache: dict = {}
_GEOCODE_CACHE_MAX = 256


def _geocode_cached(address: str, city_hint: str) -> Optional[tuple]:
    """
    Inner geocode with success-only caching. Returns
    (display_address, lat, lon, raw_query, confidence) or None.
    Caching avoids repeated Nominatim calls for the same address within a session,
    eliminating the redundant 1 s sleep per repeated lookup. Transient Nominatim
    errors are retried once per query and never cached.
    """
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError

    cache_key = (address.casefold(), city_hint.casefold())
    if cache_key in _geocode_success_cache:
        return _geocode_success_cache[cache_key]

    geolocator = _get_geolocator()
    queries = [f"{address}, {city_hint}", address]
    viewbox = (
        (SEVILLA_BOUNDS["lat_min"], SEVILLA_BOUNDS["lon_min"]),
        (SEVILLA_BOUNDS["lat_max"], SEVILLA_BOUNDS["lon_max"]),
    )

    for query in queries:
        locations = None
        for _attempt in range(2):
            try:
                time.sleep(1)    
                locations = geolocator.geocode(
                    query, timeout=10, language="es",
                    viewbox=viewbox, bounded=True,
                    addressdetails=True, exactly_one=False, limit=5,
                )
                break
            except (GeocoderTimedOut, GeocoderServiceError):
                continue
        for location in locations or []:
            lat = round(location.latitude, 6)
            lon = round(location.longitude, 6)
            if not _in_sevilla_bounds(lat, lon):
                continue
            raw_addr = location.raw.get("address", {})
            municipality = (
                raw_addr.get("city") or
                raw_addr.get("town") or
                raw_addr.get("village") or
                raw_addr.get("county") or
                raw_addr.get("state_district") or ""
            )
            if municipality and municipality.lower() not in (
                "sevilla", "seville", "sevilla (ciudad)", "provincia de sevilla"
            ):
                continue
            confidence = "high" if city_hint in query else "medium"
            result = (location.address, lat, lon, query, confidence)
            if len(_geocode_success_cache) >= _GEOCODE_CACHE_MAX:
                _geocode_success_cache.clear()
            _geocode_success_cache[cache_key] = result
            return result
    return None


def _geocode(address: str, city_hint: str = "Sevilla, España") -> Optional[dict]:
    """Geocode an address string via the success-cached inner function."""
    result = _geocode_cached(_normalize_address(address), city_hint)
    if result is None:
        return None
    display_addr, lat, lon, raw_query, confidence = result
    return {
        "address":    display_addr,
        "latitude":   lat,
        "longitude":  lon,
        "raw_query":  raw_query,
        "confidence": confidence,
        "is_midpoint": not _has_house_number(address),
    }


@tool
def extract_location(transcript: str, city_hint: str = "Sevilla, España") -> str:
    """Extracts and geocodes the incident location from an emergency call transcript.
    Uses spaCy named-entity recognition to identify location mentions, then validates
    them against OpenStreetMap via Nominatim to obtain precise coordinates.

    Args:
        transcript: Raw text of the transcribed emergency call.
        city_hint: City/region used to disambiguate partial addresses.
                   Defaults to 'Sevilla, España'. Change to match your deployment area.

    Returns:
        A JSON string with keys:
          - found (bool): whether a location was successfully resolved
          - address (str): full resolved address or best candidate if unresolved
          - latitude (float | null): WGS84 latitude
          - longitude (float | null): WGS84 longitude
          - confidence (str): 'high', 'medium', or 'low'
          - candidates (list[str]): all location strings detected in the transcript
          - is_midpoint (bool): True when the address has no house number — coordinates
            point to the street midpoint (Nominatim centroid). ETA should be treated as
            approximate; operator should confirm the exact number.
          - fuzzy_matched (bool): True when the street name could not be geocoded
            verbatim and was corrected via fuzzy matching against the official
            Sevilla street gazetteer — operator should confirm with the caller.
          - error (str | null): error message if resolution failed
        Example (success, no house number):
          '{"found": true, "address": "Calle Sierpes, Sevilla",
            "latitude": 37.392, "longitude": -5.993,
            "confidence": "high", "candidates": ["Calle Sierpes"], "is_midpoint": true, "error": null}'
        Example (success, with house number):
          '{"found": true, "address": "Calle Sierpes, 14, Sevilla",
            "latitude": 37.392, "longitude": -5.993,
            "confidence": "high", "candidates": ["Calle Sierpes 14"], "is_midpoint": false, "error": null}'
        Example (failure):
          '{"found": false, "address": null, "latitude": null, "longitude": null,
            "confidence": "low", "candidates": [], "is_midpoint": false, "error": "No location found in transcript"}'
    """
    result = {
        "found": False,
        "address": None,
        "latitude": None,
        "longitude": None,
        "confidence": "low",
        "candidates": [],
        "is_midpoint": False,
        "fuzzy_matched": False,
        "error": None,
    }

    if not transcript or not isinstance(transcript, str):
        transcript = ""

    candidates = _extract_candidates(transcript)
    result["candidates"] = candidates

    for candidate in candidates:
        geo = _geocode(candidate, city_hint=city_hint)
        if geo:
            result.update({
                "found": True,
                "address": geo["address"],
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "confidence": geo["confidence"],
                "is_midpoint": geo.get("is_midpoint", False),
                "error": None,
            })
            return json.dumps(result, ensure_ascii=False)

    fuzzy_queries: list[str] = []
    for candidate in candidates:
        hit = match_street(_normalize_address(candidate))
        if hit:
            official, number, _score = hit
            fuzzy_queries.append(f"{official} {number}".strip())
    fuzzy_queries.extend(scan_transcript(transcript))

    tried = {c.lower() for c in candidates}
    for query in fuzzy_queries:
        if query.lower() in tried:
            continue
        tried.add(query.lower())
        geo = _geocode(query, city_hint=city_hint)
        if geo is None:
            official = re.sub(r"\s+\d+[A-Za-z]?\s*$", "", query)
            center = street_center(official)
            if center:
                geo = {
                    "address": f"{official}, Sevilla, España",
                    "latitude": center[0],
                    "longitude": center[1],
                    "is_midpoint": True,
                }
        if geo:
            result["candidates"] = candidates + [query]
            result.update({
                "found": True,
                "address": geo["address"],
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "confidence": "medium",
                "is_midpoint": geo.get("is_midpoint", False),
                "fuzzy_matched": True,
                "error": None,
            })
            return json.dumps(result, ensure_ascii=False)

    if candidates:
        result["address"] = "Dirección no localizada, operador aclare dirección"
        result["error"] = (
            f"Could not geocode any of {len(candidates)} candidate(s) within Sevilla's "
            f"operational area. Operator must clarify the address."
        )
    else:
        result["error"] = "No location candidates found in transcript"
    result["confidence"] = "low"
    return json.dumps(result, ensure_ascii=False)

if __name__ == "__main__":
    test_transcripts = [
        "Ha habido un accidente en la Avenida de la Constitución esquina con Calle Sierpes en Sevilla.",
        "My father collapsed at home, we live at 22 Calle Betis, third floor, Seville.",
        "Hay un incendio en el número 8 de la Plaza Nueva, hay humo saliendo por las ventanas.",
    ]
    for t in test_transcripts:
        print(f"\nTranscript: {t[:60]}…")
        output = extract_location(t)
        parsed = json.loads(output)
        print(json.dumps(parsed, indent=2, ensure_ascii=False))
