"""Console entry point."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import uvicorn
from pydantic import ValidationError

from dolibarr_mcp import __version__
from dolibarr_mcp.app import create_app
from dolibarr_mcp.config import Settings
from dolibarr_mcp.logging import configure_logging

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dolibarr-mcp",
        description="Run the stateless Dolibarr MCP Streamable HTTP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate CLI/configuration and run one Uvicorn process."""
    parser = build_parser()
    parser.parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        parser.error(f"invalid configuration: {exc.error_count()} validation error(s)")
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.casefold(),
        access_log=False,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
