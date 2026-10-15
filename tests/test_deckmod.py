import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from deckmod import archive, games, vdf
from deckmod.manager import GameManager, ModError
from deckmod.vpp import Packfile
from tests.test_vpp import make_vpp


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.steam = self.tmp / "steam"
        os.environ["DECKMOD_HOME"] = str(self.tmp / "home")
        os.environ["DECKMOD_STEAM_ROOT"] = str(self.steam)
        (self.steam / "steamapps").mkdir(parents=True)

    def add_steam_game(self, appid, name, installdir, prefix=True):
        write(self.steam / "steamapps" / f"appmanifest_{appid}.acf",
              f'"AppState"\n{{\n\t"appid"\t"{appid}"\n\t"name"\t"{name}"\n\t"installdir"\t"{installdir}"\n}}\n')
        root = self.steam / "steamapps" / "common" / installdir
        root.mkdir(parents=True)
        if prefix:
            (self.steam / "steamapps" / "compatdata" / appid / "pfx" / "drive_c" / "users" / "steamuser").mkdir(parents=True)
        return root

    def mgr(self, query):
        return GameManager(games.find_game(query))


class VdfTests(unittest.TestCase):
    def test_parse(self):
        data = vdf.loads('// c\n"libraryfolders" { "0" { "path" "/a b" "apps" { "1" "2" } } }')
        self.assertEqual(data["libraryfolders"]["0"]["path"], "/a b")
        self.assertEqual(vdf.get_ci(data, "LibraryFolders")["0"]["apps"]["1"], "2")


class ScanTests(Base):
    def test_steam_libraries_and_tools_filtered(self):
        sd = self.tmp / "sdcard"
        (sd / "steamapps" / "common" / "SdGame").mkdir(parents=True)
        write(sd / "steamapps" / "appmanifest_5.acf", '"AppState" { "appid" "5" "name" "Sd Game" "installdir" "SdGame" }')
        write(self.steam / "steamapps" / "libraryfolders.vdf",
              f'"libraryfolders" {{ "0" {{ "path" "{self.steam}" }} "1" {{ "path" "{sd}" }} }}')
        self.add_steam_game("10", "Game One", "GameOne")
        self.add_steam_game("1493710", "Proton Experimental", "Proton - Experimental", prefix=False)
        names = [g.name for g in games.scan_steam()]
        self.assertEqual(sorted(names), ["Game One", "Sd Game"])
        self.assertEqual(games.find_game("sd").id, "steam-5")


