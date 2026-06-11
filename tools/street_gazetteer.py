"""
Fuzzy street-name matching against the official Sevilla street gazetteer.

The gazetteer (data/sevilla_streets.json) is built from OpenStreetMap with
tools/build_street_gazetteer.py. extract_location uses this module as a
fallback when exact Nominatim geocoding fails — typically because Whisper
misheard a street name ("Antonio Filipos Rojas" → "Calle Antonio Filpo Rojas")
or the caller used a variant ("avenida del Greco" → "Avenida El Greco").
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

try:
    from rapidfuzz import fuzz, process
    _HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    _HAS_RAPIDFUZZ = False

_GAZETTEER_PATH = Path(__file__).resolve().parents[1] / "data" / "sevilla_streets.json"

_TYPE_PREFIX_RE = re.compile(
    r"^(?:calle|avenida|avda|av|plaza|pza|paseo|pasaje|ronda|carretera|camino|"
    r"callejon|glorieta|rambla|travesia|cuesta|puente|puerta|parque|poligono|"
    r"jardines|jardin|bulevar|boulevard|alameda|autovia|autopista|rotonda|via)"
    r"\.?\s+"
)
_ARTICLE_PREFIX_RE = re.compile(r"^(?:de\s+la|de\s+los|de\s+las|del|de|la|el|los|las)\s+")
_TRAILING_NUMBER_RE = re.compile(r"[\s,]+(\d+[a-z]?)\s*$")

_SCAN_STOPWORDS = frozenset({
    "accidente", "agredido", "alguien", "ambulancia", "atacante", "atragantado",
    "ayuda", "bomberos", "buenas", "buenos", "carretera", "cuidado", "derrumbe",
    "desmayado", "edificio", "emergencia", "emergencias", "entonces", "escalera",
    "esperanza", "estacion", "estamos", "explosión", "explosion", "gracias",
    "heridos", "hospital", "incendio", "kilometro", "llamada", "llamaba",
    "manden", "necesito", "nervioso", "numero", "policia", "portal", "rapido",
    "sangrando", "senora", "sevilla", "telefono", "urgente", "urgencia",
    "vecino", "vecina", "vengan", "ventana", "victima", "vivimos", "todavia",
})

_index_cache: Optional[list[tuple[str, str]]] = None 
_centers_cache: dict = {}


def _normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _core_variants(name: str) -> set[str]:
    """Normalized variants of a street name: full, without street type, and
    without the leading article ('Avenida de la Constitución' → 'constitucion')."""
    norm = _normalize(name)
    stripped = _TYPE_PREFIX_RE.sub("", norm)
    variants = {norm, stripped, _ARTICLE_PREFIX_RE.sub("", stripped)}
    return {v for v in variants if len(v) >= 4}


def _load_index() -> list[tuple[str, str]]:
    """Lazily load the gazetteer as a list of (normalized core, official name).
    The gazetteer file maps name → [lat, lon] (street center); a plain name
    list (the legacy format, without coordinates) is also accepted.
    """
    global _index_cache
    if _index_cache is None:
        try:
            data = json.loads(_GAZETTEER_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
        if isinstance(data, dict):
            names = list(data.keys())
            _centers_cache.update(
                {name: (lat, lon) for name, (lat, lon) in data.items()}
            )
        else:
            names = data
        _index_cache = [
            (core, official)
            for official in names
            for core in _core_variants(official)
        ]
    return _index_cache


def street_center(official_name: str) -> Optional[tuple[float, float]]:
    """Return the OSM street-center (lat, lon) for an official gazetteer name,
    or None when the gazetteer has no coordinates for it."""
    _load_index()
    return _centers_cache.get(official_name)


def _best_match(query: str, score_cutoff: float) -> Optional[tuple[str, float]]:
    """Return (official_name, score) for the gazetteer entry closest to *query*."""
    index = _load_index()
    if not index or not query:
        return None
    cores = [c for c, _ in index]
    if _HAS_RAPIDFUZZ:
        hit = process.extractOne(
            query, cores, scorer=fuzz.token_sort_ratio, score_cutoff=score_cutoff
        )
        if hit:
            _, score, idx = hit
            return index[idx][1], float(score)
        return None
    matches = difflib.get_close_matches(query, cores, n=1, cutoff=score_cutoff / 100)
    if matches:
        score = difflib.SequenceMatcher(None, query, matches[0]).ratio() * 100
        official = next(off for c, off in index if c == matches[0])
        return official, score
    return None


def _subset_match(query: str) -> Optional[tuple[str, float]]:
    """Accept a gazetteer core whose tokens are all contained in *query*
    (callers often add or drop words: 'avenida del bajo Guadalquivir' vs the
    official 'Calle Guadalquivir'). Requires a distinctive core (≥ 8 chars)."""
    query_tokens = set(query.split())
    if not query_tokens:
        return None
    best: Optional[tuple[str, float]] = None
    for core, official in _load_index():
        if len(core) < 8:
            continue
        core_tokens = set(core.split())
        if core_tokens <= query_tokens:
            score = 100.0 * len(core) / max(len(query), 1)
            if best is None or score > best[1]:
                best = (official, score)
    return best


def match_street(candidate: str, score_cutoff: float = 86.0) -> Optional[tuple[str, str, float]]:
    """Fuzzy-match an address candidate against the gazetteer.
    Args:
        candidate: Address string, ideally with the house number already in
                   digit form ("calle Antonio Filipos Rojas 6").
        score_cutoff: Minimum token_sort_ratio score (0-100) to accept.

    Returns:
        (official_street_name, house_number, score) or None. house_number is
        "" when the candidate has none.
    """
    norm = _normalize(candidate)
    number = ""
    num_match = _TRAILING_NUMBER_RE.search(norm)
    if num_match:
        number = num_match.group(1)
        norm = norm[: num_match.start()].strip()

    stripped = _TYPE_PREFIX_RE.sub("", norm)
    variants = {norm, stripped, _ARTICLE_PREFIX_RE.sub("", stripped)}
    variants = {v for v in variants if len(v) >= 4}

    best: Optional[tuple[str, str, float]] = None
    for variant in variants:
        hit = _best_match(variant, score_cutoff)
        if hit and (best is None or hit[1] > best[2]):
            best = (hit[0], number, hit[1])
    if best:
        return best

    for variant in sorted(variants, key=len):
        hit = _subset_match(variant)
        if hit:
            return hit[0], number, hit[1]
    return None


def scan_transcript(transcript: str, score_cutoff: float = 90.0, max_results: int = 3) -> list[str]:
    """Find probable street names in a transcript that yielded no address
    candidates. Matches capitalized word n-grams against the
    gazetteer and returns geocodable strings, best score first.
    """
    if not _load_index() or not transcript:
        return []

    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+|\d+[A-Za-z]?", transcript)
    has_capitals = any(tok[0].isupper() and len(tok) >= 4 for tok in tokens)
    if not has_capitals:
        score_cutoff = max(score_cutoff, 92.0)

    ngram_spans: list[tuple[str, int]] = []
    for n in range(1, 5):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if any(tok[0].isdigit() for tok in gram):
                continue
            if has_capitals:
                if not any(tok[0].isupper() and len(tok) >= 4 for tok in gram):
                    continue
            else:
                if not any(len(tok) >= 6 for tok in gram):
                    continue
            norm = _normalize(" ".join(gram))
            if len(norm) < 6:
                continue
            if n == 1 and (len(norm) < 6 or norm in _SCAN_STOPWORDS):
                continue
            if all(word in _SCAN_STOPWORDS for word in norm.split()):
                continue
            ngram_spans.append((norm, i + n))

    scored: list[tuple[float, str, int]] = []
    for norm, end in ngram_spans:
        hit = _best_match(norm, score_cutoff)
        if hit:
            scored.append((hit[1], hit[0], end))

    results: list[str] = []
    seen: set[str] = set()
    for score, official, end in sorted(scored, key=lambda t: (-t[0], -len(t[1]))):
        if official in seen:
            continue
        seen.add(official)
        number = ""
        for j in (end, end + 1):
            if j < len(tokens) and re.fullmatch(r"\d+[A-Za-z]?", tokens[j]):
                number = tokens[j]
                break
        results.append(f"{official} {number}".strip())
        if len(results) >= max_results:
            break
    return results
