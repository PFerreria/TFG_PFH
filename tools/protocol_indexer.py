"""
Protocol Index Query Tool

Lookup priority (three-tier fallback):
  1. Structured JSON cache — O(1), pre-computed offline by running:
       python tools/protocol_indexer.py --ingest
     Returns full structured output: code, title, steps[], escalation, notes.
  2. ChromaDB vector search — semantic, ~100-300ms.
     Used when the cache has no entry for this type+severity combination.
  3. Built-in stub table — always available, requires no files.
     Used during development and as the last resort in production.

"""

from __future__ import annotations

import json
import logging
import os
import re as _re
from pathlib import Path
from typing import Optional

from smolagents import tool

logger = logging.getLogger(__name__)

DOCS_DIR   = Path(os.getenv("PROTOCOL_DOCS_DIR",   "./data/protocol_index"))
CHROMA_DIR = Path(os.getenv("PROTOCOL_CHROMA_DIR", "./data/protocol_index/chroma_store"))
INDEX_DIR  = CHROMA_DIR          
CACHE_PATH = Path(os.getenv("PROTOCOL_CACHE_PATH", "./data/protocol_cache.json"))
SUMMARY_PATH = Path(os.getenv("PROTOCOL_SUMMARY_PATH", "./data/protocol_ingest_summary.json"))

_STUBS: dict[str, dict] = {
    "cardiac_arrest": {
        "code": "PRT-2024-001", "title": "Protocolo PCR — Parada Cardiorrespiratoria",
        "steps": [
            "Confirmar inconsciencia y ausencia de respiración / pulso.",
            "Activar código parada: enviar SVA + SVB.",
            "Iniciar RCP inmediatamente: 30 compresiones + 2 ventilaciones.",
            "Desfibrilación precoz si DEA disponible (< 3 min).",
            "Tiempo objetivo: primera unidad en escena < 8 min.",
            "Notificar hospital receptor con pre-aviso código 90.",
            "Continuar hasta ROSC, relevo o decisión médica.",
        ],
        "escalation": "Si > 3 víctimas simultáneas: activar PEM. Refuerzo 2 SVB adicionales.",
        "notes":      "Menores de 1 año: protocolo pediátrico. Compresiones con 2 dedos, ratio 15:2.",
        "source":     "stub",
    },
    "traffic_accident": {
        "code": "PRT-2024-015", "title": "Protocolo ATT — Accidente de Tráfico con Víctimas",
        "steps": [
            "Asegurar escena: señalización a 100m en ambas direcciones.",
            "Evaluar número de vehículos y víctimas antes de acercarse.",
            "Triaje START: Rojo / Amarillo / Verde / Negro.",
            "Enviar SVA + SVB (trauma) + unidad policial.",
            "Si atrapados: bomberos con equipo de rescate (GREA).",
            "No mover con sospecha de lesión medular.",
            "PMA si > 3 víctimas. Notificar hospital receptor.",
        ],
        "escalation": "Si > 5 víctimas: PEM y coordinador sanitario.",
        "notes":      "En autopistas: perímetro mínimo 200m.",
        "source":     "stub",
    },
    "fire": {
        "code": "PRT-2024-008", "title": "Protocolo INC — Incendio Estructural",
        "steps": [
            "Confirmar dirección exacta y plantas afectadas.",
            "Enviar: 2 bomberos + SVA + unidad policial.",
            "Cortar suministros de gas y electricidad.",
            "Evacuación inmediata. Punto de encuentro a 200m.",
            "Si atrapados: activar GREA.",
            "Perímetro de seguridad 100m. Cortar tráfico.",
        ],
        "escalation": "Si afecta > 3 plantas o explosiones: 2 unidades adicionales.",
        "notes":      "Edificios con MATPEL: protocolo específico. Notificar SEPRONA.",
        "source":     "stub",
    },
    "stroke": {
        "code": "PRT-2024-019", "title": "Protocolo ICTUS — Código Ictus",
        "steps": [
            "Activar código ictus. Confirmar tiempo de inicio de síntomas.",
            "Enviar SVA con médico neurólogo de guardia en alerta.",
            "Evaluación NIHSS en campo si el equipo está entrenado.",
            "Notificar hospital receptor (código ictus). Cath lab en espera.",
            "Traslado inmediato. Tiempo objetivo puerta-aguja < 60 min.",
        ],
        "escalation": "Si síntomas > 4.5h o anticoagulado: evaluar contraindicaciones para trombólisis.",
        "notes":      "Escala FAST: Face, Arms, Speech, Time.",
        "source":     "stub",
    },
    "gas_leak": {
        "code": "PRT-2024-022", "title": "Protocolo GAS — Fuga de Gas Natural / GLP",
        "steps": [
            "NO activar ni apagar interruptores eléctricos.",
            "Evacuar inmediatamente el recinto.",
            "Ventilar abriendo puertas y ventanas al salir.",
            "Cortar suministro desde la llave exterior.",
            "Solicitar técnico de la compañía distribuidora.",
            "Inspeccionar con detector portátil antes de reentrar.",
        ],
        "escalation": "Con víctimas por inhalación o explosión: activar INC simultáneamente.",
        "notes":      "GLP más pesado que el aire: acumula en zonas bajas.",
        "source":     "stub",
    },
    "assault": {
        "code": "PRT-2024-031", "title": "Protocolo AGR — Agresión y Violencia",
        "steps": [
            "Esperar autorización policial antes de la entrada sanitaria.",
            "Evaluar número de víctimas y lesiones.",
            "Separar testigos y agresor de las víctimas.",
            "Atención sanitaria con seguridad garantizada.",
            "Preservar indicios para instrucción judicial.",
        ],
        "escalation": "Si hay armas: solicitar GEOS. Mantener perímetro.",
        "notes":      "Violencia de género: activar protocolo VIOGEN.",
        "source":     "stub",
    },
    "fall_injury": {
        "code": "PRT-2024-011", "title": "Protocolo TRA — Traumatismo por Caída",
        "steps": [
            "Estabilizar columna cervical si el mecanismo lo indica.",
            "Evaluar nivel de consciencia (Glasgow).",
            "Inmovilizar fracturas visibles antes del traslado.",
            "Control de hemorragias con compresión directa.",
            "Traslado al hospital de referencia con pre-aviso.",
        ],
        "escalation": "Si TCE moderado-severo (Glasgow < 13): activar código trauma.",
        "notes":      "Personas mayores: alta sospecha de fractura de cadera.",
        "source":     "stub",
    },
}


