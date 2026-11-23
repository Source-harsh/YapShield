"""Local candidate detection with conservative alias resolution."""

import logging
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from itertools import islice
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from spacy.language import Language


class Evidence(NamedTuple):
    source: str
    rule: str


class Mention(NamedTuple):
    start: int
    end: int
    kind: str
    source: str
    evidence: tuple[Evidence, ...] = ()


MAX_CANDIDATES = 750
LOGGER = logging.getLogger(__name__)
MODEL = "en_core_web_sm"
NAME_WORD = r"[^\W\d_]+(?:[-’'][^\W\d_]+)*\.?"
NAME_CONTEXT = re.compile(
    rf"(?:\b(?i:(?<!product )(?<!file )(?<!project )name|contact|author|patient)[ \t]*:"
    rf"|(?<!\w)(?:Mr|Mrs|Ms|Dr)\.)"
    rf"[ \t]*(?P<value>{NAME_WORD}(?:[ \t]+{NAME_WORD}){{1,3}})"
)
EMAIL_USER = r"[\w+-]+(?:\.[\w+-]+)*"
DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
DOT = r"(?:\.|[ \t]*(?:\[dot\]|\(dot\))[ \t]*)"
NANP_AREA = r"(?:\([2-9][0-9]{2}\)|[2-9][0-9]{2})"
NANP_LOCAL = rf"{NANP_AREA}[ .-]?[2-9][0-9]{{2}}[ .-]?[0-9]{{4}}"
NANP_DOMESTIC = rf"(?:\([2-9][0-9]{{2}}\)[ .-]?|[2-9][0-9]{{2}}[ .-])[2-9][0-9]{{2}}[ .-][0-9]{{4}}"
SSN = r"(?!000|666|9[0-9]{2})[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}"
STREET = (
    r"[0-9]{1,6}[ \t]+(?:[\w’'-]+[ \t]+){1,6}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way)\b\.?"
    r"(?:[ \t]+(?:Apt\.?|Suite|Unit)[ \t]+[A-Z0-9-]+)?"
)
US_CITY_ZIP = r"(?:[ \t]*,?[ \t]*\n[ \t]*|[ \t]*,[ \t]*)[^\W\d_]+(?:[ \t]+[^\W\d_]+){0,3},?[ \t]+(?-i:[A-Z]{2})[ \t]+[0-9]{5}(?:-[0-9]{4})?"


@dataclass(frozen=True)
class Rule:
    kind: str
    explanation: str
    pattern: re.Pattern[str]
    source: str = "Pattern"


def identifier_rule(kind: str, label: str) -> Rule:
    return Rule(kind, "Identifier follows a matching field label", re.compile(
        rf"\b(?:{label})[ \t]*[:=#][ \t]*(?P<value>(?=[A-Z0-9_-]{{2,32}}(?![\w-]))"
        r"(?!(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}-[0-9]{2}-[0-9]{4})(?![\w-]))"
        r"(?=[A-Z0-9_-]*[0-9])[A-Z0-9]+(?:[-_][A-Z0-9]+)*)(?![\w-]|\.[\w])",
        re.IGNORECASE,
    ), "Context rule")


RULES = (
    Rule("Email", "Standard email syntax", re.compile(
        rf"(?<![\w.+@-]){EMAIL_USER}@(?:{DOMAIN_LABEL}\.)+[a-z]{{2,63}}(?![\w@-]|\.[\w-])", re.I)),
    Rule("Email", "Email with bracketed or parenthesized at/dot markers", re.compile(
        rf"(?<![\w.+@-]){EMAIL_USER}[ \t]*(?:\[at\]|\(at\))[ \t]*"
        rf"(?:{DOMAIN_LABEL}{DOT})+[a-z]{{2,63}}(?![\w@-]|\.[\w-])", re.I)),
    Rule("Phone", "International phone with +1, +44 or +91 country code", re.compile(
        rf"(?<![\w+])(?:\+1[ .-]?{NANP_LOCAL}|\+44[ .-]?(?:[127][0-9](?:[ .-]?[0-9]){{8}})"
        r"|\+91[ .-]?[6-9](?:[ .-]?[0-9]){9})(?![\w-]|[ .][0-9])")),
    Rule("Phone", "Formatted North American phone number", re.compile(
        rf"(?<![\w+.-])(?<!\+1 )(?:1[ .-])?{NANP_DOMESTIC}(?![\w-]|[ .][0-9])")),
    Rule("Phone", "Compact North American number after a phone label", re.compile(
        r"\b(?:phone|tel|mobile)[ \t]*:[ \t]*(?P<value>[2-9][0-9]{2}[2-9][0-9]{6})(?![\w-])", re.I), "Context rule"),
    Rule("Address", "Numbered street and optional US city/state/ZIP line", re.compile(
        rf"(?<![\w-]){STREET}(?:{US_CITY_ZIP})?", re.I)),
    Rule("Address", "Post office box and optional US city/state/ZIP line", re.compile(
        rf"\bP\.?[ \t]*O\.?[ \t]+Box[ \t]+[0-9]{{1,8}}(?![\w-])(?:{US_CITY_ZIP})?", re.I)),
    Rule("US SSN", "Hyphenated US Social Security number after an SSN label", re.compile(
        rf"\b(?:US[ \t]+)?(?:SSN|Social[ \t]+Security[ \t]+(?:Number|No\.?))"
        rf"[ \t]*[:=#][ \t]*(?P<value>{SSN})(?![\w-]|\.[0-9])", re.I), "Context rule"),
    identifier_rule("Employee ID", r"employee[ \t]+(?:id|number|no\.?)"),
    identifier_rule("Account ID", r"(?:account|acct\.?)[ \t]+(?:id|number|no\.?)"),
    identifier_rule("Student ID", r"student[ \t]+(?:id|number|no\.?)"),
)


