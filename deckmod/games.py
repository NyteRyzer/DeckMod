"""Discover installed games from Steam, Heroic (GOG/Epic/Amazon), Lutris and manual entries."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, vdf

HOME = config.HOME

# Steam "apps" that are really tools/runtimes, never something you'd mod.
STEAM_SKIP_PREFIXES = (
    "Proton", "Steam Linux Runtime", "Steamworks Common", "SteamVR",
    "Steam Audio", "Steamworks SDK",
)
STEAM_SKIP_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100"}


@dataclass
class Game:
    id: str
    name: str
    source: str
    path: str
    appid: str | None = None
    prefix: str | None = None  # Wine/Proton prefix (the "pfx" dir) if any
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- Steam

def steam_roots() -> list[Path]:
    env = os.environ.get("DECKMOD_STEAM_ROOT")
    candidates = [Path(p) for p in env.split(os.pathsep)] if env else [
        HOME / ".steam" / "steam",
        HOME / ".steam" / "root",
        HOME / ".local" / "share" / "Steam",
        HOME / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        HOME / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
    ]
    seen, roots = set(), []
    for c in candidates:
        try:
            real = c.resolve()
        except OSError:
            continue
        if real in seen or not (real / "steamapps").is_dir():
            continue
        seen.add(real)
        roots.append(real)
    return roots


def steam_libraries() -> list[Path]:
    libs: list[Path] = []
    seen = set()
    for root in steam_roots():
        found = [root]
        lf = root / "steamapps" / "libraryfolders.vdf"
        if lf.is_file():
            data = vdf.get_ci(vdf.load(lf), "libraryfolders", {})
            for entry in data.values():
                if isinstance(entry, dict):
                    p = vdf.get_ci(entry, "path")
                elif isinstance(entry, str) and "/" in entry:  # very old format
                    p = entry
                else:
                    p = None
                if p:
                    found.append(Path(p))
        for lib in found:
            try:
                real = lib.resolve()
            except OSError:
                continue
            if real not in seen and (real / "steamapps").is_dir():
                seen.add(real)
                libs.append(real)
    return libs


def find_compatdata(appid: str) -> Path | None:
    """Proton prefixes may live in any library, not necessarily the game's own."""
    for lib in steam_libraries():
        pfx = lib / "steamapps" / "compatdata" / appid / "pfx"
        if pfx.is_dir():
            return pfx
    return None


def scan_steam() -> list[Game]:
    games = []
    for lib in steam_libraries():
        steamapps = lib / "steamapps"
        for acf in sorted(steamapps.glob("appmanifest_*.acf")):
            try:
                state = vdf.get_ci(vdf.load(acf), "AppState", {})
            except OSError:
                continue
            appid = str(vdf.get_ci(state, "appid", ""))
            name = vdf.get_ci(state, "name", "") or f"App {appid}"
            installdir = vdf.get_ci(state, "installdir")
            if not appid or not installdir or appid in STEAM_SKIP_APPIDS:
                continue
            if name.startswith(STEAM_SKIP_PREFIXES):
                continue
            path = steamapps / "common" / installdir
            if not path.is_dir():
                continue
            pfx = steamapps / "compatdata" / appid / "pfx"
            prefix = pfx if pfx.is_dir() else find_compatdata(appid)
            games.append(Game(
                id=f"steam-{appid}", name=name, source="steam", path=str(path),
                appid=appid, prefix=str(prefix) if prefix else None,
            ))
    return games


# ---------------------------------------------------------------- Heroic

def _heroic_config_dirs() -> list[Path]:
    return [p for p in (
        HOME / ".config" / "heroic",
        HOME / ".var" / "app" / "com.heroicgameslauncher.hgl" / "config" / "heroic",
    ) if p.is_dir()]


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _heroic_prefix(cfg_dir: Path, app_name: str) -> str | None:
    data = _read_json(cfg_dir / "GamesConfig" / f"{app_name}.json") or {}
    pfx = (data.get(app_name) or {}).get("winePrefix")
    if pfx:
        p = Path(pfx)
        # Proton-style prefixes put the real prefix in a "pfx" subdirectory.
        if (p / "pfx").is_dir():
            p = p / "pfx"
        if p.is_dir():
            return str(p)
    return None