_cache:         Optional[dict]   = None  
_cache_mtime:   float            = 0.0    
_query_engine:  Optional[object] = None   
_index_store:   Optional[object] = None   


def _load_cache() -> dict:
    global _cache, _cache_mtime
    if _cache is not None:
        try:
            current_mtime = CACHE_PATH.stat().st_mtime
            if current_mtime <= _cache_mtime:
                return _cache 
            logger.info("[query_protocol_index] Cache file changed on disk — reloading.")
        except OSError:
            return _cache
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and any(isinstance(v, dict) for v in data.values()):
                _cache = data
                _cache_mtime = CACHE_PATH.stat().st_mtime
                entry_count = sum(len(v) for v in _cache.values() if isinstance(v, dict))
                logger.info(f"[query_protocol_index] Cache loaded: {entry_count} entries")
            else:
                logger.warning(
                    f"[query_protocol_index] File at {CACHE_PATH} is not a structured protocol cache. "
                    "Falling back to empty cache."
                )
                _cache = {}
        except Exception as e:
            logger.error(f"[query_protocol_index] Failed to load structured cache: {e}")
            _cache = {}
    else:
        _cache = {}
        logger.warning(
            f"[query_protocol_index] Cache not found at {CACHE_PATH}. "
            "Procedural lookups will fall back to vector search and stubs."
        )
    return _cache


load_cache = _load_cache


def vector_store_available() -> bool:
    """Fast check — returns True when the ChromaDB collection has been ingested.
    Does NOT load the embedding model; reads only the SQLite row count."""
    if not CHROMA_DIR.exists():
        return False
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        col    = client.get_or_create_collection("imers_protocols")
        return col.count() > 0
    except Exception:
        return False


