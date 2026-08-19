"""Strict Authorization parser tests, including property-based cases."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from starlette.authentication import AuthenticationError

from dolibarr_mcp.auth import MAX_BEARER_TOKEN_LENGTH, parse_bearer_token


def test_valid_bearer_token_is_returned_without_modification() -> None:
    token = "AbC-._~+/0123=="
    assert parse_bearer_token([(b"authorization", f"Bearer {token}".encode())]) == token


def test_scheme_is_case_insensitive() -> None:
    assert parse_bearer_token([(b"Authorization", b"bEaReR AbC123")]) == "AbC123"


@st.composite
def invalid_headers(draw: st.DrawFn) -> list[tuple[bytes, bytes]]:
    malformed_value = draw(
        st.sampled_from(
            [
                b"",
                b"Bearer",
                b"Bearer ",
                b"Basic abc",
                b"Bearer  abc",
                b"Bearer abc def",
                b"Bearer abc\tdef",
                b"Bearer abc\x7fdef",
                b"Bearer abc:def",
            ]
        )
    )
    return [(b"authorization", malformed_value)]


@given(invalid_headers())
def test_malformed_authorization_is_always_rejected(headers: list[tuple[bytes, bytes]]) -> None:
    with pytest.raises(AuthenticationError):
        parse_bearer_token(headers)


def test_missing_header_is_rejected() -> None:
    with pytest.raises(AuthenticationError):
        parse_bearer_token([(b"content-type", b"application/json")])


def test_duplicate_headers_are_rejected() -> None:
    with pytest.raises(AuthenticationError):
        parse_bearer_token(
            [(b"authorization", b"Bearer first"), (b"Authorization", b"Bearer second")]
        )


def test_excessively_long_token_is_rejected() -> None:
    value = b"Bearer " + (b"a" * (MAX_BEARER_TOKEN_LENGTH + 1))
    with pytest.raises(AuthenticationError):
        parse_bearer_token([(b"authorization", value)])


@given(st.from_regex(r"[A-Za-z0-9\-._~+/]{1,256}=*", fullmatch=True))
def test_valid_token68_round_trips_exactly(token: str) -> None:
    assert parse_bearer_token([(b"authorization", f"Bearer {token}".encode())]) == token


@given(st.binary(min_size=1, max_size=128))
def test_parser_never_reflects_arbitrary_input_in_errors(value: bytes) -> None:
    with pytest.raises(AuthenticationError) as captured:
        parse_bearer_token([(b"authorization", value)])
    decoded = value.decode("ascii", errors="ignore")
    assert str(captured.value) == "Authentication required"
    if len(decoded) > len(str(captured.value)):
        assert decoded not in str(captured.value)
