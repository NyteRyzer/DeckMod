"""Mod library + deployment engine.

Each game gets a private staging area (~/.local/share/deckmod/games/<id>/).
Mods are unpacked there, never straight into the game. "Deploying" links or
copies every enabled mod's files into the game folder in load order (later
mods win conflicts), backing up any original game file it replaces and
recording everything in a manifest so it can be undone exactly.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath

from . import archive, config, engines
from .games import Game

IGNORED_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}
DOC_RE = re.compile(r"^(readme|license|licence|changelog|credits|install|instructions)\b.*|.*\.(txt|md|pdf|url|htm|html|rtf|nfo)$", re.I)
KNOWN_TOP_DIRS = {
    "bepinex", "melonloader", "mods", "plugins", "data", "reframework", "natives", "binaries",
    "content", "~mods", "ue4ss", "userlibs", "userdata", "bin", "x64", "win64", "win32", "dlls",
}
PLUGIN_EXTS = (".esp", ".esm", ".esl")


class ModError(Exception):
    pass


def settings() -> dict:
    return {"link_mode": "hardlink", **config.read_json(config.data_dir() / "settings.json", {})}


def _rel_files(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in IGNORED_NAMES]
        for f in filenames:
            if f.lower() in IGNORED_NAMES:
                continue
            out.append(Path(dirpath, f).relative_to(root).as_posix())
    return sorted(out)


def resolve_case(base: Path, rel: str) -> str:
    """Map ``rel`` onto existing entries under ``base`` ignoring case.

    Windows games running through Proton expect case-insensitive paths, so a
    mod shipping ``data/textures`` must land in the game's existing ``Data/``
    instead of creating a second, invisible ``data/`` folder.
    """
    cur = base
    parts_out = []
    for part in PurePosixPath(rel).parts:
        match = part
        if cur.is_dir() and not (cur / part).exists():
            low = part.lower()
            try:
                for entry in os.listdir(cur):
                    if entry.lower() == low:
                        match = entry
                        break
            except OSError:
                pass
        parts_out.append(match)
        cur = cur / match
    return "/".join(parts_out)


class GameManager:
    def __init__(self, game: Game):
        self.game = game
        self.dir = config.game_dir(game.id)
        self.state_file = self.dir / "state.json"
        self.manifest_file = self.dir / "manifest.json"
        self.state = config.read_json(self.state_file, {"mods": [], "deployed": True})
        self.last_deploy: dict | None = None
        override = self.state.get("path_override")
        self.root = Path(override or game.path)

    # ------------------------------------------------------------ state

    def save(self) -> None:
        self.state["game"] = {"id": self.game.id, "name": self.game.name}
        config.write_json(self.state_file, self.state)

    @property
    def mods(self) -> list[dict]:
        return self.state["mods"]

    def mod_files_dir(self, mod_id: str) -> Path:
        return config.mods_dir(self.game.id) / mod_id / "files"

    def get_mod(self, query: str) -> dict:
        q = str(query).strip().lower()
        if q.isdigit() and 1 <= int(q) <= len(self.mods):
            return self.mods[int(q) - 1]
        for m in self.mods:
            if m["id"].lower() == q or m["name"].lower() == q:
                return m
        partial = [m for m in self.mods if q in m["name"].lower()]
        if len(partial) == 1:
            return partial[0]
        raise ModError(f"No single mod matches '{query}' for {self.game.name}.")

    def engine(self) -> engines.EngineProfile:
        staged = set()
        for m in self.mods:
            for rel in _rel_files(self.mod_files_dir(m["id"])):
                full = PurePosixPath(m.get("target") or "", rel)
                for i in range(1, len(full.parts) + 1):
                    staged.add("/".join(full.parts[:i]).lower())

        def exists(rel: str) -> bool:
            return rel.lower() in staged or (self.root / resolve_case(self.root, rel)).exists()

        return engines.detect(self.root, self.game.appid, exists)

    # ------------------------------------------------------------ install

    def install(self, source: str, name: str | None = None, target: str | None = None,
                display_name: str | None = None) -> dict:
        if re.match(r"^https?://", source):
            src = archive.download(source)
        else:
            src = Path(source).expanduser().resolve()
        if not src.exists():
            raise ModError(f"Not found: {src}")

        base_name = name or (archive.archive_stem(Path(display_name or src.name)) if src.is_file() else src.name)
        mod_id = self._new_id(base_name)
        mod_dir = config.mods_dir(self.game.id) / mod_id
        work = Path(tempfile.mkdtemp(prefix="extract-", dir=self.dir.parent.parent))
        try:
            content = work / "content"
            if src.is_dir():
                shutil.copytree(src, content, symlinks=False)
            elif archive.is_archive(src):
                archive.extract(src, content)
            else:  # a single loose file, e.g. a .pak or .dll
                content.mkdir()
                shutil.copy2(src, content / (display_name or src.name))

            content, docs = self._strip_wrappers(content)
            files = _rel_files(content)
            if not files:
                raise ModError("The mod contains no files.")
            guessed = False
            if target is None:
                target, guessed, reason = self.guess_target(content, files)
            else:
                target, reason = target.strip("/"), "chosen manually"

            mod_dir.mkdir(parents=True)
            shutil.move(str(content), mod_dir / "files")
            if docs:
                (mod_dir / "docs").mkdir()
                for d in docs:
                    shutil.move(str(d), mod_dir / "docs" / d.name)
        finally:
            shutil.rmtree(work, ignore_errors=True)

        mod = {
            "id": mod_id, "name": base_name, "enabled": True, "target": target,
            "guessed": guessed, "reason": reason, "source": str(source if display_name is None else display_name),
            "installed": time.strftime("%Y-%m-%d %H:%M"), "files": len(files),
        }
        self.mods.append(mod)
        self.save()
        self.sync()
        return mod

    def _new_id(self, name: str) -> str:
        base = config.safe_name(name).lower()[:60]
        existing = {m["id"] for m in self.mods}
        mod_id, n = base, 2
        while mod_id in existing or (config.mods_dir(self.game.id) / mod_id).exists():
            mod_id, n = f"{base}-{n}", n + 1
        return mod_id

    def _strip_wrappers(self, content: Path) -> tuple[Path, list[Path]]:
        """Descend through pointless wrapper folders like ``MyMod-v1.2/``."""
        docs: list[Path] = []
        prof = self.engine()
        known = KNOWN_TOP_DIRS | set(prof.dir_targets)
        for _ in range(5):
            entries = [e for e in content.iterdir() if e.name.lower() not in IGNORED_NAMES]
            dirs = [e for e in entries if e.is_dir()]
            files = [e for e in entries if e.is_file()]
            if len(dirs) != 1 or any(not DOC_RE.match(f.name) for f in files):
                break
            only = dirs[0]
            if only.name.lower() in known or self._exists_in_game(only.name, prof):
                break
            docs.extend(files)
            content = only
        return content, docs

    def _exists_in_game(self, name: str, prof: engines.EngineProfile) -> bool:
        return any(self._ci_exists(f"{b}/{name}" if b else name) for b in prof.search_bases)

    def _ci_exists(self, rel: str) -> bool:
        return (self.root / resolve_case(self.root, rel)).exists()

    def guess_target(self, content: Path, files: list[str]) -> tuple[str, bool, str]:
        prof = self.engine()
        top = {}
        for f in files:
            first = f.split("/", 1)[0]
            top[first] = "/" in f  # True if it's a directory
        top_files = [n for n, is_dir in top.items() if not is_dir]
        top_dirs = [n for n, is_dir in top.items() if is_dir]

        # 1. Loader / proxy DLLs and executables belong next to the game's exe.
        if any(n.lower() in engines.PROXY_DLLS or n.lower().endswith(".exe") for n in top_files):
            return prof.exe_dirs[0], False, "contains a loader DLL / executable"

        # 2. Top-level folders that already exist somewhere in the game.
        best, best_hits = None, 0
        for base in prof.search_bases:
            hits = sum(1 for n in top_dirs if self._ci_exists(f"{base}/{n}" if base else n))
            if hits > best_hits:
                best, best_hits = base, hits
        if best is not None:
            return best, False, f"folder layout matches the game ({best or 'game root'})"

        # 3. Well-known folder names for this engine.
        for n in top_dirs:
            if n.lower() in prof.dir_targets:
                return prof.dir_targets[n.lower()], False, f"'{n}' is a known {prof.name} folder"

        # 4. File types this engine loads from a known place.
        counts = Counter()
        for f in files:
            ext = PurePosixPath(f).suffix.lower()
            if ext in prof.ext_targets:
                counts[prof.ext_targets[ext]] += 1
        if counts:
            tgt = counts.most_common(1)[0][0]
            return tgt, False, f"file types match {prof.name} mod folder"

        return prof.default_target, True, "guessed - check the mod's readme"

    # ------------------------------------------------------------ edit

    def set_enabled(self, query: str, enabled: bool) -> dict:
        mod = self.get_mod(query)
        mod["enabled"] = enabled
        self.save()
        self.sync()
        return mod

    def move(self, query: str, position: int) -> dict:
        mod = self.get_mod(query)
        self.mods.remove(mod)
        position = max(1, min(position, len(self.mods) + 1))
        self.mods.insert(position - 1, mod)
        self.save()
        self.sync()
        return mod

    def set_target(self, query: str, target: str) -> dict:
        mod = self.get_mod(query)
        mod["target"] = target.strip().strip("/")
        mod["guessed"] = False
        mod["reason"] = "chosen manually"
        self.save()
        self.sync()
        return mod

    def uninstall(self, query: str) -> dict:
        mod = self.get_mod(query)
        self.mods.remove(mod)
        self.save()
        self.sync()
        shutil.rmtree(config.mods_dir(self.game.id) / mod["id"], ignore_errors=True)
        return mod

    def set_path_override(self, path: str | None) -> None:
        if self.manifest_file.exists():
            self.undeploy()
        self.state["path_override"] = path
        self.root = Path(path or self.game.path)
        self.save()
        self.sync()

    # ------------------------------------------------------------ deploy

    def plan(self) -> tuple[dict[str, tuple[dict, Path, str]], list[dict]]:
        """Return (final file map, conflicts). Later mods in the list win."""
        winners: dict[str, tuple[dict, Path, str]] = {}
        owners: dict[str, list[str]] = {}
        for mod in self.mods:
            if not mod["enabled"]:
                continue
            src_root = self.mod_files_dir(mod["id"])
            for rel in _rel_files(src_root):
                dest = str(PurePosixPath(mod.get("target") or "", rel))
                key = dest.lower()
                winners[key] = (mod, src_root / rel, dest)
                owners.setdefault(key, []).append(mod["name"])
        conflicts = [
            {"file": winners[k][2], "winner": names[-1], "overridden": names[:-1]}
            for k, names in owners.items() if len(names) > 1
        ]
        return winners, conflicts

    def sync(self) -> dict | None:
        if self.state.get("deployed", True):
            return self.deploy()
        return None

    def deploy(self) -> dict:
        if not self.root.is_dir():
            raise ModError(f"Game folder is missing: {self.root}")
        if self.manifest_file.exists():
            self.undeploy(keep_flag=True)
        mode = settings()["link_mode"]
        winners, conflicts = self.plan()
        backups = config.backups_dir(self.game.id)
        manifest = {"files": [], "dirs": [], "plugins": None}
        skipped: list[str] = []
        try:
            for _key, (mod, src, dest) in sorted(winners.items()):
                rel = resolve_case(self.root, dest)
                out = self.root / rel
                # Create parent folders, remembering which ones are ours.
                missing = []
                p = out.parent
                while not p.exists():
                    missing.append(p)
                    p = p.parent
                for d in reversed(missing):
                    d.mkdir()
                    manifest["dirs"].append(d.relative_to(self.root).as_posix())
                backed_up = False
                if out.exists() or out.is_symlink():
                    if out.is_dir():
                        skipped.append(rel)  # never replace a folder with a file
                        continue
                    bak = backups / rel
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(out), bak)
                    backed_up = True
                used = self._place(src, out, mode)
                st = out.lstat()
                manifest["files"].append({
                    "path": rel, "mod": mod["id"], "backup": backed_up, "mode": used,
                    "size": st.st_size, "mtime": st.st_mtime_ns, "ino": st.st_ino,
                })
            manifest["plugins"] = self._activate_plugins(manifest["files"])
        finally:
            config.write_json(self.manifest_file, manifest)
            self.state["deployed"] = True
            self.save()
        self.last_deploy = {"files": len(manifest["files"]), "conflicts": conflicts, "skipped": skipped,
                            "launch_options": self.launch_options(manifest)}
        return self.last_deploy

    @staticmethod
    def _place(src: Path, out: Path, mode: str) -> str:
        if mode == "symlink":
            out.symlink_to(src)
            return "symlink"
        if mode == "hardlink":
            try:
                os.link(src, out)
                return "hardlink"
            except OSError:
                pass  # different filesystem (e.g. game on SD card) - fall back to copying
        shutil.copy2(src, out)
        return "copy"

    def undeploy(self, keep_flag: bool = False) -> dict:
        manifest = config.read_json(self.manifest_file, None)
        warnings = []
        removed = 0
        if manifest:
            self._deactivate_plugins(manifest.get("plugins"))
            backups = config.backups_dir(self.game.id)
            for entry in reversed(manifest["files"]):
                out = self.root / entry["path"]
                ours = False
                try:
                    st = out.lstat()
                    if entry["mode"] == "symlink":
                        ours = out.is_symlink()
                    elif entry["mode"] == "hardlink":
                        ours = st.st_ino == entry["ino"]
                    else:
                        ours = st.st_size == entry["size"] and st.st_mtime_ns == entry["mtime"]
                except FileNotFoundError:
                    st = None
                if ours:
                    out.unlink()
                    removed += 1
                elif st is not None:
                    warnings.append(f"left {entry['path']} alone (changed by the game or an update)")
                bak = backups / entry["path"]
                if entry["backup"] and bak.exists():
                    if out.exists():
                        bak.unlink()  # the game replaced the file; its version is newer
                    else:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(bak), out)
            for d in sorted(manifest["dirs"], key=lambda s: s.count("/"), reverse=True):
                try:
                    (self.root / d).rmdir()
                except OSError:
                    pass  # not empty: the game put its own files there
            self.manifest_file.unlink()
            bdir = config.backups_dir(self.game.id)
            if bdir.is_dir() and not any(p.is_file() for p in bdir.rglob("*")):
                shutil.rmtree(bdir, ignore_errors=True)
        if not keep_flag:
            self.state["deployed"] = False
            self.save()
        return {"removed": removed, "warnings": warnings}

    # ------------------------------------------------------------ Bethesda plugins

    def _plugins_file(self, create: bool) -> tuple[Path, bool] | None:
        prof = self.engine()
        if not prof.plugins_txt or not self.game.prefix:
            return None
        folder, star = prof.plugins_txt
        users = Path(self.game.prefix) / "drive_c" / "users"
        for u in sorted(users.glob("*")) if users.is_dir() else []:
            if u.name.lower() == "public":
                continue
            f = u / "AppData" / "Local" / folder / "Plugins.txt"
            if f.parent.is_dir():
                return f, star
        if create and users.is_dir():
            f = users / "steamuser" / "AppData" / "Local" / folder / "Plugins.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            return f, star
        return None

    def _activate_plugins(self, files: list[dict]) -> dict | None:
        order = {m["id"]: i for i, m in enumerate(self.mods)}
        found_plugins = [(order.get(f["mod"], 0), PurePosixPath(f["path"]).name) for f in files
                         if PurePosixPath(f["path"]).parent.as_posix().lower() == "data"
                         and f["path"].lower().endswith(PLUGIN_EXTS)]
        if not found_plugins:
            return None
        found = self._plugins_file(create=True)
        if not found:
            return None
        path, star = found
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        present = {ln.lstrip("*").strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")}
        # Masters (.esm) load before regular plugins; otherwise keep mod load order.
        found_plugins.sort(key=lambda t: (not t[1].lower().endswith(".esm"), t[0]))
        plugins = [name for _, name in found_plugins]
        added = []
        for p in plugins:
            if p.lower() not in present:
                lines.append(f"*{p}" if star else p)
                added.append(p)
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return {"file": str(path), "added": added}

    def _deactivate_plugins(self, info: dict | None) -> None:
        if not info or not info.get("added"):
            return
        path = Path(info["file"])
        if not path.exists():
            return
        drop = {p.lower() for p in info["added"]}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = [ln for ln in lines if ln.lstrip("*").strip().lower() not in drop]
        path.write_text("\r\n".join(kept) + ("\r\n" if kept else ""), encoding="utf-8")

    # ------------------------------------------------------------ info

    def launch_options(self, manifest: dict | None = None) -> str | None:
        """Steam launch options needed for proxy-DLL loaders under Proton."""
        manifest = manifest if manifest is not None else config.read_json(self.manifest_file, None)
        if not manifest:
            return None
        prof = self.engine()
        exe_dirs = {d.lower() for d in prof.exe_dirs}
        dlls = set()
        for f in manifest["files"]:
            p = PurePosixPath(f["path"])
            parent = "" if str(p.parent) == "." else p.parent.as_posix().lower()
            if p.name.lower() in engines.PROXY_DLLS and parent in exe_dirs | {""}:
                dlls.add(p.stem.lower())
        dlls = sorted(dlls)
        if not dlls or not self._is_windows_game():
            return None
        return f'WINEDLLOVERRIDES="{",".join(dlls)}=n,b" %command%'

    def _is_windows_game(self) -> bool:
        if self.game.prefix:
            return True
        prof = self.engine()
        return any(any((self.root / d).glob("*.exe")) for d in prof.exe_dirs if (self.root / d).is_dir())

    def info(self) -> dict:
        prof = self.engine()
        _, conflicts = self.plan()
        manifest = config.read_json(self.manifest_file, None)
        return {
            "game": self.game.to_dict(), "root": str(self.root), "engine": prof.to_dict(),
            "deployed": bool(manifest) and self.state.get("deployed", True),
            "deployed_files": len(manifest["files"]) if manifest else 0,
            "mods": self.mods, "conflicts": conflicts,
            "launch_options": self.launch_options(manifest),
            "path_override": self.state.get("path_override"),
        }
