# engines/

The engine files are not stored in this repository (each Kestrel build is about 80 MB).

Put the Kestrel Windows builds from the [Kestrel releases](https://github.com/Alon-Berg/Kestrel-chess-engine/releases) into `engines/Kestrel/` under these names:

| release file | name here | for |
|---|---|---|
| `Kestrel-3.2-windows-x86-64-v3.exe` | `kestrel-3.2-avx2.exe` | CPUs with AVX2 (almost every PC since 2013) |
| `Kestrel-3.2-windows-x86-64-v2.exe` | `kestrel-3.2-sse42.exe` | CPUs with SSE4.2 only |
| `Kestrel-3.2-windows-x86-64.exe` | `kestrel-3.2-generic.exe` | any 64-bit CPU |

The app picks the best build for the CPU it runs on, and a newer `kestrel-<version>-<avx2|sse42|generic>.exe` shows up in the engine list by itself. Any other UCI engine can be added in the app (Engines tab).
