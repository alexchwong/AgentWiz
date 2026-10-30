"""Command line: agentwiz [--port N] [--no-browser] [--workspaces-dir PATH] [--workspace NAME]
[--profiles-dir PATH] [--mcp-servers-dir PATH] [--version]
(techspec 0.1.1, 7.2; 0.1.2, 5.4).

Exit statuses: 0 after Ctrl-C; 1 when the workspace cannot be opened or the port cannot be bound;
2 for usage errors, including an invalid workspace name; 3 when another AgentWiz process holds the
workspace. The address is always the first stdout line.
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the printed address is the reliable route (e.g. WSL2)
        pass


def _workspace_name(value: str) -> str:
    """argparse type: the name unchanged, or a usage error. (valid_name is a predicate, not a type.)"""
    from .workspace import NAME_RULE, valid_name

    if not valid_name(value):
        raise argparse.ArgumentTypeError(f"invalid workspace name; use {NAME_RULE}")
    return value


def _holder_text(holder: dict) -> str:
    """' (pid P on HOST, started T)' with whichever details the lock file had, or ''."""
    text = f"pid {holder['pid']}" if "pid" in holder else ""
    if "host" in holder:
        text += (" on " if text else "on ") + holder["host"]
    if "started" in holder:
        text += (", " if text else "") + "started " + holder["started"]
    return f" ({text})" if text else ""


def _say(line: str, err: bool = False) -> None:
    print(f"[AgentWiz] {line}", file=sys.stderr if err else sys.stdout, flush=True)


def main(argv: list[str] | None = None) -> None:
    import modelspell

    from . import __version__
    from .ui.server import UIServer
    from .workspace import WorkspaceError, WorkspaceInUse, open_workspace

    parser = argparse.ArgumentParser(
        prog="agentwiz",
        description="AgentWiz: local browser UI for building and testing LLM steps. "
                    "Binds to 127.0.0.1 only.")
    parser.add_argument("--port", type=int, default=0, help="port to bind (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--workspaces-dir", default="./workspaces", metavar="PATH",
                        help="folder holding workspaces, created if missing (default: ./workspaces)")
    parser.add_argument("--workspace", type=_workspace_name, default="default", metavar="NAME",
                        help="workspace to use, created empty if missing (default: default)")
    parser.add_argument("--profiles-dir", type=Path, default=Path("./model_profiles"), metavar="PATH",
                        help="shared default profiles, created if missing (default: ./model_profiles)")
    parser.add_argument("--mcp-servers-dir", type=Path, default=Path("./mcp_servers"), metavar="PATH",
                        help="shared MCP server library, created if missing (default: ./mcp_servers)")
    parser.add_argument("--version", action="version",
                        version=f"AgentWiz {__version__} (modelSpell {modelspell.__version__})")
    args = parser.parse_args(argv)

    try:
        ws = open_workspace(Path(args.workspaces_dir), args.workspace)
    except WorkspaceInUse as exc:
        _say(f"workspace '{args.workspace}' is in use by another AgentWiz process"
             + _holder_text(exc.holder), err=True)
        sys.exit(3)
    except WorkspaceError as exc:
        where = Path(args.workspaces_dir).absolute() / args.workspace
        _say(f"cannot open workspace '{args.workspace}' at {where}: {exc}", err=True)
        sys.exit(1)

    try:
        try:
            server = UIServer(args.port, workspace=ws, profiles_dir=args.profiles_dir,
                              mcp_servers_dir=args.mcp_servers_dir)
        except (OSError, OverflowError) as exc:
            reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
            _say(f"cannot listen on 127.0.0.1:{args.port}: {reason}", err=True)
            sys.exit(1)

        _say(server.url)
        _say("this machine only; the address carries a one-time session token. Ctrl-C to stop.")
        _say(f"workspace '{ws.name}' at {ws.path}" + (" (created)" if ws.created else ""))
        _say(f"MCP servers at {server.state.mcp_library.folder}")
        problems = server.state.problems
        for problem in problems:
            _say(f"warning: {problem.file}: {problem.message}")
        if problems:
            _say("repair or restore the file, then restart AgentWiz.")
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
    finally:
        ws.close()


if __name__ == "__main__":
    main()