def _load_vector_engine():
    """Lazily load the LlamaIndex query engine over the ChromaDB vector store."""
    global _query_engine, _index_store
    if _query_engine is not None:
        return _query_engine
    if not CHROMA_DIR.exists():
        return None
    try:
        from llama_index.core import VectorStoreIndex, StorageContext, Settings
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        from llama_index.vector_stores.chroma import ChromaVectorStore
        import chromadb

        Settings.embed_model = HuggingFaceEmbedding(
            model_name="intfloat/multilingual-e5-base", max_length=512
        )
        Settings.llm = None

        chroma_client     = chromadb.PersistentClient(path=str(CHROMA_DIR))
        chroma_collection = chroma_client.get_or_create_collection("imers_protocols")

        if chroma_collection.count() == 0:
            logger.warning(
                "[query_protocol_index] ChromaDB collection is empty. "
                "Run: python tools/protocol_indexer.py --ingest"
            )
            return None

        vector_store  = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_ctx   = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(
            vector_store, storage_context=storage_ctx
        )
        _index_store  = index
        _query_engine = index.as_query_engine(similarity_top_k=4, response_mode="compact")
        logger.info("[query_protocol_index] Vector engine loaded from ChromaDB")
    except Exception as e:
        logger.warning(f"[query_protocol_index] Could not load vector engine: {e}")
        _query_engine = None
    return _query_engine


def search_chunks(query: str, k: int = 6) -> list[dict]:
    """Return the top-k most relevant protocol text chunks for a free-text query.

    Uses the same ChromaDB / embedding model as the Procedure Agent.
    Returns an empty list when the vector store is not available.

    Args:
        query: Free-text question or keyword string.
        k:     Number of results to return (capped at 15).

    Returns:
        List of dicts with keys: text (str), score (float 0-1), source (str filename).
    """
    _load_vector_engine()          
    if _index_store is None:
        return []
    try:
        retriever = _index_store.as_retriever(similarity_top_k=min(k, 15))
        nodes     = retriever.retrieve(query)
        results   = []
        for n in nodes:
            meta   = n.node.metadata or {}
            source = (meta.get("file_name") or meta.get("filename")
                      or meta.get("source") or "—")
            results.append({
                "text":   n.node.text[:500].strip(),
                "score":  round(float(n.score or 0.0), 3),
                "source": source,
            })
        return results
    except Exception as exc:
        logger.warning(f"[search_chunks] Failed: {exc}")
        return []


def _from_cache(incident_type: str, severity: str) -> Optional[dict]:
    """Tier 1: O(1) exact lookup in the pre-computed JSON cache."""
    cache = _load_cache()
    if not cache:
        return None

    if incident_type in cache and severity in cache[incident_type]:
        entry = dict(cache[incident_type][severity])
        entry["retrieval_tier"] = "cache"
        return entry

    for fallback_sev in ("critical", "high", "medium", "low"):
        if incident_type in cache and fallback_sev in cache[incident_type]:
            entry = dict(cache[incident_type][fallback_sev])
            entry["retrieval_tier"] = "cache_severity_fallback"
            return entry

    return None


def _from_vector(incident_type: str, severity: str, extra_context: str) -> Optional[dict]:
    """Tier 2: Semantic vector search against ChromaDB."""
    engine = _load_vector_engine()
    if engine is None:
        return None
    try:
        query    = (
            f"Emergency response protocol for {incident_type.replace('_', ' ')} "
            f"({severity} severity): required units, step-by-step actions, "
            f"escalation criteria, hospital pre-notification."
            + (f" Context: {extra_context}" if extra_context else "")
        )
        response = engine.query(query)
        text     = str(response).strip()
        if not text or text.lower() in ("empty response", "none", "n/a"):
            return None
        def _latin_printable(c: str) -> bool:
            cp = ord(c)
            return (0x20 <= cp <= 0x7E) or (0xA0 <= cp <= 0x024F) or c in "\n\r\t"

        chunk_blocks = _re.findall(r"-{5,}\n(.*?)(?:\n-{5,}|$)", text, _re.DOTALL)
        content_to_check = "\n".join(chunk_blocks) if chunk_blocks else text
        if content_to_check:
            latin_ratio = (
                sum(_latin_printable(c) for c in content_to_check)
                / len(content_to_check)
            )
            if latin_ratio < 0.70:
                logger.warning(
                    f"[query_protocol_index] Vector result appears to be binary content "
                    f"(latin_ratio={latin_ratio:.2f}). "
                    "Falling back to stub. Re-run protocol_indexer.py --ingest to fix."
                )
                return None
        return {
            "code":           "VECTOR-RESULT",
            "title":          f"Protocol: {incident_type} / {severity}",
            "steps":          [text],   
            "escalation":     "",
            "notes":          "",
            "raw_text":       text,
            "source":         "vector",
            "retrieval_tier": "vector",
        }
    except Exception as e:
        logger.warning(f"[query_protocol_index] Vector search failed: {e}")
        return None


