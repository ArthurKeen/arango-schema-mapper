# Proposed PRD patch — denormalization detection (ArangoDB adapter of a shared, cross-project taxonomy)

**Target:** `arango-schema-analyzer/docs/PRD.md`
**Status:** PROPOSED — requires acceptance via `/prd-sync` (per CLAUDE.md: PRD patches are never auto-applied)

**Companions (this is deliberately cross-project):**
- `r2g/docs/internal/PLAN-denormalization-analysis.md` (r2g PRD "Phase 11") — the
  relational-side design this converges with. The `DenormFinding` model and the
  detector taxonomy **originate there**; this proposal adopts them rather than inventing a parallel set.
- `relational-schema-analyzer` (RSA) — the relational twin of this project (parallel
  `fk_inference.py` / `taxonomy.py` / `discriminator.py` / `mapping.py` / `naming.py` /
  `samplers.py` / `r2rml_export.py`). The same denormalization analysis note is under review there.
- `r2g/docs/internal/PLAN-rsa-dependency-reversal.md` — records **why a literal shared
  core (dependency reversal) is DEFERRED**: r2g, RSA, and this repo have diverged in both
  directions (`types.py`, `fk_inference.py`, `connectors/*`), and a merge is a persisted-data-shape
  migration, not a refactor.
- `conceptual-taxonomy` — the paradigm-neutral optional-dependency **precedent** for how shared
  detection logic should be housed (already used by `taxonomy.py`).

---

## Motivation

Bridging the impedance mismatch between a physical store and a semantic ontology means
reverse-engineering the designer's performance optimizations back into conceptual models.
Denormalization patterns hide latent ontology structure: an embedded array is a class, a
delimited string is a set of edges, a pre-joined column group is a separate entity. This
project detects **none** of these today (verified: detectors exist for FK, taxonomy,
discriminator, VCI, sharding, multitenancy, RPT, GraphRAG — not denormalization; and the
snapshot profiles only top-level `ATTRIBUTES(d)`).

Three parallel codebases (this repo, RSA, r2g) run the same pipeline. r2g already has a
detailed Phase 11 denormalization design. The FK engine was previously **copy-ported** between
them and has since drifted three ways — the anti-pattern this proposal exists to avoid. The
goal is **convergence, not a fourth fork.**

## Scope & role (read this first)

`arango-schema-analyzer` is **read-only**: it DETECTS denormalization in an ArangoDB physical
schema and emits conceptual entities/relationships plus mapping annotations. It never shreds,
pivots, or reifies data — that is r2g's ETL job. So every pattern below reduces to the same
shape already used for `FOREIGN_KEY`/`taxonomy`:

> **detect the signal → emit a `DenormFinding` (conceptual entity/relationship + mapping
> annotation, with confidence, evidence, and a `metadata.denormalizationStatus` degradation
> flag)** that a downstream consumer (r2g, a transpiler, AOE) acts on.

## Convergence principles

1. **Adopt r2g's `DenormFinding` contract verbatim** as the cross-project vocabulary:
   `kind`, `collection`/`table`, `fields`/`columns`, `recommendedAction`, `confidence`,
   `evidence`. Sharing the *output contract* stops the result from forking even while detector
   *code* is still duplicated.
2. **Direction inverts by paradigm.** Relational→graph (r2g/RSA) **extracts a vertex** or
   **embeds an array**; ArangoDB→ontology (this repo) **un-embeds** a sub-document/array into a
   distinct class + edge for a pure ontology. Same `kind`, paradigm-specific `recommendedAction`.
3. **Paradigm-neutral algorithms go in a shared neutral lib** (the `conceptual-taxonomy`
   precedent), **not** a fourth port. Profiling and remedy-direction stay per-adapter.

## Taxonomy — shared kinds + the three this note adds

| Denormalization pattern | Shared `DenormFinding.kind` | State across the family |
|---|---|---|
| Horizontal partitioning (single-table inheritance / discriminator) | (handled separately by `discriminator` / LPG `LABEL`) | ✅ already solved in all three |
| Vertical partitioning (pre-joined column group → latent entity) | `embedded_lookup` (FD/2NF/3NF) | planned in r2g; **absent here** |
| Repeating groups (`phone1/phone2`, `addr_1..3`) | `repeating_group` | planned in r2g; **absent here** |
| Delimited multi-value string (`"12,45,89"`) | `multi_valued` | planned in r2g; **absent here** |
| Redundant reference data | `redundant_reference` | planned in r2g; **absent here** |
| 1:1 over-normalization | `one_to_one` | planned in r2g; **absent here** |
| **EAV / open schema** | **`eav`** *(new)* | **gap in the shared taxonomy** — overlaps `rdf_topology`/`TRIPLE` here |
| **Derived / aggregate attributes** | **`derived_attribute`** *(new)* | **gap in the shared taxonomy** — cheap, high ROI |
| **Temporal / SCD versioning** | **`temporal_versioning`** *(new, flag-only)* | **gap** — reification stays out of scope (AOE owns temporal, §3.13.4) |

