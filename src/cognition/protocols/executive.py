"""Strict executive dispatch and the supported frozen request contract registry."""

from cognition.protocols.cognition_v1 import CognitionDecisionV1
from cognition.protocols.cognition_v2 import CognitionDecisionV2
from cognition.protocols.common import ProtocolModel
from cognition.protocols.model_v1 import ModelRequestV1, ModelResultV1
from cognition.protocols.model_v2 import ModelRequestV2, ModelResultV2

type CognitionDecision = CognitionDecisionV1 | CognitionDecisionV2
type ModelRequest = ModelRequestV1 | ModelRequestV2
type ModelResult = ModelResultV1 | ModelResultV2

_REQUEST_CONTRACTS = frozenset(
    {
        (1, 1, "CognitionDecisionV1", "2.0"),
        (1, 1, "CognitionDecisionV1", "3.0"),
        (1, 1, "CognitionDecisionV1", "3.1"),
        (2, 2, "CognitionDecisionV2", "3.2"),
    }
)


class IncompatibleExecutiveContract(ValueError):
    """A retained or selected executive version cannot safely run here."""


def _payload(value: object) -> object:
    # Typed models remain mutable; validate their fields again, never reuse them.
    return (
        value.model_dump(warnings=False) if isinstance(value, ProtocolModel) else value
    )


def _version(value: object) -> int:
    if not isinstance(value, dict):
        raise IncompatibleExecutiveContract("Executive payload must be an object")
    version = value.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise IncompatibleExecutiveContract("Unsupported executive schema version")
    return version


def parse_decision(value: object) -> CognitionDecision:
    value = _payload(value)
    model = CognitionDecisionV1 if _version(value) == 1 else CognitionDecisionV2
    return model.model_validate(value)


def parse_request(value: object) -> ModelRequest:
    value = _payload(value)
    model = ModelRequestV1 if _version(value) == 1 else ModelRequestV2
    return model.model_validate(value)


def parse_result(value: object) -> ModelResult:
    value = _payload(value)
    model = ModelResultV1 if _version(value) == 1 else ModelResultV2
    return model.model_validate(value)


def validate_request_contract(
    request: ModelRequest,
    *,
    config_schema_version: int | None = None,
    configured_protocol: int | None = None,
) -> None:
    """Check compatibility without rewriting a request or consulting current config.

    Optional configuration scalars must come from the snapshot's linked revision,
    not a later active revision. Structural parsing alone never confers support.
    """
    try:
        checked = parse_request(request)
    except (ValueError, TypeError) as error:
        raise IncompatibleExecutiveContract("Invalid executive request") from error
    contract = (
        checked.schema_version,
        checked.cognition_protocol_version,
        checked.output_schema,
        checked.runtime_contract_version,
    )
    if contract not in _REQUEST_CONTRACTS:
        raise IncompatibleExecutiveContract("Unsupported executive request contract")
    for configured in (config_schema_version, configured_protocol):
        if configured is not None and (
            type(configured) is not int or configured != checked.schema_version
        ):
            raise IncompatibleExecutiveContract("Executive configuration mismatch")
