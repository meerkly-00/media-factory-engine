"""
Orchestrateur principal du briefing matinal.

Engine partage - utilise par plusieurs projets clients (Presto, LE BUZZER, etc.).
Le `project_root` est resolu dans l'ordre :
  1. Parametre explicite `project_root` passe a run().
  2. Variable d'environnement `PROJECT_ROOT`.
  3. Repertoire de travail courant (cwd).
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .aggregate import aggregate
from .generate import generate_script, load_system_prompt, load_recent_context, save_context, _format_date_fr, _MOIS
from .tts import generate_audio
from .feed import add_episode, prune_old_episodes

logger = logging.getLogger(__name__)


def _resolve_project_root(explicit=None):
    """Resout la racine du projet client.

    Ordre : argument explicite > env PROJECT_ROOT > cwd.
    """
    if explicit:
        return Path(explicit).resolve()
    env_root = os.getenv("PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def _audio_url(filename):
    audio_base = os.getenv("AUDIO_BASE_URL")
    if audio_base:
        return f"{audio_base.rstrip('/')}/{filename}"
    base = os.getenv("PODCAST_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/audio/{filename}"


def run(
    date=None,
    duree_cible=None,
    since_hours=None,
    skip_tts=False,
    skip_feed=False,
    dry_run=False,
    project_root=None,
):
    project_root_p = _resolve_project_root(project_root)
    load_dotenv(project_root_p / ".env")

    if date is None:
        date = datetime.now(timezone.utc)

    duree_cible = duree_cible or int(os.getenv("BRIEFING_DUREE_CIBLE", 12))
    since_hours = since_hours or int(os.getenv("BRIEFING_FENETRE_HEURES", 24))
    keep_days   = int(os.getenv("FEED_KEEP_DAYS", 7))

    date_slug = date.strftime("%Y-%m-%d")
    scripts_dir = project_root_p / "output" / "scripts"
    audio_dir = project_root_p / "output" / "audio"
    data_dir = project_root_p / "data"

    # Cree les dossiers s'ils n'existent pas
    scripts_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    feed_path   = os.getenv("FEED_FILE",          str(project_root_p / "feed.xml"))
    config_path = os.getenv("SOURCES_FILE",       str(project_root_p / "config" / "sources.yaml"))
    prompt_path = os.getenv("SYSTEM_PROMPT_FILE", str(project_root_p / "prompts" / "system_briefing_v1.md"))
    audio_prefix        = os.getenv("AUDIO_PREFIX", "")
    context_file        = os.getenv("CONTEXT_FILE", "context.json")
    episode_title_pfx   = os.getenv("EPISODE_TITLE_PREFIX", "Presto - edition du")

    # Resolution des chemins relatifs depuis project_root
    if not Path(config_path).is_absolute():
        config_path = str(project_root_p / config_path)
    if not Path(prompt_path).is_absolute():
        prompt_path = str(project_root_p / prompt_path)
    if not Path(feed_path).is_absolute():
        feed_path = str(project_root_p / feed_path)

    result = {"date": date_slug}

    # 1. Agregation
    logger.info("=== Etape 1 : Agregation des articles ===")
    articles_xml = aggregate(config_path, since_hours=since_hours)
    result["articles_xml_len"] = len(articles_xml)

    if dry_run:
        logger.info("Mode dry-run : arret apres agregation.")
        result["articles_xml"] = articles_xml
        return result

    # 2. Contexte recent
    logger.info("=== Etape 2 : Chargement du contexte ===")
    context_recent = load_recent_context(str(data_dir), context_file=context_file)
    system_prompt = load_system_prompt(prompt_path)

    # 3. Generation du script
    logger.info("=== Etape 3 : Generation du script ===")
    script_xml = generate_script(
        articles_xml=articles_xml,
        system_prompt=system_prompt,
        date=date,
        duree_cible=duree_cible,
        context_recent=context_recent,
    )

    script_path = scripts_dir / f"{date_slug}.xml"
    script_path.write_text(script_xml, encoding="utf-8")
    result["script_path"] = str(script_path)

    save_context(script_xml, _format_date_fr(date), str(data_dir), context_file=context_file)

    if skip_tts:
        return result

    # 4. Generation audio
    logger.info("=== Etape 4 : Generation audio TTS ===")
    audio_filename = f"{audio_prefix}{date_slug}.mp3"
    audio_path = str(audio_dir / audio_filename)
    generate_audio(script_xml, audio_path, project_root=project_root_p)
    result["audio_path"] = audio_path

    if skip_feed:
        return result

    # 5. Mise a jour du flux RSS
    logger.info("=== Etape 5 : Mise a jour du flux RSS ===")
    audio_size = Path(audio_path).stat().st_size
    word_count = len(script_xml.split())
    duration_sec = int(word_count / 150 * 60)
    existing_mp3s = sorted(audio_dir.glob("*.mp3"))

    mois_fr = _MOIS[date.month - 1]
    titre_episode = f"{episode_title_pfx} {date.day} {mois_fr} {date.year}"

    add_episode(
        feed_path=feed_path,
        title=titre_episode,
        audio_url=_audio_url(audio_filename),
        audio_size_bytes=audio_size,
        script_xml=script_xml,
        pub_date=date,
        duration_sec=duration_sec,
        episode_number=len(existing_mp3s),
    )
    result["feed_path"] = feed_path

    # 6. Purge des episodes plus vieux que keep_days
    if keep_days > 0:
        logger.info("=== Etape 6 : Purge des episodes > %d jours ===", keep_days)
        pruned = prune_old_episodes(feed_path, keep_days=keep_days, now=date)
        result["pruned_episodes"] = pruned

    logger.info("=== Briefing termine. ===")
    return result
