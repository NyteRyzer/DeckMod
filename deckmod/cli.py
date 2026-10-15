"""Command-line interface: ``deckmod <command> ...``."""

from __future__ import annotations

import argparse
import sys

from . import __version__, archive, games
from .manager import GameManager, ModError


def _mgr(query: str) -> GameManager:
    return GameManager(games.find_game(query))


def _print_deploy(result: dict | None) -> None:
    if not result:
        print("Mods are currently disabled for this game (run 'deckmod deploy' to apply them).")
        return
    print(f"Deployed {result['files']} file(s).")
    if result.get("packed"):
        print(f"Packed {result['packed']['count']} mod file(s) into {result['packed']['file']} "
              "- the game is ready to play.")
    for w in result.get("warnings", []):
        print(f"  warning: {w}")
    for c in result["conflicts"]:
        print(f"  conflict: {c['file']} -> using '{c['winner']}' over {', '.join(c['overridden'])}")
    for s in result.get("skipped", []):
        print(f"  skipped: {s} (a folder with that name already exists)")
    if result.get("launch_options"):
        print("\nThis game uses a DLL-based mod loader. Set its Steam launch options to:\n"
              f"  {result['launch_options']}\n"
              "(Steam > game > Properties > General > Launch Options. "
              "For Heroic/Lutris, add WINEDLLOVERRIDES as an environment variable.)")


def cmd_games(a):
    found = games.scan_all()
    if a.search:
        found = [g for g in found if a.search.lower() in g.name.lower()]
    if not found:
        print("No games found. Add one with: deckmod add-game \"Name\" /path/to/game")
        return
    for g in found:
        print(f"{g.id:<26} {g.name}  [{g.source}]")
    print(f"\n{len(found)} game(s). Refer to a game by id, Steam app id, or part of its name.")


def cmd_info(a):
    m = _mgr(a.game)
    info = m.info()
    print(f"{m.game.name}  ({m.game.id})")
    print(f"  folder:  {info['root']}")
    if m.game.prefix:
        print(f"  prefix:  {m.game.prefix}")
    print(f"  engine:  {info['engine']['name']}")
    print(f"  status:  {'mods deployed' if info['deployed'] else 'vanilla (mods not deployed)'}")
    for n in info["engine"]["notes"]:
        print(f"  note:    {n}")
    if info["launch_options"]:
        print(f"  launch options: {info['launch_options']}")
    print()
    cmd_mods(a, m)


def cmd_mods(a, m: GameManager | None = None):
    m = m or _mgr(a.game)
    if not m.mods:
        print("No mods installed. Install one with: deckmod install \"<game>\" <archive|folder|url>")
        return
    print("  #  on  name                                   target")
    for i, mod in enumerate(m.mods, 1):
        flag = "x" if mod["enabled"] else " "
        tgt = mod["target"] or "(game root)"
        guess = "  <- guessed" if mod.get("guessed") else ""
        print(f"{i:>3}  [{flag}] {mod['name'][:38]:<38} {tgt}{guess}")
    print("\nLower in the list = higher priority (wins file conflicts).")


def cmd_install(a):
    m = _mgr(a.game)
    for src in a.source:
        mod = m.install(src, name=a.name, target=a.target)
        print(f"Installed '{mod['name']}' -> {mod['target'] or '(game root)'}  ({mod['reason']}, {mod['files']} files)")
        if mod.get("readme"):
            print("  The mod's own instructions say:\n    " + mod["readme"].replace("\n", "\n    "))
        if mod["guessed"]:
            print("  ! Couldn't tell where this mod goes. If it doesn't work, set the folder with:\n"
                  f"    deckmod set-target \"{a.game}\" \"{mod['name']}\" <folder inside the game>")
    _print_deploy(_last_result(m))


def _last_result(m: GameManager):
    return m.last_deploy if m.state.get("deployed", True) else None


def cmd_toggle(a, enabled: bool):
    m = _mgr(a.game)
    for q in a.mod:
        mod = m.set_enabled(q, enabled)
        print(f"{'Enabled' if enabled else 'Disabled'} '{mod['name']}'.")
    _print_deploy(_last_result(m))


def cmd_move(a):
    m = _mgr(a.game)
    mod = m.move(a.mod, a.position)
    print(f"Moved '{mod['name']}' to position {m.mods.index(mod) + 1}.")
    _print_deploy(_last_result(m))


