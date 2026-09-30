"""The NC tone pool: a local folder of outcome recordings.

Not-connected calls have no real audio. The sheets point at tone files such as
``D:\\TestRec\\test\\Ringing.mp3`` -- a path from the machine that produced the
export, which almost never exists here. This module resolves those references
against a local folder by filename, so the recorded outcome (Ringing vs Busy vs
Switch) is preserved rather than being randomly reassigned.
"""

import os
import random

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

# What a row's Recording_Path resolved to.
URL = "url"          # an http(s) link -> download it
TONE = "tone"        # a local pool file -> copy it
UNRESOLVED = "none"  # blank, or names a tone the pool does not have


def list_tones(folder):
    """Every audio file in the pool folder, sorted. [] if the folder is gone."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [
        os.path.join(folder, n)
        for n in names
        if n.lower().endswith(AUDIO_EXTENSIONS)
    ]


def index(folder):
    """{lowercased filename: full path} for matching a sheet reference."""
    return {os.path.basename(p).lower(): p for p in list_tones(folder)}


def basename(value):
    """Last path segment of a Windows or POSIX path."""
    return str(value).replace("\\", "/").rstrip("/").split("/")[-1]


def label(path):
    """'Ringing.mp3' -> 'Ringing'"""
    return os.path.splitext(os.path.basename(path))[0]


def resolve(value, tone_index):
    """Work out how a Recording_Path should be turned into a file.

    Returns (kind, target). `target` is a URL, a local path, or None.
    Matching is by filename and case-insensitive, so the sheet's Switch.mp3
    finds switch.mp3 on disk.
    """
    if value is None:
        return UNRESOLVED, None

    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return UNRESOLVED, None

    if text.lower().startswith(("http://", "https://")):
        return URL, text

    hit = tone_index.get(basename(text).lower())
    return (TONE, hit) if hit else (UNRESOLVED, None)


def pick(tones):
    """A random tone, for rows that carry no outcome at all."""
    if not tones:
        raise ValueError(
            "The NC tone folder has no audio files. Point Settings at the "
            "folder holding Ringing.mp3, Busy.mp3 and the rest."
        )
    return random.choice(tones)