The three additions (`eav`, `derived_attribute`, `temporal_versioning`) are contributed **back
to the shared taxonomy** — they are missing from r2g Phase 11 too.

---

## Proposed PRD patches

### Patch 1 — §3.1 Physical Schema Introspection (profiling extension) — *prerequisite*

The snapshot must capture what these detectors read. Extend `_detect_observed_fields` /
`snapshot_physical_schema` beyond top-level `ATTRIBUTES(d)` to emit, per field:
- **nested-structure descriptors** — is-array / is-object, and a one-level shape of array
  elements / embedded objects (the input for embedded-entity detection);
- **value-pattern signals** — delimiter rate + token uniformity (multi-value), aggregate-name
  match (derived), distinct-value cardinality ratios (redundant reference / EAV shape).

This profile is deliberately shaped to align with RSA's `PhysicalSchema` / `Column`, so it is
**also** progress on the shared-profile contract the deferred dependency reversal is blocked on.

### Patch 2 — §6.2 Enhanced Pattern Detection (new "Denormalization detection" family)

A deterministic, read-only, LLM-independent detector family parallel to `infer_foreign_keys`,
emitting `DenormFinding[]` and a `metadata.denormalizationStatus` (`ok` / `degraded` with a
count of unprobed candidates, per the §3.4 transparency rule). Detectors, priority order:

1. **`embedded_entity`** — *flagship, ArangoDB-native.* A document embeds a sub-document/array
   that is a latent class (`User.permissions[]` → `Permission` class + `HAS_PERMISSION` edge).
   This is the pattern more common in ArangoDB than in SQL. Needs Patch 1's nested profiling.
2. **`derived_attribute`** — *cheapest win.* Name + value heuristics (`total_*`, `*_count`,
   `last_*`, `*_ltv`) flag attributes as observations/aggregates, not intrinsic properties, so
   they are annotated rather than modeled as first-class.
3. **`multi_valued`** — delimited scalar → split/dedup → multi-value relationship.
4. **`redundant_reference` / vertical-partition** — co-varying column cluster / FD →
   latent entity. **Highest false-positive risk** without a FK anchor; ships behind a confidence gate.
5. **`eav`** — extend `rdf_topology.py` (EAV is near-isomorphic to the SPO/`TRIPLE` shape).
6. **`temporal_versioning`** — *flag only.* Detect effective/expiration + multiple versions per
   key; reification into temporal states is deferred to AOE (§3.13.4).

### Patch 3 — §3.3 Physical Mapping vocabulary (new styles)

New relationship/entity mapping styles for the adapter, consistent with the recently added
`FOREIGN_KEY` / `JOIN_TABLE`:
- **`EMBEDDED`** — a latent class projected from an embedded sub-document/array (compiled as a
  document-path expansion + synthetic edge).
- **`MULTI_VALUE`** (a.k.a. `DELIMITED`) — a relationship carried by a split scalar.
- `derived_attribute` is a **property annotation**, not a class; `eav` is likely a
  **specialization of `TRIPLE`**, not a new top-level style.

### Patch 4 — §7 Dependencies (optional)

Add the paradigm-neutral detection lib (name TBD — see Open decisions) that houses the shared
name/value/FD heuristics; absent → denormalization detection is skipped (no failure), mirroring
`conceptual-taxonomy`.

---

## Shared-code decision (the "common code" question)

- **Now:** converge the `DenormFinding` contract + detector `kind` vocabulary (cheap; stops the
  *output* forking). Record it on both sides (this doc + a pointer in r2g Phase 11).
- **Next:** extract the paradigm-neutral heuristics (name/value/FD-candidate probing) into a
  **neutral shared lib both import** — the `conceptual-taxonomy` model — instead of porting a
  fourth copy from r2g/RSA.
- **Later / deferred:** full package sharing waits on the RSA dependency reversal, which is
  blocked for documented reasons (`types.py` divergence, persisted-shape migration).
- **Always per-adapter:** profiling (ArangoDB nested-doc walking vs SQL `Column`) and the
  remedy direction (embed vs un-embed).

## Acceptance criteria

- Each detector is deterministic, bounded, opt-in, and degrades transparently
  (`metadata.denormalizationStatus`), like `fk_inference`.
- Findings are advisory: nothing is auto-applied to the conceptual schema without the same
  confidence + evidence framing FK inference uses.
- The emitted `DenormFinding` shape is byte-compatible with r2g Phase 11's (modulo
  `recommendedAction` direction).

## Open decisions

1. **Where the neutral lib lives** — a new standalone lib, folded into `conceptual-taxonomy`, or
   into RSA once the reversal unblocks. (Recommend a new small lib now; converge into RSA later.)
2. **`EMBEDDED` as a mapping style vs. a metadata annotation** — style is more consistent with
   `FOREIGN_KEY`/`JOIN_TABLE`; annotation is lower-commitment.
3. **`temporal_versioning`: detector-flag vs. explicit non-goal** — leaning flag-only, reification to AOE.
4. **Confidence gates** for vertical-partition / `redundant_reference` (the highest false-positive detector).
