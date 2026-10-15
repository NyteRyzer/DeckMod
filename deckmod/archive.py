"""Extract mod archives safely using the stdlib, falling back to system tools for 7z/rar."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import config

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz",
                    ".tar.bz2", ".tbz2", ".tar.zst")


class ArchiveError(Exception):
    pass


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(ARCHIVE_SUFFIXES) or zipfile.is_zipfile(path) if path.is_file() else False


def archive_stem(path: Path) -> str:
    name = path.name
    for suf in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return path.stem


def _check_inside(dest: Path, member: str) -> None:
    target = (dest / member).resolve()
    if target != dest and dest not in target.parents:
        raise ArchiveError(f"Archive entry escapes the extraction folder: {member}")


def _extract_zip(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")  # Windows-made zips sometimes use backslashes
            if not name or name.startswith("__MACOSX/"):
                continue
            _check_inside(dest, name)
            out = dest / name
            if name.endswith("/"):
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as fin, open(out, "wb") as fout:
                shutil.copyfileobj(fin, fout)


def _extract_tar(src: Path, dest: Path) -> None:
    with tarfile.open(src) as tf:
        for m in tf.getmembers():
            _check_inside(dest, m.name)
        tf.extractall(dest, filter="data")


def _external_tools(src: Path, dest: Path) -> list[list[str]]:
    s, d = str(src), str(dest)
    cmds = []
    for tool in ("7zz", "7z", "7za"):
        if shutil.which(tool):
            cmds.append([tool, "x", "-y", "-bd", f"-o{d}", s])
    if shutil.which("bsdtar"):
        cmds.append(["bsdtar", "-xf", s, "-C", d])
    if src.name.lower().endswith(".rar"):
        for tool in ("unrar", "unar"):
            if shutil.which(tool):
                cmds.append([tool, "x", "-o+", s, d + "/"] if tool == "unrar" else [tool, "-f", "-o", d, s])
    return cmds


def _verify_tree(dest: Path) -> None:
    root = dest.resolve()
    for p in dest.rglob("*"):
        if p.is_symlink():
            target = p.resolve()
            if target != root and root not in target.parents:
                p.unlink()


def extract(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    name = src.name.lower()
    if zipfile.is_zipfile(src):
        _extract_zip(src, dest)
        return
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")) or (
        not name.endswith((".7z", ".rar")) and tarfile.is_tarfile(src)
    ):
        _extract_tar(src, dest)
        return
    cmds = _external_tools(src, dest)
    if not cmds:
        raise ArchiveError(
            f"Can't open {src.name}: no 7z/rar extractor found. On Steam Deck, install the "
            "'7-Zip' or 'Ark' flatpak, or 'p7zip' via your distro, or re-pack the mod as .zip."
        )
    errors = []
    for cmd in cmds:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            _verify_tree(dest)
            return
        errors.append(f"{cmd[0]}: {(res.stderr or res.stdout).strip()[-300:]}")
    raise ArchiveError(f"Failed to extract {src.name}:\n" + "\n".join(errors))


def download(url: str) -> Path:
    """Download a direct mod link into DeckMod's download cache."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ArchiveError("Only http(s) links can be downloaded.")
    req = urllib.request.Request(url, headers={"User-Agent": "DeckMod/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        name = None
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            name = cd.split("filename=", 1)[1].split(";")[0].strip().strip('"')
        name = config.safe_name(name or Path(urllib.parse.unquote(parsed.path)).name or "mod-download")
        out = config.downloads_dir() / name
        with open(out, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    return out
