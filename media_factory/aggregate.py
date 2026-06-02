"""
Agrégation des articles RSS des dernières N heures.
Produit un dump XML dans le format attendu par le prompt système.
"""

import logging
import re
import socket
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

import feedparser
import requests
import trafilatura
import yaml

FEED_TIMEOUT = 15  # secondes par feed

logger = logging.getLogger(__name__)


def _parse_time(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _fetch_full_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return text or ""
    except Exception as e:
        logger.debug("trafilatura a échoué sur %s : %s", url, e)
        return ""


def _entry_text(entry, min_length: int, url: str | None) -> str:
    for attr in ("content", "summary_detail", "summary"):
        val = getattr(entry, attr, None)
        if val:
            text = val[0].value if attr == "content" else (val.value if hasattr(val, "value") else val)
            text = re.sub(r"<[^>]+>", " ", text).strip()
            if len(text) >= min_length:
                return text
            # Si pas assez long mais no_scrape, accepte quand même ce qu'on a
            if url is None and len(text) > 30:
                return text
    if url:
        return _fetch_full_text(url)
    return ""


def fetch_feed(feed_cfg: dict, since: datetime, max_articles: int, min_text_length: int) -> list[dict]:
    url = feed_cfg["url"]
    source = feed_cfg["source"]
    region = feed_cfg.get("region", "International")
    theme = feed_cfg.get("theme", "Politique")
    no_scrape = feed_cfg.get("no_scrape", False)

    logger.info("Fetch %s ...", url)
    try:
        # feedparser n'a pas de timeout natif — on passe par requests
        resp = requests.get(url, timeout=FEED_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
    except Exception as e:
        logger.warning("Impossible de lire le feed %s : %s", url, e)
        return []

    articles = []
    for entry in parsed.entries[:max_articles * 2]:
        pub = _parse_time(entry)
        if pub and pub < since:
            continue

        link = getattr(entry, "link", "")
        titre = getattr(entry, "title", "Sans titre")
        texte = _entry_text(entry, min_text_length, link if not no_scrape else None)

        if not texte:
            logger.debug("Texte vide pour %s, article ignoré", link)
            continue

        articles.append({
            "source": source,
            "date": pub.isoformat() if pub else datetime.now(timezone.utc).isoformat(),
            "region": region,
            "theme": theme,
            "titre": titre,
            "texte": texte[:4000],  # plafond par article pour maîtriser la taille du contexte
            "url": link,
        })

        if len(articles) >= max_articles:
            break

    logger.info("%d articles retenus depuis %s", len(articles), source)
    return articles


def articles_to_xml(articles: list[dict]) -> str:
    root = Element("articles")
    for a in articles:
        art = SubElement(root, "article")
        for key in ("source", "date", "region", "theme", "titre", "texte", "url"):
            el = SubElement(art, key)
            el.text = a.get(key, "")

    raw = tostring(root, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)


def aggregate(config_path: str, since_hours: int = 24) -> str:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    min_len = cfg.get("min_text_length", 200)
    max_per = cfg.get("max_per_feed", 10)

    all_articles: list[dict] = []
    for feed_cfg in cfg.get("feeds", []):
        try:
            all_articles.extend(fetch_feed(feed_cfg, since, max_per, min_len))
        except Exception as e:
            logger.error("Erreur sur le feed %s : %s", feed_cfg.get("url"), e)

    all_articles.sort(key=lambda a: a["date"], reverse=True)
    logger.info("Total articles agrégés : %d", len(all_articles))
    return articles_to_xml(all_articles)
