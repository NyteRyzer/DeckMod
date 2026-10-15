# DeckMod

A mod manager for Steam Deck and Linux that works with **any installed game**, including games with no Steam Workshop.

- **Finds your games automatically:** Steam (internal storage and SD card), Heroic (GOG, Epic, Amazon), and Lutris. You can add any other game folder by hand.
- **Installs mods from whatever you download:** `.zip`, `.7z`, `.rar`, `.tar.*`, plain folders, single `.pak`/`.dll`/`.esp` files, or a direct download URL.
- **Puts files in the right place:** it detects Unity, Unreal, Bethesda, RE Engine and Godot games, strips pointless wrapper folders, and matches the mod's layout against the game's.
- **Never breaks your game:** mods are kept in a separate staging area and deployed with hardlinks or copies. Every original file it overwrites is backed up. **Restore vanilla** puts the game folder back exactly as it was.
- **Handles Proton quirks:**
  - It merges `data/` into `Data/` instead of creating a duplicate folder that Wine can't see.
  - It tells you the `WINEDLLOVERRIDES` launch option that DLL loaders like BepInEx, REFramework and UE4SS need.
  - For Bethesda games, it updates `Plugins.txt` inside the Proton prefix.
- **Repacks game archives for you:** some games ignore loose mod files. For Saints Row 2, DeckMod packs mod files into `patch.vpp_pc` itself, so there's no need for Gibbed tools or GOTR. It first checks that it fully understands your game's archive format, and leaves the game untouched if it doesn't.
- **Load order and conflicts:** mods lower in the list win when two mods ship the same file. Mods can be turned on and off with one click.
- **No dependencies:** it only needs the Python 3 that already ships with SteamOS. There is no pip and no root, and SteamOS updates won't wipe it.

## Install (Steam Deck)

1. Switch to **Desktop Mode**.
2. Open **Konsole**.
3. Run:

```bash
git clone https://github.com/NyteRyzer/cool-app.git ~/deckmod-src
~/deckmod-src/install.sh
```

**DeckMod** then appears in the application menu. To launch it from Game Mode, right-click it in the menu and choose **Add to Steam**.

## Using it

### The easy way

Run `deckmod ui`, or click DeckMod in the app menu. A page opens in your browser:

1. Pick a game.
2. Drop in a mod archive, or click **Install** next to anything in `~/Downloads`.
3. That's it. Mods are applied straight away.

### Command line

```bash
deckmod games                                  # list everything it found
deckmod install "hollow" ~/Downloads/Mod.zip   # game = id, Steam appid, or part of the name
deckmod mods hollow                            # load order
deckmod disable hollow 2                       # by number, id or name
deckmod move hollow "Big Texture Pack" 1       # change priority
deckmod set-target hollow 3 "Mods"             # fix a mod that landed in the wrong folder
deckmod purge hollow                           # back to vanilla (mods stay installed)
deckmod deploy hollow                          # re-apply mods
deckmod add-game "My Game" /path/to/game       # games bought outside any launcher
```

## How placement works

DeckMod tries these rules in order:

1. If the mod contains a loader DLL (`winhttp.dll`, `dinput8.dll`, `version.dll`, and similar) or an `.exe`, it goes next to the game's executable.
2. If the mod's top-level folders already exist in the game (`Data/`, `BepInEx/`, `Content/Paks/`, …), it is merged there.
3. If those folders have well-known names for the game's engine, it uses them. For example, `textures/` in a Bethesda game goes to `Data/`.
4. Otherwise it decides by file type: `.pak` goes to `~mods` in Unreal games, `.dll` goes to `BepInEx/plugins` in Unity games, `.esp` goes to `Data` in Bethesda games.
5. Failing all of that, it uses the engine's default folder and flags the mod **"location guessed"**. Check the mod's readme and use **Folder** (or `set-target`) if it doesn't work.

## Things to know

- **No tool can make every mod work in every game.** Some mods need a game-specific loader, such as BepInEx, MelonLoader, SKSE, UE4SS or REFramework. Install the loader first, like any other mod, then install mods for it. DeckMod tells you which launch option the loader needs under Proton.
- **Updates and file checks:** a Steam update or "Verify integrity of game files" may restore original files. Click **Re-apply mods** afterwards. If the game itself changed a mod file, DeckMod leaves it alone rather than deleting it.
- **7z and rar archives** need a system extractor. SteamOS usually has `bsdtar`. If an archive won't open, install the 7-Zip or Ark flatpak, or re-pack the mod as a `.zip`.
- **Stored data:** everything lives in `~/.local/share/deckmod`. Deleting that folder after **Restore vanilla** removes DeckMod completely.
- **Copy mode:** if you'd rather use plain copies than hardlinks, put `{"link_mode": "copy"}` in `~/.local/share/deckmod/settings.json`. Games on an SD card always use copies.

## Development

```bash
python3 -m unittest discover -s tests -v
./deckmod.sh ui          # run from the source tree
```
