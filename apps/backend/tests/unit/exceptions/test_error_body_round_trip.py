"""Pins that ``ErrorBody`` round-trips the handler's output dict (issue #345).

The exception handler in ``app/api/errors.py`` produces the ``params`` field by
calling ``exc.params.model_dump()``, which statically returns ``dict[str, Any]``
and at runtime can include values outside a narrow ``str | int | float | bool``
union (``None`` from ``Optional`` fields, nested objects, lists). Pinning
``ErrorBody.params`` to that narrow union advertised a closed shape that the
handler's output could not round-trip through ``ErrorBody.model_validate(...)``
— the OpenAPI schema claimed one thing, the wire format shipped another.

This test pins the contract the other direction: any dict shape the handler
can legally produce must validate back through ``ErrorBody``. The canonical
fix is to widen ``params`` to ``dict[str, Any]`` (matching ``model_dump``'s
return type) and route the handler envelope through ``ErrorResponse`` so
Pydantic asserts the round-trip at response time rather than leaving the
mismatch to ship silently.
"""

from typing import Any

from app.schemas import ErrorBody, ErrorResponse


def test_error_body_params_round_trips_handler_output_with_none_value() -> None:
    """``ErrorBody.model_validate`` accepts ``None`` values inside ``params``.

    A future ``*Params`` model with an ``Optional[...]`` field would emit
    ``None`` from ``model_dump()``; the schema must not reject it.
    """
    handler_output: dict[str, Any] = {
        "code": "SOME_ERROR",
        "params": {"reason": None},
        "details": None,
        "request_id": "00000000000000000000000000000000",
    }

    body = ErrorBody.model_validate(handler_output)

    assert body.params == {"reason": None}


def test_error_body_params_round_trips_handler_output_with_nested_dict() -> None:
    """``ErrorBody.model_validate`` accepts nested dict values inside ``params``.

    A future ``*Params`` model with a nested ``BaseModel`` field would emit a
    nested dict from ``model_dump()``; the schema must not reject it.
    """
    handler_output: dict[str, Any] = {
        "code": "SOME_ERROR",
        "params": {"context": {"source": "parser", "page": 3}},
        "details": None,
        "request_id": "00000000000000000000000000000000",
    }

    body = ErrorBody.model_validate(handler_output)

    assert body.params == {"context": {"source": "parser", "page": 3}}


def test_error_body_params_round_trips_handler_output_with_list_value() -> None:
    """``ErrorBody.model_validate`` accepts list values inside ``params``.

    A future ``*Params`` model with a ``list[...]`` field would emit a list
    from ``model_dump()``; the schema must not reject it.
    """
    handler_output: dict[str, Any] = {
        "code": "SOME_ERROR",
        "params": {"missing_fields": ["invoice_number", "total"]},
        "details": None,
        "request_id": "00000000000000000000000000000000",
    }

    body = ErrorBody.model_validate(handler_output)

    assert body.params == {"missing_fields": ["invoice_number", "total"]}


def test_error_response_round_trips_handler_envelope_with_mixed_params() -> None:
    """Full envelope round-trip through ``ErrorResponse`` for the handler's shape.

    Pins the contract the exception handler relies on: building the response
    via ``ErrorResponse(error=ErrorBody(...)).model_dump()`` must succeed for
    any dict ``model_dump`` can produce from a ``*Params`` model — including
    mixed primitive / ``None`` / nested-container values.
    """
    envelope: dict[str, Any] = {
        "error": {
            "code": "SOME_ERROR",
            "params": {
                "actual_bytes": 1024,
                "max_bytes": 512,
                "reason": None,
                "extras": {"nested": True},
                "tags": ["a", "b"],
            },
            "details": None,
            "request_id": "00000000000000000000000000000000",
        },
    }

    response = ErrorResponse.model_validate(envelope)

    assert response.model_dump() == envelope
