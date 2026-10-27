"""Routing seed schema; prose examples are intentionally not supplied by Codex."""

from __future__ import annotations

from training.phase2.protocol import ABSTAIN, PHENOMENA, REASONS, SPLITS


def record_schema(capabilities: list[str]) -> dict:
    identifier = {"type": "string", "minLength": 1, "maxLength": 200}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "version",
            "id",
            "text",
            "label",
            "reason",
            "split",
            "semantic_family",
            "template_family",
            "paraphrase_family",
            "phenomena",
            "provenance",
        ],
        "properties": {
            "schema": {"const": "blux.routing-record"},
            "version": {"const": 1},
            "id": identifier,
            "text": {"type": "string", "minLength": 1, "maxLength": 8000},
            "label": {"enum": sorted(capabilities) + [ABSTAIN]},
            "reason": {"enum": ["route", *REASONS]},
            "split": {"enum": list(SPLITS)},
            "semantic_family": identifier,
            "template_family": {"anyOf": [identifier, {"type": "null"}]},
            "paraphrase_family": {"anyOf": [identifier, {"type": "null"}]},
            "phenomena": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(PHENOMENA)}},
            "provenance": {
                "type": "object",
                "additionalProperties": False,
                "required": ["method", "authors", "rights", "reviewer", "approved", "source", "test_only"],
                "properties": {
                    "method": {"enum": ["owner-authored", "source-record", "deterministic-template", "test-fixture"]},
                    "authors": {"type": "array", "minItems": 1, "items": identifier},
                    "rights": identifier,
                    "reviewer": identifier,
                    "approved": {"const": True},
                    "test_only": {"type": "boolean"},
                    "source": {
                        "type": "object",
                        "required": ["repository", "commit", "path", "sha256"],
                        "additionalProperties": False,
                        "properties": {
                            "repository": identifier,
                            "commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                            "path": identifier,
                            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        },
                    },
                },
            },
        },
    }
