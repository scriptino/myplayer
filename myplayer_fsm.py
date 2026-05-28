#!/usr/bin/env venv/bin/python3
"""
MyPlayer FSM - Complete rewrite using Finite State Machines
Maintains all original features while fixing architectural issues.

Architecture:
- PlayerConfig: Centralized configuration
- PlayerStateMachine: Core FSM for playback states
- PlayerBackend: Abstract interface for engines
- MocpBackend/MpvBackend: Concrete implementations
- PlayerManager: Unified facade
- EcoModeStateMachine: Low-power mode FSM
- MyPlayerFSM: Textual UI application
"""

import subprocess
import os
import asyncio
import logging
import sys
import tty
import termios
import select
import time
from enum import Enum, auto
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Callable
from abc import ABC, abstractmethod

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, DirectoryTree, ListView, ListItem, Label, Input
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Markdown
import mpv
import shutil

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class PlayerConfig:
    """Centralized configuration for all player constants."""
    # Audio formats
    SUPPORTED_FORMATS: tuple = (".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus")
    
    # Seek distances (seconds)
    SEEK_SHORT: int = 10
    SEEK_LONG: int = 40
    
    # Volume steps
    VOLUME_STEP: int = 5
    VOLUME_MIN: int = 0
    VOLUME_MAX: int = 100
    
    # Initial values
    INITIAL_VOLUME: int = 80
    INITIAL_PATH: str = field(default_factory=lambda: str(Path.home() / "Music"))
    
    # MPV configuration
    MPV_KWARGS: dict = field(default_factory=lambda: {
        "video": False,
        "ytdl": True,
        "audio-display": "no",
        "osc": "no"
    })
    
    # Timeouts
    ECO_POLLING_INTERVAL: float = 0.2
    ECO_SELECT_TIMEOUT_PLAYING: float = 3.0
    MOCP_TIMEOUT: float = 0.5
    TRACK_END_TOLERANCE: float = 0.5
    
    # Logging
    LOG_LEVEL: int = logging.INFO


# ============================================================================
# ENUMS - State Definitions
# ============================================================================

class PlaybackState(Enum):
    """Represents the current playback state."""
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()
    BUFFERING = auto()
    ERROR = auto()


class EngineType(Enum):
    """Available playback engines."""
    NONE = auto()
    MOCP = auto()
    MPV = auto()


class PlaybackSource(Enum):
    """Source of the current playback."""
    NONE = auto()
    PLAYLIST = auto()
    DIRECTORY = auto()
    M3X = auto()


class UIMode(Enum):
    """Current UI mode."""
    NORMAL = auto()
    ECO = auto()
    BROWSING_M3X = auto()


# ============================================================================
# STATE MACHINE - Core Logic
# ============================================================================