def _from_stub(incident_type: str) -> dict:
    """Tier 3: Built-in stub table — always succeeds."""
    entry = _STUBS.get(incident_type) or _STUBS.get("traffic_accident")
    result = dict(entry)
    result["retrieval_tier"] = "stub"
    return result


@tool
def query_protocol_index(
    incident_type: str,
    severity:      str,
    extra_context: str = "",
) -> str:
    """Retrieves the emergency response protocol for a given incident type and severity.
    Checks a structured JSON cache first (O(1)), falls back to semantic vector search,
    then built-in stubs as a last resort. Always returns a usable result.

    Args:
        incident_type: Emergency type — one of: traffic_accident, fire, cardiac_arrest,
                       stroke, assault, robbery, drowning, fall_injury, gas_leak,
                       explosion, missing_person, mental_health_crisis, flooding,
                       infrastructure_collapse, chemical_spill, other_medical,
                       other_police, other.
        severity:      One of: 'critical', 'high', 'medium', 'low'.
        extra_context: Optional free-text to refine vector search on cache miss,
                       e.g. 'victim is a child' or 'incident in a tunnel'.

    Returns:
        JSON string with keys:
          code (str) — protocol reference code e.g. 'PRT-2024-001'
          title (str) — protocol full title
          steps (list[str]) — numbered action steps
          escalation (str) — escalation criteria text
          notes (str) — special considerations
          source (str) — 'cache' | 'vector' | 'stub'
          retrieval_tier (str) — lookup tier used
          source_file (str, optional) — originating document (cache/vector only)
          indexed_at (str, optional) — ISO 8601 indexing timestamp (cache only)
    """
    result = _from_cache(incident_type, severity)

    if result is None:
        result = _from_vector(incident_type, severity, extra_context)

    if result is None:
        result = _from_stub(incident_type)
        logger.info(f"[query_protocol_index] Using stub for {incident_type}/{severity}")
    else:
        logger.info(
            f"[query_protocol_index] Retrieved via {result.get('retrieval_tier')} "
            f"for {incident_type}/{severity}"
        )

    return json.dumps(result, ensure_ascii=False)


