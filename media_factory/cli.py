"""
CLI partagé pour les projets clients.

Le projet client peut soit appeler `run_from_cli()` depuis son `run.py`,
soit installer la commande console `media-factory` (déclarée dans pyproject.toml).

Usage côté projet client :

    # run.py
    from media_factory.cli import run_from_cli

    if __name__ == "__main__":
        run_from_cli()

Le project_root est auto-détecté comme cwd, donc lancer `python run.py` depuis
la racine du projet client est suffisant.
"""

import argparse
import http.server
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)


def _serve(directory: str, port: int = 8000) -> None:
    os.chdir(directory)
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None  # Silence les logs HTTP

    with http.server.HTTPServer(("", port), handler) as httpd:
        print(f"\nServeur démarré sur http://localhost:{port}")
        print(f"Feed RSS :       http://localhost:{port}/feed.xml")
        print(f"Fichiers audio : http://localhost:{port}/output/audio/")
        print("Ctrl-C pour arrêter.\n")
        httpd.serve_forever()


def run_from_cli(argv: list[str] | None = None) -> None:
    """Point d'entrée CLI réutilisable.

    Le projet client appelle simplement run_from_cli() depuis son run.py local.
    Le project_root est automatiquement détecté comme le cwd.
    """
    parser = argparse.ArgumentParser(
        description="Pipeline de briefing audio automatisé (media-factory engine).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", help="Date au format YYYY-MM-DD (défaut : aujourd'hui)")
    parser.add_argument("--duree", type=int, help="Durée cible en minutes (défaut : env BRIEFING_DUREE_CIBLE ou 12)")
    parser.add_argument("--fenetre", type=int, help="Fenêtre d'agrégation en heures (défaut : 24)")
    parser.add_argument("--no-tts", action="store_true", help="Ne pas générer l'audio")
    parser.add_argument("--no-feed", action="store_true", help="Ne pas mettre à jour le feed RSS")
    parser.add_argument("--dry-run", action="store_true", help="Agrégation seulement, sans API LLM ni TTS")
    parser.add_argument("--serve", action="store_true", help="Démarre un serveur HTTP local")
    parser.add_argument("--port", type=int, default=8000, help="Port du serveur HTTP (défaut : 8000)")
    parser.add_argument("--project-root", help="Racine explicite du projet client (défaut : cwd)")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else Path.cwd().resolve()

    if args.serve:
        _serve(str(project_root), args.port)
        return

    date = None
    if args.date:
        try:
            date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Erreur : format de date invalide '{args.date}'. Utilise YYYY-MM-DD.")
            sys.exit(1)

    # Import ici pour que --serve fonctionne sans les dépendances installées
    from .pipeline import run

    result = run(
        date=date,
        duree_cible=args.duree,
        since_hours=args.fenetre,
        skip_tts=args.no_tts,
        skip_feed=args.no_feed,
        dry_run=args.dry_run,
        project_root=project_root,
    )

    print("\n=== Résultat ===")
    for key, val in result.items():
        if key == "articles_xml":
            print(f"  articles_xml      : {len(val)} caractères")
        elif key == "articles_xml_len":
            print(f"  articles_xml_len  : {val} caractères")
        else:
            print(f"  {key:<18}: {val}")


if __name__ == "__main__":
    run_from_cli()