def scan_heroic() -> list[Game]:
    games: list[Game] = []
    for cfg in _heroic_config_dirs():
        # GOG
        titles = {}
        lib = _read_json(cfg / "store_cache" / "gog_library.json") or _read_json(cfg / "gog_store" / "library.json") or {}
        for g in lib.get("games", []) if isinstance(lib, dict) else []:
            if isinstance(g, dict) and g.get("app_name"):
                titles[g["app_name"]] = g.get("title")
        inst = _read_json(cfg / "gog_store" / "installed.json") or {}
        for g in inst.get("installed", []):
            path = g.get("install_path")
            if not path or not Path(path).is_dir():
                continue
            app = str(g.get("appName"))
            games.append(Game(
                id=f"gog-{app}", name=titles.get(app) or Path(path).name, source="heroic-gog",
                path=path, prefix=_heroic_prefix(cfg, app),
            ))
        # Epic (via legendary)
        for leg in (cfg / "legendaryConfig" / "legendary", HOME / ".config" / "legendary"):
            data = _read_json(leg / "installed.json") or {}
            for app, g in data.items():
                path = g.get("install_path")
                if not path or not Path(path).is_dir():
                    continue
                if any(x.id == f"epic-{app}" for x in games):
                    continue
                games.append(Game(
                    id=f"epic-{app}", name=g.get("title") or app, source="heroic-epic",
                    path=path, prefix=_heroic_prefix(cfg, app),
                ))
        # Amazon (via nile)
        data = _read_json(cfg / "nile_config" / "nile" / "installed.json") or []
        for g in data if isinstance(data, list) else []:
            path = g.get("path")
            if path and Path(path).is_dir():
                app = str(g.get("id"))
                games.append(Game(
                    id=f"amazon-{app}", name=Path(path).name, source="heroic-amazon",
                    path=path, prefix=_heroic_prefix(cfg, app),
                ))
    return games


# ---------------------------------------------------------------- Lutris

def scan_lutris() -> list[Game]:
    games = []
    for db in (HOME / ".local" / "share" / "lutris" / "pga.db",
               HOME / ".var" / "app" / "net.lutris.Lutris" / "data" / "lutris" / "pga.db"):
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT slug, name, directory, runner FROM games WHERE installed = 1"
            ).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for slug, name, directory, runner in rows:
            if not directory or not Path(directory).is_dir():
                continue
            prefix = directory if runner == "wine" and (Path(directory) / "drive_c").is_dir() else None
            games.append(Game(
                id=f"lutris-{slug}", name=name or slug, source="lutris", path=directory, prefix=prefix,
            ))
    return games


# ---------------------------------------------------------------- Manual

def _manual_file() -> Path:
    return config.data_dir() / "manual_games.json"


def scan_manual() -> list[Game]:
    return [Game(**g) for g in config.read_json(_manual_file(), [])]


def add_manual(name: str, path: str, prefix: str | None = None) -> Game:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    entries = config.read_json(_manual_file(), [])
    gid = f"manual-{config.safe_name(name).lower()}"
    entries = [e for e in entries if e["id"] != gid]
    game = Game(id=gid, name=name, source="manual", path=str(p),
                prefix=str(Path(prefix).expanduser().resolve()) if prefix else None)
    entries.append(game.to_dict())
    config.write_json(_manual_file(), entries)
    return game


def remove_manual(game_id: str) -> bool:
    entries = config.read_json(_manual_file(), [])
    kept = [e for e in entries if e["id"] != game_id]
    config.write_json(_manual_file(), kept)
    return len(kept) != len(entries)


# ---------------------------------------------------------------- All

def scan_all() -> list[Game]:
    games: dict[str, Game] = {}
    for scanner in (scan_steam, scan_heroic, scan_lutris, scan_manual):
        try:
            for g in scanner():
                games.setdefault(g.id, g)
        except Exception as exc:  # one broken launcher config shouldn't hide the rest
            print(f"warning: {scanner.__name__} failed: {exc}")
    return sorted(games.values(), key=lambda g: g.name.lower())


def find_game(query: str, games: list[Game] | None = None) -> Game:
    """Resolve a game by id, Steam appid, or (unique) case-insensitive name fragment."""
    games = games if games is not None else scan_all()
    q = query.strip().lower()
    for g in games:
        if g.id.lower() == q or (g.appid and g.appid == q):
            return g
    exact = [g for g in games if g.name.lower() == q]
    if len(exact) == 1:
        return exact[0]
    partial = [g for g in games if q in g.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise LookupError(f"No installed game matches '{query}'. Run 'deckmod games' to list them.")
    names = "\n  ".join(f"{g.id:<24} {g.name}" for g in partial[:15])
    raise LookupError(f"'{query}' matches several games, be more specific:\n  {names}")