def _run_ingest() -> None:
    """
    Offline step: read all PDFs (and .txt / .md) from DOCS_DIR,
    split into chunks, embed with multilingual-e5-base, and store
    into ChromaDB at CHROMA_DIR.
    """
    import datetime
    try:
        from llama_index.core import (
            VectorStoreIndex, StorageContext, Settings, SimpleDirectoryReader,
        )
        from llama_index.core.node_parser import SentenceSplitter
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        from llama_index.vector_stores.chroma import ChromaVectorStore
        import chromadb
    except ImportError as exc:
        print(f"[ingest] Missing dependency: {exc}")
        print("  pip install llama-index llama-index-embeddings-huggingface "
              "llama-index-vector-stores-chroma chromadb")
        raise SystemExit(1)

    if not DOCS_DIR.exists():
        print(f"[ingest] Documents directory not found: {DOCS_DIR}")
        raise SystemExit(1)

    supported = {".pdf", ".txt", ".md"}
    doc_files = [
        f for f in DOCS_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in supported
    ]
    if not doc_files:
        print(f"[ingest] No PDF/TXT/MD files found in {DOCS_DIR}")
        raise SystemExit(1)

    print(f"[ingest] Found {len(doc_files)} document(s) in {DOCS_DIR}:")
    for f in doc_files:
        print(f"         • {f.name}  ({f.stat().st_size // 1024} KB)")

    print("[ingest] Loading embedding model (intfloat/multilingual-e5-base)…")
    Settings.embed_model = HuggingFaceEmbedding(
        model_name="intfloat/multilingual-e5-base", max_length=512
    )
    Settings.llm = None

    print("[ingest] Reading and chunking documents…")
    reader = SimpleDirectoryReader(
        input_files=[str(f) for f in doc_files]
    )
    documents = reader.load_data()
    print(f"[ingest] Loaded {len(documents)} page/section(s) from source files.")

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes = splitter.get_nodes_from_documents(documents)
    print(f"[ingest] Split into {len(nodes)} chunk(s).")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    chroma_client     = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        chroma_client.delete_collection("imers_protocols")
        print("[ingest] Cleared existing ChromaDB collection.")
    except Exception:
        pass
    chroma_collection = chroma_client.create_collection("imers_protocols")
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_ctx       = StorageContext.from_defaults(vector_store=vector_store)

    print("[ingest] Embedding chunks and writing to ChromaDB… (this may take a while)")
    VectorStoreIndex(nodes, storage_context=storage_ctx, show_progress=True)

    final_count = chroma_collection.count()
    print(f"[ingest] ✓ Done. {final_count} vectors stored in {CHROMA_DIR}")

    summary = {
        "indexed_at": datetime.datetime.utcnow().isoformat() + "Z",
        "chroma_dir": str(CHROMA_DIR),
        "docs_dir":   str(DOCS_DIR),
        "doc_count":  len(doc_files),
        "node_count": len(nodes),
        "vector_count": final_count,
        "files": [f.name for f in doc_files],
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"[ingest] Ingestion summary written to {SUMMARY_PATH}")


def _run_summary() -> None:
    """Print the current state of the ChromaDB collection and cache."""
    import chromadb

    print("-- Protocol Index Summary " + "-" * 35)
    print(f"  Docs dir  : {DOCS_DIR}")
    print(f"  Chroma dir: {CHROMA_DIR}")
    print(f"  Summary file: {SUMMARY_PATH}")

    if SUMMARY_PATH.exists():
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  Indexed at  : {cache.get('indexed_at', 'unknown')}")
        print(f"  Documents   : {cache.get('doc_count', '?')}")
        print(f"  Chunks      : {cache.get('node_count', '?')}")
        print(f"  Vectors     : {cache.get('vector_count', '?')}")
        print("  Files:")
        for fn in cache.get("files", []):
            print(f"    • {fn}")
    else:
        print("  Summary     : NOT FOUND — run --ingest first")

    if CHROMA_DIR.exists():
        try:
            chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            col = chroma_client.get_or_create_collection("imers_protocols")
            print(f"  ChromaDB  : {col.count()} vector(s) in collection 'imers_protocols'")
        except Exception as e:
            print(f"  ChromaDB  : error reading — {e}")
    else:
        print("  ChromaDB  : chroma_store/ not found — run --ingest first")


if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--ingest" in sys.argv:
        _run_ingest()

    elif "--summary" in sys.argv:
        _run_summary()

    else:
        test_cases = [
            ("cardiac_arrest",   "critical", ""),
            ("traffic_accident", "high",     "victim is elderly, possible spinal injury"),
            ("fire",             "critical", "building with 10 floors, people trapped"),
            ("gas_leak",         "high",     ""),
            ("stroke",           "critical", ""),
        ]

        print("query_protocol_index: test results\n" + "-" * 60)
        for itype, sev, ctx in test_cases:
            result = json.loads(query_protocol_index(itype, sev, ctx))
            print(f"\n[{itype} / {sev}]")
            print(f"  Tier   : {result['retrieval_tier']}")
            print(f"  Code   : {result['code']}")
            print(f"  Title  : {result['title']}")
            print(f"  Steps  : {len(result['steps'])} steps")
            if result['steps']:
                print(f"  Step 1 : {result['steps'][0][:80]}")
            if result.get('escalation'):
                print(f"  Escal. : {result['escalation'][:80]}")