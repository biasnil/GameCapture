# GameCapture

Self-contained game recorder (bundled FFmpeg, no OBS). Records every League of Legends match
automatically, marks your highlights, and turns them into clips - built to support more games later.

## Project layout
One folder deep from the root, no deeper:

```
GameCapture/
├─ main.py              CLI entry point (GameCaptureCLI)
├─ GameCapture.pyw      double-click launcher (no console)
├─ config.json
├─ requirements.txt
├─ Assets/              app_icon.png + app_icon.ico (the app icon), *.svg (all in-app UI icons)
├─ Bin/                 ffmpeg.exe (downloaded by Tools/get_ffmpeg.py)
├─ Capture/             FFmpeg wrapper, recorder, system audio, chapters, global hotkeys
├─ Core/                config, paths, engine, recording library, sidecar files, clip export
├─ Games/               game registry + League integration (live client, match watcher)
├─ Theme/               colours (Palette, MarkerStyle) and the Qt stylesheet
├─ UI/                  main window, pages, widgets, SVG icon loader
├─ Test/                automated tests, fake match scenario, fake League server
├─ Tools/               setup scripts (get_ffmpeg.py)
└─ Logs/                created at runtime
```

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
python main.py live       # what League's API reports right now
```
Desktop shortcut: point it at `GameCapture.pyw` and set its icon to `Assets\app_icon.ico`.

## Supported games

| Game | How it's detected | Highlights |
|---|---|---|
| League of Legends | Riot's local Live Client Data API | Automatic: kills, deaths, assists, multikills, objectives, steals, aces |
| Counter-Strike 2 | Valve's official Game State Integration (VAC-safe). Settings > Counter-Strike 2 > **Install**, then restart CS2 once | Automatic: kills, headshots, Double/Triple/Quadra kill, ACE, deaths, assists, round MVPs, final score |
| Teamfight Tactics | League's local API (game mode TFT) - one video per match | Bookmarks |
| Apex Legends, Valorant, Deadlock, Marvel Rivals, R.E.P.O. | The game's process is running - one video per play session | Bookmarks |

**Bookmarks:** press **Ctrl+Alt+B** (or the Bookmark button) after a great play in any game, including
manual recordings. Bookmarks become highlights like any other: timeline icons, clips, reels, Highlights mode.

Each game can be switched on/off in Settings > Games. If a game update renames its executable, change
it on that game's settings page. Valorant has no local match API and Riot doesn't issue personal keys
for it; Marvel Rivals' and Deadlock's community APIs only have match totals after the game, so those
games record whole sessions.

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
Switch in the top bar (**Mode: Session | Match | Highlights**) or Settings > League of Legends.
Applies from the next match / session.

| Mode | What you get |
|---|---|
| **Session** | Recording starts when you open League and stops when you close it: one video with every match's highlights (markers say "Match 2 start - Ahri" etc.) |
| **Match** | One video per match, game start to end screen (default) |
| **Highlights** | The match is recorded, then only your highlight moments (kills, multikills, objectives, first blood, aces + clip padding) are kept as one short video; the full recording is deleted. Built with stream copy, so it takes seconds and costs no FPS |

If you stop a Session recording by hand, it stays stopped until you close League.

## The app
- **Sessions** - match cards (thumbnail, champion, result, KDA), highlight title bar
  (favourite / folder / delete / Share clip / Export clips / Highlight reel), player, and an
  icon timeline above a minute ruler. Share copies the clip so you can paste it into Discord.
- **Favorites** - starred highlights from every match
- **Clips** - gallery of exported clips with their size; gold = too big for Discord.
  **Fit for Discord** makes a copy under your limit (20 MB free / 50 MB Nitro Basic / 500 MB Nitro,
  set in Settings > Clips) and copies it for pasting. **Share clip** on a highlight does this automatically
- **Log** - everything the engine reports
- **Settings** (saves as you change things; a banner offers *Restart now* when needed)
  - *General*: **Games** (searchable tiles, on/off per game - League is supported, others are
    listed as planned), **Capture** (Low / Medium / High / Ultra presets or Custom: resolution,
    frame rate, constant-quality or bitrate, encoder, monitor, audio - with a banner that warns
    if anything costs in-game FPS), **Auto-record**, **Clips**, **Storage** (folder, colour-coded drive usage with a legend, and an
    auto-delete limit that removes the oldest recordings - never clips, and optionally never
    recordings with favourites),
    **Notifications** (tray pop-ups when a recording starts / is saved), **App** (tray, hotkeys)
  - *My Games*: per-game page (auto-record toggle, modes to skip, what's detected)
- Resizing (e.g. 1440p screen -> 1080p video) is done on the GPU when possible; GameCapture tests
  which resizer works on your PC and falls back to the CPU only if it must (the banner tells you)
- Closing the window keeps recording in the **tray** (tray icon shows the state as a dot)

| Key | Action |
|---|---|
| Space | play / pause |
| Left / Right | -5 s / +5 s |
| N / P | next / previous highlight |
| Ctrl+Alt+R | manual record (works in game) |
| Ctrl+Alt+B | bookmark a highlight (works in game) |
| Ctrl+Alt+Q | quit |

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
123 tests, about a minute, no League needed: a scripted match runs through the real match watcher
on a fake clock; plus sidecar versioning, library, clip maths, project-layout rules and - if
ffmpeg is present - real clip/reel/chapter/thumbnail exports.

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
