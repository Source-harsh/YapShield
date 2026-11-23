"""Document-local identity proposals. A proposal never establishes identity."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import product
from typing import Literal

from .detection import MAX_CANDIDATES, Mention, candidate

TITLE = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof"}
COMMON_WORDS = {"april", "august", "bill", "hope", "june", "mark", "may", "park", "rose", "summer", "will"}
PRONOUNS = {"he", "her", "him", "his", "i", "it", "she", "their", "them", "they", "we", "you"}
TOKEN = re.compile(r"(?<![\w@])[^\W\d_]+(?:[’‘ʼ'\-‐‑][^\W\d_]+)*(?![\w@])")
PUNCTUATION = str.maketrans("’‘ʼ‐‑", "'''--")
NEARBY = 500
Relationship = Literal["primary", "exact_name", "first_name", "initials", "honorific_surname", "surname"]


def normalize(token: str) -> str:
    return unicodedata.normalize("NFKC", token).translate(PUNCTUATION).casefold()


def name_parts(value: str) -> tuple[str, ...]:
    words = tuple(normalize(match.group()) for match in TOKEN.finditer(value))
    return words[1:] if words and words[0] in TITLE else words


def initial(value: str) -> str:
    return unicodedata.normalize("NFD", value)[0]


def mention_id(mention: Mention) -> str:
    return f"{mention.start}:{mention.end}:{mention.kind}"


@dataclass(frozen=True)
class Link:
    mention: Mention
    relationship: Relationship
    evidence: str


@dataclass
class Cluster:
    primary: Mention
    alternates: list[Link] = field(default_factory=list)
    relationship: Relationship = "primary"
    possible_primaries: tuple[Mention, ...] = ()
    evidence: str = "Detected person mention"


def windows(text: str) -> Iterator[tuple[int, int, tuple[str, ...], tuple[str, ...]]]:
    """Read adjacent source tokens with exact offsets, including forms like R.J."""
    tokens = list(TOKEN.finditer(text))
    for index, first in enumerate(tokens):
        originals = []
        parts = []
        previous = first
        for pos in range(index, min(index + 5, len(tokens))):
            current = tokens[pos]
            if pos != index:
                gap = text[previous.end():current.start()]
                dotted = (len(originals[-1]) == 1 or parts[-1] in TITLE) and (
                    gap.startswith(".") and (len(gap) == 1 or gap[1:].isspace()) and "\n" not in gap
                )
                if not dotted and not (gap and gap.isspace()):
                    break
            originals.append(current.group())
            parts.append(normalize(current.group()))
            yield first.start(), current.end(), tuple(parts), tuple(originals)
            previous = current


def initials_keys(parts: tuple[str, ...]) -> set[tuple[str, ...]]:
    first, *middle, surname = parts
    keys = {(initial(first), surname), (first[0], surname)}
    if len(parts) <= 5:
        for leading in product(*({part[0], initial(part)} for part in (first, *middle))):
            keys.update({(*leading, surname), ("".join(leading), surname)})
            if middle:
                keys.add((first, *leading[1:], surname))
    return keys - {parts}


def overlaps(start: int, end: int, mention: Mention) -> bool:
    return start < mention.end and mention.start < end


def infer_primaries(text: str, detected: list[Mention]) -> list[Mention]:
    """Seed unlabelled two-word names only with a separate dotted initial reference."""
    initial_positions: dict[tuple[str, str], list[int]] = defaultdict(list)
    for start, end, parts, originals in windows(text):
        if (len(parts) == 2 and len(parts[0]) == 1 and originals[0].isupper()
                and originals[1][0].isupper() and text[start + len(originals[0])] == "."
                and not any(overlaps(start, end, item) for item in detected if item.kind != "Person")):
            initial_positions[parts].append(start)
    if not initial_positions:
        return []
    inferred = []
    for start, end, parts, originals in windows(text):
        if (len(parts) == 2 and all(len(part) > 1 and part not in TITLE for part in parts)
                and all(word[0].isupper() and not word.isupper() for word in originals)
                and "\n" not in text[start:end]
                and any(abs(start - position) <= NEARBY for position in initial_positions.get((initial(parts[0]), parts[1]), ()))
                and not any(overlaps(start, end, item) for item in detected)):
            inferred.append(candidate(start, end, "Person", "Identity context", "Name has a nearby dotted initial and surname reference"))
            if len(inferred) + len(detected) > MAX_CANDIDATES:
                raise ValueError("Too many candidates. Review the document in smaller sections.")
    return inferred


def resolve(text: str, detected: list[Mention]) -> tuple[list[list[Mention]], dict[Mention, Cluster]]:
    detected = sorted([*detected, *infer_primaries(text, detected)])
    groups: dict[tuple[str, str], list[Mention]] = {}
    clusters: dict[Mention, Cluster] = {}
    full_names: dict[tuple[str, ...], list[Mention]] = defaultdict(list)
    person_spans: dict[tuple[int, int], Mention] = {}
    for item in detected:
        value = text[item.start:item.end]
        if item.kind != "Person":
            groups.setdefault((item.kind, value.casefold()), []).append(item)
            continue
        person_spans[item.start, item.end] = item
        parts = name_parts(value)
        if len(parts) >= 2 and len(parts[0]) > 1 and len(parts[-1]) > 1:
            full_names[parts].append(item)
        clusters[item] = Cluster(item)

    # A model can return an initialed variant as a full PERSON span. When an
    # expanded form provides an exact initials key, preserve the raw span as
    # evidence for a proposal rather than using it as another full-name anchor.
    abbreviated = {key for parts in full_names for key in initials_keys(parts)}
    for parts in full_names.keys() & abbreviated:
        first, *middle, _ = parts
        if any(len(part) == 1 for part in middle):
            del full_names[parts]
        elif len(first) <= 4 and not middle:
            anchors = [item for item in full_names[parts]
                       if not text[item.start:item.end].split()[0].isupper()]
            if anchors:
                full_names[parts] = anchors
            else:
                del full_names[parts]

    if not full_names:
        return sorted([*groups.values(), *([item] for item in clusters)], key=lambda group: group[0].start), clusters

    # Repeated full names count as distinct anchors, even if only one was labeled or tagged by NER.
    for start, end, parts, _ in windows(text):
        if parts not in full_names or any(overlaps(start, end, item) for item in detected):
            continue
        mention = candidate(start, end, "Person", "Identity context", "Exact normalized full-name occurrence")
        detected.append(mention)
        if len(detected) > MAX_CANDIDATES:
            raise ValueError("Too many candidates. Review the document in smaller sections.")
        person_spans[start, end] = mention
        full_names[parts].append(mention)
        clusters[mention] = Cluster(mention)

    primaries = {person for people in full_names.values() for person in people}

    indexes: dict[Relationship, dict[tuple[str, ...], list[Mention]]] = {
        name: defaultdict(list) for name in ("exact_name", "initials", "honorific_surname", "first_name", "surname")
    }
    for parts, people in full_names.items():
        indexes["exact_name"][parts].extend(people)
        indexes["first_name"][parts[:1]].extend(people)
        indexes["surname"][parts[-1:]].extend(people)
        indexes["honorific_surname"][parts[-1:]].extend(people)
        for key in initials_keys(parts):
            indexes["initials"][key].extend(people)
        if len(people) > 1:
            for person in people:
                clusters[person] = Cluster(person, relationship="exact_name",
                                           possible_primaries=tuple(item for item in people if item != person),
                                           evidence="Identical normalized full names may describe different people")

    # Prefer the longest recognized occurrence at a start, including a title when present.
    matches: dict[int, tuple[int, Relationship, list[Mention], bool]] = {}
    for start, end, parts, originals in windows(text):
        eligible = (
            ("exact_name", parts), ("honorific_surname", parts[1:] if len(parts) == 2 and parts[0] in TITLE else ()),
            ("initials", parts), ("first_name", parts if parts[0] not in PRONOUNS else ()),
            ("surname", parts if parts[0] not in PRONOUNS else ()),
        )
        for relation, key in eligible:
            owners = indexes[relation].get(key, [])
            if not owners:
                continue
            if relation in {"first_name", "surname"} and not originals[0][0].isupper():
                continue
            if relation == "initials" and not originals[0][0].isupper():
                continue
            if relation == "honorific_surname" and not originals[1][0].isupper():
                continue
            if relation == "first_name" and indexes["surname"].get(key):
                owners = sorted(set(owners + indexes["surname"][key]))
                relation = "surname"
            matches[start] = (end, relation, owners, parts[0] in COMMON_WORDS)
            break

    consumed_until = 0
    for start, (end, relation, owners, common) in sorted(matches.items()):
        if start < consumed_until:
            continue
        raw = person_spans.get((start, end))
        if any(item != raw and overlaps(start, end, item) for item in detected):
            continue
        if raw in primaries:
            continue
        consumed_until = end
        owner = owners[0]
        nearby = min(abs(start - owner.end), abs(end - owner.start)) <= NEARBY
        can_propose = (len(owners) == 1 and nearby and not common and relation in {"initials", "honorific_surname", "first_name"}
                       and (relation != "first_name" or start >= owner.end))
        if can_propose:
            explanations = {
                "first_name": "Capitalized first name follows one nearby compatible full name",
                "initials": "Initials and surname match one nearby full name",
                "honorific_surname": "Honorific and surname match one nearby full name",
            }
            evidence = explanations[relation]
            mention = raw or candidate(start, end, "Person", "Identity context", evidence)
            if raw is not None:
                del clusters[raw]
            clusters[owner].alternates.append(Link(mention, relation, evidence))
        else:
            if len(owners) > 1:
                evidence = "Multiple person mentions match this name form"
            elif relation == "exact_name":
                evidence = "An exact full-name match may describe a different person"
            elif relation == "surname":
                evidence = "A surname alone does not establish identity"
            elif common:
                evidence = "This word can also occur in ordinary text"
            else:
                evidence = "Insufficient nearby context to link this mention"
            kind = "Person" if relation == "exact_name" else "Ambiguous name"
            mention = raw or candidate(start, end, kind, "Identity context", evidence)
            clusters[mention] = Cluster(mention, relationship=relation, possible_primaries=tuple(owners), evidence=evidence)

    result = [*groups.values(), *([cluster.primary, *(link.mention for link in cluster.alternates)] for cluster in clusters.values())]
    if sum(map(len, result)) > MAX_CANDIDATES:
        raise ValueError("Too many candidates. Review the document in smaller sections.")
    return sorted(result, key=lambda group: min(item.start for item in group)), clusters
