# IMERS — Inteligencia Multiagente para Emergencias y Respuesta Sanitaria

Sistema de IA multiagente para la gestión y despacho de llamadas de emergencia.

---

## Requisitos

- Python 3.11+
- Node.js 18+ y npm
- Rust (stable) — para la aplicación de escritorio
- ffmpeg (opcional, para archivos de audio que no sean WAV)

---

## Instalación del backend

**Clonar y entrar al proyecto**
```
git clone <repo-url> && cd TFG_PFH
```

**Crear y activar el entorno virtual**
```
python -m venv venv
venv\Scripts\activate
```

**Instalar dependencias Python**
```
pip install -r requirements.txt
```

**Descargar el modelo spaCy en español**
```
python -m spacy download es_core_news_lg
```

**Configurar variables de entorno**
```
copy .env.examples .env       # editar .env con las claves API
```

Variables requeridas en `.env`:

| Variable | Descripción |
|---|---|
| `GROQ_API_KEY` | Clave API de Groq |
| `FIREWORKS_API_KEY` | Clave API de Fireworks AI |
| `HF_TOKEN` | Token de Hugging Face |
| `ORS_API_KEY` | Clave API de OpenRouteService |

**Poblar la base de datos con incidentes de muestra**
```
python scripts/seed_incidents.py
```

**Iniciar el servidor backend**
```
python dashboard/api_entry.py
```

La API estará disponible en `http://127.0.0.1:8000`.

---

## Aplicación de escritorio (Tauri)

**Instalar dependencias del frontend**
```
cd IMERS && npm install
```

**Ejecutar en modo desarrollo (hot reload)**
```
npm run tauri dev
```

**Compilar la aplicación de escritorio**
```
npm run tauri build
```

**Ejecutar solo el servidor Vite (sin Tauri)**
```
npm run dev
```

---

## Tests

**Ejecutar todos los tests**
```
python -m pytest
```

**Ejecutar un módulo de tests específico**
```
test\run_tests.bat classify       # clasificación de incidentes
test\run_tests.bat recommend      # recomendación de unidades
test\run_tests.bat route          # tests de enrutamiento
test\run_tests.bat protocol       # indexador de protocolos
test\run_tests.bat tts            # agente TTS
test\run_tests.bat analysis       # agente de análisis
test\run_tests.bat pipeline       # nodos del pipeline
```

**Filtrar tests por palabra clave**
```
python -m pytest -k "cardiac"
```

---

## Compilar binario independiente

**Generar ejecutable con PyInstaller**
```
pyinstaller imers-backend.spec
```

Resultado: `dist/imers-backend.exe`

> PyTorch/Whisper se excluyen por defecto para reducir el tamaño (~200–400 MB). El agente TTS usa Groq como fallback.

---

## Receptor de audio (prueba independiente)

**Entrada de micrófono**
```
python realtime/call_receiver.py --source mic --duration 10
```

**Desde un archivo de audio**
```
python realtime/call_receiver.py --source file --file ruta/al/audio.wav
```

**Via socket TCP**
```
python realtime/call_receiver.py --source socket --host 0.0.0.0 --port 9999
```

---

## Referencia de variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `IMERS_DB_PATH` | `./data/incidents.db` | Ruta de la base de datos SQLite |
| `IMERS_CHROMA_DIR` | `./data/protocol_index/chroma_store` | Directorio del vector store ChromaDB |
| `WHISPER_MODEL` | `medium` | Tamaño del modelo Whisper |
| `IMERS_MOCK_MODE` | `0` | Poner a `1` para usar respuestas simuladas |
| `IMERS_SILENCE_HANGUP` | `2` | Chunks de silencio antes de colgar la llamada |
| `GROQ_MAX_CONCURRENT` | `2` | Peticiones concurrentes máximas a Groq |
| `IMERS_EARLY_TRIGGER_WORDS` | `40` | Palabras antes del disparo anticipado del agente |

---
---

# IMERS — Multi-agent Intelligence for Emergencies and Health Response

Multi-agent AI system for emergency call management and dispatching.

---

## Requirements

- Python 3.11+
- Node.js 18+ and npm
- Rust (stable) — for the desktop app
- ffmpeg (optional, for non-WAV audio files)

---

## Backend Setup

**Clone and enter the project**
```
git clone <repo-url> && cd TFG_PFH
```

**Create and activate the virtual environment**
```
python -m venv venv
venv\Scripts\activate
```

**Install Python dependencies**
```
pip install -r requirements.txt
```

**Download the Spanish spaCy model**
```
python -m spacy download es_core_news_lg
```

**Configure environment variables**
```
copy .env.examples .env       # then edit .env with your API keys
```

Required keys in `.env`:

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Groq cloud LLM API key |
| `FIREWORKS_API_KEY` | Fireworks AI API key |
| `HF_TOKEN` | Hugging Face token |
| `ORS_API_KEY` | OpenRouteService routing API key |

**Seed the database with sample incidents**
```
python scripts/seed_incidents.py
```

**Start the backend server**
```
python dashboard/api_entry.py
```

The API will be available at `http://127.0.0.1:8000`.

---

## Desktop App (Tauri)

**Install frontend dependencies**
```
cd IMERS && npm install
```

**Run in development mode (hot reload)**
```
npm run tauri dev
```

**Build the production desktop app**
```
npm run tauri build
```

**Run the Vite dev server only (no Tauri)**
```
npm run dev
```

---

## Testing

**Run all tests**
```
python -m pytest
```

**Run a specific test module**
```
test\run_tests.bat classify       # incident classification tests
test\run_tests.bat recommend      # unit recommendation tests
test\run_tests.bat route          # routing tests
test\run_tests.bat protocol       # protocol indexer tests
test\run_tests.bat tts            # TTS agent tests
test\run_tests.bat analysis       # analysis agent tests
test\run_tests.bat pipeline       # pipeline node tests
```

**Run tests matching a keyword**
```
python -m pytest -k "cardiac"
```

---

## Building a Standalone Binary

**Build the backend as a single executable (PyInstaller)**
```
pyinstaller imers-backend.spec
```

Output: `dist/imers-backend.exe`

> PyTorch/Whisper are excluded by default to keep binary size manageable (~200–400 MB). The TTS agent falls back to Groq cloud.

---

## Audio / Call Receiver (standalone test)

**Test microphone input**
```
python realtime/call_receiver.py --source mic --duration 10
```

**Test with an audio file**
```
python realtime/call_receiver.py --source file --file path/to/audio.wav
```

**Test via TCP socket**
```
python realtime/call_receiver.py --source socket --host 0.0.0.0 --port 9999
```

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `IMERS_DB_PATH` | `./data/incidents.db` | SQLite database path |
| `IMERS_CHROMA_DIR` | `./data/protocol_index/chroma_store` | ChromaDB vector store path |
| `WHISPER_MODEL` | `medium` | Whisper model size |
| `IMERS_MOCK_MODE` | `0` | Set to `1` to use mocked AI responses |
| `IMERS_SILENCE_HANGUP` | `2` | Silent chunks before call hangup |
| `GROQ_MAX_CONCURRENT` | `2` | Max concurrent Groq requests |
| `IMERS_EARLY_TRIGGER_WORDS` | `40` | Word count before early agent trigger |
