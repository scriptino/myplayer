# myplayer7b.py - Complete Feature Analysis

## PROJECT OVERVIEW
A Textual TUI music player that combines:
- **MOCP (Music On Console)**: For local audio files
- **MPV**: For streaming (YouTube, web URLs)
- **Gum**: For advanced playlist search with multi-select
- **Eco Mode**: Low-power terminal UI with smart polling

---

## CLASS STRUCTURE & RESPONSIBILITIES

### 1. HelpScreen (ModalScreen)
**Purpose**: Display keyboard shortcuts in a modal popup
**Key Features**:
- Markdown-based help text in Italian
- Closable via ESC or H key
- Scrollable container

---

### 2. MocpDirectoryTree (DirectoryTree)
**Purpose**: Left panel file browser
**Key Features**:
- Navigate file system
- Bindings:
  - `H`: Jump to home directory
  - `M`: Jump to /mnt directory  
  - `a`: Add selected audio file to playlist
- Supports: .mp3, .flac, .ogg, .wav, .m4a, .opus

---

### 3. MocpPlaylist (ListView)
**Purpose**: Right panel - internal playlist visualization
**Key Features**:
- Visual list of queued tracks
- Bindings:
  - `d`: Delete track
  - `C`: Clear playlist
  - `Enter`: Play selected

---

### 4. M3XListView (ListView)
**Purpose**: M3X playlist viewer
**Key Features**:
- Parse and display M3X playlists
- Real-time filtering with text input
- Bindings:
  - `a`: Add to queue
  - `Backspace`: Exit M3X
  - `g`/`G`: Launch Gum search (normal/fuzzy)
- Multi-select via Gum with TAB key

**M3X Format**: `Title | URL\n` (one per line)

---

### 5. MocpUltimateCombo (Main App)
**Purpose**: Orchestrator and state manager

**Central State**:
- `playlist_interna`: Source of truth (list of track dicts)
- `indice_corrente`: Current playing index
- `sorgente_riproduzione`: Playback source ("playlist"/"directory"/None)
- `active_engine`: Current engine ("mocp"/"mpv"/None)
- `mpv_volume`: Volume 0-100
- `brani_m3x_correnti`: Current M3X tracks
- `update_timer`: 1Hz supervision loop

**Lifecycle**:
1. `__init__()`: Initialize MPV, setup MPV events
2. `on_mount()`: Start MOCP server if needed
3. `compose()`: Build UI layout
4. Main loop: Process events, supervise playback

---

## FEATURE BREAKDOWN

### A. PLAYBACK ENGINES

#### MOCP (Subprocess-based)
- Local file support only
- Commands:
  - Clear: `mocp -c`
  - Add: `mocp -a <file>`
  - Play: `mocp -p`
  - Pause: `mocp -G` (toggle)
  - Stop: `mocp -s`
  - Seek: `mocp -k ±N`
  - Volume: `mocp -v [+/-]N`
  - Info: `mocp -i` (full), `mocp -Q FORMAT`

#### MPV (Python Library - libmpv wrapper)
- Streaming support (YouTube, HLS, HTTP)
- Python API:
  - `mpv_player.play(url)`
  - `mpv_player.pause`
  - `mpv_player.seek(±N)`
  - `mpv_player.volume = N`
  - `mpv_player.time_pos`, `mpv_player.duration`
- Events: `@mpv_player.property_observer('time-pos')`

---

### B. TRACK LOADING & PLAYBACK

#### Directory Selection Flow
```
User clicks file
  ↓
on_directory_tree_file_selected()
  ↓
If .m3x: carica_file_m3x() → populate M3XListView
If audio: riproduci_indice_playlist() → play via MOCP
```

#### URL Input Flow
```
User pastes YouTube URL
  ↓
on_input_submitted()
  ↓
aggiungi_a_playlist_interna()
  ↓
Auto-play if no engine active
```

#### M3X Playlist Flow
```
carica_file_m3x():
  Parse file (title | url format)
  Populate brani_m3x_correnti
  Create M3XListView items
  Hide file browser, show M3X view
```

---

### C. PLAYLIST MANAGEMENT

#### Data Structure
```python
playlist_interna = [
    {"path": "file.mp3", "title": "Song 1"},
    {"path": "https://youtube.com/watch?v=...", "title": "Song 2"},
    ...
]
```