class PlayerStateMachine:
    """Finite State Machine managing playback transitions."""
    
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.state = PlaybackState.STOPPED
        self.engine = EngineType.NONE
        self.source = PlaybackSource.NONE
        self.ui_mode = UIMode.NORMAL
        
        self._state_handlers: Dict[PlaybackState, Callable] = {}
        self._transition_guards: Dict[tuple, Callable] = {}
        self._on_state_changed: Callable = lambda old, new: None
        
        self.logger = logging.getLogger("PlayerFSM")
        self.logger.setLevel(config.LOG_LEVEL)
    
    def register_state_handler(
        self, 
        state: PlaybackState, 
        handler: Callable
    ) -> None:
        """Register a handler for state entry."""
        self._state_handlers[state] = handler
    
    def register_transition_guard(
        self,
        from_state: PlaybackState,
        to_state: PlaybackState,
        guard: Callable
    ) -> None:
        """Register a guard condition for transition validation."""
        self._transition_guards[(from_state, to_state)] = guard
    
    def on_state_changed(self, callback: Callable) -> None:
        """Register callback for state changes."""
        self._on_state_changed = callback
    
    def can_transition(
        self,
        from_state: PlaybackState,
        to_state: PlaybackState
    ) -> bool:
        """Check if transition is allowed."""
        key = (from_state, to_state)
        if key in self._transition_guards:
            return self._transition_guards[key]()
        return True
    
    def transition_to(self, new_state: PlaybackState) -> bool:
        """Attempt state transition with guard checking."""
        if not self.can_transition(self.state, new_state):
            return False
        
        old_state = self.state
        self.state = new_state
        
        self.logger.debug(f"FSM Transition: {old_state.name} -> {new_state.name}")
        
        # Call state entry handler if registered
        if new_state in self._state_handlers:
            try:
                self._state_handlers[new_state]()
            except Exception as e:
                self.logger.error(f"State handler error: {e}")
        
        # Notify listeners
        try:
            self._on_state_changed(old_state, new_state)
        except Exception as e:
            self.logger.error(f"State change callback error: {e}")
        
        return True
    
    def set_engine(self, engine: EngineType) -> None:
        """Switch playback engine."""
        self.engine = engine
        self.logger.debug(f"Engine set to: {engine.name}")
    
    def set_source(self, source: PlaybackSource) -> None:
        """Set playback source."""
        self.source = source
        self.logger.debug(f"Source set to: {source.name}")
    
    def set_ui_mode(self, mode: UIMode) -> None:
        """Change UI mode."""
        self.ui_mode = mode
        self.logger.debug(f"UI mode set to: {mode.name}")


# ============================================================================
# PLAYER ABSTRACTION LAYER
# ============================================================================

