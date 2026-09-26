# GameCapture

Self-contained game recorder (bundled FFmpeg, no OBS). Records your games automatically, marks your
highlights, and turns them into clips - with an Outplayed-style video editor to cut, trim and join them.
Works with any game: pick one of the built-in games or add your own by its `.exe`.

## Project layout
One folder deep from the root, no deeper:

```
GameCapture/
├─ main.py              CLI entry point (GameCaptureCLI)
├─ GameCapture.pyw      double-click launcher (no console)
├─ config.json          old settings location: copied to %APPDATA% on first start, then unused
├─ requirements.txt
├─ Assets/              app_icon.png + app_icon.ico (the app icon), *.svg (all in-app UI icons)
├─ Bin/                 ffmpeg.exe (downloaded by Tools/get_ffmpeg.py)
├─ Capture/             FFmpeg wrapper, recorder, system audio, chapters, global hotkeys
├─ Core/                config, paths, engine, recording library, sidecar files, clip export, editor projects
├─ Games/               game registry, per-game integrations (League, CS2) and the any-game process watcher
├─ Theme/               colours (Palette, MarkerStyle) and the Qt stylesheet
├─ UI/                  main window, pages (incl. the video editor), widgets, SVG icon loader
├─ Test/                automated tests, fake match scenario, fake League server
└─ Tools/               setup scripts (get_ffmpeg.py)
```

