"""Foreign-key inference for relationships carried by scalar attributes.

ArangoDB enforces no referential constraint, so a relationship persisted as
``Album.ArtistId -> Artist._key`` is invisible to edge-collection introspection: it
produces no relationship in the mapping and the class ends up an isolated island.

This is a **port** of ``relational_schema_analyzer.fk_inference`` (PRD §6.2), adapted from
tables/columns to collections/fields. The candidate generation, type gating, composite pass,
sampler fold, dedup and confidence model are RSA's — including its ``InferenceOptions``
defaults, which are already exercised across five value samplers. Divergences are marked
``ARANGO:`` below and are limited to what the paradigm actually changes:

* ``_id``-shaped values (``Artist/42``) name their target collection directly, which is a
  stronger signal than any name heuristic and has no relational analogue.
* ``_key`` is the default target field; relational PKs vary per table.
* Nothing is ever declared or enforced, so every result is a proposal. RSA's "unenforced FK
  is evidence, not proof" stance is unconditional here.
* Fields excluded by ``type_detection._ID_SUFFIXES`` are deliberately re-admitted. That
  exclusion is correct for discriminator detection and exactly wrong here; the two candidate
  sets are disjoint by construction.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .defaults import (
    FK_ALLOW_COMPOSITE,
    FK_GENERIC_KEY_NAMES,
    FK_MAX_CANDIDATES_PER_FIELD,
    FK_MIN_CONFIDENCE,
    FK_OVERLAP_VETO_ON_ZERO,
)
from .utils import singularize

logger = logging.getLogger(__name__)

InferenceMethod = Literal[
    "id_shape",
    "name_suffix",
    # ARANGO: camelCase/PascalCase reference names are a first-class ArangoDB convention,
    # not the sloppy fallback they are in SQL. Scored equal to the snake_case pattern.
    "camel_suffix",
    "name_no_underscore",
    "key_name_match",
    "composite",
]

#: A camel/Pascal boundary immediately before the reference suffix — `ArtistId`, `artistId`,
#: `artistID`, `ownerKey`. The capital is as deliberate a separator as an underscore, which
#: is what distinguishes these from `artistid` (no boundary, genuinely weaker evidence).
_CAMEL_REF = re.compile(r"^(?P<prefix>.*[a-z0-9])(?:Id|ID|Key|KEY)$")

#: ``sampler(local_collection, local_field, foreign_collection, foreign_field)`` returning a
#: containment ratio in [0, 1], or ``None`` for "could not evaluate" — which skips the
#: overlap signal rather than vetoing the candidate.
Sampler = Callable[[str, str, str, str], "float | None"]

_ID_VALUE = re.compile(r"^[A-Za-z0-9_\-]+/[^/\s]+$")

_COMPATIBLE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"integer", "float", "number"}),
    frozenset({"string"}),
    frozenset({"boolean"}),
)


@dataclass
class CollectionShape:
    """The slice of a physical snapshot this detector needs."""

    name: str
    #: field name → coarse type category ("string" / "integer" / "float" / "boolean" / ...)
    fields: dict[str, str] = field(default_factory=dict)
    #: Fields that identify a document. ``_key`` unless a natural unique key is known.
    key_fields: list[str] = field(default_factory=lambda: ["_key"])
    #: Fields carrying a unique index — the 1:1 signal (PRD §6.4.1).
    unique_fields: set[str] = field(default_factory=set)
    #: field name → sampled distinct values, used only for ``_id``-shape detection.
    sample_values: dict[str, list[Any]] = field(default_factory=dict)
    count: int = 0


@dataclass
class InferredForeignKey:
    collection: str
    fields: list[str]
    foreign_collection: str
    foreign_fields: list[str]
    confidence: float
    method: InferenceMethod
    evidence: list[str] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        """Render as a ``FOREIGN_KEY`` relationship mapping (PRD §3.3)."""
        return {
            "style": "FOREIGN_KEY",
            "fromCollection": self.collection,
            "fromFields": list(self.fields),
            "toCollection": self.foreign_collection,
            "toKeyFields": list(self.foreign_fields),
            # ARANGO: never conditional. ArangoDB enforces no referential constraint, so
            # every inferred relationship is evidence rather than proof.
            "enforced": False,
            "confidence": self.confidence,
            "method": self.method,
            "evidence": list(self.evidence),
        }


@dataclass
class InferenceOptions:
    """Mirrors RSA's ``InferenceOptions`` so the two can be diffed."""

    min_confidence: float = FK_MIN_CONFIDENCE
    generic_key_names: frozenset[str] = FK_GENERIC_KEY_NAMES
    max_candidates_per_field: int = FK_MAX_CANDIDATES_PER_FIELD
    allow_composite: bool = FK_ALLOW_COMPOSITE
    #: Off by default. Containment probing is the first cross-collection DB cost in this
    #: analyzer and scales with candidate count, not collection count (PRD §6.2).
    sample_overlap: bool = False
    overlap_veto_on_zero: bool = FK_OVERLAP_VETO_ON_ZERO


