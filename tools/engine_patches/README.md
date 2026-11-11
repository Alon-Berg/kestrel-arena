# Patched engine builds

Some engines of the roster have bugs in their own time handling that only show in very fast games (0.5 s + 0.005 s and shorter): a search that is
cut off before its first iteration is done, a limit that rounds down to "no limit", a 15.6 ms timer, a negative duration. Instead of working around
them in the Arena (`KNOWN_QUIRKS` in `arena/engines.py`), the bug was found in the engine's open source and fixed there. Only the broken path was
changed - the search is the same.

**How the Arena uses them.** If `engines/patched/<id>.exe` exists, a tournament (and `tools/check_roster.py`) plays that file instead of the
official exe of the engine with the same id. The engine is shown as `... (patched)`, gets none of the workarounds of `KNOWN_QUIRKS` (it does not
need them) and the command-line arguments / folder of the official entry are not used. `python tools/check_roster.py --official` plays the
official exes, and `"patched": false` in an engine's entry of `data/settings.json` does the same for one engine. The builds are not part of the
repository (`engines/*` is ignored): rebuild them as described below, or copy the exes into `engines/patched/`.

| id | engine | source (tag) | licence | the bug | the fix |
|---|---|---|---|---|---|
| `quanticade` | Quanticade Cronus 3.0 | github.com/Quanticade/Quanticade (`Cronus-3.0`) | GPL-3.0 | The clock is `GetTickCount` / `gettimeofday`, which advance in steps of 15.6 ms on Windows. A search with a budget of a few ms was cut off at the first tick - on average after 15 ms instead of 5 - and in about 1 search of 100 before depth 1 was done: `bestmove (none)`. | `get_time_ms` uses `QueryPerformanceCounter`; the hard time limit is not checked during the first iteration; if there is still no move, any legal move is played instead of `(none)`. |
| `yukari` | Yukari 2025.11.1 | github.com/yukarichess/yukari (`2025.11.1`) | Prosperity Public License 3.0.0 (noncommercial) | `search_time` takes a fixed 20 ms off every time. Below 20 ms that is negative, and `Duration::from_secs_f32` panics ("cannot convert float seconds to Duration: value is negative"); a search that ends before depth 1 leaves no move and the caller's `pv[0]` panics too. | The 20 ms are at most a quarter of the time that is left; limits are never below 2 ms; an empty result becomes the first legal move. |
| `smallbrain` | Smallbrain 7 | github.com/Disservin/Smallbrain (`7.0.0`) | GPL-3.0 | With less than about 50 ms on the clock both time limits round down to 0, which the search takes for "no limit": it searches until it is stopped (a flag falls). The principal variation is never cleared between searches, so a search cut off at depth 1 can play the move of the previous position. | Limits are at least 1 ms; `pvTable[0][0]` is cleared at the start of a search; if there is no move when the search ends, the first legal move is played. |
| (not used) | Elixir 3.0 | github.com/ArjunBasandrai/elixir-chess-engine (`v3.0`) | GPL-3.0 | `DEFAULT_MOVE_OVERHEAD` (100 ms) is taken off every time, also off `go movetime`: `go movetime 100` has a budget of 0 ms and ends after the first 1024 nodes (depth 1). | For `go movetime` only about 5 ms are kept back (`elixir-v3.0.patch`). **Not in use:** the build of the v3.0 source searches differently from the official release (other node counts), so the Arena adds 95 ms to a fixed time per move for Elixir instead (`KNOWN_QUIRKS["elixir"]`). |

The patches are in this folder (`<engine>-<tag>.patch`, made with `git diff`; apply them with `git apply` in a checkout of the tag).

## What was checked

* **Same search.** At a fixed depth the official exe and the patched build give the same node count, in three positions (start, middlegame, endgame):
  Yukari depth 12 and 17, Quanticade depth 15 and 19, Smallbrain depth 14 and 19 (every number identical). Playing strength is therefore the same except
  where the official engine fails.
* **Speed** at the same depths (nodes per second, the official exe = 100 %): Smallbrain 95 - 143 %, Quanticade 105 - 128 % (the release is a clang
  build, these are gcc builds), Yukari about 90 - 98 % (a plain `cargo build`; the release is probably profile-optimised). Small samples on a laptop,
  so +/- 10 %: the patched Quanticade is a little faster than the engine that was rated, the patched Yukari a little slower.
