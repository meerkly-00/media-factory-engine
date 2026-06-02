# media-factory-engine

Engine partagé pour les podcasts/revues audio générés automatiquement. Sert de base technique à plusieurs médias (Presto, LE BUZZER, …) qui se distinguent par leur configuration (sources, prompts, branding) tout en partageant la même infrastructure (agrégation RSS, génération de script via Claude, TTS, flux RSS podcast).

## Installation

Depuis un autre projet :

```bash
pip install git+https://github.com/meerkly-00/media-factory-engine.git@main
```

Ou en mode dev local :

```bash
git clone https://github.com/meerkly-00/media-factory-engine.git
cd media-factory-engine
pip install -e .
```

## Usage côté projet client

Un projet média est constitué de :

```
mon-podcast/
├── config/
│   └── sources.yaml          # feeds RSS
├── prompts/
│   └── system.md             # prompt LLM
├── output/                   # scripts + audio (gitignored sauf scripts/)
├── data/                     # contexte récent
├── .env                      # clés API + config
├── feed.xml                  # flux RSS du podcast
└── run.py
```

Le `run.py` du projet client est minimal :

```python
from media_factory.cli import run_from_cli

if __name__ == "__main__":
    run_from_cli()
```

Le `.env` du projet client expose les variables nécessaires :

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
PODCAST_TITLE=Mon Podcast
PODCAST_BASE_URL=https://mon-podcast.com
SOURCES_FILE=config/sources.yaml
SYSTEM_PROMPT_FILE=prompts/system.md
TTS_PROVIDER=openai
TTS_VOICE=onyx
BRIEFING_DUREE_CIBLE=12
```

Variables disponibles : voir `media_factory/pipeline.py`.

## API publique

```python
from media_factory import run

result = run(
    date=None,                # datetime, défaut now
    duree_cible=None,         # minutes, défaut env BRIEFING_DUREE_CIBLE
    since_hours=None,         # heures, défaut env BRIEFING_FENETRE_HEURES
    skip_tts=False,
    skip_feed=False,
    dry_run=False,
    project_root=None,        # Path du projet client, défaut cwd
)
```

Renvoie un dict avec `script_path`, `audio_path`, `feed_path`, `articles_xml_len`, etc.

## Modules

| Module | Rôle |
|---|---|
| `media_factory.aggregate` | Agrégation RSS, extraction de texte via trafilatura |
| `media_factory.generate` | Appel Claude pour générer le script XML |
| `media_factory.tts` | Conversion script → MP3 via OpenAI TTS ou edge-tts |
| `media_factory.feed` | Génération/maj du flux RSS podcast |
| `media_factory.pipeline` | Orchestration des étapes |
| `media_factory.cli` | Entrée CLI réutilisable |

## Variables d'environnement attendues

| Var | Défaut | Rôle |
|---|---|---|
| `ANTHROPIC_API_KEY` | (requise) | Clé Claude pour génération script |
| `OPENAI_API_KEY` | (requise si TTS_PROVIDER=openai) | Clé OpenAI TTS |
| `CLAUDE_MODEL` | claude-sonnet-4-6 | Modèle LLM |
| `TTS_PROVIDER` | openai si OPENAI_API_KEY sinon edge | TTS backend |
| `TTS_MODEL` | tts-1 | Modèle OpenAI TTS |
| `TTS_VOICE` | onyx (openai) / fr-CA-AntoineNeural (edge) | Voix |
| `BRIEFING_DUREE_CIBLE` | 12 | Durée cible en minutes |
| `BRIEFING_FENETRE_HEURES` | 24 | Fenêtre d'agrégation en heures |
| `FEED_KEEP_DAYS` | 7 | Rétention des épisodes dans le flux |
| `SOURCES_FILE` | config/sources.yaml | Fichier YAML des sources RSS |
| `SYSTEM_PROMPT_FILE` | prompts/system_briefing_v1.md | Fichier prompt système |
| `FEED_FILE` | feed.xml | Fichier flux RSS |
| `CONTEXT_FILE` | context.json | Contexte récent (dans data/) |
| `AUDIO_PREFIX` | (vide) | Préfixe des fichiers MP3 |
| `EPISODE_TITLE_PREFIX` | Presto — édition du | Préfixe titre d'épisode |
| `PODCAST_TITLE` | Presto | Titre du flux |
| `PODCAST_DESCRIPTION` | … | Description du flux |
| `PODCAST_BASE_URL` | http://localhost:8000 | URL de base |
| `PODCAST_AUTHOR` | Presto | Auteur |
| `PODCAST_OWNER_EMAIL` | prestopodcast@gmail.com | Email owner iTunes |
| `PODCAST_ARTWORK_URL` | $BASE_URL/artwork.jpg | URL de la cover |
| `AUDIO_BASE_URL` | (vide) | Hôte des MP3 (GitHub Releases ou autre) |
| `PROJECT_ROOT` | cwd | Racine du projet client |

## Tests

```bash
pip install -e ".[dev]"
pytest tests/
```

## Versioning

Suit semver. Les médias clients pinnent une version exacte ou un range :

```
# requirements.txt côté client
media-factory @ git+https://github.com/meerkly-00/media-factory-engine.git@v0.1.0
```

## Licence

Proprietary. Tous droits réservés.
