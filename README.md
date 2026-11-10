<p align="center"><img src="assets/logo/kestrel-logo-256.png" width="128" alt="Kestrel logo"></p>

# Kestrel Arena

A desktop app that runs chess-engine tournaments with real games between UCI engines, and lets you play against and analyse with the **Kestrel** engine that is bundled with it. Every result comes from an actual game - nothing is estimated or drawn with random numbers.

Start it with `KestrelArena.exe` (or `python main.py` from the source folder). Nothing is installed and no network is used: the app talks to the engines through UCI and shows its pages in a small local window.

## Tournament tab - real games
* Pick engines (quick picks: "Kestrel + 7 nearest" in Elo, top 8, top 16), a format (**round robin, gauntlet, Swiss, knockout**), the number of games per pairing and a time control (clock + increment, fixed time, nodes or depth per move).
* Games run in parallel (set "Parallel games"). Every pair of games uses the same opening in both colours; openings come from the bundled balanced suite (600 positions made with Kestrel), your own EPD/PGN file, or the start position.
* Live view: boards with ticking clocks and evaluations, standings with a fitted **Elo +- error**, W/D/L, score and Sonneborn-Berger, a crosstable, a bracket (knockout), a replay viewer with move list and evaluation graph, PGN and CSV export.
* **Pause**, **Stop** and **Continue**: a stopped tournament (even after closing the app) is listed under "Saved tournaments" and continues with the games that are missing. Results are in `data/tournaments/<id>/`.
* Games are played to their natural end (mate, draw by rule) - adjudication is off by default; you can switch on draw / win adjudication (shown in the setup) to save time. Illegal moves, crashes and flag falls lose the game; the time margin (default 600 ms, the value your fastchess runs use) is the allowed overrun.

For fair results keep *parallel games x threads* below the number of logical CPUs. Engines and the app run at **above-normal priority** (setup: *Process priority*), so programs that work in the background cannot make a move late: with the PC busy (a Stockfish labelling job on every core) the same 30-game test lost 18 games on time at normal priority and none at above normal. On a quiet PC the setting makes no difference. At very fast time controls (3+0.03) a flag is still possible when the CPU is overloaded by the games themselves.

## Play tab
Play against Kestrel (or any engine in your list) by clicking a piece and its target square. At **full strength** you choose the thinking time. For Kestrel you can also pick a **level**: twelve levels from about 500 to about 2800. A level decides how many moves ahead Kestrel looks, how many of its best moves it keeps in mind and how carelessly it chooses among them, and now and then it overlooks something altogether, as a weaker player does (level 1300 makes a serious mistake about every twenty moves, level 500 every eight to twelve). The numbers are estimates, matched to the size of the mistakes players of that rating make; they are not ratings from games against people. They are the same levels as on the web page (https://alon-berg.github.io/Kestrel-chess-engine/play/); `arena/levels.py` is a port of its `levels.js`, and `tests/test_levels.py` checks that both choose the same moves. Kestrel's own `UCI_Elo` option is not used for this: it limits the nodes searched per move, but the engine looks at its node count only every 2,048 nodes, so every setting below about 2300 plays alike. For other engines that have a `UCI_Elo` option the strength can still be limited with it. *Take back*, *Resign*, PGN download. The **Analysis** board shows the engine's best lines (1-5) for any position; move pieces for both sides, paste a FEN.

## Engines tab
Kestrel 3.2 is bundled (`engines/Kestrel`; the build for your CPU - AVX2, SSE4.2 or generic - is chosen automatically, and a newer `kestrel-<version>-avx2.exe` dropped into that folder shows up by itself). Add any UCI engine (file, optional command-line arguments, Elo, UCI options). "Import tournament roster" adds the engines of the `chess_engine_tournament_roundrobin` project (`config/verify_results.json`) with their ratings.

## Running from source
Windows 10/11 with Python 3.12 (Edge WebView2 is part of Windows 11).
1. `pip install -r requirements.txt`
2. Put the Kestrel builds into `engines/Kestrel/` - the engine files are not stored in the repository, see `engines/README.md` (the packaged exe contains them). Without them the app still works with engines you add yourself.
3. `python main.py` (or `python main.py --browser` to use your web browser instead of the app window).
4. Optional: `python tools/build_exe.py` builds `dist/KestrelArena/KestrelArena.exe` (needs PyInstaller and Pillow; it keeps the `data` folder of an earlier build); `python tools/make_openings.py` rebuilds the opening suite with Kestrel.

## Files
* `data/settings.json` - engine list and preferences; `data/tournaments/` - saved tournaments (delete a folder to remove one). Both are created when you use the app and are not part of the repository.
* Source: `arena/` (Python: UCI, games, formats, tournaments, levels, server), `web/` (the interface), `tests/` (`python tests/smoke_backend.py`, `smoke_resume.py`, `test_formats.py`, `test_levels.py`), `tools/` (`build_exe.py`, `make_openings.py`, `make_roster_snapshot.py`, `make_logo.py`, `make_level_vectors.py`).
* When the levels of the web page change (the table in `docs/play/levels.js` of the Kestrel repository): `python tools/make_level_vectors.py` (needs Deno) rewrites `tests/levels_vectors.json` from the JavaScript; update `TABLE` in `arena/levels.py` until `python tests/test_levels.py` passes.
* `assets/logo/` - the Kestrel logo (a white K on a small chessboard; SVG, PNG 16-1024 px, ICO), the same one for the engine and the app; `python tools/make_logo.py` redraws it.
* `data/openings.epd` (600 balanced opening positions made with Kestrel) and `data/roster_ratings.json` (ratings of the 100-engine roster tournament, used when importing the roster) are shipped with the app.

## Third-party software
python-chess (GPL-3.0), pywebview (BSD-3-Clause), psutil (BSD-3-Clause), PyInstaller (GPL-2.0 with the bootloader exception). Kestrel is MIT licensed.
