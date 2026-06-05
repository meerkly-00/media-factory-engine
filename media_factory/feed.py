"""
Gestion du flux RSS podcast.
Produit un feed.xml compatible iTunes/Spotify.
"""

import email.utils
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, parse as parse_xml, ElementTree
from xml.dom import minidom

logger = logging.getLogger(__name__)

_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


def _register_namespaces():
    import xml.etree.ElementTree as ET
    ET.register_namespace("itunes", _ITUNES_NS)
    ET.register_namespace("content", _CONTENT_NS)


def _create_channel(title, description, base_url, author, artwork_url):
    _register_namespaces()
    rss = Element("rss", {"version": "2.0"})
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = title
    SubElement(channel, "description").text = description
    SubElement(channel, "language").text = "fr-CA"
    SubElement(channel, "link").text = base_url
    SubElement(channel, f"{{{_ITUNES_NS}}}author").text = author
    owner = SubElement(channel, f"{{{_ITUNES_NS}}}owner")
    SubElement(owner, f"{{{_ITUNES_NS}}}name").text = author
    SubElement(owner, f"{{{_ITUNES_NS}}}email").text = os.getenv("PODCAST_OWNER_EMAIL", "prestopodcast@gmail.com")
    SubElement(channel, f"{{{_ITUNES_NS}}}explicit").text = "no"
    SubElement(channel, f"{{{_ITUNES_NS}}}type").text = "episodic"
    cat = SubElement(channel, f"{{{_ITUNES_NS}}}category", {"text": "News"})
    SubElement(cat, f"{{{_ITUNES_NS}}}category", {"text": "Daily News"})
    if artwork_url:
        SubElement(channel, f"{{{_ITUNES_NS}}}image", {"href": artwork_url})
        img = SubElement(channel, "image")
        SubElement(img, "url").text = artwork_url
        SubElement(img, "title").text = title
        SubElement(img, "link").text = base_url
    return rss


def _load_or_create(feed_path):
    if Path(feed_path).exists():
        _register_namespaces()
        return parse_xml(feed_path).getroot()
    base_url = os.getenv("PODCAST_BASE_URL", "http://localhost:8000")
    artwork_url = os.getenv("PODCAST_ARTWORK_URL", f"{base_url}/artwork.jpg")
    return _create_channel(
        title=os.getenv("PODCAST_TITLE", "Presto"),
        description=os.getenv("PODCAST_DESCRIPTION", "Briefing matinal francophone quebecois genere par IA."),
        base_url=base_url,
        author=os.getenv("PODCAST_AUTHOR", "Presto"),
        artwork_url=artwork_url,
    )


def _extract_chapter_list(script_xml):
    titles = re.findall(r'<chapitre titre="([^"]+)">', script_xml)
    return ", ".join(titles) if titles else ""


def _serialize_feed(rss, feed_path):
    raw = minidom.parseString(
        __import__("xml.etree.ElementTree", fromlist=["tostring"]).tostring(rss, encoding="unicode")
    ).toprettyxml(indent="  ", encoding=None)
    lines = [l for l in raw.splitlines() if l.strip()]
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def add_episode(feed_path, title, audio_url, audio_size_bytes, script_xml, pub_date, duration_sec, episode_number=None):
    rss = _load_or_create(feed_path)
    channel = rss.find("channel")
    if channel is None:
        raise ValueError("Flux RSS corrompu : element <channel> introuvable.")

    chapters = _extract_chapter_list(script_xml)
    description = f"Au menu : {chapters}" if chapters else title

    item = Element("item")
    SubElement(item, "title").text = title
    SubElement(item, "description").text = description
    SubElement(item, "pubDate").text = email.utils.format_datetime(pub_date)
    SubElement(item, "guid", {"isPermaLink": "false"}).text = audio_url
    SubElement(item, "enclosure", {
        "url": audio_url,
        "type": "audio/mpeg",
        "length": str(audio_size_bytes),
    })
    SubElement(item, f"{{{_ITUNES_NS}}}duration").text = str(duration_sec)
    SubElement(item, f"{{{_ITUNES_NS}}}summary").text = description
    if episode_number is not None:
        SubElement(item, f"{{{_ITUNES_NS}}}episode").text = str(episode_number)
    artwork_url = os.getenv("PODCAST_ARTWORK_URL", "")
    if artwork_url:
        SubElement(item, f"{{{_ITUNES_NS}}}image", {"href": artwork_url})

    # Dé-duplication : retire tout item existant ayant le même guid (même date/épisode)
    # pour éviter les doublons quand un épisode est (re)généré le même jour.
    for existing in list(channel.findall("item")):
        g = existing.find("guid")
        if g is not None and g.text == audio_url:
            channel.remove(existing)

    first_item = channel.find("item")
    if first_item is not None:
        channel.insert(list(channel).index(first_item), item)
    else:
        channel.append(item)

    _serialize_feed(rss, feed_path)
    logger.info("Feed mis a jour : %s", feed_path)


def prune_old_episodes(feed_path, keep_days=7, now=None):
    """Supprime du flux RSS les items dont pubDate > keep_days. Retourne nombre retires."""
    if not Path(feed_path).exists():
        return 0
    _register_namespaces()
    rss = parse_xml(feed_path).getroot()
    channel = rss.find("channel")
    if channel is None:
        return 0
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=keep_days)
    removed = 0
    for item in list(channel.findall("item")):
        pub = item.find("pubDate")
        if pub is None or not pub.text:
            continue
        try:
            dt = email.utils.parsedate_to_datetime(pub.text)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            channel.remove(item)
            removed += 1
    if removed:
        _serialize_feed(rss, feed_path)
        logger.info("Purge feed : %d episode(s) > %d jours retire(s).", removed, keep_days)
    return removed
