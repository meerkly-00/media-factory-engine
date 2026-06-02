"""
Conversion du script XML en MP3 avec marqueurs de chapitre ID3.
Provider : OpenAI TTS (tts-1 ou tts-1-hd) si OPENAI_API_KEY défini,
           sinon edge-tts (gratuit, Microsoft Edge).
Dépendances système : ffmpeg et ffprobe dans le PATH.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from mutagen.id3 import ID3, CHAP, CTOC, CTOCFlags, TIT2, ID3NoHeaderError

logger = logging.getLogger(__name__)


# ── Normalisation du texte avant TTS ────────────────────────────────────────

def _normalize_numbers_fr(text: str) -> str:
    """Convertit les nombres en mots français pour un meilleur rendu TTS."""
    try:
        from num2words import num2words
    except ImportError:
        return text

    def _n2w(n: int | float, is_ordinal: bool = False) -> str:
        try:
            return num2words(n, lang="fr", to="ordinal" if is_ordinal else "cardinal")
        except Exception:
            return str(n)

    # Pourcentages : "3,5 %" ou "3.5%" → "trois virgule cinq pourcent"
    def _replace_pct(m: re.Match) -> str:
        raw = m.group(1).replace(",", ".").replace(" ", "")
        try:
            val = float(raw)
            int_part = int(val)
            dec = round(val - int_part, 6)
            if dec == 0:
                return f"{_n2w(int_part)} pourcent"
            dec_str = f"{dec:.6f}".rstrip("0").split(".")[1]
            dec_words = " ".join(_n2w(int(d)) for d in dec_str)
            return f"{_n2w(int_part)} virgule {dec_words} pourcent"
        except Exception:
            return m.group(0)

    text = re.sub(r"([\d][\d\s,\.]*)\s*%", _replace_pct, text)

    # Années 1900-2099 → lues comme un nombre entier (ex: "2026" → "deux mille vingt-six")
    text = re.sub(r"\b(1[89]\d{2}|20[0-9]{2})\b",
                  lambda m: _n2w(int(m.group(1))), text)

    # Grands nombres avec espaces comme séparateurs : "1 200 000"
    text = re.sub(r"\b(\d{1,3}(?:\s\d{3})+)\b",
                  lambda m: _n2w(int(m.group(1).replace(" ", ""))), text)

    # Nombres décimaux simples : "3,5" ou "3.5"
    text = re.sub(r"\b(\d+)[,\.](\d+)\b", lambda m: (
        f"{_n2w(int(m.group(1)))} virgule {' '.join(_n2w(int(d)) for d in m.group(2))}"
    ), text)

    # Entiers restants (≥ 1000 pour éviter de sur-convertir)
    text = re.sub(r"\b(\d{4,})\b", lambda m: _n2w(int(m.group(1))), text)

    return text


def _preprocess_tts(text: str) -> str:
    """Pré-traitement complet du texte avant envoi au TTS."""
    # Remplacer les abréviations courantes
    replacements = {
        r"\bM\.\s": "Monsieur ",
        r"\bMme\.\s": "Madame ",
        r"\bMM\.\s": "Messieurs ",
        r"\bDr\.\s": "Docteur ",
        r"\bPr\.\s": "Professeur ",
        r"\bSt\.\s": "Saint ",
        r"\bÉ\.-U\.\b": "États-Unis",
        r"\bU\.S\.\b": "États-Unis",
        r"\bG7\b": "G sept",
        r"\bG20\b": "G vingt",
        r"\bONU\b": "O-N-U",
        r"\bFMI\b": "F-M-I",
        r"\bFBI\b": "F-B-I",
        r"\bCIA\b": "C-I-A",
        r"\bPIB\b": "P-I-B",
        r"\bPNB\b": "P-N-B",
        r"\bPQ\b": "Parti Québécois",
        r"\bCAQ\b": "Coalition Avenir Québec",
        r"\$\s*([\d\s,\.]+)\s*[Mm]ards?\b": r"\1 milliards de dollars",
        r"\$\s*([\d\s,\.]+)\s*[Mm](?:illions?)?\b": r"\1 millions de dollars",
        r"\$\s*([\d\s,\.]+)\b": r"\1 dollars",
    }
    for pattern, repl in replacements.items():
        text = re.sub(pattern, repl, text)

    text = _normalize_numbers_fr(text)
    return text


def _strip_xml_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_script(script_xml: str) -> list[dict]:
    segments = []
    intro_m = re.search(r"<intro>(.*?)</intro>", script_xml, re.DOTALL)
    if intro_m:
        segments.append({"label": "intro", "title": "Introduction", "text": _strip_xml_tags(intro_m.group(1))})
    for m in re.finditer(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL):
        segments.append({"label": "chapitre", "title": m.group(1), "text": _strip_xml_tags(m.group(2))})
    outro_m = re.search(r"<outro>(.*?)</outro>", script_xml, re.DOTALL)
    if outro_m:
        segments.append({"label": "outro", "title": "Conclusion", "text": _strip_xml_tags(outro_m.group(1))})
    return segments


_OPENAI_TTS_LIMIT = 4096


def _split_text_chunks(text: str, max_chars: int = _OPENAI_TTS_LIMIT) -> list[str]:
    """Split text into chunks ≤ max_chars, breaking at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip() if current else sentence
        else:
            if current:
                chunks.append(current)
            # sentence itself may exceed limit — split by word
            if len(sentence) > max_chars:
                words, buf = sentence.split(), ""
                for w in words:
                    if len(buf) + len(w) + 1 <= max_chars:
                        buf = (buf + " " + w).strip() if buf else w
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = w
                current = buf
            else:
                current = sentence
    if current:
        chunks.append(current)
    return chunks


