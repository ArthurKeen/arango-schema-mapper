from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


ANALYSIS_OUTPUT_SCHEMA: dict[str, Any] = {
    "$id": "https://arangodb.com/schema-analyzer/analysis-output.schema.json",
    "type": "object",
    "additionalProperties": False,
    "required": ["conceptualSchema", "physicalMapping", "metadata"],
    "properties": {
        "conceptualSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entities", "relationships", "properties"],
            "properties": {
                "entities": {"type": "array", "items": {"type": "object"}},
                "relationships": {"type": "array", "items": {"type": "object"}},
                "properties": {"type": "array", "items": {"type": "object"}},
            },
        },
        "physicalMapping": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entities", "relationships"],
            "properties": {
                "entities": {"type": "object", "additionalProperties": {"type": "object"}},
                "relationships": {"type": "object", "additionalProperties": {"type": "object"}},
            },
        },
        "metadata": {
            "type": "object",
            "additionalProperties": True,
            "required": ["confidence", "timestamp", "analyzedCollectionCounts", "detectedPatterns"],
            "properties": {
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "timestamp": {"type": "string"},
                "analyzedCollectionCounts": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["documentCollections", "edgeCollections"],
                    "properties": {
                        "documentCollections": {"type": "number", "minimum": 0},
                        "edgeCollections": {"type": "number", "minimum": 0},
                    },
                },
                "detectedPatterns": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


_validator = Draft202012Validator(ANALYSIS_OUTPUT_SCHEMA)


def validate_analysis_output(data: dict[str, Any]) -> list[str]:
    errors = []
    for err in sorted(_validator.iter_errors(data), key=str):
        errors.append(err.message)
    return errors