def cmd_target(a):
    m = _mgr(a.game)
    mod = m.set_target(a.mod, a.target)
    print(f"'{mod['name']}' now installs into: {mod['target'] or '(game root)'}")
    _print_deploy(_last_result(m))


def cmd_uninstall(a):
    m = _mgr(a.game)
    for q in a.mod:
        mod = m.uninstall(q)
        print(f"Removed '{mod['name']}'.")


def cmd_deploy(a):
    _print_deploy(_mgr(a.game).deploy())


def cmd_purge(a):
    res = _mgr(a.game).undeploy()
    print(f"Removed {res['removed']} mod file(s); original game files restored. Mods stay installed "
          "- run 'deckmod deploy' to re-apply them.")
    for w in res["warnings"]:
        print(f"  note: {w}")


def cmd_conflicts(a):
    _, conflicts = _mgr(a.game).plan()
    if not conflicts:
        print("No file conflicts between enabled mods.")
    for c in conflicts:
        print(f"{c['file']}\n    wins: {c['winner']}   overridden: {', '.join(c['overridden'])}")


def cmd_add_game(a):
    g = games.add_manual(a.name, a.path, a.prefix)
    print(f"Added {g.name} as '{g.id}'.")


def cmd_remove_game(a):
    print("Removed." if games.remove_manual(a.game) else "Only manually added games can be removed.")


def cmd_set_path(a):
    m = _mgr(a.game)
    m.set_path_override(a.path)
    print(f"{m.game.name} mods now deploy into: {m.root}")


def cmd_ui(a):
    from . import webui
    webui.serve(a.port, open_browser=not a.no_browser)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deckmod", description="Universal mod manager for Steam Deck / Linux.")
    p.add_argument("--version", action="version", version=f"deckmod {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("games", help="list installed games")
    s.add_argument("search", nargs="?")
    s.set_defaults(func=cmd_games)

    s = sub.add_parser("info", help="show engine, status and mods for a game")
    s.add_argument("game")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("mods", help="list a game's mods in load order")
    s.add_argument("game")
    s.set_defaults(func=cmd_mods)

    s = sub.add_parser("install", help="install mods from archives, folders, files or URLs")
    s.add_argument("game")
    s.add_argument("source", nargs="+")
    s.add_argument("--name", help="display name for the mod")
    s.add_argument("--target", help="folder inside the game to install into ('' = game root)")
    s.set_defaults(func=cmd_install)

    for name, enabled in (("enable", True), ("disable", False)):
        s = sub.add_parser(name, help=f"{name} mods (by number, id or name)")
        s.add_argument("game")
        s.add_argument("mod", nargs="+")
        s.set_defaults(func=lambda a, e=enabled: cmd_toggle(a, e))

    s = sub.add_parser("move", help="change a mod's load order position")
    s.add_argument("game")
    s.add_argument("mod")
    s.add_argument("position", type=int)
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("set-target", help="change which game folder a mod installs into")
    s.add_argument("game")
    s.add_argument("mod")
    s.add_argument("target")
    s.set_defaults(func=cmd_target)

    s = sub.add_parser("uninstall", help="delete mods from DeckMod")
    s.add_argument("game")
    s.add_argument("mod", nargs="+")
    s.set_defaults(func=cmd_uninstall)

    s = sub.add_parser("deploy", help="apply all enabled mods to the game")
    s.add_argument("game")
    s.set_defaults(func=cmd_deploy)

    s = sub.add_parser("purge", help="remove all mod files from the game and restore originals")
    s.add_argument("game")
    s.set_defaults(func=cmd_purge)

    s = sub.add_parser("conflicts", help="show files that several mods replace")
    s.add_argument("game")
    s.set_defaults(func=cmd_conflicts)

    s = sub.add_parser("add-game", help="add a game DeckMod didn't find automatically")
    s.add_argument("name")
    s.add_argument("path")
    s.add_argument("--prefix", help="its Wine prefix, if any")
    s.set_defaults(func=cmd_add_game)

    s = sub.add_parser("remove-game", help="forget a manually added game")
    s.add_argument("game")
    s.set_defaults(func=cmd_remove_game)

    s = sub.add_parser("set-path", help="deploy into a different folder than the detected one")
    s.add_argument("game")
    s.add_argument("path", nargs="?", help="omit to reset to the detected folder")
    s.set_defaults(func=cmd_set_path)

    s = sub.add_parser("ui", help="open the controller-friendly web interface")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_ui)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (LookupError, ValueError, ModError, archive.ArchiveError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