* **The bug itself, in a raw UCI session** (`go movetime 5`, 400 random positions): official Quanticade 4 x `(none)`, median answer time 15 ms; patched
  0 x `(none)`, median 2.3 ms. Official Yukari: crash (panic) at every movetime of 20 ms or less; patched: 0 unusable answers in 300 positions at each of 5, 15, 20 and 30 ms
  and with clocks of 10 and 200 ms. Official Smallbrain: no answer at all with a clock of 40, 20 or 5 ms (it searches until stopped); patched: a legal
  move in 3 - 10 ms.
* **Games against Kestrel 3.2** with every workaround of the Arena switched off (`--quirks '{"<id>": {"retry_bad_move": false}}'`), 48 games each:

| engine | time control | official exe | patched build |
|---|---|---|---|
| Quanticade Cronus 3.0 | 0.5 s + 0.005 s | 8 games lost ("no move") | 0 |
| Yukari 2025.11.1 | 0.5 s + 0.005 s | 4 games lost (crash) | 0 |
| Smallbrain 7 | 0.25 s + 0.0025 s | 25 games lost (time forfeit) | 0 |

With the defaults of the Arena (patched builds in use, no workarounds for them): 48 games each of the three at 0.5 s + 0.005 s and at 0.25 s + 0.0025 s, 0 faults.

## How the builds were made

Windows 11, g++ 16.2 (WinLibs MinGW-w64), Rust 1.99.1 (`x86_64-pc-windows-gnu`). Each engine: `git clone --depth 1 --branch <tag> <source>`, `git apply <patch>`, then

```
# Quanticade (BMI2 build; the network hati.nnue is in the repository and is embedded)
gcc -std=gnu11 -O3 -funroll-loops -flto -fno-exceptions -DIS_64BIT -DNDEBUG -DUSE_AVX2 -DUSE_SIMD -mavx2 -mbmi -mbmi2 -march=haswell \
    -DNETWORK_NAME=\"hati.nnue\" -DEVALFILE=\"hati.nnue\" -static -o quanticade.exe Source/*.c -pthread -lm

# Yukari (its source needs nightly features: RUSTC_BOOTSTRAP=1 allows them on the stable compiler; the main thread needs a bigger stack than
# the default 1 MB, because the engine keeps several MB of tables on it - without --stack it dies at start-up with "has overflowed its stack")
set EVALFILE=<path>/yukari_91fb3bbf.bin
set RUSTFLAGS=-C target-cpu=x86-64-v3 -C link-arg=-Wl,--stack,67108864
set RUSTC_BOOTSTRAP=1
cargo build --release -p yukari            # -> target/release/yukari.exe

# Smallbrain (its own Makefile; the network is embedded)
cd src && mingw32-make build=x86-64-bmi2 -j6     # -> smallbrain-x86-64-bmi2.exe
```

Copy the result to `engines/patched/<id>.exe` (`quanticade.exe`, `yukari.exe`, `smallbrain.exe`).

## Not patched

* **Rice 8.0.0** and **Altair 7.0.0** (now and then a stale illegal move when the machine is busy; Rice also keeps 50 ms back from every time): source found
  (github.com/rafid-dev/rice, github.com/Alex2262/AltairChessEngine), not done. The Arena's second try ("every engine in very fast games") covers them.
* **Uralochka 3.42a**: the source is at github.com/FreemanZlat/Uralochka3, but the network is not in the repository, so the release cannot be rebuilt.
  Lower `TimeMargin` (the Arena's automatic engine time overhead does) and the second try cover it.
* **Bagatur 5.5c** (Java, rebuilds its board for every new start position) and **Serendipity 1.0** (Java, github.com/xu-shawn/Serendipity, AGPL-3.0):
  JVM engines. Their pauses (class loading, JIT, garbage collection: 10 - 30 ms, a second for Bagatur's first search of a new position) come from the
  JVM, and they are started through a launcher, not as one exe, so a patched build would need more than a time-handling fix. They keep the workarounds of `KNOWN_QUIRKS`.
* **Titan 1.1.0** (ignores `go movetime`): no source found (author Jeff Powell). Use a clock, not a fixed time per move, with it.