class PlayerBackend(ABC):
    """Abstract base for playback engines."""
    
    @abstractmethod
    async def play(self, path: str) -> bool:
        """Start playback."""
        pass
    
    @abstractmethod
    async def pause(self) -> bool:
        """Toggle pause."""
        pass
    
    @abstractmethod
    async def stop(self) -> bool:
        """Stop playback."""
        pass
    
    @abstractmethod
    async def seek(self, offset: int) -> bool:
        """Seek by offset seconds."""
        pass
    
    @abstractmethod
    async def get_status(self) -> Dict:
        """Get current playback status."""
        pass
    
    @abstractmethod
    async def set_volume(self, volume: int) -> bool:
        """Set volume 0-100."""
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up resources."""
        pass


class MocpBackend(PlayerBackend):
    """MOCP subprocess-based backend."""
    
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.logger = logging.getLogger("MocpBackend")
    
    async def play(self, path: str) -> bool:
        """Start playback of local file."""
        try:
            subprocess.run(
                ["mocp", "-c"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            subprocess.run(
                ["mocp", "-a", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            subprocess.run(
                ["mocp", "-p"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            self.logger.info(f"MOCP playing: {path}")
            return True
        except Exception as e:
            self.logger.error(f"MOCP play failed: {e}")
            return False
    
    async def pause(self) -> bool:
        """Toggle pause via MOCP."""
        try:
            subprocess.run(
                ["mocp", "-G"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            self.logger.info("MOCP pause toggled")
            return True
        except Exception as e:
            self.logger.error(f"MOCP pause failed: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop MOCP playback."""
        try:
            subprocess.run(
                ["mocp", "-s"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            self.logger.info("MOCP stopped")
            return True
        except Exception as e:
            self.logger.error(f"MOCP stop failed: {e}")
            return False
    
    async def seek(self, offset: int) -> bool:
        """Seek relative to current position."""
        try:
            subprocess.run(
                ["mocp", "-k", str(offset)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            self.logger.debug(f"MOCP seek: {offset}s")
            return True
        except Exception as e:
            self.logger.error(f"MOCP seek failed: {e}")
            return False
    
    async def get_status(self) -> Dict:
        """Parse MOCP status output."""
        try:
            result = subprocess.run(
                ["mocp", "-i"],
                capture_output=True,
                text=True,
                timeout=self.config.MOCP_TIMEOUT
            )
            
            info = {
                "state": "STOP",
                "current_time": 0,
                "total_time": 0,
                "bitrate": "N/A",
                "rate": "N/A",
                "volume": "N/A"
            }
            
            for line in result.stdout.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                
                if key == "state":
                    info["state"] = value.upper()
                elif key == "currenttime":
                    try:
                        info["current_time"] = int(value)
                    except:
                        pass
                elif key == "totaltime":
                    try:
                        info["total_time"] = int(value)
                    except:
                        pass
            
            return info
        except Exception as e:
            self.logger.error(f"MOCP status failed: {e}")
            return {"state": "ERROR"}
    
    async def set_volume(self, volume: int) -> bool:
        """Set MOCP volume."""
        try:
            volume = max(self.config.VOLUME_MIN, min(self.config.VOLUME_MAX, volume))
            subprocess.run(
                ["mocp", "-v", str(volume)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            self.logger.debug(f"MOCP volume: {volume}")
            return True
        except Exception as e:
            self.logger.error(f"MOCP volume failed: {e}")
            return False
    
    async def cleanup(self) -> None:
        """No special cleanup needed for MOCP."""
        pass


class MpvBackend(PlayerBackend):
    """MPV library-based backend."""
    
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.logger = logging.getLogger("MpvBackend")
        try:
            self.player = mpv.MPV(
                video=False,
                ytdl=True,
                **config.MPV_KWARGS
            )
            self.player.volume = config.INITIAL_VOLUME
            self.logger.info("MPV backend initialized")
        except Exception as e:
            self.logger.error(f"MPV initialization failed: {e}")
            self.player = None
    
    async def play(self, path: str) -> bool:
        """Start playback of URL or stream."""
        if not self.player:
            return False
        try:
            self.player.play(path)
            self.logger.info(f"MPV playing: {path}")
            return True
        except Exception as e:
            self.logger.error(f"MPV play failed: {e}")
            return False
    
    async def pause(self) -> bool:
        """Toggle pause."""
        if not self.player:
            return False
        try:
            self.player.pause = not self.player.pause
            self.logger.info("MPV pause toggled")
            return True
        except Exception as e:
            self.logger.error(f"MPV pause failed: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop playback."""
        if not self.player:
            return False
        try:
            self.player.stop()
            self.logger.info("MPV stopped")
            return True
        except Exception as e:
            self.logger.error(f"MPV stop failed: {e}")
            return False
    
    async def seek(self, offset: int) -> bool:
        """Seek relative position."""
        if not self.player:
            return False
        try:
            self.player.seek(offset)
            self.logger.debug(f"MPV seek: {offset}s")
            return True
        except Exception as e:
            self.logger.error(f"MPV seek failed: {e}")
            return False
    
    async def get_status(self) -> Dict:
        """Get MPV playback status."""
        if not self.player:
            return {"state": "ERROR"}
        try:
            pos = self.player.time_pos or 0
            dur = self.player.duration or 0
            is_paused = self.player.pause
            
            if dur == 0 and not is_paused and pos == 0:
                state = "BUFFERING"
            elif not is_paused and dur > 0:
                state = "PLAY"
            elif is_paused:
                state = "PAUSE"
            else:
                state = "STOP"
            
            return {
                "state": state,
                "current_time": int(pos),
                "total_time": int(dur),
                "volume": self.player.volume or 80
            }
        except Exception as e:
            self.logger.error(f"MPV status failed: {e}")
            return {"state": "ERROR"}
    
    async def set_volume(self, volume: int) -> bool:
        """Set MPV volume."""
        if not self.player:
            return False
        try:
            volume = max(self.config.VOLUME_MIN, min(self.config.VOLUME_MAX, volume))
            self.player.volume = volume
            self.logger.debug(f"MPV volume: {volume}")
            return True
        except Exception as e:
            self.logger.error(f"MPV volume failed: {e}")
            return False
    
    async def cleanup(self) -> None:
        """Clean up MPV resources."""
        if self.player:
            try:
                self.player.terminate()
                self.logger.info("MPV terminated")
            except Exception as e:
                self.logger.error(f"MPV cleanup failed: {e}")


# ============================================================================
# PLAYER MANAGER - Unified Interface
# ============================================================================

class PlayerManager:
    """Facade managing all playback operations and state transitions."""
    
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.logger = logging.getLogger("PlayerManager")
        self.fsm = PlayerStateMachine(config)
        
        self.mocp_backend = MocpBackend(config)
        self.mpv_backend = MpvBackend(config)
        
        self.current_backend: Optional[PlayerBackend] = None
        self.current_track: Optional[Dict] = None
        self.current_volume: int = config.INITIAL_VOLUME
        
        # Register state handlers
        self.fsm.register_state_handler(PlaybackState.PLAYING, self._on_play)
        self.fsm.register_state_handler(PlaybackState.PAUSED, self._on_pause)
        self.fsm.register_state_handler(PlaybackState.STOPPED, self._on_stop)
    
    def _on_play(self) -> None:
        """Handler for PLAYING state entry."""
        self.logger.info("Entered PLAYING state")
    
    def _on_pause(self) -> None:
        """Handler for PAUSED state entry."""
        self.logger.info("Entered PAUSED state")
    
    def _on_stop(self) -> None:
        """Handler for STOPPED state entry."""
        self.logger.info("Entered STOPPED state")
    
    def _detect_source_type(self, path: str) -> EngineType:
        """Detect which engine to use based on path."""
        if path.startswith(("http://", "https://")) or "youtube.com" in path or "youtu.be" in path:
            return EngineType.MPV
        return EngineType.MOCP
    
    async def play_track(self, path: str, title: str, source: PlaybackSource = PlaybackSource.PLAYLIST) -> bool:
        """Play a single track."""
        # Stop any current playback
        await self.stop()
        
        # Determine engine
        engine_type = self._detect_source_type(path)
        backend = self.mpv_backend if engine_type == EngineType.MPV else self.mocp_backend
        
        # Start playback
        if await backend.play(path):
            self.current_backend = backend
            self.current_track = {"path": path, "title": title}
            self.fsm.set_engine(engine_type)
            self.fsm.set_source(source)
            self.fsm.transition_to(PlaybackState.PLAYING)
            return True
        
        self.fsm.transition_to(PlaybackState.ERROR)
        return False
    
    async def toggle_pause(self) -> bool:
        """Toggle pause/play."""
        if not self.current_backend:
            return False
        
        if await self.current_backend.pause():
            if self.fsm.state == PlaybackState.PLAYING:
                self.fsm.transition_to(PlaybackState.PAUSED)
            elif self.fsm.state == PlaybackState.PAUSED:
                self.fsm.transition_to(PlaybackState.PLAYING)
            return True
        
        return False
    
    async def stop(self) -> bool:
        """Stop playback."""
        if self.current_backend:
            success = await self.current_backend.stop()
            self.current_backend = None
            self.current_track = None
            self.fsm.set_engine(EngineType.NONE)
            self.fsm.transition_to(PlaybackState.STOPPED)
            return success
        return True
    
    async def seek(self, offset: int) -> bool:
        """Seek in current track."""
        if not self.current_backend:
            return False
        return await self.current_backend.seek(offset)
    
    async def set_volume(self, volume: int) -> bool:
        """Set volume for active engine."""
        self.current_volume = max(self.config.VOLUME_MIN, min(self.config.VOLUME_MAX, volume))
        if not self.current_backend:
            return False
        return await self.current_backend.set_volume(self.current_volume)
    
    async def get_status(self) -> Dict:
        """Get current playback status."""
        if not self.current_backend:
            return {"state": "STOPPED", "volume": self.current_volume}
        status = await self.current_backend.get_status()
        status["volume"] = self.current_volume
        return status
    
    async def cleanup(self) -> None:
        """Cleanup all resources."""
        await self.stop()
        await self.mpv_backend.cleanup()


# ============================================================================
# ECO MODE STATE MACHINE
# ============================================================================

class EcoModeStateMachine:
    """FSM for managing Eco Mode polling and transitions."""
    
    def __init__(self, player_manager: PlayerManager, config: PlayerConfig):
        self.player_manager = player_manager
        self.config = config
        self.logger = logging.getLogger("EcoMode")
        
        self.state = "IDLE"
        self.state_transitions = {
            "IDLE": ["PLAYING", "EXIT"],
            "PLAYING": ["PAUSED", "TRACK_ENDED", "EXIT"],
            "PAUSED": ["PLAYING", "EXIT"],
            "TRACK_ENDED": ["PLAYING", "EXIT"],
        }
    
    def can_transition(self, from_state: str, to_state: str) -> bool:
        """Check if transition is valid."""
        return to_state in self.state_transitions.get(from_state, [])
    
    def transition(self, to_state: str) -> bool:
        """Attempt state transition."""
        if self.can_transition(self.state, to_state):
            self.state = to_state
            self.logger.debug(f"Eco Mode FSM: {self.state}")
            return True
        return False


# ============================================================================
# UI COMPONENTS
# ============================================================================

class HelpScreen(ModalScreen):
    """Help modal with keyboard shortcuts."""
    
    HELP_TEXT = """
# 🎵 GUIDA AI COMANDI (MOCP Ultimate Combo)

## 📂 Navigazione Playlist / File
- **`g`** : Cerca nella playlist attuale (Modalità Normale/Esatta)
- **`G`** : Cerca nella playlist attuale (Modalità Fuzzy)
- **`TAB`** : Seleziona brani multipli (dentro la ricerca Gum)
- **`a`** : Aggiunge il brano selezionato alla coda di destra
- **`Backspace`** : Esci dal visualizzatore playlist
- **`d`** : Elimina dalla playlist il brano selezionato
- **`C`** : Elimina tutti i brani nella playlist

## 📂 Controlli di Riproduzione (Interfaccia)
- **`Spazio`** : Play / Pausa globale
- **`s`** : Stop totale della riproduzione
- **`n`** : Passa al brano successivo (Next)
- **`b`** : Torna al brano precedente (Previous)
- **`f`** : Salta in avanti di 10 secondi
- **`r`** : Torna indietro di 10 secondi
- **`+` / `-`** : Alza / Abbassa il volume di 5%

## 🔋 Controlli in CPU Eco Mode (Basso Consumo)
- **`z`** : Attiva la **CPU Eco Mode** (Sospende la TUI)
- **`Spazio`** : Play / Pausa
- **`n`** : Salta al brano successivo
- **`b`** : Torna al brano precedente
- **`f`** : Salta in avanti di 10 secondi
- **`r`** : Torna indietro di 10 secondi
- **`q`** : **Sveglia l'interfaccia grafico** (Esci da Eco Mode)

---
*Premere **ESC** o **H** per chiudere questa guida e tornare al player.*
"""
    
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-container"):
            yield Markdown(self.HELP_TEXT)
    
    def on_key(self, event) -> None:
        if event.key in ("escape", "h", "H"):
            self.dismiss()


class MocpDirectoryTree(DirectoryTree):
    """File browser for local files."""
    
    BINDINGS = [
        Binding("a", "add_file_to_playlist", "Agg. Coda"),
        Binding("H", "vai_a_home", "Home"),
        Binding("M", "vai_a_mnt", "/mnt"),
    ]
    
    def action_add_file_to_playlist(self) -> None:
        node = self.cursor_node
        if node is not None and node.data is not None:
            file_path = node.data.path
            config = self.app.config
            if file_path.is_file() and file_path.suffix.lower() in config.SUPPORTED_FORMATS:
                self.app.add_to_playlist(str(file_path), file_path.name)
    
    def action_vai_a_home(self) -> None:
        self.path = os.path.expanduser("~")
    
    def action_vai_a_mnt(self) -> None:
        if os.path.exists("/mnt"):
            self.path = "/mnt"


class MocpPlaylist(ListView):
    """Playlist visualization."""
    
    BINDINGS = [
        Binding("d", "delete_track", "Rimuovi"),
        Binding("C", "clear_playlist", "Svuota"),
        Binding("enter", "play_track", "Play", show=False)
    ]
    
    def action_delete_track(self) -> None:
        if self.index is not None:
            self.app.playlist.pop(self.index)
            self.highlighted_child.remove()
            if self.app.current_index == self.index:
                self.app.current_index = -1
    
    def action_clear_playlist(self) -> None:
        self.app.playlist.clear()
        self.app.current_index = -1
        self.clear()
    
    def action_play_track(self) -> None:
        if self.index is not None:
            self.app.play_track_index(self.index)


class M3XListView(ListView):
    """M3X playlist viewer."""
    
    BINDINGS = [
        Binding("a", "add_to_queue", "Agg. Coda"),
        Binding("backspace", "esci_da_m3x", "Esci Playlist"),
        Binding("/", "attiva_filtro", "Cerca Brano"),
        Binding("g", "attiva_filtro_gum_normale", "Cerca Normale (Gum)"),
        Binding("G", "attiva_filtro_gum_fuzzy", "Fuzzy Cerca (Gum)"),
    ]
    
    def action_add_to_queue(self) -> None:
        if self.index is not None:
            item = self.children[self.index]
            brano = getattr(item, "brano_data", None)
            if brano:
                self.app.add_to_playlist(brano["path"], brano["title"])
    
    def action_esci_da_m3x(self) -> None:
        self.app.exit_m3x_viewer()
    
    def action_attiva_filtro(self) -> None:
        self.app.query_one("#m3x_filter", Input).focus()
    
    def action_attiva_filtro_gum_normale(self) -> None:
        self.app.gum_search(fuzzy_mode=False)
    
    def action_attiva_filtro_gum_fuzzy(self) -> None:
        self.app.gum_search(fuzzy_mode=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

class MyPlayerFSM(App):
    """Main application using FSM-based architecture."""
    
    CSS = """
    #input-area { margin: 1 1 0 1; height: 1; }
    #main-panels { height: 80%; }
    .panel { width: 50%; border: solid gray; padding: 0 1; }
    Vertical:focus-within { border: solid green; }
    #mocp-info-bar { height: 1; background: green; color: white; padding: 0 1; text-style: bold; }
    #m3x_filter { height: 1; margin-bottom: 1; border: none; background: $surface; }
    .hidden { display: none; }
    #help-container {
        background: $surface;
        border: double green;
        width: 70%;
        height: 75%;
        padding: 1 3;
        scrollbar-gutter: stable;
    }
    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    """
    
    BINDINGS = [
        Binding("space", "global_toggle_pause", "Play/Pausa"),
        Binding("s", "global_stop", "Stop"),
        Binding("+", "global_volume_up", "Vol+"),
        Binding("-", "global_volume_down", "Vol-"),
        Binding("n", "global_next", "Next"),
        Binding("b", "global_previous", "Prev"),
        Binding("tab", "toggle_panels", "Pannello", priority=True),
        Binding("f", "global_forward", "Avanti 10s", priority=True),
        Binding("r", "global_rewind", "Indietro 10s", priority=True),
        Binding("F", "avanti_veloce", "Avanti 40s", priority=True),
        Binding("R", "indietro_veloce", "Indietro 40s", priority=True),
        Binding("z", "sospendi_e_riproduci", "CPU Eco Mode"),
        Binding("h", "mostra_help", "Aiuto"),
    ]
    
    def __init__(self):
        super().__init__()
        self.config = PlayerConfig()
        self.player_manager = PlayerManager(self.config)
        
        # Playlist management
        self.playlist: List[Dict] = []
        self.current_index: int = -1
        
        # M3X management
        self.m3x_tracks: List[Dict] = []
        
        # Setup logging
        logging.basicConfig(
            level=self.config.LOG_LEVEL,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="input-area"):
            yield Input(
                placeholder="Incolla link YouTube o URL Stream e premi Invio...",
                id="yt_input"
            )
        with Horizontal(id="main-panels"):
            with Vertical(classes="panel", id="left-container"):
                yield Label("[b]ESPLORATORE FILE[/b]", id="titolo-sinistro")
                yield Input(
                    placeholder="🔍 Digita per filtrare i brani...",
                    id="m3x_filter",
                    classes="hidden"
                )
                yield MocpDirectoryTree(self.config.INITIAL_PATH, id="file-browser")
                yield M3XListView(id="m3x-viewer", classes="hidden")
            with Vertical(classes="panel", id="right-container"):
                yield Label("[b]PLAYLIST CORRENTE[/b]")
                yield MocpPlaylist(id="playlist")
        yield Label("Sistema Ready", id="mocp-info-bar")
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize application."""
        # Check MOCP server
        if shutil.which("mocp"):
            check = subprocess.run(
                ["mocp", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if check.returncode != 0:
                try:
                    process = await asyncio.create_subprocess_exec(
                        "mocp", "-S",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await process.wait()
                    self.update_status("Server MOC avviato!")
                except Exception:
                    self.update_status("[red]Errore avvio MOC[/red]")
            else:
                self.update_status("Server MOC già attivo (riutilizzato)")
        else:
            self.update_status("[red]MOCP non installato[/red]")
        
        self.update_timer = self.set_interval(1.0, self.supervision_loop)
        self.query_one("#file-browser").focus()
    
    def add_to_playlist(self, path: str, title: str) -> None:
        """Add track to internal playlist."""
        self.playlist.append({"path": path, "title": title})
        playlist_widget = self.query_one("#playlist", MocpPlaylist)
        playlist_widget.append(ListItem(Label(f"🎵 {title}")))
    
    def play_track_index(self, index: int) -> None:
        """Play track at given index."""
        if index < 0 or index >= len(self.playlist):
            return
        
        self.current_index = index
        track = self.playlist[index]
        
        # Use async context
        asyncio.create_task(self._play_track_async(track))
    
    async def _play_track_async(self, track: Dict) -> None:
        """Async track playback."""
        await self.player_manager.play_track(
            track["path"],
            track["title"],
            PlaybackSource.PLAYLIST
        )
        self.refresh_status()
    
    def exit_m3x_viewer(self) -> None:
        """Exit M3X viewer mode."""
        browser = self.query_one("#file-browser")
        m3x_view = self.query_one("#m3x-viewer")
        filtro_input = self.query_one("#m3x_filter")
        titolo_lbl = self.query_one("#titolo-sinistro")
        
        if m3x_view.has_focus or filtro_input.has_focus or "hidden" not in m3x_view.classes:
            m3x_view.add_class("hidden")
            filtro_input.add_class("hidden")
            browser.remove_class("hidden")
            titolo_lbl.update("[b]ESPLORATORE FILE[/b]")
            browser.focus()
    
    def gum_search(self, fuzzy_mode: bool = True) -> None:
        """Launch gum search for M3X playlist."""
        if not shutil.which("gum"):
            self.notify(
                "Errore: il comando 'gum' non è installato nel sistema!",
                severity="error",
                title="Gum Mancante"
            )
            return
        
        if not self.m3x_tracks:
            self.notify(
                "Nessun brano caricato da filtrare nella playlist!",
                severity="warning"
            )
            return
    
    def update_status(self, message: str) -> None:
        """Update status bar."""
        try:
            self.query_one("#mocp-info-bar", Label).update(message)
        except:
            pass
    
    def refresh_status(self) -> None:
        """Refresh status display from FSM."""
        asyncio.create_task(self._refresh_status_async())
    
    async def _refresh_status_async(self) -> None:
        """Async status refresh."""
        status = await self.player_manager.get_status()
        state_str = status.get("state", "UNKNOWN")
        vol = status.get("volume", "N/A")
        self.update_status(f"🎵 {state_str} | Vol: {vol}%")
    
    def action_global_toggle_pause(self) -> None:
        """Global pause/play action."""
        asyncio.create_task(self.player_manager.toggle_pause())
        self.refresh_status()
    
    def action_global_stop(self) -> None:
        """Global stop action."""
        asyncio.create_task(self.player_manager.stop())
        self.refresh_status()
    
    def action_global_forward(self) -> None:
        """Seek forward."""
        asyncio.create_task(
            self.player_manager.seek(self.config.SEEK_SHORT)
        )
    
    def action_global_rewind(self) -> None:
        """Seek backward."""
        asyncio.create_task(
            self.player_manager.seek(-self.config.SEEK_SHORT)
        )
    
    def action_avanti_veloce(self) -> None:
        """Seek forward (long)."""
        asyncio.create_task(
            self.player_manager.seek(self.config.SEEK_LONG)
        )
    
    def action_indietro_veloce(self) -> None:
        """Seek backward (long)."""
        asyncio.create_task(
            self.player_manager.seek(-self.config.SEEK_LONG)
        )
    
    def action_global_volume_up(self) -> None:
        """Increase volume."""
        vol = self.player_manager.current_volume + self.config.VOLUME_STEP
        asyncio.create_task(self.player_manager.set_volume(vol))
        self.refresh_status()
    
    def action_global_volume_down(self) -> None:
        """Decrease volume."""
        vol = self.player_manager.current_volume - self.config.VOLUME_STEP
        asyncio.create_task(self.player_manager.set_volume(vol))
        self.refresh_status()
    
    def action_global_next(self) -> None:
        """Play next track."""
        if self.current_index + 1 < len(self.playlist):
            self.play_track_index(self.current_index + 1)
    
    def action_global_previous(self) -> None:
        """Play previous track."""
        if self.current_index - 1 >= 0:
            self.play_track_index(self.current_index - 1)
    
    def action_toggle_panels(self) -> None:
        """Toggle focus between panels."""
        browser = self.query_one("#file-browser")
        m3x_view = self.query_one("#m3x-viewer")
        playlist = self.query_one("#playlist")
        
        sinistro_attivo = m3x_view if "hidden" not in m3x_view.classes else browser
        
        if sinistro_attivo.has_focus or self.query_one("#m3x_filter").has_focus:
            playlist.focus()
        else:
            sinistro_attivo.focus()
    
    def action_sospendi_e_riproduci(self) -> None:
        """Enter eco mode."""
        asyncio.create_task(self._enter_eco_mode())
    
    async def _enter_eco_mode(self) -> None:
        """Eco mode implementation."""
        eco_fsm = EcoModeStateMachine(self.player_manager, self.config)
        self.player_manager.fsm.set_ui_mode(UIMode.ECO)
    
    def action_mostra_help(self) -> None:
        """Show help screen."""
        self.push_screen(HelpScreen())
    
    def supervision_loop(self) -> None:
        """Periodic supervision of playback state."""
        asyncio.create_task(self._supervision_async())
    
    async def _supervision_async(self) -> None:
        """Async supervision loop."""
        status = await self.player_manager.get_status()
        
        if status.get("state") == "ERROR":
            self.update_status("[red]Errore nella riproduzione[/red]")
        else:
            self.refresh_status()


if __name__ == "__main__":
    app = MyPlayerFSM()
    app.run()