def infer_foreign_keys(
    collections: dict[str, CollectionShape],
    *,
    options: InferenceOptions | None = None,
    sampler: Sampler | None = None,
    existing_relationships: set[tuple[str, str]] | None = None,
) -> list[InferredForeignKey]:
    """Return ranked FK candidates, deduped and filtered by ``min_confidence``.

    ``existing_relationships`` holds ``(from_collection, to_collection)`` pairs already
    backed by an edge collection — the Arango stand-in for RSA's declared-FK skip. An
    attribute duplicating an existing edge is denormalization, not a new relationship.
    """
    opts = options or InferenceOptions()
    edges = existing_relationships or set()
    key_index = _build_key_index(collections)

    single: list[InferredForeignKey] = []
    for name, shape in sorted(collections.items()):
        for field_name in sorted(shape.fields):
            if field_name in shape.key_fields:
                # The identifying field is the referenced side, not an FK origin.
                continue
            if field_name.startswith("_"):
                continue
            single.extend(_candidates_for_field(collections, name, field_name, shape, key_index, opts, edges))

    composite: list[InferredForeignKey] = []
    if opts.allow_composite:
        composite = _find_composite_candidates(single)

    all_candidates = single + composite
    if sampler is not None and opts.sample_overlap:
        sampled = [_apply_sampler(c, sampler, opts) for c in all_candidates]
        all_candidates = [c for c in sampled if c is not None]

    ranked = sorted(_dedupe(all_candidates), key=lambda c: (-c.confidence, c.collection, c.fields))
    return [c for c in ranked if c.confidence >= opts.min_confidence]


# ── candidate generation ─────────────────────────────────────────────────────


def _build_key_index(collections: dict[str, CollectionShape]) -> dict[str, list[str]]:
    """``{normalized collection name: [collection, ...]}`` for prefix matching."""
    index: dict[str, list[str]] = {}
    for name in collections:
        for variant in _name_variants(name):
            index.setdefault(variant, []).append(name)
    return index


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _name_variants(name: str) -> set[str]:
    base = _normalize(name)
    singular = _normalize(singularize(name))
    return {base, singular, f"{singular}s", f"{singular}es"}


def _split_prefix(field_name: str) -> list[tuple[str, str, InferenceMethod, float]]:
    """``(prefix, required_key_suffix, method, base_confidence)``.

    Ported from RSA, with one ARANGO divergence: ArangoDB schemas use snake_case and
    camelCase interchangeably, so ``artistId`` is exactly as intentional a reference as
    ``artist_id`` and scores the same. RSA has no camel branch because SQL identifiers are
    conventionally snake_case, and its underscore-less pattern exists to catch sloppiness
    rather than a parallel convention.

    ``artistid`` — no separator of any kind — keeps the weak score. That is the case the
    underscore-less pattern is actually for.
    """
    out: list[tuple[str, str, InferenceMethod, float]] = []
    lowered = field_name.lower()

    if lowered.endswith("_id") and len(lowered) > 3:
        out.append((lowered[:-3], "", "name_suffix", 0.75))
    if lowered.endswith("_key") and len(lowered) > 4:
        out.append((lowered[:-4], "", "name_suffix", 0.75))

    camel = _CAMEL_REF.match(field_name)
    if camel and len(camel.group("prefix")) >= 2:
        out.append((camel.group("prefix"), "", "camel_suffix", 0.75))

    if "_" in lowered:
        prefix, _, suffix = lowered.rpartition("_")
        if prefix and suffix not in ("", "id", "key") and len(suffix) >= 2:
            out.append((prefix, suffix, "name_suffix", 0.6))

    if lowered.endswith("id") and not lowered.endswith("_id") and not camel and len(lowered) > 3 and lowered != "uuid":
        out.append((lowered[:-2], "", "name_no_underscore", 0.45))
    return out


