"""Conservative local checks on the final extractive summary."""

import re

from .detection import detect_mentions
from .documents import canonical, validate

DIRECT = re.compile(
    r"[^\s@]+@[^\s@]+|\b[\w.+-]+\s*(?:\[at\]|\(at\)|\bat\b)\s*[\w-]+\s*"
    r"(?:\.|\[dot\]|\(dot\)|\bdot\b)\s*[a-z]{2,}\b"
    r"|(?<!\w)\+?\(?\d(?:[(). -]*\d){6,}(?!\w)"
    r"|\b(?:passport|national|government|tax|employee|student|account)\s+(?:id|number|no\.?)[\s:#=]+\w*\d",
    re.I,
)
STRONG_QUASI = re.compile(
    r"\b(?:date of birth|dob|born on|birthplace)\b|\b\d{1,3}[- ](?:years?[- ]old|year-old)\b"
    r"|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"
)
QUASI = (
    re.compile(r"\b(?:age[ :]+\d{1,3}|aged \d{1,3}|elderly|teenager)\b"),
    re.compile(r"\b(?:zip(?: code)?|post(?:al )?code|hometown|resides|lives in|resident of)\b|\b\d{5}\b"),
    re.compile(r"\b(?:occupation|job title|employer|works at|department|school|university|"
               r"engineer|teacher|nurse|physician|director|manager|analyst)\b"),
    re.compile(r"\b(?:gender|ethnicity|nationality|female|male|nonbinary|citizenship)\b"),
    re.compile(r"\b(?:19\d{2}|20\d{2}|january|february|march|april|june|july|august|september|"
               r"october|november|december)\b"),
)
# Conservative fallback for unlabelled capitalized names when optional NER is unavailable.
NAME_PAIR = re.compile(r"(?<!\w)(?=([^\W\d_]+)\.?[ \t]+([^\W\d_]{2,})(?!\w))")


def check_summary(summary: str, source: str, blocked: set[str]) -> None:
    if not isinstance(summary, str):
        raise ValueError("Summary must be text.")
    if not summary:
        return
    for excerpt in summary.split("\n\n"):
        if not excerpt or (excerpt not in source and (not excerpt.endswith("…") or excerpt[:-1] not in source)):
            raise ValueError("Summary contains text outside the sanitized source.")
    validate(summary, blocked)
    visible = summary.replace("[REDACTED]", " ")
    normalized = canonical(visible)
    suspected_name = any(first[0].isupper() and last[0].isupper() for first, last in NAME_PAIR.findall(visible))
    if (detect_mentions(visible) or DIRECT.search(normalized) or suspected_name
            or STRONG_QUASI.search(normalized) or sum(bool(pattern.search(normalized)) for pattern in QUASI) >= 2):
        raise ValueError("Summary privacy verification failed.")
