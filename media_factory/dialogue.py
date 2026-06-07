"""
Mode DIALOGUE (2 voix) avec jingles — rendu audio via ElevenLabs.

Opt-in : activé seulement quand EPISODE_MODE=dialogue. Le chemin par défaut
(monologue, edge/openai) reste inchangé pour les autres projets (ex. Presto).

Config (env, résolue relativement à project_root pour les chemins) :
  ELEVENLABS_API_KEY        clé API (obligatoire)
  ELEVENLABS_MODEL          défaut eleven_multilingual_v2
  DIALOGUE_VOICE_F / _S     IDs de voix ElevenLabs (animateur / analyste)
  DIALOGUE_VOICE_F_SETTINGS / _S_SETTINGS   JSON voice_settings (optionnel)
  JINGLE_INTRO_MUSIC        défaut assets/jingles/intro.mp3
  JINGLE_BUZZER             défaut assets/jingles/buzzer.mp3
  JINGLE_STINGER            défaut assets/jingles/stinger.mp3
  PRONUNCIATION_FILE        défaut config/pronunciation.json  (dict {mot: prononcé})
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_API = "https://api.elevenlabs.io/v1"

# IDs par défaut (voix QC validées pour LE BUZZER) — surchargeables par env.
_DEFAULT_VOICE_F = "RBhYSNMNu6b2CGZ9Fn1M"  # Felix Tabarnak (animateur)
_DEFAULT_VOICE_S = "JtIwrj5lfRwnEmUwGlhB"  # Sébastien (analyste)
_DEFAULT_SET_F = {"stability": 0.42, "similarity_boost": 0.80, "style": 0.45,
                  "use_speaker_boost": True, "speed": 1.0}
_DEFAULT_SET_S = {"stability": 0.22, "similarity_boost": 0.80, "style": 0.62,
                  "use_speaker_boost": True, "speed": 1.10}


# ── Parsing du dialogue ──────────────────────────────────────────────────────

def parse_dialogue(dialogue_xml: str) -> list[dict]:
    """<dialogue><seg titre="..">F: ..\nS: ..</seg></dialogue>
    -> [{title, lines:[(who, text)]}]."""
    segs = []
    for m in re.finditer(r'<seg titre="([^"]*)">(.*?)</seg>', dialogue_xml, re.DOTALL):
        title, body = m.group(1), m.group(2)
        lines = []
        for raw in body.splitlines():
            lm = re.match(r"\s*([FS])\s*:\s*(.+)$", raw)
            if lm:
                lines.append((lm.group(1), lm.group(2).strip()))
        if lines:
            segs.append({"title": title or "Segment", "lines": lines})
    return segs


# ── Prononciation ────────────────────────────────────────────────────────────

def load_pronunciation(project_root: Path) -> list[tuple[str, str]]:
    path = os.getenv("PRONUNCIATION_FILE", str(project_root / "config" / "pronunciation.json"))
    p = Path(path)
    if not p.is_absolute():
        p = project_root / path
    if not p.exists():
        return []
    rules = json.loads(p.read_text(encoding="utf-8"))
    # match le plus long d'abord (ex: "pole position" avant "pole")
    return sorted(rules.items(), key=lambda kv: len(kv[0]), reverse=True)


def apply_pronunciation(text: str, rules: list[tuple[str, str]]) -> str:
    for clean, spoken in rules:
        text = re.sub(rf"\b{re.escape(clean)}\b", spoken, text, flags=re.IGNORECASE)
    return text


# ── TTS ElevenLabs ───────────────────────────────────────────────────────────

def _tts_elevenlabs(text: str, path: str, voice_id: str, settings: dict, model: str) -> None:
    import requests
    r = requests.post(
        f"{_API}/text-to-speech/{voice_id}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"],
                 "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={"text": text, "model_id": model, "voice_settings": settings},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    Path(path).write_bytes(r.content)


# ── Assemblage ffmpeg ────────────────────────────────────────────────────────

def _ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _build_intro(music: str, buzzer: str, out: str) -> None:
    _ff(["-i", music, "-i", buzzer, "-filter_complex",
         "[0:a]atrim=0:6,afade=t=out:st=4.8:d=1.2,aformat=sample_rates=44100:channel_layouts=stereo[m];"
         "[1:a]adelay=300|300,volume=2dB,aformat=sample_rates=44100:channel_layouts=stereo[b];"
         "[m][b]amix=inputs=2:duration=longest:normalize=0[o]", "-map", "[o]", out])


def _build_outro(music: str, out: str) -> None:
    _ff(["-i", music, "-filter_complex",
         "[0:a]atrim=0:5,afade=t=in:st=0:d=0.4,afade=t=out:st=3.8:d=1.2,"
         "aformat=sample_rates=44100:channel_layouts=stereo[o]", "-map", "[o]", out])


def _concat_loudnorm(paths: list[str], out: str) -> None:
    inputs = []
    for p in paths:
        inputs += ["-i", p]
    n = len(paths)
    pre = "".join(f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}];"
                  for i in range(n))
    cat = "".join(f"[a{i}]" for i in range(n))
    graph = (pre + cat + f"concat=n={n}:v=0:a=1[c];"
             "[c]loudnorm=I=-16:TP=-1.5:LRA=11[o]")
    _ff([*inputs, "-filter_complex", graph, "-map", "[o]",
         "-c:a", "libmp3lame", "-qscale:a", "3", out])


# ── Point d'entrée ───────────────────────────────────────────────────────────

def generate_audio_dialogue(script_xml: str, output_path: str, project_root: Path) -> str:
    """Convertit le script en dialogue, TTS 2 voix ElevenLabs, monte avec jingles."""
    from .generate import generate_dialogue  # import tardif (évite cycle)
    from .tts import _preprocess_tts, _get_duration_ms, _add_chapter_markers

    if not os.getenv("ELEVENLABS_API_KEY"):
        raise RuntimeError("ELEVENLABS_API_KEY manquant pour le mode dialogue.")

    dialogue_xml = generate_dialogue(script_xml)
    segs = parse_dialogue(dialogue_xml)
    if not segs:
        raise ValueError("Aucun segment dans le dialogue généré.")

    model = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    vf = os.getenv("DIALOGUE_VOICE_F", _DEFAULT_VOICE_F)
    vs = os.getenv("DIALOGUE_VOICE_S", _DEFAULT_VOICE_S)
    set_f = json.loads(os.getenv("DIALOGUE_VOICE_F_SETTINGS", "")) if os.getenv("DIALOGUE_VOICE_F_SETTINGS") else _DEFAULT_SET_F
    set_s = json.loads(os.getenv("DIALOGUE_VOICE_S_SETTINGS", "")) if os.getenv("DIALOGUE_VOICE_S_SETTINGS") else _DEFAULT_SET_S
    rules = load_pronunciation(project_root)

    def _path(env_key, default_rel):
        p = Path(os.getenv(env_key, str(project_root / default_rel)))
        return p if p.is_absolute() else project_root / p

    music = _path("JINGLE_INTRO_MUSIC", "assets/jingles/intro.mp3")
    buzzer = _path("JINGLE_BUZZER", "assets/jingles/buzzer.mp3")
    stinger = _path("JINGLE_STINGER", "assets/jingles/stinger.mp3")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        intro_mix = str(tmp / "intro.mp3")
        outro_mix = str(tmp / "outro.mp3")
        _build_intro(str(music), str(buzzer), intro_mix)
        _build_outro(str(music), outro_mix)

        order = [intro_mix]
        seg_first_idx = {}  # title -> index dans `order` (pour chapitres)
        for si, seg in enumerate(segs):
            is_outro = si == len(segs) - 1
            # stinger entre les segments (sauf juste avant l'outro)
            if si > 0 and not is_outro:
                order.append(str(stinger))
            seg_first_idx[seg["title"]] = len(order)
            for li, (who, text) in enumerate(seg["lines"]):
                spoken = apply_pronunciation(_preprocess_tts(text), rules)
                vid, st = (vf, set_f) if who == "F" else (vs, set_s)
                lp = str(tmp / f"s{si:02d}_{li:02d}.mp3")
                _tts_elevenlabs(spoken, lp, vid, st, model)
                order.append(lp)
        order.append(outro_mix)

        # chapitres : début de chaque segment dans la timeline
        durs = [_get_duration_ms(p) for p in order]
        starts, cum = [], 0
        for d in durs:
            starts.append(cum)
            cum += d
        chapters = []
        for seg in segs:
            idx = seg_first_idx[seg["title"]]
            chapters.append({"title": seg["title"], "start_ms": starts[idx],
                             "end_ms": cum})
        for i in range(len(chapters) - 1):
            chapters[i]["end_ms"] = chapters[i + 1]["start_ms"]

        logger.info("Dialogue : %d segments, durée ~%.1f min", len(segs), cum / 60000)
        _concat_loudnorm(order, output_path)

    try:
        _add_chapter_markers(output_path, chapters)
    except Exception as e:  # noqa: BLE001
        logger.warning("Marqueurs de chapitre ignorés : %s", e)
    return output_path