#### Operations
1. **Add**: `aggiungi_a_playlist_interna(path, title)`
2. **Play**: `riproduci_indice_playlist(index)`
3. **Delete**: Widget action calls `playlist_interna.pop(index)`
4. **Clear**: Widget action clears entire list

---

### D. GUM SEARCH

#### Two Modes
- **Normal** (`--fuzzy=false`): Exact matching
- **Fuzzy**: Loose pattern matching

#### Multi-Select Process
1. Suspend Textual
2. Pipe M3X titles to `gum filter --no-limit`
3. User selects with TAB, confirms with ENTER
4. Add all selected tracks to playlist
5. Resume Textual

---

### E. ECO MODE

#### Purpose
Ultra-low CPU usage terminal player (0% when idle)

#### Key Features
- Pauses TUI update loop
- Raw terminal mode input
- Smart polling:
  - Playing: 3-second timeout
  - Idle: Blocks indefinitely
  - 200ms sleep between polls
- Track-end detection (PLAY→STOP or time_pos ≥ duration)
- Auto-advance to next track
- Keyboard: Space/n/b/f/r/F/R/q

#### Restoration
- Resumes update timer
- Restores terminal mode
- Refreshes TUI layout

---

### F. SUPERVISION LOOP

#### `engine_supervisor()` - Runs Every 1 Second

**MPV Branch**:
- Check: pause, time_pos, duration
- Display: Status with time
- Auto-advance: When time_pos ≥ duration - 0.5s

**MOCP Branch**:
- Parse: `mocp -i` output
- Display: Formatted status
- Auto-advance: PLAY→STOP transition

**Global**:
- Updates status bar
- Advances playlist if enabled
- Handles disconnections

---

## CRITICAL ISSUES IN CURRENT CODE

1. **Race Condition**: `engine_supervisor()` and `setup_mpv_events()` both trigger `action_global_next()`
   - Can cause double-skip or conflicts
   - Especially visible returning from eco mode

2. **State Desynchronization**:
   - `active_engine` and `sorgente_riproduzione` can disagree
   - No atomicity in state transitions

3. **Eco Mode Conflicts**:
   - Returns to eco mode while TUI supervisor running
   - Terminal state not properly isolated

4. **Resource Leaks**:
   - MPV player never explicitly closed
   - Subprocess handles not always managed
   - Gum processes could leak if exceptions occur

5. **Error Handling**:
   - Silent failures (empty except blocks)
   - No systematic logging
   - Exceptions swallowed with `except Exception: pass`

6. **Hardcoded Constants**:
   - Seek distances (10s, 40s)
   - Volume steps (5%)
   - Timeouts and timeouts
   - Audio format list

7. **No Type Hints**:
   - Makes refactoring risky
   - IDE can't help with autocompletion

8. **Subprocess Performance**:
   - Synchronous blocking calls
   - Can freeze UI momentarily

---

## KEY BINDINGS REFERENCE

### Global (Main App)
```
Space  → Play/Pause
s      → Stop
n/b    → Next/Previous
f/r    → Seek ±10s
F/R    → Seek ±40s
+/-    → Volume ±5%
Tab    → Switch focus
z      → Eco Mode
h      → Help
```

### Directory Tree
```
H      → Home
M      → /mnt
a      → Add to queue
```

### Playlist
```
d      → Delete
C      → Clear
Enter  → Play
```

### M3X Viewer
```
a      → Add to queue
/      → Filter
g      → Gum normal
G      → Gum fuzzy
Bksp   → Exit M3X
```

### Eco Mode
```
Space  → Play/Pause
n/b    → Next/Prev
f/r    → Seek ±10s
F/R    → Seek ±40s
q      → Return
```

---

## NEXT STEPS FOR REFACTORING

The professional rewrite will:

1. ✅ **Extract enums and dataclasses** for type safety
2. ✅ **Create player abstraction layer** (strategy pattern)
3. ✅ **Implement PlayerManager facade** for unified control
4. ✅ **Separate eco mode logic** into dedicated class
5. ✅ **Add configuration object** for constants
6. ✅ **Implement proper error handling** with logging
7. ✅ **Add type hints throughout**
8. ✅ **Fix race conditions** with state isolation
9. ✅ **Preserve ALL functionality** from original
10. ✅ **Maintain Italian UI** and user preferences