def _candidates_for_field(
    collections: dict[str, CollectionShape],
    name: str,
    field_name: str,
    shape: CollectionShape,
    key_index: dict[str, list[str]],
    opts: InferenceOptions,
    edges: set[tuple[str, str]],
) -> list[InferredForeignKey]:
    candidates: list[InferredForeignKey] = []
    lowered = field_name.lower()

    # ARANGO: `_id`-shaped values resolve the target directly. No relational analogue, and
    # stronger than any name heuristic — the data states the target rather than implying it.
    target, ratio = _id_shape_target(shape.sample_values.get(field_name), collections)
    if target is not None:
        candidate = _make_candidate(
            collections,
            name,
            [field_name],
            target,
            ["_id"],
            method="id_shape",
            base_confidence=0.6 + 0.35 * ratio,
            evidence=[f"{ratio:.0%} of sampled values are '{target}/...' document ids"],
            edges=edges,
        )
        if candidate is not None:
            candidates.append(candidate)

    # Original casing, not `lowered` — the camel branch needs the boundary to still be there.
    for prefix, required_suffix, method, base in _split_prefix(field_name):
        for foreign in key_index.get(_normalize(prefix), []):
            if foreign == name:
                continue
            foreign_shape = collections[foreign]
            if len(foreign_shape.key_fields) != 1:
                continue
            foreign_field = foreign_shape.key_fields[0]
            if required_suffix and _normalize(required_suffix) != _normalize(foreign_field):
                continue
            candidate = _make_candidate(
                collections,
                name,
                [field_name],
                foreign,
                [foreign_field],
                method=method,
                base_confidence=base,
                evidence=[f"name pattern '{field_name}' → {foreign}.{foreign_field}"],
                edges=edges,
            )
            if candidate is not None:
                candidates.append(candidate)

    if lowered not in opts.generic_key_names:
        for foreign, foreign_shape in collections.items():
            if foreign == name or len(foreign_shape.key_fields) != 1:
                continue
            if _normalize(foreign_shape.key_fields[0]) != _normalize(field_name):
                continue
            candidate = _make_candidate(
                collections,
                name,
                [field_name],
                foreign,
                list(foreign_shape.key_fields),
                method="key_name_match",
                base_confidence=0.55,
                evidence=[f"field '{field_name}' matches the key of '{foreign}' (non-generic)"],
                edges=edges,
            )
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=lambda c: -c.confidence)
    return candidates[: opts.max_candidates_per_field]


def _id_shape_target(values: list[Any] | None, collections: dict[str, CollectionShape]) -> tuple[str | None, float]:
    """Resolve the target collection from ``Coll/key``-shaped sampled values."""
    if not values:
        return None, 0.0
    prefixes: dict[str, int] = {}
    considered = 0
    for value in values:
        if not isinstance(value, str) or not _ID_VALUE.match(value):
            continue
        considered += 1
        prefixes[value.split("/", 1)[0]] = prefixes.get(value.split("/", 1)[0], 0) + 1
    if not considered:
        return None, 0.0
    best, hits = max(prefixes.items(), key=lambda kv: kv[1])
    if best not in collections:
        return None, 0.0
    return best, hits / len(values)


