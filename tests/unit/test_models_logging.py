"""Safe model projection and logging redaction tests."""

from __future__ import annotations

import json
import logging

from dolibarr_mcp.errors import DolibarrUnavailableError
from dolibarr_mcp.logging import REDACTED, JsonFormatter, configure_logging, redact
from dolibarr_mcp.models import DolibarrUserPayload


def test_upstream_model_discards_extra_fields() -> None:
    payload = DolibarrUserPayload.model_validate(
        {
            "id": "7",
            "login": "bob",
            "firstname": "Bob",
            "lastname": "Smith",
            "api_key": "must-not-escape",
            "admin": 1,
        }
    )
    identity = payload.to_identity()
    assert identity.model_dump() == {
        "user_id": 7,
        "login": "bob",
        "first_name": "Bob",
        "last_name": "Smith",
    }
    assert "must-not-escape" not in repr(identity)


def test_redaction_handles_nested_secret_fields_and_strings() -> None:
    secret = "SuperSecretToken123"
    value = {
        "authorization": f"Bearer {secret}",
        "nested": [{"DOLAPIKEY": secret}, f"Authorization: Bearer {secret}"],
        "safe": "visible",
    }
    result = redact(value)
    assert secret not in repr(result)
    assert result["authorization"] == REDACTED
    assert result["safe"] == "visible"
    assert redact(123) == 123


def test_json_formatter_redacts_bearer_values() -> None:
    secret = "FakeGeneratedToken123"
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request Authorization: Bearer %s",
        args=(secret,),
        exc_info=None,
    )
    rendered = JsonFormatter().format(record)
    parsed = json.loads(rendered)
    assert secret not in rendered
    assert REDACTED in parsed["message"]


def test_domain_error_repr_is_safe_and_stable() -> None:
    error = DolibarrUnavailableError()
    assert repr(error) == "DolibarrUnavailableError()"
    assert str(error) == "Authentication service is temporarily unavailable."


def test_logging_configuration_sets_json_handler_and_quiets_http_clients() -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    httpx_level = logging.getLogger("httpx2").level
    httpcore_level = logging.getLogger("httpcore2").level
    try:
        configure_logging("ERROR")
        assert root.level == logging.ERROR
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert logging.getLogger("httpx2").level == logging.WARNING
        assert logging.getLogger("httpcore2").level == logging.WARNING
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        logging.getLogger("httpx2").setLevel(httpx_level)
        logging.getLogger("httpcore2").setLevel(httpcore_level)
