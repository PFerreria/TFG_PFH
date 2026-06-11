"""
Generates realistic synthetic emergency call audio files in Spanish
for testing the IMERS speech-to-text and classification pipeline.

Backends tried in order: edge-tts, gtts, pyttsx3
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

@dataclass
class Scenario:
    name: str          
    category: str       
    text: str          
    tags: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(
        name="traffic_accident_01",
        category="traffic_accident",
        tags=["trapped", "multiple_victims"],
        text=(
            "Hola, llamo porque ha habido un accidente de tráfico muy grave "
            "en la Avenida de la Constitución esquina con Calle Sierpes en Sevilla. "
            "Hay tres vehículos implicados. Uno de los conductores está inconsciente "
            "y atrapado dentro del coche. Otro tiene heridas visibles en la cabeza "
            "y hay humo saliendo del motor del tercer vehículo. "
            "Necesitamos ambulancias y bomberos urgente, por favor."
        ),
    ),
    Scenario(
        name="traffic_accident_02",
        category="traffic_accident",
        tags=["pedestrian_hit"],
        text=(
            "Buenas tardes, acabo de ver cómo un coche ha atropellado a un peatón "
            "en la calle Betis a la altura del número veinte. "
            "El hombre está tumbado en el suelo, no se mueve y tiene mucha sangre. "
            "El conductor ha parado. Vengan rápido, por favor."
        ),
    ),
    Scenario(
        name="traffic_accident_03",
        category="traffic_accident",
        tags=["motorcycle", "minor"],
        text=(
            "Sí, hola, llamaba para avisar de un accidente de moto en el paseo "
            "de la Alameda de Hércules. El motorista ha caído pero está consciente, "
            "dice que le duele mucho el brazo. No parece grave, pero necesita asistencia médica."
        ),
    ),
    Scenario(
        name="traffic_accident_04",
        category="traffic_accident",
        tags=["mass_casualty"],
        text=(
            "Es un accidente muy grave en la autovía A-4 kilómetro ciento veinte, "
            "sentido Cádiz. Ha habido una colisión entre un camión y dos turismos. "
            "Hay al menos cinco personas heridas, dos de ellas en estado muy grave. "
            "Una persona está atrapada entre los hierros. Necesitamos todo lo que tengan."
        ),
    ),

    Scenario(
        name="cardiac_arrest_01",
        category="cardiac_arrest",
        tags=["no_pulse", "single_victim"],
        text=(
            "Mi padre acaba de sufrir un paro cardíaco en casa. "
            "No respira y no tiene pulso. Estamos en la Calle Feria número treinta y dos, "
            "Sevilla, cuarto piso. Por favor, manden una ambulancia urgente, "
            "no sé qué hacer, está inconsciente."
        ),
    ),
    Scenario(
        name="cardiac_arrest_02",
        category="cardiac_arrest",
        tags=["public_space"],
        text=(
            "Hola, estoy en la Plaza Nueva de Sevilla y un hombre mayor "
            "ha caído al suelo y ha dejado de respirar. "
            "Hay gente intentando hacerle reanimación. "
            "Necesitan venir ya, tiene los labios morados."
        ),
    ),
    Scenario(
        name="cardiac_arrest_03",
        category="cardiac_arrest",
        tags=["in_progress_cpr"],
        text=(
            "Llamo porque mi compañero de trabajo se ha desmayado y no responde. "
            "Estoy haciendo el masaje cardíaco pero no sé si lo estoy haciendo bien. "
            "Estamos en el polígono industrial Sur, nave cuarenta y cinco. "
            "¡Dénse prisa, por favor!"
        ),
    ),

    Scenario(
        name="fire_01",
        category="fire",
        tags=["building_fire", "trapped"],
        text=(
            "Hay un incendio en el edificio de apartamentos en la Calle Resolana número ocho. "
            "Veo llamas saliendo por las ventanas del tercer piso. "
            "Hay gente gritando arriba, creo que hay personas atrapadas. "
            "El humo es muy negro. Manden bomberos ya, por favor."
        ),
    ),
    Scenario(
        name="fire_02",
        category="fire",
        tags=["vehicle_fire"],
        text=(
            "Un coche está ardiendo en el aparcamiento del centro comercial Nervión. "
            "Las llamas están muy altas y se están extendiendo a los vehículos de al lado. "
            "No hay nadie dentro del coche, pero hay gente en el parking."
        ),
    ),
    Scenario(
        name="fire_03",
        category="fire",
        tags=["smoke_only", "restaurant"],
        text=(
            "Estoy en el restaurante de la Calle Sierpes y hay mucho humo saliendo "
            "de la cocina. No veo llamas todavía pero el humo es negro y huele a quemado. "
            "Estamos evacuando el local."
        ),
    ),

    Scenario(
        name="gas_leak_01",
        category="gas_leak",
        tags=["residential", "strong_smell"],
        text=(
            "Llamo porque huele muchísimo a gas en toda mi escalera. "
            "Vivimos en la Avenida Menéndez Pelayo número doce, Sevilla. "
            "El olor es muy fuerte y tengo miedo de que haya una explosión. "
            "He llamado a los vecinos para que salgan."
        ),
    ),
    Scenario(
        name="gas_leak_02",
        category="gas_leak",
        tags=["street_level"],
        text=(
            "Estoy en la Calle Reyes Católicos y hay un olor fortísimo a gas "
            "saliendo de la acera. Parece que la tubería está rota. "
            "Hay mucha gente por la calle, estoy alejando a la gente. "
            "Vengan lo antes posible."
        ),
    ),

    Scenario(
        name="stroke_01",
        category="stroke",
        tags=["facial_droop", "speech_difficulty"],
        text=(
            "Mi madre ha tenido un ictus, creo. Tiene la cara torcida, "
            "no puede hablar bien y el brazo izquierdo no lo puede levantar. "
            "Empezó hace unos diez minutos. Estamos en casa, Calle San Jacinto cuarenta, "
            "Triana, Sevilla, tercero B. Por favor, ¡es muy urgente!"
        ),
    ),
    Scenario(
        name="stroke_02",
        category="stroke",
        tags=["sudden_onset"],
        text=(
            "Mi marido estaba bien y de repente ha empezado a hablar raro, "
            "dice palabras sin sentido y se ha caído. "
            "Estoy muy asustada, vivimos en la Plaza del Salvador número tres. "
            "Manden una ambulancia urgente, por favor."
        ),
    ),

    Scenario(
        name="assault_01",
        category="assault",
        tags=["ongoing", "weapon"],
        text=(
            "Me están pegando en la calle, por favor ayúdenme. "
            "Estoy en el callejón de la Calle Imagen, cerca de la Alameda. "
            "Son dos hombres, uno tiene una navaja. Me han dado en la cabeza, "
            "estoy sangrando. ¡Vengan rápido!"
        ),
    ),
    Scenario(
        name="domestic_violence_01",
        category="domestic_violence",
        tags=["partner_violence"],
        text=(
            "Mi marido me está pegando, necesito ayuda urgente. "
            "Estoy en la Calle Torneo número cuarenta y cinco, Sevilla, segundo piso. "
            "Tiene un cuchillo. Mis hijos están aquí. "
            "Por favor, vengan ya, tengo miedo."
        ),
    ),

    Scenario(
        name="drowning_01",
        category="drowning",
        tags=["child", "pool"],
        text=(
            "Mi hijo se está ahogando en la piscina, tiene cuatro años. "
            "Lo he sacado del agua pero no respira. "
            "Estamos en la Avenida Eduardo Dato número veintidós, "
            "la urbanización tiene piscina comunitaria. "
            "¡Manden ayuda, no sé qué hacer!"
        ),
    ),
    Scenario(
        name="drowning_02",
        category="drowning",
        tags=["river", "adult"],
        text=(
            "Una persona se está ahogando en el río Guadalquivir, "
            "a la altura del puente de Triana. "
            "Ha saltado al agua y no puede salir. "
            "Hay gente intentando ayudarle desde la orilla. "
            "Necesitan un equipo de rescate acuático."
        ),
    ),

    Scenario(
        name="fall_injury_01",
        category="fall_injury",
        tags=["elderly", "stairs"],
        text=(
            "Mi abuela se ha caído por las escaleras de casa. "
            "Tiene ochenta y tres años. Está consciente pero con mucho dolor "
            "en la cadera y no puede moverse. "
            "Vivimos en la Calle González Cuadrado número cinco, Sevilla. "
            "Necesitamos una ambulancia."
        ),
    ),
    Scenario(
        name="fall_injury_02",
        category="fall_injury",
        tags=["work_accident", "height"],
        text=(
            "Ha habido un accidente laboral en la obra de la Calle Luis Montoto. "
            "Un trabajador se ha caído desde el andamio del cuarto piso. "
            "Está en el suelo, inconsciente, con una herida muy grande en la cabeza. "
            "¡Vengan urgente, por favor!"
        ),
    ),

    Scenario(
        name="missing_person_01",
        category="missing_person",
        tags=["child", "park"],
        text=(
            "He perdido a mi hija en el parque de María Luisa de Sevilla. "
            "Tiene seis años, pelo rizado, lleva un abrigo rojo. "
            "Se llama Lucía. La busco desde hace veinte minutos "
            "y no la encuentro por ningún lado. "
            "Estoy muy asustada."
        ),
    ),
    Scenario(
        name="missing_person_02",
        category="missing_person",
        tags=["elderly_dementia"],
        text=(
            "Mi padre tiene Alzheimer y se ha escapado de casa. "
            "Tiene setenta y ocho años, mide un metro setenta, lleva pijama azul. "
            "Lleva desaparecido desde las dos de la tarde. "
            "Vivimos en la Calle Amor de Dios. "
            "Necesitamos ayuda para buscarlo."
        ),
    ),

    Scenario(
        name="mental_health_01",
        category="mental_health_crisis",
        tags=["suicidal", "bridge"],
        text=(
            "Hay un hombre en el puente de la Barqueta que dice que se va a tirar. "
            "Está muy alterado, llorando. Lleva un rato ahí parado. "
            "Necesitan venir policía y alguien que pueda hablar con él. "
            "Estoy mirándole desde abajo y tengo miedo de que salte."
        ),
    ),
    Scenario(
        name="mental_health_02",
        category="mental_health_crisis",
        tags=["panic_attack"],
        text=(
            "Mi vecino está teniendo una crisis de ansiedad muy fuerte. "
            "No puede respirar, le tiembla todo el cuerpo y dice que se va a morir. "
            "Estamos en la Calle Canalejas número diez. "
            "Es la primera vez que le pasa y no sé cómo ayudarle."
        ),
    ),

    Scenario(
        name="flooding_01",
        category="flooding",
        tags=["street_flood", "vehicles"],
        text=(
            "La Calle Torneo está completamente inundada. "
            "El agua llega hasta las rodillas y hay coches atascados. "
            "Hay una familia con niños dentro de uno de los coches "
            "y el agua está subiendo. "
            "Necesitan bomberos con botes o equipo de rescate."
        ),
    ),

    Scenario(
        name="robbery_01",
        category="robbery",
        tags=["armed", "in_progress"],
        text=(
            "Me están atracando ahora mismo, estoy en el cajero del banco "
            "de la Avenida de Andalucía. "
            "Son dos personas, uno tiene una pistola. "
            "Me han quitado el monedero y el móvil. "
            "Por favor, manden policía urgente, todavía están aquí."
        ),
    ),

    Scenario(
        name="explosion_01",
        category="explosion",
        tags=["building", "multiple_victims"],
        text=(
            "Ha habido una explosión muy fuerte en el bar de la Calle Cervantes. "
            "Hay cristales rotos por todas partes, hay gente herida en el suelo. "
            "El edificio está humeando. "
            "Hay al menos tres personas con heridas graves que yo pueda ver. "
            "¡Manden todo lo que puedan!"
        ),
    ),

    Scenario(
        name="overdose_01",
        category="overdose",
        tags=["unconscious"],
        text=(
            "Mi amigo ha tomado algo y está inconsciente. "
            "Creo que se ha metido heroína, lo encontré en el baño de la discoteca "
            "en la Calle Marqués de Contadero. "
            "Está respirando pero muy lento y no responde cuando le llamo. "
            "Por favor, vengan ya."
        ),
    ),

    Scenario(
        name="infrastructure_collapse_01",
        category="infrastructure_collapse",
        tags=["building_collapse", "trapped"],
        text=(
            "Se ha derrumbado parte de un edificio antiguo en la Calle Catalanes. "
            "Hay escombros en la calle y se escuchan voces debajo. "
            "Creo que hay personas atrapadas entre los escombros. "
            "El edificio parece que puede seguir cayendo. "
            "Necesitamos bomberos y equipos de rescate urgente."
        ),
    ),

    Scenario(
        name="chemical_spill_01",
        category="chemical_spill",
        tags=["industrial_area", "fumes"],
        text=(
            "En el polígono industrial Calonge ha habido un derrame de productos químicos. "
            "Hay un camión volcado y está saliendo un líquido verde. "
            "El olor es muy fuerte y hay trabajadores tosiendo. "
            "Hemos evacuado la nave pero no sabemos qué es el producto. "
            "Necesitan bomberos con trajes especiales."
        ),
    ),
]


def _try_edge_tts(text: str, out_path: Path) -> bool:
    """Generate audio using Microsoft Edge TTS (neural voices, best quality)."""
    try:
        import edge_tts
    except ImportError:
        return False

    async def _generate():
        communicate = edge_tts.Communicate(text, voice="es-ES-AlvaroNeural")
        await communicate.save(str(out_path.with_suffix(".mp3")))

    try:
        asyncio.run(_generate())
        mp3_path = out_path.with_suffix(".mp3")
        if mp3_path != out_path and mp3_path.exists():
            mp3_path.rename(out_path)
        return True
    except Exception as e:
        print(f"  [edge-tts] failed: {e}")
        return False


def _try_gtts(text: str, out_path: Path) -> bool:
    """Generate audio using Google TTS."""
    try:
        from gtts import gTTS
    except ImportError:
        return False

    try:
        tts = gTTS(text=text, lang="es", slow=False)
        mp3_path = out_path.with_suffix(".mp3")
        tts.save(str(mp3_path))
        if mp3_path != out_path and mp3_path.exists():
            mp3_path.rename(out_path)
        return True
    except Exception as e:
        print(f"  [gtts] failed: {e}")
        return False


def _try_pyttsx3(text: str, out_path: Path) -> bool:
    """Generate audio using pyttsx3 (offline, system voices)."""
    try:
        import pyttsx3
    except ImportError:
        return False

    try:
        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        for v in voices:
            if "es" in (v.languages or []) or "spanish" in v.name.lower():
                engine.setProperty("voice", v.id)
                break

        engine.setProperty("rate", 150)
        wav_path = out_path.with_suffix(".wav")
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
        if wav_path != out_path and wav_path.exists():
            wav_path.rename(out_path)
        return True
    except Exception as e:
        print(f"  [pyttsx3] failed: {e}")
        return False


def _generate_silent_wav(out_path: Path, duration: float = 2.0):
    """Last-resort fallback: creates a tiny silent WAV (usable for pipeline smoke tests)."""
    import struct
    import wave
    import math

    sr = 16000
    wav_path = out_path.with_suffix(".wav")
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(int(sr * duration)):
            val = int(8000 * math.sin(2 * math.pi * 440 * i / sr))
            wf.writeframes(struct.pack("<h", val))
    if wav_path != out_path:
        wav_path.rename(out_path)


def generate_audio(
    scenario: Scenario,
    out_dir: Path,
    backend: Optional[str] = None,
    extension: str = "mp3",
) -> Path:
    """
    Generate audio for a single scenario using the specified (or auto-detected) backend.
    Returns the path to the generated audio file.
    """
    out_path = out_dir / f"{scenario.name}.{extension}"

    if out_path.exists():
        print(f"  [skip] {out_path.name} already exists")
        return out_path

    print(f"  Generating: {scenario.name}  ({scenario.category})…", end=" ", flush=True)

    backends = {
        "edge-tts": _try_edge_tts,
        "gtts":     _try_gtts,
        "pyttsx3":  _try_pyttsx3,
    }

    order = [backend] if backend else ["edge-tts", "gtts", "pyttsx3"]

    for name in order:
        fn = backends.get(name)
        if fn and fn(scenario.text, out_path):
            for ext in ["mp3", "wav", "ogg"]:
                candidate = out_dir / f"{scenario.name}.{ext}"
                if candidate.exists() and candidate != out_path:
                    candidate.rename(out_path)
                    break
            if not out_path.exists():
                for ext in ["mp3", "wav", "ogg"]:
                    candidate = out_dir / f"{scenario.name}.{ext}"
                    if candidate.exists():
                        out_path = candidate
                        break
            print(f"✓ ({name})")
            return out_path

    wav_out = out_dir / f"{scenario.name}.wav"
    _generate_silent_wav(wav_out)
    print("✓ (synthetic_wav — no TTS backend found)")
    return wav_out


def save_transcripts(scenarios: list[Scenario], out_dir: Path) -> None:
    """Save all scenario texts as plain .txt files alongside the audio."""
    for s in scenarios:
        txt_path = out_dir / f"{s.name}.txt"
        txt_path.write_text(s.text, encoding="utf-8")
    print(f"\n[transcripts] Saved {len(scenarios)} .txt files to {out_dir}")


def list_scenarios() -> None:
    """Print all available scenarios without generating audio."""
    print(f"\nAvailable scenarios ({len(SCENARIOS)} total):\n")
    by_category: dict[str, list[Scenario]] = {}
    for s in SCENARIOS:
        by_category.setdefault(s.category, []).append(s)

    for cat, items in sorted(by_category.items()):
        print(f"  [{cat}]")
        for s in items:
            tags = ", ".join(s.tags) if s.tags else "-"
            print(f"    {s.name:<40} tags: {tags}")
    print()



def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate synthetic emergency call audio for IMERS testing."
    )
    parser.add_argument(
        "--out", default="data/recordings",
        help="Output directory (default: data/recordings)"
    )
    parser.add_argument(
        "--backend", choices=["edge-tts", "gtts", "pyttsx3"],
        help="Force a specific TTS backend. Default: auto-detect best available."
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all scenarios without generating audio."
    )
    parser.add_argument(
        "--category", default=None,
        help="Only generate scenarios for this incident category (e.g. fire)."
    )
    parser.add_argument(
        "--transcripts-only", action="store_true",
        help="Save only .txt transcript files (no audio)."
    )
    parser.add_argument(
        "--ext", default="mp3", choices=["mp3", "wav"],
        help="Audio file extension to produce (default: mp3)."
    )
    args = parser.parse_args(argv)

    if args.list:
        list_scenarios()
        return

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    out_dir = (project_root / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = SCENARIOS
    if args.category:
        scenarios = [s for s in SCENARIOS if s.category == args.category]
        if not scenarios:
            print(f"[error] No scenarios found for category '{args.category}'")
            sys.exit(1)

    print(f"\nIMERS — Emergency Call Audio Generator")
    print(f"{'─' * 50}")
    print(f"  Output dir : {out_dir}")
    print(f"  Scenarios  : {len(scenarios)}")
    print(f"  Backend    : {args.backend or 'auto'}")
    print(f"  Extension  : {args.ext}")
    print()

    if args.transcripts_only:
        save_transcripts(scenarios, out_dir)
        return

    generated: list[Path] = []
    for scenario in scenarios:
        try:
            path = generate_audio(scenario, out_dir, backend=args.backend, extension=args.ext)
            generated.append(path)
        except Exception as e:
            print(f"  [error] {scenario.name}: {e}")

    save_transcripts(scenarios, out_dir)

    print(f"\n{'─' * 50}")
    print(f"Done! {len(generated)}/{len(scenarios)} audio files generated in:")
    print(f"  {out_dir}")
    print()
    print("Install a TTS backend if audio is missing:")
    print("  pip install edge-tts    # best quality (neural, needs internet)")
    print("  pip install gtts        # good quality (Google, needs internet)")
    print("  pip install pyttsx3     # offline (uses system voices)")


if __name__ == "__main__":
    main()
