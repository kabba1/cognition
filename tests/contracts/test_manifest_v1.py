"""Portable manifests contain explicit continuity and nonsecret requirements."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cognition.protocols.portability_v1 import PortableManifestV1

GOLDEN = Path(__file__).parents[1] / "golden"


def example():
    return json.loads((GOLDEN / "portable_manifest_v1.json").read_text("utf-8"))


def test_golden_manifest_and_schema():
    payload = example()
    parsed = PortableManifestV1.model_validate(payload)
    assert parsed.model_dump(mode="json") == payload
    assert PortableManifestV1.model_validate_json(parsed.model_dump_json()) == parsed
    expected = json.loads(
        (GOLDEN / "portable_manifest_v1.schema.json").read_text("utf-8")
    )
    assert PortableManifestV1.model_json_schema() == expected


@pytest.mark.parametrize("intent", ["restore", "migration", "fork", "simulation", None])
def test_import_intents_remain_distinct(intent):
    parsed = PortableManifestV1.model_validate(
        {**example(), "import_intent_hint": intent}
    )
    assert parsed.import_intent_hint == intent


@pytest.mark.parametrize("value", [True, 0, 1, "false", None])
def test_portable_binding_requires_literal_boolean_false(value):
    payload = example()
    payload["external_bindings"][0]["secret_included"] = value
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate(payload)


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_manifest_format_version_is_integer_one(version):
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate({**example(), "format_version": version})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "postgres-backup"),
        ("import_intent_hint", "merge"),
    ],
)
def test_manifest_rejects_unrecognized_format_or_import_intent(field, value):
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate({**example(), field: value})


def test_manifest_all_sections_required_and_no_top_level_extras():
    payload = example()
    for field in payload:
        with pytest.raises(ValidationError):
            PortableManifestV1.model_validate(
                {k: v for k, v in payload.items() if k != field}
            )
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate({**payload, "automatic_authority": True})


@pytest.mark.parametrize(
    "path",
    [
        ("individual",),
        ("snapshot",),
        ("software",),
        ("requirements",),
        ("requirements", "capability_definitions", 0),
        ("contents",),
        ("contents", "artifacts", 0),
        ("external_bindings", 0),
        ("integrity",),
    ],
)
def test_nested_sections_are_closed_and_fields_required(path):
    payload = example()
    section = payload
    for part in path:
        section = section[part]
    for field in list(section):
        value = section.pop(field)
        with pytest.raises(ValidationError):
            PortableManifestV1.model_validate(payload)
        section[field] = value
    section["secret_value"] = "must-not-export"
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate(payload)


@pytest.mark.parametrize("path", [("created_at",), ("individual", "birth_at")])
def test_manifest_dates_are_aware_and_normalized(path):
    payload = example()
    section = payload
    for part in path[:-1]:
        section = section[part]
    section[path[-1]] = "2026-09-22T09:00:00"
    with pytest.raises(ValidationError):
        PortableManifestV1.model_validate(payload)
    section[path[-1]] = "2026-09-22T09:00:00-05:00"
    normalized = PortableManifestV1.model_validate(payload).model_dump(mode="json")
    for part in path:
        normalized = normalized[part]
    assert normalized == "2026-09-22T14:00:00Z"


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_snapshot_sequence_and_artifact_size_are_nonnegative_integers(value):
    for section, field in [("snapshot", "head_event_sequence"), ("artifact", "size")]:
        payload = example()
        target = (
            payload["snapshot"]
            if section == "snapshot"
            else payload["contents"]["artifacts"][0]
        )
        target[field] = value
        with pytest.raises(ValidationError):
            PortableManifestV1.model_validate(payload)


@pytest.mark.parametrize("value", ["", "abc", "g" * 64, "a" * 63])
def test_integrity_hashes_and_artifact_digest_require_sha256_hex(value):
    for field in ["canonical_data_sha256", "manifest_sha256", "sha256"]:
        payload = example()
        target = (
            payload["contents"]["artifacts"][0]
            if field == "sha256"
            else payload["integrity"]
        )
        target[field] = value
        with pytest.raises(ValidationError):
            PortableManifestV1.model_validate(payload)
