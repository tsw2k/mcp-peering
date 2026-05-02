"""Console entry point: ``python -m mcp_peering`` or ``mcp-peering``."""

from .server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