def _tts_openai(text: str, path: str, voice: str, model: str) -> None:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Optional instructions for gpt-4o-mini-tts (steering tone)
    instructions = os.getenv("TTS_INSTRUCTIONS", "").strip()
    extra = {"instructions": instructions} if instructions and "gpt-4o" in model else {}

    chunks = _split_text_chunks(text)
    if len(chunks) == 1:
        response = client.audio.speech.create(model=model, voice=voice, input=chunks[0], response_format="mp3", **extra)
        response.stream_to_file(path)
        return
    # multiple chunks - generate each, then concat with ffmpeg
    with tempfile.TemporaryDirectory() as tmpdir:
        chunk_paths = []
        for i, chunk in enumerate(chunks):
            chunk_path = str(Path(tmpdir) / f"chunk_{i:03d}.mp3")
            response = client.audio.speech.create(model=model, voice=voice, input=chunk, response_format="mp3", **extra)
            response.stream_to_file(chunk_path)
            chunk_paths.append(chunk_path)
        list_file = str(Path(tmpdir) / "chunks.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in chunk_paths:
                f.write(f"file '{Path(p).as_posix()}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", path],
            check=True, capture_output=True,
        )


async def _tts_edge_async(text: str, path: str, voice: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)


def _tts_edge(text: str, path: str, voice: str) -> None:
    asyncio.run(_tts_edge_async(text, path, voice))


def _tts_segment(text: str, path: str) -> None:
    text = _preprocess_tts(text)
    provider = os.getenv("TTS_PROVIDER", "openai" if os.getenv("OPENAI_API_KEY") else "edge")
    if provider == "openai":
        model = os.getenv("TTS_MODEL", "tts-1")
        voice = os.getenv("TTS_VOICE", "onyx")
        _tts_openai(text, path, voice, model)
    else:
        voice = os.getenv("TTS_VOICE", "fr-CA-AntoineNeural")
        _tts_edge(text, path, voice)


def _get_duration_ms(audio_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True, text=True, check=True,
    )
    return int(float(json.loads(result.stdout)["format"]["duration"]) * 1000)


def _concat_audio(segment_paths: list[str], output_path: str) -> None:
    """Concatène les segments et normalise le volume à -16 LUFS (standard podcast EBU R128)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        concat_file = f.name
        for p in segment_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")

    tmp_concat = output_path + ".tmp.mp3"
    try:
        # Étape 1 : concat sans re-encodage
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", tmp_concat],
            check=True, capture_output=True,
        )
        # Étape 2 : normalisation loudness EBU R128 (-16 LUFS, -1.5 dBTP)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_concat,
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=none",
                "-codec:a", "libmp3lame", "-qscale:a", "3",  # VBR ~190 kbps
                output_path,
            ],
            check=True, capture_output=True,
        )
        logger.info("Loudness normalisé à -16 LUFS (EBU R128).")
    finally:
        os.unlink(concat_file)
        if os.path.exists(tmp_concat):
            os.unlink(tmp_concat)


def _add_chapter_markers(mp3_path: str, chapters: list[dict]) -> None:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
    chapter_ids = []
    for i, ch in enumerate(chapters):
        cid = f"chp{i}"
        chapter_ids.append(cid)
        tags.add(CHAP(element_id=cid, start_time=ch["start_ms"], end_time=ch["end_ms"],
                      start_offset=0xFFFFFFFF, end_offset=0xFFFFFFFF,
                      sub_frames=[TIT2(encoding=3, text=[ch["title"]])]))
    tags.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                  child_element_ids=chapter_ids, sub_frames=[TIT2(encoding=3, text=["Table des matières"])]))
    tags.save(mp3_path, v2_version=3)


def generate_audio(script_xml: str, output_path: str) -> str:
    segments = parse_script(script_xml)
    if not segments:
        raise ValueError("Aucun segment trouvé dans le script XML.")

    with tempfile.TemporaryDirectory() as tmpdir:
        segment_paths = []
        for i, seg in enumerate(segments):
            seg_path = str(Path(tmpdir) / f"seg_{i:03d}.mp3")
            logger.info("TTS %d/%d : %s ...", i + 1, len(segments), seg["title"])
            _tts_segment(seg["text"], seg_path)
            segment_paths.append(seg_path)
            if i < len(segments) - 1:
                time.sleep(1)

        chapter_data, cumulative_ms = [], 0
        for seg, seg_path in zip(segments, segment_paths):
            dur = _get_duration_ms(seg_path)
            chapter_data.append({"title": seg["title"], "start_ms": cumulative_ms, "end_ms": cumulative_ms + dur})
            cumulative_ms += dur

        logger.info("Durée totale : %.0f min", cumulative_ms / 60000)
        _concat_audio(segment_paths, output_path)

    _add_chapter_markers(output_path, chapter_data)
    return output_path
