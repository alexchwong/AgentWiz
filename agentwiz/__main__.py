"""Command line: agentwiz [--port N] [--no-browser] [--version]  (techspec 7.2, R0-3)."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the printed address is the reliable route (e.g. WSL2)
        pass


def main(argv: list[str] | None = None) -> None:
    import modelspell

    from . import __version__
    from .ui.server import UIServer

    parser = argparse.ArgumentParser(
        prog="agentwiz",
        description="AgentWiz: local browser UI for building and testing LLM steps. "
                    "Binds to 127.0.0.1 only.")
    parser.add_argument("--port", type=int, default=0, help="port to bind (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--version", action="version",
                        version=f"AgentWiz {__version__} (modelSpell {modelspell.__version__})")
    args = parser.parse_args(argv)

    try:
        server = UIServer(args.port)
    except (OSError, OverflowError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        print(f"[AgentWiz] cannot listen on 127.0.0.1:{args.port}: {reason}", file=sys.stderr, flush=True)
        sys.exit(1)

    print(f"[AgentWiz] {server.url}", flush=True)
    print("[AgentWiz] this machine only; the address carries a one-time session token. Ctrl-C to stop.",
          flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.3, _open_browser, args=(server.url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