Your settings and app data are **not** in the project folder - see [Settings and data](#settings-and-data).

Every module is class-based. `Test/test_project_layout.py` fails if anyone adds a nested folder,
uses an icon that isn't in `Assets/`, or turns the app icon into an SVG.

## Setup (once)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python Tools\get_ffmpeg.py
```

## Run
```powershell
python main.py            # desktop app  (or double-click GameCapture.pyw)
python main.py run        # console only
python main.py check      # verify ffmpeg, GPU encoder, audio
python main.py test       # record 5 s
python main.py live       # what League's live API reports right now (League only)
```
Desktop shortcut: point it at `GameCapture.pyw` and set its icon to `Assets\app_icon.ico`.

## Supported games

| Game | How it's detected | Highlights |
|---|---|---|
| League of Legends | Riot's local Live Client Data API | Automatic: kills, deaths, assists, multikills, objectives, steals, aces |
| Counter-Strike 2 | Valve's official Game State Integration (VAC-safe). Settings > Counter-Strike 2 > **Install**, then restart CS2 once | Automatic: kills, headshots, Double/Triple/Quadra kill, ACE, deaths, assists, round MVPs, final score |
| Teamfight Tactics | League's local API (game mode TFT) - one video per match | Bookmarks |
| Deadlock | The game's process is running; after you close it, the community Deadlock API (deadlock-api.com) cuts the session into one video per match | Automatic after the session: kills, multikills, deaths - plus bookmarks |
| Apex Legends, Valorant, Marvel Rivals, R.E.P.O. | The game's process is running - one video per play session (on by default) | Bookmarks |
| Dota 2, Overwatch 2, Fortnite, Rocket League, Minecraft (Bedrock), PUBG, Rainbow Six Siege, GTA V, Roblox, Genshin Impact, Destiny 2, Helldivers 2, Elden Ring | The game's process is running - one video per play session (switch on in Settings > Games) | Bookmarks |
| **Any other game** | Settings > Games > **Add a game**: pick it from the running programs or browse to its `.exe` | Bookmarks |

**Bookmarks:** press **Ctrl+Alt+B** (or the Bookmark button) after a great play in any game, including
manual recordings. Bookmarks become highlights like any other: timeline icons, clips, reels, Highlights mode.

Each game can be switched on/off in Settings > Games. If a game update renames its executable, change
it on that game's settings page - it takes effect straight away, no restart. Games without a live match
API (Valorant, Marvel Rivals and every game you add yourself) record whole play sessions. Deadlock
records the session too, then splits it into matches once they appear online (Settings > Deadlock).

### Adding any game
1. Start the game.
2. Settings > Games > **Add a game**, pick it from the list of running programs (or **Browse...** to
   its `.exe`), check the name, press **Add game**.
3. From now on it records whenever that program runs. Rename it, change its `.exe` or remove it
   on its own page under *My Games* (removing a game never deletes its recordings).

## Audio isolation
Settings > Capture > Audio:
- **Everything I hear** - all sound on the PC (default), or **Game + chosen apps** - only the game
  being recorded (detected automatically) plus apps you add, e.g. `Discord.exe` for your friends' voices.
  Uses Windows' per-app capture (proc-tap), Windows 10 2004 or newer.
- **Microphone** - add your default mic in either mode.
- **Volume per source** (0-200 %).
- **Separate audio tracks** - track 1 is the mix (what players and Discord play); each source also gets its
  own named track (Game, Discord, Microphone...) so you can rebalance or mute them in an editor.
  Clips keep every track; Discord-size copies keep only the mix.

## Recording modes
For games with a live match API (League of Legends / TFT). Switch in the top bar
(**Mode: Session | Match | Highlights**) or on the game's settings page. Applies from the next match / session.
Every other game records whole sessions.

| Mode | What you get |
|---|---|
| **Session** | Recording starts when you open the game and stops when you close it: one video with every match's highlights (markers say "Match 2 start - <character>" etc.) |
| **Match** | One video per match, game start to end screen (default) |
| **Highlights** | The match is recorded, then only your highlight moments (kills, multikills, objectives, first blood, aces + clip padding) are kept as one short video; the full recording is deleted. Built with stream copy, so it takes seconds and costs no FPS |

If you stop a Session recording by hand, it stays stopped until you close the game.

## The app
- **Sessions** - recording cards (thumbnail, game, character, result, KDA - whatever the game
  reports), highlight title bar (favourite / folder / delete / Share clip / Edit / Export clips /
  Highlight reel), player, and an icon timeline above a minute ruler. Share copies the clip so you
  can paste it into Discord; **Edit** sends the ticked highlights to the video editor.
- **Favorites** - starred highlights from every recording
- **Clips** - gallery of exported clips with their size; gold = too big for Discord.
  **Fit for Discord** makes a copy under your limit (20 MB free / 50 MB Nitro Basic / 500 MB Nitro,
  set in Settings > Clips) and copies it for pasting. **Share clip** on a highlight does this automatically
- **Video editor** - see [below](#video-editor)
- **Log** - everything the engine reports
- **Settings** (saves as you change things; a banner offers *Restart now* when needed)
  - *General*: **Games** (searchable tiles, on/off per game, **Add a game** for anything not
    listed), **Capture** (Low / Medium / High / Ultra presets or Custom: resolution,
    frame rate, constant-quality or bitrate, encoder, monitor, audio - with a banner that warns
    if anything costs in-game FPS), **Auto-record**, **Clips**, **Storage** (folder, colour-coded drive usage with a legend, and an
    auto-delete limit that removes the oldest recordings - never clips, and optionally never
    recordings with favourites),
    **Notifications** (tray pop-ups when a recording starts / is saved), **App** (tray, hotkeys)
  - *My Games*: per-game page (auto-record toggle, executable, modes to skip, what's detected;
    rename / remove for games you added)
- Resizing (e.g. 1440p screen -> 1080p video) is done on the GPU when possible; GameCapture tests
  which resizer works on your PC and falls back to the CPU only if it must (the banner tells you)
- Closing the window keeps recording in the **tray** (tray icon shows the state as a dot)

| Key | Action |
|---|---|
| Space | play / pause (Sessions and editor) |
| Left / Right | -5 s / +5 s |
| N / P | next / previous highlight (editor: next / previous clip) |
| S | editor: split the clip at the playhead |
| Delete | editor: remove the selected clip |
| Ctrl+D | editor: duplicate the selected clip |
| Ctrl+Alt+R | manual record (works in game) |
| Ctrl+Alt+B | bookmark a highlight (works in game) |
| Ctrl+Alt+Q | quit |

## Video editor
Outplayed-style editing, in the sidebar under **Video editor**:

- **Project videos** (left): videos you imported (**Import videos**: any recording, clip or other
  `.mp4` / `.mkv` / `.mov` / `.webm`) or sent from Sessions with **Edit**. Double-click one (or
  **Add to timeline**) to put the whole video on the timeline. Right-click to rename or remove it.
- **Timeline** (bottom): one filmstrip per clip with its highlight icons. Click to select and seek,
  drag a clip to move it, drag its edges to trim it. **Split** (scissors / `S`) cuts the clip at the
  playhead, **Duplicate** (`Ctrl+D`) and **Delete** do what they say. The preview plays the clips in
  order, straight across cuts and files.
- **Export settings**: resolution (same as the video, 1440p ... 480p - never upscaled), frame rate,
  quality and sound. **Export video** renders one `.mp4` into your clips folder, using the GPU encoder
  the recorder picked. Clips of different sizes are letterboxed; clips without sound get silence.
- **Projects** are saved automatically as you edit (`Projects\` in the [settings folder](#settings-and-data)).
  Switch between them, start a **New project** or delete one from the top bar - editing never
  changes your original videos, and deleting a project keeps them.

## Settings and data
Settings live in your user profile, so updating, moving or re-downloading GameCapture never loses them:

| What | Where |
|---|---|
| Settings (`config.json`) | `%APPDATA%\GameCapture\config.json` |
| Logs | `%APPDATA%\GameCapture\Logs\` |
| Video editor projects | `%APPDATA%\GameCapture\Projects\` |
| Editor filmstrip cache | `%APPDATA%\GameCapture\Cache\` (safe to delete) |
| Recordings and clips | `~\Videos\GameCapture` by default - Settings > Storage |

Settings > App > **Open settings folder** takes you there. An old `config.json` in the project folder is
copied over the first time you start this version (the original is left alone). Set the environment
variable `GAMECAPTURE_HOME` to keep everything in another folder instead (e.g. a portable install).

## Icons
- **App icon**: `Assets/app_icon.png` (window, sidebar logo, tray) and `Assets/app_icon.ico`
  (Windows shortcuts / taskbar). Raster on purpose.
- **UI icons**: `Assets/*.svg`, 24x24, drawn with `currentColor`. `UI/icons.py` tints them to
  any theme colour at runtime, so one file covers muted/hover/accent/disabled states.
  To add one: drop `name.svg` in `Assets/` and use `Icons.icon("name", colour)`.
- Timeline/highlight icons: `marker_<type>.svg`, mapped in `Theme/palette.py` (`MarkerStyle`).

## Development & testing
```powershell
python -m unittest discover -s Test -v
```
About 150 tests, about a minute, no game needed: a scripted match runs through the real match watcher
on a fake clock; plus games you add yourself, the settings folder, editor timeline maths and projects,
sidecar versioning, library, clip maths, project-layout rules and - if ffmpeg is present - real
clip/reel/chapter/thumbnail and editor exports.

End to end with a fake League:
```powershell
python Test\fake_league.py                                             # terminal 1
$env:GAMECAPTURE_LIVE_URL = "http://127.0.0.1:2998/liveclientdata/"    # terminal 2
python main.py
```

## Sidecar format
Every auto-recording gets a `.json` with `"schema"` and `"game_id"`. Older files are upgraded
automatically; files from a newer GameCapture open read-only (`Core/sidecar.py`).

---
GameCapture is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games
or anyone officially involved in producing or managing Riot Games properties. Riot Games and all
associated properties are trademarks or registered trademarks of Riot Games, Inc.
