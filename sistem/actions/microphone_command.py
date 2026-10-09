"""Exact local microphone commands, including fragmented Turkish transcripts."""
import re
import unicodedata


def microphone_command(text):
    """Return the requested muted state, or None for ordinary conversation."""
    value = unicodedata.normalize("NFKD", str(text).casefold().replace("ı", "i"))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z]", "", value)
    # ASR can stretch a sound ("Mi krofonuu kapat"). None of the exact
    # command spellings below needs doubled letters. Keep whole-utterance
    # matching so suffixes such as "kapatma" never become a close command.
    value = re.sub(r"([a-z])\1+", r"\1", value)
    if value.startswith("jarvis"):
        value = value[len("jarvis"):]
    if value.endswith("jarvis"):
        value = value[:-len("jarvis")]
    if value in {"mikrofonacil", "mikrofonac", "mikrofonuac"}:
        return False
    if value in {"mikrofonkapan", "mikrofonkapat", "mikrofonukapat"}:
        return True
    return None
