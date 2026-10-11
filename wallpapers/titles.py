"""Turning upload file names into titles a person would write.

Files arrive named like ``ronin_at_dusk-LeniltonSL [9c0ae833] (1).mp4``. The
app shows the title on a card, so the hashes, brackets, separators and
resolution tags have to go, and the result has to fit in 40 characters.
"""

import os
import re

TITLE_MAX_LENGTH = 40
FALLBACK_TITLE = "Wallpaper"

_BRACKETED = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
# A run of 6+ hex characters containing at least one digit: upload hashes,
# never real words.
_HEX_TOKEN = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{6,}\b", re.IGNORECASE)
_RESOLUTION = re.compile(
    r"\b(\d{3,4}\s*[x×]\s*\d{3,4}|4k|8k|uhd|fhd|qhd|hd|\d{3,4}p|\d{2}fps)\b", re.IGNORECASE
)
_COPY_SUFFIX = re.compile(r"\b(copy|final|edit(ed)?)\b\s*\d*$", re.IGNORECASE)
_SEPARATORS = re.compile(r"[_\-.+~=]+")
_SPACES = re.compile(r"\s+")


def title_from_filename(filename: str) -> str:
    """``beach sunset.jpg`` -> ``Beach Sunset``; junk stripped, max 40 chars."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return clean_title(stem, from_filename=True)


def clean_title(raw: str, *, from_filename: bool = False) -> str:
    text = raw or ""
    if from_filename:
        text = _SEPARATORS.sub(" ", text)
    text = _BRACKETED.sub(" ", text)
    text = _HEX_TOKEN.sub(" ", text)
    if from_filename:
        text = _RESOLUTION.sub(" ", text)
        text = _COPY_SUFFIX.sub(" ", text.strip())
    text = _SPACES.sub(" ", text).strip(" -_,.;:|")
    if from_filename and text and (text.islower() or text.isupper()):
        text = text.title()
    return shorten(text) or FALLBACK_TITLE


def shorten(text: str, limit: int = TITLE_MAX_LENGTH) -> str:
    """Cut at a word boundary so a title never ends mid-word."""
    text = _SPACES.sub(" ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut and text[limit] != " ":
        cut = cut[: cut.rfind(" ")]
    return cut.strip(" -_,.;:|")
