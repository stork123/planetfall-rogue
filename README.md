# Planetfall — Visual Mapper

A Rogue-style visual interface for the 1983 Infocom text adventure **Planetfall**, layered on top of the real Z-machine game engine via dfrotz. No game logic was re-implemented — the Z-machine handles everything (puzzles, combat, NPC dialogue, Floyd's sacrifice, all of it). We just add a visual map layer.

## What it does

- **105 rooms** across 14 disconnected regions (ship, planet surface, underground base, shuttles, etc.)
- **234 bidirectional exits** with conditional locks marked `[locked]`
- **Rogue-style tile rendering** — each room has walls, floor tiles, door openings, item glyphs, and room-type-specific coloring
- **Fog of war** — unexplored rooms are nearly invisible, explored rooms glow, adjacent rooms are dimly visible
- **Animated `@` player** — smoothly slides between rooms with a pulsing glow
- **Real Planetfall game text** — room descriptions, NPC dialogue, puzzle responses, all from dfrotz
- **Multi-session web server** — each player gets their own dfrotz process

## Deploy with Docker

```bash
# Build and run
docker compose up -d

# Or build manually
docker build -t planetfall-rogue .
docker run -p 5000:5000 planetfall-rogue
```

Then open `http://your-server:5000` in any browser.

## Run locally (without Docker)

### Prerequisites
- Python 3.12+
- `frotz` package (provides `dfrotz`)

### Install
```bash
# Install frotz
sudo apt install frotz       # Debian/Ubuntu
# brew install frotz          # macOS

# Install Python dependencies
pip install flask

# Run
python server.py
```

Open `http://localhost:5000`.

## Run as Electron desktop app (Windows)

```bash
npm install
npm start              # dev mode
npx electron-builder --win   # build portable exe
```

The portable exe will be at `dist/PlanetfallRogue-1.0.0-Portable.exe`.

## Architecture

```
Player types "take laser" in the browser
  → HTTP POST /api/command {command, session_id}
  → Flask server looks up the player's dfrotz subprocess
  → Pipes the command to dfrotz stdin
  → dfrotz processes it through the Z-machine game logic
  → dfrotz outputs room name + text to stdout
  → Server parses status line (room, score, moves) and body text
  → Returns JSON {room, score, moves, text, session_id}
  → Browser updates map (@ animates), text panel, exits, score
```

Each player gets a UUID-based session stored in `localStorage`. Sessions time out after 30 minutes of inactivity. Max 20 concurrent sessions (configurable via `MAX_SESSIONS` env var).

## Security

- Input sanitization: only alphanumeric characters, spaces, and basic punctuation are accepted
- Max command length: 200 characters
- Session timeout: 30 minutes
- Max concurrent sessions: 20
- dfrotz runs as a subprocess — no shell access, no file system access beyond the story file

## Files

| File | Purpose |
|---|---|
| `server.py` | Multi-session Flask web server |
| `index.html` | Rogue-style visual client (canvas map + terminal panel) |
| `PLANETFA.DAT` | Z-machine v3 story file (the actual game) |
| `planetfall_data.json` | Map data extracted from ZIL source (room positions, exits, items) |
| `Dockerfile` | Container build |
| `docker-compose.yml` | Container orchestration |
| `main.js` | Electron main process (for desktop app) |
| `preload.js` | Electron preload (IPC bridge) |
| `package.json` | Electron + electron-builder config |

## Map data extraction

The map data (`planetfall_data.json`) was extracted from the [Planetfall ZIL source code](https://github.com/historicalsource/planetfall) — the original Infocom game source. Room exits are in game-logic bytecode inside the compiled `.DAT` file, not in a simple data table, so the ZIL source is the authoritative reference. The `zwalker` Python Z-machine interpreter (github.com/avwohl/zwalker) was used for binary analysis of room and object structures.
