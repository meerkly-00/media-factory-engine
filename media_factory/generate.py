"""
Génération du script de briefing via l'API Claude.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _format_date_fr(dt: datetime) -> str:
    jour = _JOURS[dt.weekday()]
    mois = _MOIS[dt.month - 1]
    return f"{jour} {dt.day} {mois} {dt.year}"


def load_system_prompt(prompt_path: str) -> str:
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


def load_recent_context(data_dir: str, n: int = 3, context_file: str = "context.json") -> str:
    path = Path(data_dir) / context_file
    if not path.exists():
        return ""
    with open(path, encoding="utf-8") as f:
        entries: list[dict] = json.load(f)
    entries = entries[-n:]
    if not entries:
        return ""
    lines = []
    for e in entries:
        lines.append(f"=== Briefing du {e['date']} ===\n{e['summary']}")
    return "\n\n".join(lines)


def _extract_chapter_summaries(script_xml: str) -> str:
    chapters = re.findall(r'<chapitre titre="([^"]+)">(.*?)</chapitre>', script_xml, re.DOTALL)
    summaries = []
    for title, body in chapters:
        sentences = [s.strip() for s in body.strip().split(".") if s.strip()]
        excerpt = ". ".join(sentences[:3]) + "."
        if len(excerpt) > 300:
            excerpt = excerpt[:297] + "..."
        summaries.append(f"- {title} : {excerpt}")
    return "\n".join(summaries)


def save_context(script_xml: str, date_fr: str, data_dir: str, context_file: str = "context.json") -> None:
    path = Path(data_dir) / context_file
    entries: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    summary = _extract_chapter_summaries(script_xml)
    entries.append({"date": date_fr, "summary": summary})
    entries = entries[-10:]  # garde 10 briefings max
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def generate_script(
    articles_xml: str,
    system_prompt: str,
    date: datetime | None = None,
    duree_cible: int = 12,
    context_recent: str = "",
    model: str | None = None,
) -> str:
    if date is None:
        date = datetime.now()
    date_fr = _format_date_fr(date)
    model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    user_parts = [
        f"Date : {date_fr}",
        f"Durée cible : {duree_cible} minutes",
    ]
    if context_recent:
        user_parts.append(f"Contexte récent (résumé des derniers briefings) :\n{context_recent}")

    user_parts.append(articles_xml)
    user_message = "\n\n".join(user_parts)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    logger.info("Appel Claude %s ...", model)
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    script = message.content[0].text
    logger.info(
        "Script généré : ~%d mots, ~%.0f min de lecture",
        len(script.split()),
        len(script.split()) / 150,
    )
    return script


# ── Mode DIALOGUE (2 voix) ──────────────────────────────────────────────────
# Convertit un script monologue en dialogue radio à deux animateurs.
# Opt-in : utilisé seulement quand EPISODE_MODE=dialogue (voir tts.generate_audio).

_DIALOGUE_SYSTEM_PROMPT = """Tu transformes un script de bulletin sportif (monologue) en DIALOGUE radio \
à deux animateurs québécois.

Les deux voix :
- F = l'animateur : énergique, lance les sujets, pose des questions courtes, fait les \
transitions, réagit ("Han!", "Voyons donc!", "Wow").
- S = l'analyste : développe, donne les faits, les chiffres et les sources, donne son \
opinion ("mon take").

Règles STRICTES :
- Conserve TOUS les faits, noms, chiffres et sources ("selon La Presse", "selon Sportsnet"…) \
du script source. N'invente RIEN, n'ajoute aucune info absente.
- Style parlé québécois naturel : phrases courtes, vraies réactions, tours INÉGAUX (l'animateur \
lance court, l'analyste développe). Pas de ping-pong 50/50 mécanique.
- Emploie les termes FRANÇAIS du sport (blanchissage, prolongation, avantage numérique, \
repêchage), jamais l'équivalent anglais.
- NE dis JAMAIS de méta du genre "les détails ne sont pas dans les sources" : si une info \
manque, on n'en parle simplement pas.
- Pas de fausse citation d'un seul mot ("je le cite … fin de citation"). Cite seulement de \
vraies phrases complètes.
- Garde la MÊME structure de segments que le source, avec les MÊMES titres.
- L'animateur ouvre par "Sept heures, t'es au Buzzer!" et termine par une formule chaleureuse \
type "C'était le Buzzer. Bonne journée, pis lâchez-pas!".

Réponds UNIQUEMENT avec ce format, rien d'autre :
<dialogue>
<seg titre="Introduction">
F: ...
S: ...
</seg>
<seg titre="Titre du chapitre">
F: ...
S: ...
</seg>
</dialogue>"""


def generate_dialogue(
    script_xml: str,
    model: str | None = None,
    system_prompt: str | None = None,
) -> str:
    """2e appel Claude : convertit le script monologue en dialogue 2 voix.

    Le prompt peut être surchargé via le fichier DIALOGUE_PROMPT_FILE (env).
    """
    model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    if system_prompt is None:
        prompt_file = os.getenv("DIALOGUE_PROMPT_FILE")
        if prompt_file and Path(prompt_file).exists():
            system_prompt = Path(prompt_file).read_text(encoding="utf-8")
        else:
            system_prompt = _DIALOGUE_SYSTEM_PROMPT

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    logger.info("Appel Claude %s (conversion dialogue) ...", model)
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": script_xml}],
    )
    dialogue = message.content[0].text.strip()
    logger.info("Dialogue généré : ~%d caractères", len(dialogue))
    return dialogue
