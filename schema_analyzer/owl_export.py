from __future__ import annotations

from typing import Any


def _ttl_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def export_conceptual_model_as_owl_turtle(
    analysis: Any,
    *,
    base_iri: str = "http://arangodb.com/schema/hybrid#",
    phys_iri: str = "http://arangodb.com/schema/physical#",
) -> str:
    """
    Minimal OWL Turtle export for the conceptual schema + physical mappings.
    This is a normalized conceptual model intended to be used as the basis for:
    - LPG-like conceptual Cypher (labels + rel types)
    - SPARQL conceptual queries
    - physical mappings to guide translation to AQL
    """
    if hasattr(analysis, "model_dump"):
        data = analysis.model_dump()
    else:
        data = analysis

    cs = data.get("conceptual_schema") or data.get("conceptualSchema") or {}
    pm = data.get("physical_mapping") or data.get("physicalMapping") or {}

    entities = cs.get("entities") or []
    rels = cs.get("relationships") or []

    lines: list[str] = []
    lines.append(f"@prefix : <{base_iri}> .")
    lines.append("@prefix owl: <http://www.w3.org/2002/07/owl#> .")
    lines.append("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    lines.append(f"@prefix phys: <{phys_iri}> .")
    lines.append("")
    lines.append(": a owl:Ontology ;")
    lines.append('  rdfs:label "Conceptual Schema" ;')
    lines.append('  rdfs:comment "Conceptual schema inferred from ArangoDB physical schema." .')
    lines.append("")

    # Declare annotation properties for physical mappings (best-effort; aligns with docs/OWL-FOR-CONCEPTUAL-SCHEMA.md)
    for ap in ["mappingStyle", "collectionName", "typeField", "typeValue", "edgeCollectionName"]:
        lines.append(f"phys:{ap} a owl:AnnotationProperty .")
    lines.append("")

    # Classes
    if isinstance(entities, list):
        for e in entities:
            if not isinstance(e, dict):
                continue
            name = e.get("name")
            if not isinstance(name, str) or not name:
                continue
            iri = f":{name}"
            lines.append(f"{iri} a owl:Class ;")
            lines.append(f'  rdfs:label "{_ttl_escape(name)}" .')
            # Physical mapping annotations for entities
            mapping = (pm.get("entities") or {}).get(name) if isinstance(pm.get("entities"), dict) else None
            if isinstance(mapping, dict):
                # Re-emit as additional triples
                style = mapping.get("style")
                if style:
                    lines.append(f"{iri} phys:mappingStyle \"{_ttl_escape(str(style))}\" .")
                if mapping.get("collectionName"):
                    lines.append(f"{iri} phys:collectionName \"{_ttl_escape(str(mapping['collectionName']))}\" .")
                if mapping.get("typeField"):
                    lines.append(f"{iri} phys:typeField \"{_ttl_escape(str(mapping['typeField']))}\" .")
                if mapping.get("typeValue"):
                    lines.append(f"{iri} phys:typeValue \"{_ttl_escape(str(mapping['typeValue']))}\" .")
            lines.append("")

    # Object properties (relationships)
    if isinstance(rels, list):
        for r in rels:
            if not isinstance(r, dict):
                continue
            rtype = r.get("type")
            if not isinstance(rtype, str) or not rtype:
                continue
            from_e = r.get("fromEntity")
            to_e = r.get("toEntity")
            iri = f":{rtype}"
            lines.append(f"{iri} a owl:ObjectProperty ;")
            lines.append(f'  rdfs:label "{_ttl_escape(rtype)}" ;')
            if isinstance(from_e, str) and from_e:
                lines.append(f"  rdfs:domain :{from_e} ;")
            if isinstance(to_e, str) and to_e:
                lines.append(f"  rdfs:range :{to_e} ;")
            lines[-1] = lines[-1].rstrip(";") + " ." if lines[-1].endswith(";") else lines[-1]

            # Physical mapping annotations for relationships
            mapping = (pm.get("relationships") or {}).get(rtype) if isinstance(pm.get("relationships"), dict) else None
            if isinstance(mapping, dict):
                style = mapping.get("style")
                if style:
                    lines.append(f"{iri} phys:mappingStyle \"{_ttl_escape(str(style))}\" .")
                if mapping.get("edgeCollectionName"):
                    lines.append(f"{iri} phys:edgeCollectionName \"{_ttl_escape(str(mapping['edgeCollectionName']))}\" .")
                if mapping.get("collectionName"):
                    lines.append(f"{iri} phys:collectionName \"{_ttl_escape(str(mapping['collectionName']))}\" .")
                if mapping.get("typeField"):
                    lines.append(f"{iri} phys:typeField \"{_ttl_escape(str(mapping['typeField']))}\" .")
                if mapping.get("typeValue"):
                    lines.append(f"{iri} phys:typeValue \"{_ttl_escape(str(mapping['typeValue']))}\" .")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"

