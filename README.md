# NoJunk2 — Crimson Desert drop-table editor

A small, fully offline tool for Crimson Desert. It reads the game's own files on your PC,
lets you untick the junk you don't want enemies dropping anymore, and writes a small patch.
No network calls, no telemetry, no third-party dependencies — only the Python standard library.

- Author: **135207**
- Nexus Mods: https://www.nexusmods.com/crimsondesert/mods/3784
- Current version: **1.0.3**

## What it does

1. Reads `dropsetinfo` out of the game's own PAZ archives (on your machine, read-only)
2. Builds a list of every NPC and every item it can drop
3. You tick what you want gone
4. It writes a small overlay patch — game files are never modified in place

The patch is standard CDUMM loose-file layout (`mods/NoJunk/files/...`), so any
CDUMM-compatible loader can mount it.

## Timeline

| Date | Event |
|---|---|
| 2026-10-23 | First public release on Nexus Mods (mod 3784) |
| 2026-10-30 | Adapted to game build 1.0.0.3392 (PAZ layout changed) |
| 2026-10-31 | Re-verified against build 1.0.0.3424 |
| 2026-11-01 | Rebuilt the NPC → drop-table mapping from game files (804 NPCs, 14,078 item entries) |

## Release hashes (SHA-256)

```
50eec19dfcef5beece074bdbb67818356179cdd3fcda0ea77fd5b95c7ee3e865  NoJunk2-1.0.3-src.zip  (499,276 bytes)
2d7bf880b191ec2180ee78e3c286ad8b80e8b1dc13817efcae5ad54536670a3d  NoJunk2.exe            (13,114,465 bytes)
```

## What this repository contains — and what it does not

The source code is here. It ships **no game assets** and redistributes **no game data**:
everything it needs is read from the user's own installation at runtime.

Third-party reference data (display names and drop rates gathered from community databases)
is **not** included in this repository, and is not bundled for redistribution while the
relevant permission request is still pending. The tool degrades gracefully without it.

## Credits

- **Pearl Abyss** — Crimson Desert; all game assets remain their property
- **CDUMM** (Crimson Desert Ultimate Mods Manager) — mounting layer, no code used
- ChaCha20 per RFC 7539, LZ4 per the official frame format, `hashlittle` by Bob Jenkins (lookup3)

## License

Source is published for transparency and reference. Building and redistributing binaries
from it is permitted with attribution to the author. No warranty of any kind.
