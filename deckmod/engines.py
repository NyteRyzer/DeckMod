"""Game engine detection and knowledge about where mods go for each engine.

Nothing here is required for a mod to install: unknown games fall back to the
"generic" profile, which drops mod files into the game folder. Engine profiles
just make the automatic placement smarter and give loader-specific hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# DLLs that mod loaders drop next to a game's .exe so Windows loads them first.
# Under Proton/Wine they only work if Wine is told to prefer the native copy.
PROXY_DLLS = {
    "winhttp.dll", "dinput8.dll", "version.dll", "dxgi.dll", "d3d8.dll", "d3d9.dll",
    "d3d10.dll", "d3d11.dll", "d3d12.dll", "dsound.dll", "xinput1_3.dll", "xinput1_4.dll",
    "xinput9_1_0.dll", "winmm.dll", "dwmapi.dll", "ddraw.dll", "wininet.dll", "msacm32.dll",
    "opengl32.dll", "binkw32.dll", "bink2w64.dll",
}

# Bethesda games: AppData\Local\<folder>\Plugins.txt and whether it uses "*" to mark active plugins.
BETHESDA_PLUGINS = {
    "489830": ("Skyrim Special Edition", True),
    "611670": ("Skyrim VR", True),
    "72850": ("Skyrim", False),
    "377160": ("Fallout4", True),
    "611660": ("Fallout4VR", True),
    "1716740": ("Starfield", True),
    "22380": ("FalloutNV", False),
    "22370": ("Fallout3", False),
    "22300": ("Fallout3", False),
    "22330": ("Oblivion", False),
    "976620": ("Enderal Special Edition", True),
}

BETHESDA_DATA_DIRS = {
    "meshes", "textures", "scripts", "interface", "sound", "music", "materials", "seq",
    "skse", "f4se", "sfse", "nvse", "obse", "fose", "shadersfx", "strings", "video",
    "lodsettings", "grass", "calientetools", "nemesis_engine", "mcm", "platform",
}


@dataclass
class EngineProfile:
    key: str
    name: str
    # Directories (relative to the game root) to look in when matching a mod's
    # top-level folders against what already exists in the game.
    search_bases: list[str] = field(default_factory=lambda: [""])
    # Lower-case file extension -> target folder (relative to game root).
    ext_targets: dict[str, str] = field(default_factory=dict)
    # Lower-case top-level folder name in a mod -> target folder.
    dir_targets: dict[str, str] = field(default_factory=dict)
    # Where to put mods we can't place any other way.
    default_target: str = ""
    # Folders that contain the game's executable (where proxy DLLs belong).
    exe_dirs: list[str] = field(default_factory=lambda: [""])
    notes: list[str] = field(default_factory=list)
    plugins_txt: tuple[str, bool] | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "default_target": self.default_target,
            "exe_dirs": self.exe_dirs, "notes": self.notes,
        }


def _first_dir(root: Path, pattern: str) -> Path | None:
    try:
        return next((p for p in sorted(root.glob(pattern)) if p.is_dir()), None)
    except OSError:
        return None


def detect(game_path: str | Path, appid: str | None = None, exists=None) -> EngineProfile:
    """Work out the engine for a game folder.

    ``exists(relpath)`` lets callers report files that are not deployed yet but
    are part of an installed mod (e.g. BepInEx sitting in the staging area).
    """
    root = Path(game_path)
    if exists is None:
        def exists(rel: str) -> bool:
            return (root / rel).exists()

    # ---- Bethesda (Creation / Gamebryo)
    data = root / "Data"
    if data.is_dir() and (any(data.glob("*.esm")) or appid in BETHESDA_PLUGINS):
        prof = EngineProfile(
            key="bethesda", name="Bethesda (Creation/Gamebryo)", search_bases=["", "Data"],
            ext_targets={e: "Data" for e in (".esp", ".esm", ".esl", ".bsa", ".ba2")},
            dir_targets={d: "Data" for d in BETHESDA_DATA_DIRS},
            default_target="Data",
            plugins_txt=BETHESDA_PLUGINS.get(appid or ""),
            notes=[
                "Plugins (.esp/.esm/.esl) are activated in Plugins.txt automatically when deployed.",
                "Script extenders (SKSE/F4SE/...) install to the game folder; launch through their "
                "loader by renaming it or using a launcher-replacement build.",
            ],
        )
        return prof

    # ---- Unreal Engine 4/5
    for paks in sorted(root.glob("*/Content/Paks")):
        if not paks.is_dir() or paks.parent.parent.name == "Engine":
            continue
        proj = paks.parent.parent.name
        win64 = f"{proj}/Binaries/Win64"
        ue4ss_mods = next((p for p in (f"{win64}/ue4ss/Mods", f"{win64}/Mods") if exists(p)), f"{win64}/Mods")
        return EngineProfile(
            key="unreal", name=f"Unreal Engine ({proj})",
            search_bases=["", proj, f"{proj}/Content", f"{proj}/Content/Paks", win64, f"{win64}/ue4ss"],
            ext_targets={".pak": f"{proj}/Content/Paks/~mods", ".utoc": f"{proj}/Content/Paks/~mods",
                         ".ucas": f"{proj}/Content/Paks/~mods", ".lua": ue4ss_mods},
            dir_targets={"~mods": f"{proj}/Content/Paks", "logicmods": f"{proj}/Content/Paks",
                         "paks": f"{proj}/Content", "binaries": proj, "content": proj,
                         "ue4ss": win64},
            default_target=f"{proj}/Content/Paks/~mods",
            exe_dirs=[win64, ""],
            notes=["Pak mods go in Content/Paks/~mods. Script mods need UE4SS (proxy DLL: dwmapi.dll)."],
        )

    # ---- Unity
    unity_data = next((p for p in sorted(root.glob("*_Data")) if p.is_dir() and (
        (p / "Managed").is_dir() or (p / "il2cpp_data").is_dir() or (p / "globalgamemanagers").exists()
    )), None)
    if unity_data is not None or (root / "UnityPlayer.dll").exists() or (root / "UnityPlayer.so").exists():
        if exists("MelonLoader") and not exists("BepInEx"):
            dll_target, loader = "Mods", "MelonLoader"
        else:
            dll_target, loader = "BepInEx/plugins", "BepInEx"
        bases = ["", "BepInEx"] + ([unity_data.name] if unity_data else [])
        return EngineProfile(
            key="unity", name="Unity", search_bases=bases,
            ext_targets={".dll": dll_target},
            dir_targets={"plugins": "BepInEx", "patchers": "BepInEx", "config": "BepInEx",
                         "mods": "", "userlibs": "", "userdata": ""},
            default_target=dll_target,
            notes=[f"Code mods need a loader ({loader}). Install the loader zip like any other mod "
                   "first; BepInEx uses winhttp.dll and MelonLoader uses version.dll."],
        )

    # ---- RE Engine (Capcom)
    if (root / "re_chunk_000.pak").exists():
        return EngineProfile(
            key="re_engine", name="RE Engine", search_bases=["", "reframework", "natives"],
            ext_targets={".lua": "reframework/autorun", ".pak": ""},
            dir_targets={"autorun": "reframework", "plugins": "reframework"},
            notes=["Most mods need REFramework (proxy DLL: dinput8.dll). Loose files go in natives/."],
        )

    # ---- Godot
    if any(root.glob("*.pck")):
        return EngineProfile(key="godot", name="Godot",
                             notes=["Godot games usually load mods as .pck/.zip via a game-specific loader."])

    return EngineProfile(
        key="generic", name="Generic",
        notes=["Unknown engine: mod files are copied into the game folder exactly as packed. "
               "Use a custom target folder if the mod's instructions say so."],
    )
