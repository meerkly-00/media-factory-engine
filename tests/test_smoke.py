"""
Tests fumée pour vérifier que le package s'installe et expose son API publique.
Ne teste pas les appels réseau (LLM, RSS) — ces tests-là vont dans tests/integration.
"""

import importlib

import pytest


def test_package_imports():
    """Le package racine s'importe sans erreur."""
    mod = importlib.import_module("media_factory")
    assert hasattr(mod, "run")
    assert hasattr(mod, "__version__")


def test_submodules_import():
    """Tous les modules clés s'importent."""
    for name in ("aggregate", "generate", "tts", "feed", "pipeline", "cli"):
        importlib.import_module(f"media_factory.{name}")


def test_run_signature():
    """L'API publique run() expose les bons paramètres."""
    from inspect import signature
    from media_factory import run

    sig = signature(run)
    params = set(sig.parameters.keys())
    expected = {
        "date", "duree_cible", "since_hours",
        "skip_tts", "skip_feed", "dry_run", "project_root",
    }
    assert expected.issubset(params), f"Manque : {expected - params}"


def test_resolve_project_root_cwd_default(tmp_path, monkeypatch):
    """En l'absence de tout override, project_root = cwd."""
    from media_factory.pipeline import _resolve_project_root

    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _resolve_project_root() == tmp_path.resolve()


def test_resolve_project_root_env(tmp_path, monkeypatch):
    """La variable d'env PROJECT_ROOT prime sur cwd."""
    from media_factory.pipeline import _resolve_project_root

    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    assert _resolve_project_root() == tmp_path.resolve()


def test_resolve_project_root_explicit(tmp_path, monkeypatch):
    """L'argument explicite prime sur l'env."""
    from media_factory.pipeline import _resolve_project_root

    monkeypatch.setenv("PROJECT_ROOT", "/non/existent")
    assert _resolve_project_root(tmp_path) == tmp_path.resolve()


def test_cli_help_runs(capsys):
    """`media-factory --help` ne crash pas."""
    from media_factory.cli import run_from_cli

    with pytest.raises(SystemExit) as exc:
        run_from_cli(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "media-factory" in captured.out.lower() or "pipeline" in captured.out.lower()
