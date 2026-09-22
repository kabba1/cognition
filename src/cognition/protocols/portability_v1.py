"""Logical portable-state manifest; importing bytes grants no external authority."""

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from cognition.protocols.common import (
    CognitionId,
    JsonObject,
    NonEmptyString,
    ProtocolModel,
    UTCDateTime,
    VersionOne,
)

type ImportIntent = Literal["restore", "migration", "fork", "simulation"]
type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
type NonNegativeInteger = Annotated[int, Field(strict=True, ge=0)]


def _require_false(value: object) -> object:
    """Prevent Literal[False] from accepting the numerically equivalent zero."""
    if value is not False:
        raise ValueError("secret_included must be the boolean false")
    return value


type SecretExcluded = Annotated[Literal[False], BeforeValidator(_require_false)]


class PortableIndividualV1(ProtocolModel):
    """Identity and lineage metadata, independent of import intent."""

    individual_id: CognitionId
    birth_at: UTCDateTime
    parent_individual_id: CognitionId | None


class PortableSnapshotV1(ProtocolModel):
    """The logical snapshot and its included evidence head."""

    snapshot_id: CognitionId
    head_event_id: CognitionId
    head_event_sequence: NonNegativeInteger


class PortableSoftwareV1(ProtocolModel):
    """Software and protocol versions needed to interpret canonical state."""

    cognition_version: NonEmptyString
    database_schema_revision: NonEmptyString
    cognition_protocol_versions: list[NonEmptyString]


class PortableCapabilityRequirementV1(ProtocolModel):
    """A software requirement, not a permission or a credential."""

    key: NonEmptyString
    version: NonEmptyString


class PortableRequirementsV1(ProtocolModel):
    """Capability definition dependencies that an importer must resolve."""

    capability_definitions: list[PortableCapabilityRequirementV1]


class PortableArtifactV1(ProtocolModel):
    """Inventory metadata; this contract performs no filesystem access."""

    artifact_id: CognitionId
    path: NonEmptyString
    sha256: Sha256
    size: NonNegativeInteger


class PortableContentsV1(ProtocolModel):
    """Locations of canonical data and included artifacts within the bundle."""

    canonical_data_path: NonEmptyString
    artifacts: list[PortableArtifactV1]


class PortableExternalBindingV1(ProtocolModel):
    """Nonsecret reconnection hints without credentials or live authority.

    Exporters remain responsible for excluding secrets from descriptor values;
    arbitrary JSON cannot be classified by its shape alone.
    """

    capability_or_connector: NonEmptyString
    label: NonEmptyString
    descriptor: JsonObject
    secret_included: SecretExcluded


class PortableIntegrityV1(ProtocolModel):
    """Declared digests; calculation and verification belong to export/import."""

    canonical_data_sha256: Sha256
    manifest_sha256: Sha256


class PortableManifestV1(ProtocolModel):
    """Appendix D manifest; restore, migration, fork, and simulation differ."""

    format: Literal["cognition-portable"]
    format_version: VersionOne
    bundle_id: CognitionId
    created_at: UTCDateTime
    import_intent_hint: ImportIntent | None
    individual: PortableIndividualV1
    snapshot: PortableSnapshotV1
    software: PortableSoftwareV1
    requirements: PortableRequirementsV1
    contents: PortableContentsV1
    external_bindings: list[PortableExternalBindingV1]
    integrity: PortableIntegrityV1
