"""Minimal internationalisation helpers.

Usage::

    from solver.i18n import t, detect_lang

    msg = t("duplicate_task_id", task_id="T001", line=42)
    msg_es = t("duplicate_task_id", lang="es", task_id="T001", line=42)

Resolution order for a requested *lang*:

1. Exact match in :data:`~solver.i18n_catalog.MESSAGES` for that key + lang.
2. Fall back to ``"en"`` if the requested lang is absent.
3. Return the bare *key* if even ``"en"`` is absent, and log a warning to
   ``stderr`` so the omission is visible without crashing.
"""

from __future__ import annotations

import logging
import os

from .i18n_catalog import MESSAGES

__all__ = ["t", "detect_lang"]

_log = logging.getLogger(__name__)

_LANG_ENV_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def detect_lang() -> str:
    """Detect the preferred language from standard POSIX environment variables.

    Returns a two-letter ISO 639-1 code (lower-case), e.g. ``"en"`` or
    ``"es"``.  Falls back to ``"en"`` when no recognised language is found.
    """
    for var in _LANG_ENV_VARS:
        raw = os.environ.get(var, "")
        if not raw:
            continue
        # LANGUAGE may be colon-separated list; take first entry.
        first = raw.split(":")[0].strip()
        if not first or first in ("C", "POSIX"):
            continue
        # Strip encoding suffix: "es_ES.UTF-8" → "es_ES", then take lang part.
        lang_part = first.split(".")[0].split("_")[0].lower()
        if lang_part and lang_part.isalpha():
            return lang_part
    return "en"


def t(key: str, *, lang: str = "en", **kwargs: object) -> str:
    """Look up *key* in the message catalog and interpolate *kwargs*.

    Parameters
    ----------
    key:
        Snake_case message key (e.g. ``"duplicate_task_id"``).
    lang:
        Two-letter language code.  Defaults to ``"en"``.
    **kwargs:
        Named placeholders forwarded to :meth:`str.format`.

    Returns
    -------
    str
        Translated and interpolated message string.
    """
    entry = MESSAGES.get(key)
    if entry is None:
        _log.warning("i18n key %r not found; returning key as-is", key)
        return key

    template = entry.get(lang) or entry.get("en")
    if template is None:
        _log.warning(
            "i18n key %r has no translation for lang=%r or 'en'; returning key",
            key, lang,
        )
        return key

    if not kwargs:
        return template

    try:
        return template.format(**kwargs)
    except (KeyError, ValueError) as exc:
        _log.warning("i18n formatting error for key=%r: %s", key, exc)
        return template