def _make_candidate(
    collections: dict[str, CollectionShape],
    name: str,
    fields: list[str],
    foreign: str,
    foreign_fields: list[str],
    *,
    method: InferenceMethod,
    base_confidence: float,
    evidence: list[str],
    edges: set[tuple[str, str]],
) -> InferredForeignKey | None:
    local = collections.get(name)
    remote = collections.get(foreign)
    if local is None or remote is None:
        return None
    if (name, foreign) in edges:
        # Already reachable via an edge collection; the attribute is denormalization.
        return None

    confidence = base_confidence
    details = list(evidence)

    for local_field, foreign_field in zip(fields, foreign_fields, strict=True):
        local_type = local.fields.get(local_field)
        remote_type = remote.fields.get(foreign_field) or "string"
        if local_type is None:
            return None

        # ARANGO: `_key` and `_id` are *always* strings, whatever the value means. A numeric
        # reference pointing at them is the norm, not a mismatch — anything imported from a
        # relational source looks like this. Type-gating against them is a category error
        # and rejects every genuine candidate (found by scoring against Chinook, where it
        # produced recall 0.0). No identical-type bonus either: there is nothing to compare.
        if foreign_field in ("_key", "_id"):
            continue

        if not _types_compatible(local_type, remote_type):
            return None
        if local_type == remote_type:
            confidence += 0.1
            details.append(f"identical type '{local_type}'")
        if local_field in local.unique_fields:
            # A unique index on the referencing field is the 1:1 evidence §6.4.1 requires
            # before a FK column may be emitted instead of a junction.
            confidence += 0.05
            details.append(f"'{local_field}' carries a unique index (1:1)")

    return InferredForeignKey(
        collection=name,
        fields=list(fields),
        foreign_collection=foreign,
        foreign_fields=list(foreign_fields),
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        method=method,
        evidence=details,
    )


def _types_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in group and b in group for group in _COMPATIBLE_GROUPS)


def _find_composite_candidates(single: list[InferredForeignKey]) -> list[InferredForeignKey]:
    """Fold single-field candidates sharing a (collection, foreign_collection) pair."""
    grouped: dict[tuple[str, str], list[InferredForeignKey]] = {}
    for candidate in single:
        if candidate.method == "id_shape":
            continue
        grouped.setdefault((candidate.collection, candidate.foreign_collection), []).append(candidate)

    out: list[InferredForeignKey] = []
    for (name, foreign), members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda c: c.fields[0])
        out.append(
            InferredForeignKey(
                collection=name,
                fields=[m.fields[0] for m in members],
                foreign_collection=foreign,
                foreign_fields=[m.foreign_fields[0] for m in members],
                confidence=round(min(1.0, min(m.confidence for m in members) + 0.05), 3),
                method="composite",
                evidence=[f"composite of {len(members)} single-field candidates"],
            )
        )
    return out


def _apply_sampler(
    candidate: InferredForeignKey, sampler: Sampler, opts: InferenceOptions
) -> InferredForeignKey | None:
    """Fold measured value containment into the confidence score."""
    scores: list[float] = []
    any_zero = False
    for local_field, foreign_field in zip(candidate.fields, candidate.foreign_fields, strict=True):
        try:
            score = sampler(candidate.collection, local_field, candidate.foreign_collection, foreign_field)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "fk sampler failed for %s.%s -> %s.%s: %s",
                candidate.collection,
                local_field,
                candidate.foreign_collection,
                foreign_field,
                err,
            )
            return candidate  # sampler noise must not drop a candidate
        if score is None:
            continue
        if score <= 0.0:
            any_zero = True
        scores.append(score)

    if not scores:
        return candidate
    if any_zero and opts.overlap_veto_on_zero:
        return None

    average = sum(scores) / len(scores)
    bump = 0.15 if average >= 0.9 else 0.05 if average >= 0.5 else -0.25 if average <= 0.0 else 0.0
    return replace(
        candidate,
        confidence=round(max(0.0, min(1.0, candidate.confidence + bump)), 3),
        evidence=[*candidate.evidence, f"value containment avg={average:.2f} ({len(scores)} sampled)"],
    )


def _dedupe(candidates: list[InferredForeignKey]) -> list[InferredForeignKey]:
    best: dict[tuple[str, tuple[str, ...], str], InferredForeignKey] = {}
    for candidate in candidates:
        key = (candidate.collection, tuple(candidate.fields), candidate.foreign_collection)
        prior = best.get(key)
        if prior is None or candidate.confidence > prior.confidence:
            best[key] = candidate
    return list(best.values())
