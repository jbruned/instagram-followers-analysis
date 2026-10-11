import argparse
import logging
import socket
import sys

import uvicorn

from .config import Settings
from .security import LOCAL_NAMES

log = logging.getLogger("follow_audit")


def main(argv: list[str] | None = None) -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(prog="follow-audit", description="Follow Audit web app")
    parser.add_argument("--host", default=settings.host, help="address to listen on")
    parser.add_argument("--port", type=int, default=settings.port, help="port to listen on")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.host not in LOCAL_NAMES and args.host != "localhost":
        log.warning(
            "listening on %s: anyone who can reach this address can read and change your data;"
            " the app has no login",
            args.host,
        )
    with socket.socket() as probe:
        if probe.connect_ex((args.host if args.host != "0.0.0.0" else "127.0.0.1", args.port)) == 0:
            sys.exit(f"Port {args.port} is already in use. Pick another with --port.")
    uvicorn.run(
        "follow_audit.app:create_app", factory=True, host=args.host, port=args.port, log_config=None
    )


if __name__ == "__main__":
    main()