@lru_cache(maxsize=1)
def local_nlp() -> "Language | None":
    try:
        if find_spec("spacy") is None or find_spec(MODEL) is None:
            return None
        import spacy

        return spacy.load(MODEL, enable=["tok2vec", "ner"])
    except Exception as error:
        # An optional model or one of its native dependencies may be unusable.
        LOGGER.warning("Optional spaCy model could not load (%s). Using local rules.", type(error).__name__)
        return None


def candidate(start: int, end: int, kind: str, source: str, rule: str) -> Mention:
    return Mention(start, end, kind, source, (Evidence(source, rule),))


def rule_mentions(text: str) -> Iterator[Mention]:
    for rule in RULES:
        for match in islice(rule.pattern.finditer(text), MAX_CANDIDATES + 1):
            yield candidate(*match.span("value" if match.lastgroup else 0), rule.kind, rule.source, rule.explanation)
    for match in islice(NAME_CONTEXT.finditer(text), MAX_CANDIDATES + 1):
        start, end = match.span("value")
        words = list(re.finditer(NAME_WORD, text[start:end]))
        usable = []
        for word in words:
            value = word.group().rstrip(".")
            if not value[0].isupper():
                break
            usable.append(word)
            if len(value) > 1 and word.group().endswith("."):
                break
        if len(usable) >= 2 and len(usable[-1].group().rstrip(".")) > 1:
            last = usable[-1]
            yield candidate(start, start + last.start() + len(last.group().rstrip(".")), "Person",
                            "Context rule", "Capitalized name after a person label or title")


def fuse(mentions: list[Mention]) -> list[Mention]:
    spans: dict[tuple[int, int, str], Mention] = {}
    for mention in mentions:
        key = (mention.start, mention.end, mention.kind)
        existing = spans.get(key)
        if existing is None:
            spans[key] = mention
        else:
            evidence = tuple(dict.fromkeys((*existing.evidence, *mention.evidence)))
            spans[key] = existing._replace(evidence=evidence)
    return list(spans.values())


def detect_mentions(text: str, *, use_nlp: bool = True) -> list[Mention]:
    mentions = list(rule_mentions(text))
    structured = [item for item in mentions if item.kind != "Person"]
    nlp = local_nlp() if use_nlp else None
    if nlp is not None:
        for entity in nlp(text).ents:
            if entity.label_ == "PERSON" and len(entity.text.split()) >= 2 and not any(
                item.start <= entity.start_char and entity.end_char <= item.end for item in structured
            ):
                mentions.append(candidate(entity.start_char, entity.end_char, "Person", "spaCy", f"{MODEL} PERSON entity"))
    mentions = fuse(mentions)
    if len(mentions) > MAX_CANDIDATES:
        raise ValueError("Too many candidates. Review the document in smaller sections.")
    return mentions


def detect(text: str, *, use_nlp: bool = True) -> list[list[Mention]]:
    """Keep the Phase 2 detector and its evaluation interface available."""
    people: dict[str, list[Mention]] = {}
    groups: dict[tuple[str, str], list[Mention]] = {}
    mentions = detect_mentions(text, use_nlp=use_nlp)
    for mention in mentions:
        key = text[mention.start:mention.end].casefold()
        target = people.setdefault(key, []) if mention.kind == "Person" else groups.setdefault((mention.kind, key), [])
        target.append(mention)
    for name in people:
        groups[("Person", name)] = people[name]

    aliases: dict[str, set[str]] = {}
    for name in people:
        first, last = name.split()[0], name.split()[-1]
        short_forms = {part for part in (first, last) if len(part) >= 2 and not part.endswith(".")}
        initial = unicodedata.normalize("NFD", first[0])[0]
        initial_forms = {f"{letter}{dot} {last}" for letter in {initial, first[0]} for dot in ("", ".")}
        for alias in {name, *short_forms, *initial_forms}:
            aliases.setdefault(alias, set()).add(name)
    if aliases:
        pattern = re.compile(
            r"(?<![\w@])(?:" + "|".join(map(re.escape, sorted(aliases, key=lambda item: (-len(item), item))))
            + r")(?![\w@])", re.IGNORECASE
        )
        for count, match in enumerate(pattern.finditer(text), start=1):
            if count > MAX_CANDIDATES:
                raise ValueError("Too many name mentions. Review the document in smaller sections.")
            key = match.group().casefold()
            owners = aliases.get(key, set())
            if any(item.start <= match.start() and match.end() <= item.end for item in mentions):
                continue
            if " " not in key and not match.group()[0].isupper():
                continue
            if len(owners) == 1:
                name = next(iter(owners))
                people[name].append(candidate(*match.span(), "Person", "Unique name match",
                                              "Name variant has one matching full name in the document"))
            elif len(owners) > 1:
                groups.setdefault(("Ambiguous name", key), []).append(
                    candidate(*match.span(), "Ambiguous name", "Shared name match",
                              "Name variant matches multiple full names in the document")
                )
    result = [sorted(group) for group in groups.values()]
    if sum(map(len, result)) > MAX_CANDIDATES:
        raise ValueError("Too many candidates. Review the document in smaller sections.")
    return sorted(result, key=lambda group: group[0].start)