class DeployTests(Base):
    def test_unity_loader_and_plugin(self):
        root = self.add_steam_game("111", "Unity Game", "UG")
        (root / "UG_Data" / "Managed").mkdir(parents=True)
        write(root / "UG.exe")
        m = self.mgr("unity")
        m.install(str(make_zip(self.tmp / "BepInEx.zip", {
            "winhttp.dll": "w", "doorstop_config.ini": "c", "BepInEx/core/BepInEx.dll": "b"})))
        mod = m.install(str(make_zip(self.tmp / "Cool.zip", {"Cool-1.0/Cool.dll": "p", "README.md": "r"})))
        self.assertEqual(mod["target"], "BepInEx/plugins")
        self.assertTrue((root / "BepInEx/plugins/Cool.dll").exists())
        self.assertEqual(m.last_deploy["launch_options"], 'WINEDLLOVERRIDES="winhttp=n,b" %command%')
        m.undeploy()
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["UG.exe", "UG_Data"])

    def test_backup_restore_and_conflicts(self):
        root = self.add_steam_game("20", "Generic Game", "GG")
        write(root / "assets" / "tex.png", "original")
        m = self.mgr("generic")
        a = self.tmp / "a"; write(a / "assets" / "tex.png", "mod-a")
        b = self.tmp / "b"; write(b / "assets" / "tex.png", "mod-b")
        m.install(str(a), name="A")
        m.install(str(b), name="B")
        self.assertEqual((root / "assets/tex.png").read_text(), "mod-b")
        self.assertEqual(len(m.plan()[1]), 1)
        m.move("B", 1)
        self.assertEqual((root / "assets/tex.png").read_text(), "mod-a")
        m.set_enabled("A", False)
        self.assertEqual((root / "assets/tex.png").read_text(), "mod-b")
        m.undeploy()
        self.assertEqual((root / "assets/tex.png").read_text(), "original")
        m.deploy()
        m.uninstall("B")
        self.assertEqual((root / "assets/tex.png").read_text(), "original")

    def test_bethesda_case_merge_and_plugins(self):
        root = self.add_steam_game("489830", "Skyrim Special Edition", "Skyrim Special Edition")
        write(root / "Data" / "Skyrim.esm")
        write(root / "Data" / "Textures" / "a.dds")
        m = self.mgr("489830")
        mod = m.install(str(make_zip(self.tmp / "Cool Armor.zip", {
            "Cool Armor/cool.esp": "p", "Cool Armor/textures/armor/x.dds": "t"})))
        self.assertEqual(mod["target"], "Data")
        self.assertTrue((root / "Data/Textures/armor/x.dds").exists())
        self.assertFalse((root / "Data/textures").exists())
        plugins = Path(m.game.prefix) / "drive_c/users/steamuser/AppData/Local/Skyrim Special Edition/Plugins.txt"
        self.assertIn("*cool.esp", plugins.read_text())
        m.undeploy()
        self.assertNotIn("cool.esp", plugins.read_text())
        self.assertFalse((root / "Data/Textures/armor").exists())

    def test_unreal_pak(self):
        root = self.add_steam_game("30", "Unreal Game", "UE")
        write(root / "Proj" / "Content" / "Paks" / "Proj-Windows.pak")
        (root / "Engine").mkdir()
        mod = self.mgr("unreal").install(str(make_zip(self.tmp / "m.zip", {"MyMod_P.pak": "p"})))
        self.assertEqual(mod["target"], "Proj/Content/Paks/~mods")
        self.assertTrue((root / "Proj/Content/Paks/~mods/MyMod_P.pak").exists())

    def _sr2(self, patch: bytes):
        root = self.add_steam_game("9480", "Saints Row 2", "Saints Row 2")
        write(root / "SR2_pc.exe")
        (root / "patch.vpp_pc").write_bytes(patch)
        zip_path = make_zip(self.tmp / "shrek_1.0.zip", {
            "INSTALLATION.txt": "Pack files from MOD_FILES folder into patch.vpp_pc using Gibbed tools.",
            "MOD_FILES/customization_items.xtbl": "shrek", "MOD_FILES/cmbod_human.g_cmesh_pc": "ogre"})
        return root, self.mgr("saints"), zip_path

    def test_saints_row_2_packs_into_patch_vpp(self):
        vanilla = make_vpp([("weapons.xtbl", b"vanilla"), ("customization_items.xtbl", b"old")])
        root, m, zip_path = self._sr2(vanilla)
        mod = m.install(str(zip_path))
        self.assertEqual(mod["target"], "")
        self.assertFalse(mod["guessed"])
        self.assertIn("Gibbed", mod["readme"])
        self.assertEqual(m.last_deploy["packed"], {"file": "patch.vpp_pc", "count": 2})
        pf = Packfile((root / "patch.vpp_pc").read_bytes())
        self.assertEqual(pf.read("customization_items.xtbl"), b"shrek")
        self.assertEqual(pf.read("cmbod_human.g_cmesh_pc"), b"ogre")
        self.assertEqual(pf.read("weapons.xtbl"), b"vanilla")
        self.assertFalse((root / "customization_items.xtbl").exists())  # nothing loose
        m.set_enabled("shrek", False)
        self.assertEqual((root / "patch.vpp_pc").read_bytes(), vanilla)
        m.set_enabled("shrek", True)
        m.undeploy()
        self.assertEqual((root / "patch.vpp_pc").read_bytes(), vanilla)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["SR2_pc.exe", "patch.vpp_pc"])

    def test_saints_row_2_unreadable_packfile_falls_back_safely(self):
        root, m, zip_path = self._sr2(b"not a packfile")
        m.install(str(zip_path))
        self.assertEqual((root / "patch.vpp_pc").read_bytes(), b"not a packfile")
        self.assertTrue(m.last_deploy["warnings"])
        self.assertTrue((root / "customization_items.xtbl").exists())

    def test_file_changed_by_game_is_left_alone(self):
        root = self.add_steam_game("40", "Another", "AN")
        m = self.mgr("another")
        src = self.tmp / "cfg"; write(src / "settings.ini", "mod")
        m.install(str(src))
        (root / "settings.ini").unlink()
        write(root / "settings.ini", "game wrote this")
        res = m.undeploy()
        self.assertEqual((root / "settings.ini").read_text(), "game wrote this")
        self.assertTrue(res["warnings"])

    def test_zip_slip_rejected(self):
        root = self.add_steam_game("50", "Victim", "V")
        bad = make_zip(self.tmp / "bad.zip", {"../../evil.txt": "x"})
        with self.assertRaises(archive.ArchiveError):
            self.mgr("victim").install(str(bad))
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertEqual(list(root.iterdir()), [])

    def test_unknown_mod(self):
        self.add_steam_game("60", "Thing", "T")
        with self.assertRaises(ModError):
            self.mgr("thing").get_mod("nope")


if __name__ == "__main__":
    unittest.main()
