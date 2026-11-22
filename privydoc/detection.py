"""Local candidate detection with conservative alias resolution."""

import re
from functools import lru_cache
from importlib.util import find_spec
from itertools import islice
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from spacy.language import Language


class Mention(NamedTuple):
    start: int
    end: int
    kind: str
    source: str


NAME_WORD = r"[A-Z][a-z]+(?:[-’'][A-Z]?[a-z]+)*"
NAME = rf"{NAME_WORD}(?:[ \t]+{NAME_WORD}){{1,2}}"
PATTERNS = {
    "Email": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "Phone": r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\w)",
    "US SSN": r"(?<!\w)\d{3}-\d{2}-\d{4}(?!\w)",
    "Person": rf"(?m)\b(?:[Nn]ame|[Cc]ontact|[Aa]uthor|[Pp]atient)[ \t]*:[ \t]*(?P<value>{NAME})|\b(?:Mr|Mrs|Ms|Dr)\.[ \t]+(?P<titled>{NAME})",
}
MAX_CANDIDATES = 750


@lru_cache(maxsize=1)
def local_nlp() -> "Language | None":
    if find_spec("spacy") is None or find_spec("en_core_web_sm") is None:
        return None
    import spacy

    return spacy.load("en_core_web_sm")


def detect(text: str) -> list[list[Mention]]:
    people: dict[str, list[Mention]] = {}
    groups: dict[tuple[str, str], list[Mention]] = {}
    for kind, pattern in PATTERNS.items():
        for match in islice(re.finditer(pattern, text), MAX_CANDIDATES + 1):
            group = match.lastgroup or 0
            start, end = match.span(group)
            key = text[start:end].casefold()
            mention = Mention(start, end, kind, "Context rule" if kind == "Person" else "Pattern")
            target = people.setdefault(key, []) if kind == "Person" else groups.setdefault((kind, key), [])
            target.append(mention)
    nlp = local_nlp()
    if nlp is not None:
        for entity in nlp(text).ents:
            if entity.label_ == "PERSON" and len(entity.text.split()) >= 2:
                people.setdefault(entity.text.casefold(), []).append(
                    Mention(entity.start_char, entity.end_char, "Person", "spaCy")
                )
    for name in people:
        groups[("Person", name)] = people[name]

    aliases: dict[str, set[str]] = {}
    for name in people:
        first, last = name.split()[0], name.split()[-1]
        for alias in {name, first, last, f"{first[0]}. {last}"}:
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
            if len(owners) == 1:
                name = next(iter(owners))
                existing = people[name]
                if not any(item.start == match.start() and item.end == match.end() for item in existing):
                    existing.append(Mention(*match.span(), "Person", "Unique name match"))
            elif len(owners) > 1:
                groups.setdefault(("Ambiguous name", key), []).append(
                    Mention(*match.span(), "Ambiguous name", "Shared name match")
                )
    result = [sorted({(item.start, item.end): item for item in group}.values()) for group in groups.values()]
    if sum(map(len, result)) > MAX_CANDIDATES:
        raise ValueError("Too many candidates. Review the document in smaller sections.")
    return sorted(result, key=lambda group: group[0].start)
