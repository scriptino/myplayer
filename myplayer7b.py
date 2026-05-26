#!/usr/bin/env venv/bin/python3

import subprocess
import os
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, DirectoryTree, ListView, ListItem, Label, Input
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Markdown
from textual.containers import Container
import mpv
import shutil

class HelpScreen(ModalScreen):
    """Schermata modale che mostra la guida ai comandi dello script."""
    
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
        # Usiamo VerticalScroll al posto di Container per sbloccare la barra laterale
        with VerticalScroll(id="help-container"): 
            yield Markdown(self.HELP_TEXT)

    def on_key(self, event) -> None:
        if event.key in ("escape", "h", "H"):  # Esteso anche ad H così si chiude con lo stesso tasto con cui si apre!
            self.dismiss()

class MocpDirectoryTree(DirectoryTree):
    """Pannello Sinistro: Esploratore file nativo."""
    BINDINGS = [
        Binding("a", "add_file_to_playlist", "Agg. Coda"),
        Binding("H", "vai_a_home", "Home"),
        Binding("M", "vai_a_mnt", "/mnt"),
    ]
    def action_add_file_to_playlist(self) -> None:
        node = self.cursor_node
        if node is not None and node.data is not None:
            file_path = node.data.path
            if file_path.is_file() and file_path.suffix.lower() in {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}:
                self.app.aggiungi_a_playlist_interna(str(file_path), file_path.name)
    def action_vai_a_home(self) -> None:
        self.path = os.path.expanduser("~")
    def action_vai_a_mnt(self) -> None:
        if os.path.exists("/mnt"):
            self.path = "/mnt"

class MocpPlaylist(ListView):
    """Pannello Destro: Lista visiva gestita da Textual (Universale)."""
    BINDINGS = [
        Binding("d", "delete_track", "Rimuovi"),
        Binding("C", "clear_playlist", "Svuota"),
        Binding("enter", "play_track", "Play", show=False)
    ]
    def action_delete_track(self) -> None:
        if self.index is not None:
            self.app.playlist_interna.pop(self.index)
            self.highlighted_child.remove()
            if self.app.indice_corrente == self.index:
                self.app.indice_corrente = -1
    def action_clear_playlist(self) -> None:
        self.app.playlist_interna.clear()
        self.app.indice_corrente = -1
        self.clear()
    def action_play_track(self) -> None:
        if self.index is not None:
            self.app.sorgente_riproduzione = "playlist"
            self.app.riproduci_indice_playlist(self.index)

class M3XListView(ListView):
    """Sottoclasse dedicata per il visualizzatore M3X per catturare correttamente i tasti."""
    BINDINGS = [
        Binding("a", "add_to_queue", "Agg. Coda"),
        Binding("backspace", "esci_da_m3x", "Esci Playlist"),
        Binding("/", "attiva_filtro", "Cerca Brano"),
        Binding("g", "attiva_filtro_gum_normale", "Cerca Normale (Gum)"), # <--- Miniscola
        Binding("G", "attiva_filtro_gum_fuzzy", "Fuzzy Cerca (Gum)"),     # <--- Maiuscola
    ]
    def action_add_to_queue(self) -> None:
        if self.index is not None:
            item = self.children[self.index]
            brano = getattr(item, "brano_data", None)
            if brano:
                self.app.aggiungi_a_playlist_interna(brano["path"], brano["title"])
                
    def action_esci_da_m3x(self) -> None:
        self.app.action_esci_da_m3x()
        
    def action_attiva_filtro(self) -> None:
        self.app.query_one("#m3x_filter", Input).focus()

    def action_attiva_filtro_gum_normale(self) -> None:
        # Avvia Gum con ricerca esatta (Fuzzy disattivato)
        self.app.action_ricerca_veloce_gum(fuzzy_mode=False)

    def action_attiva_filtro_gum_fuzzy(self) -> None:
        # Avvia Gum con ricerca Fuzzy tradizionale
        self.app.action_ricerca_veloce_gum(fuzzy_mode=True)

class MocpUltimateCombo(App):
    CSS = """
    #input-area { margin: 1 1 0 1; height: 1; }
    #main-panels { height: 80%; }
    .panel { width: 50%; border: solid gray; padding: 0 1; }
    Vertical:focus-within { border: solid green; }
    #mocp-info-bar { height: 1; background: green; color: white; padding: 0 1; text-style: bold; }
    #m3x_filter { height: 1; margin-bottom: 1; border: none; background: $surface; }
    .hidden { display: none; }
    /* REGOLE AGGIUNTE PER LA SCHERMATA DI AIUTO */
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
        self.mpv_player = mpv.MPV(
            video=False, 
            ytdl=True,
            **{"audio-display": "no", "osc": "no"}
        )
        self.mpv_volume = 80
        self.mpv_player.volume = self.mpv_volume
        self.active_engine = None
        self.web_title = ""
        self.PERCORSO_INIZIALE = str(Path.home() / "Music")
        self.playlist_interna = []
        self.indice_corrente = -1
        self.sorgente_riproduzione = None 
        self.brani_m3x_correnti = []
        self.setup_mpv_events()

    def setup_mpv_events(self) -> None:
        """Configura gli eventi di MPV per rilevare la fine del brano in modo preciso"""
    
        @self.mpv_player.property_observer('time-pos')
        def observe_time_pos(prop, value):
            if value is not None and self.active_engine == "mpv":
                dur = self.mpv_player.duration or 0
                if dur > 0 and value >= dur - 0.5 and not self.mpv_player.pause:
                    # Usa call_from_thread di Textual (funziona sempre!)
                    self.call_from_thread(self.action_global_next)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="input-area"):
            yield Input(placeholder="Incolla link YouTube o URL Stream e premi Invio...", id="yt_input")
        with Horizontal(id="main-panels"):
            with Vertical(classes="panel", id="left-container"):
                yield Label("[b]ESPLORATORE FILE[/b]", id="titolo-sinistro")
                yield Input(placeholder="🔍 Digita per filtrare i brani...", id="m3x_filter", classes="hidden")
                yield MocpDirectoryTree(self.PERCORSO_INIZIALE, id="file-browser")
                yield M3XListView(id="m3x-viewer", classes="hidden")
            with Vertical(classes="panel", id="right-container"):
                yield Label("[b]PLAYLIST CORRENTE[/b]")
                yield MocpPlaylist(id="playlist")
        yield Label("Sistema Ready", id="mocp-info-bar")
        yield Footer()

    async def on_mount(self) -> None:
        if shutil.which("mocp"):
            # Controlla se il server è già attivo inviando un comando di info rapido
            check_server = subprocess.run(["mocp", "-i"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if check_server.returncode != 0:
                # returncode != 0 significa che il server è spento, quindi lo avviamo
                try:
                    process = await asyncio.create_subprocess_exec(
                        "mocp", "-S",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await process.wait()
                    self.query_one("#mocp-info-bar", Label).update("Server MOC avviato!")
                except Exception:
                    self.query_one("#mocp-info-bar", Label).update("[red]Errore avvio MOC[/red]")
            else:
                # Il server era già attivo, aggiorna semplicemente il messaggio iniziale
                self.query_one("#mocp-info-bar", Label).update("Server MOC già attivo (riutilizzato)")
        else:
            self.query_one("#mocp-info-bar", Label).update("[red]MOCP non installato[/red]")

        self.update_timer = self.set_interval(1.0, self.engine_supervisor)
        self.query_one("#file-browser").focus()

    def action_toggle_panels(self) -> None:
        browser = self.query_one("#file-browser")
        m3x_view = self.query_one("#m3x-viewer")
        playlist = self.query_one("#playlist")
        sinistro_attivo = m3x_view if "hidden" not in m3x_view.classes else browser
        if sinistro_attivo.has_focus or self.query_one("#m3x_filter").has_focus:
            playlist.focus()
        else:
            sinistro_attivo.focus()

    def aggiungi_a_playlist_interna(self, percorso: str, titolo: str) -> None:
        self.playlist_interna.append({"path": percorso, "title": titolo})
        playlist_widget = self.query_one("#playlist", MocpPlaylist)
        playlist_widget.append(ListItem(Label(f"🎵 {titolo}")))

    def riproduci_indice_playlist(self, indice: int) -> None:
        if indice < 0 or indice >= len(self.playlist_interna):
            return
        self.stop_all_engines()
        self.indice_corrente = indice
        traccia = self.playlist_interna[indice]
        target = traccia["path"]
        
        # Sincronizza visivamente l'indice della lista grafica di Textual se attiva
        try:
            playlist_widget = self.query_one("#playlist", MocpPlaylist)
            playlist_widget.index = indice
        except Exception:
            pass

        if target.startswith("http://") or target.startswith("https://") or "youtube.com" in target or "youtu.be" in target:
            self.active_engine = "mpv"
            self.web_title = traccia["title"]
            self.mpv_player.play(target)
        else:
            self.active_engine = "mocp"
            subprocess.run(["mocp", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mocp", "-a", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mocp", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        file_path = event.path
        if file_path.suffix.lower() == ".m3x":
            self.carica_file_m3x(file_path)
        elif file_path.suffix.lower() in {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".opus"}:
            self.stop_all_engines()
            self.sorgente_riproduzione = "directory"
            self.active_engine = "mocp"
            subprocess.run(["mocp", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mocp", "-a", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mocp", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def carica_file_m3x(self, percorso_file: Path) -> None:
        browser = self.query_one("#file-browser")
        m3x_view = self.query_one("#m3x-viewer")
        filtro_input = self.query_one("#m3x_filter")
        titolo_lbl = self.query_one("#titolo-sinistro")
        self.brani_m3x_correnti.clear()
        m3x_view.clear()
        filtro_input.value = ""
        try:
            with open(percorso_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if " | " in line:
                        titolo, url = line.split(" | ", 1)
                        brano_info = {"path": url.strip(), "title": titolo.strip()}
                        self.brani_m3x_correnti.append(brano_info)
                        item = ListItem(Label(f"📺 {titolo.strip()}"))
                        item.brano_data = brano_info
                        m3x_view.append(item)
            if self.brani_m3x_correnti:
                browser.add_class("hidden")
                m3x_view.remove_class("hidden")
                filtro_input.remove_class("hidden")
                titolo_lbl.update(f"[b]PLAYLIST: {percorso_file.name}[/b]")
                m3x_view.focus()
        except Exception:
            pass

    def action_esci_da_m3x(self) -> None:
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "m3x_filter":
            testo_cercato = event.value.lower().strip()
            m3x_view = self.query_one("#m3x-viewer", M3XListView)
            m3x_view.clear()
            for brano in self.brani_m3x_correnti:
                if not testo_cercato or testo_cercato in brano["title"].lower():
                    item = ListItem(Label(f"📺 {brano['title']}"))
                    item.brano_data = brano
                    m3x_view.append(item)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "m3x_filter":
            self.query_one("#m3x-viewer").focus()
            return
        target = event.value.strip()
        if not target:
            return
        titolo_breve = target.split("v=")[-1][:10] if "v=" in target else target[-15:]
        self.aggiungi_a_playlist_interna(target, f"Web: {titolo_breve}")
        if self.active_engine is None:
            self.sorgente_riproduzione = "playlist"
            self.riproduci_indice_playlist(len(self.playlist_interna) - 1)
        self.query_one("#yt_input", Input).value = ""
        self.action_toggle_panels()
        self.action_toggle_panels()

    def action_ricerca_veloce_gum(self, fuzzy_mode: bool = True) -> None:
        """Sgancia Textual, usa 'gum filter' con selezione multipla via TAB e accoda tutto."""
        import shutil
        import subprocess
        import sys
        from textual.widgets import Label

        if not shutil.which("gum"):
            self.notify("Errore: il comando 'gum' non è installato nel sistema!", severity="error", title="Gum Mancante")
            return

        if not self.brani_m3x_correnti:
            self.notify("Nessun brano caricato da filtrare nella playlist!", severity="warning")
            return

        elenco_titoli = "\n".join([brano["title"] for brano in self.brani_m3x_correnti])
        scelte_utente = [] # Diventa una lista per raccogliere più brani

        tipo_ricerca = "Fuzzy" if fuzzy_mode else "Normale/Esatta"
        parametri_gum = [
            "gum", "filter", 
            "--placeholder", f"Cerca ({tipo_ricerca})... [TAB] seleziona, [INVIO] conferma", 
            "--height", "22",
            "--indicator", "→",
            "--match.foreground", "2",
            "--no-limit" # <--- IL TRUCCO MAGICO: sblocca la selezione multipla!
        ]

        if not fuzzy_mode:
            parametri_gum.append("--fuzzy=false")

        with self.suspend():
            try:
                with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
                    p_echo = subprocess.Popen(["echo", elenco_titoli], stdout=subprocess.PIPE)
                    
                    p_gum = subprocess.Popen(
                        parametri_gum,
                        stdin=p_echo.stdout,
                        stdout=subprocess.PIPE,
                        stderr=tty_out
                    )
                    
                    p_echo.stdout.close()
                    stdout, _ = p_gum.communicate()
                    
                    if p_gum.returncode == 0:
                        # Raccogliamo l'output dividendo per ogni riga (ogni brano scelto)
                        output_pulito = stdout.decode("utf-8", errors="ignore").strip()
                        if output_pulito:
                            scelte_utente = output_pulito.split("\n")
            except Exception:
                pass

        # Se l'utente ha selezionato uno o più brani
        if scelte_utente:
            brani_accodati = 0
            # Scorriamo i titoli restituiti da gum nello stesso ordine di selezione
            for titolo_scelto in scelte_utente:
                titolo_scelto = titolo_scelto.strip()
                for brano in self.brani_m3x_correnti:
                    if brano["title"] == titolo_scelto:
                        self.aggiungi_a_playlist_interna(brano["path"], brano["title"])
                        brani_accodati += 1
                        break # Passa al prossimo titolo scelto
            
            if brani_accodati > 0:
                self.query_one("#mocp-info-bar", Label).update(f"[green]Accodati {brani_accodati} brani con successo![/green]")
                self.notify(f"Aggiunti {brani_accodati} brani alla coda", title="Gum Multi-Playlist")
        else:
            self.query_one("#mocp-info-bar", Label).update(f"Ricerca {tipo_ricerca} annullata")

        self.refresh(layout=True)
        self.query_one("#m3x-viewer").focus()

    def action_mostra_help(self) -> None:
        """Apre la schermata modale con la guida ai comandi."""
        self.push_screen(HelpScreen())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "m3x-viewer" and event.list_view.index is not None:
            item = event.list_view.children[event.list_view.index]
            brano = getattr(item, "brano_data", None)
            if brano:
                self.stop_all_engines()
                self.sorgente_riproduzione = "directory"
                self.active_engine = "mpv"
                self.web_title = brano["title"]
                self.mpv_player.play(brano["path"])

    def stop_all_engines(self):
        subprocess.run(["mocp", "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.mpv_player.stop()
        self.active_engine = None
        self.web_title = ""

    def action_global_toggle_pause(self) -> None:
        if self.active_engine == "mocp":
            subprocess.run(["mocp", "-G"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.active_engine == "mpv":
            self.mpv_player.pause = not self.mpv_player.pause

    def action_global_stop(self) -> None:
        self.stop_all_engines()
        self.sorgente_riproduzione = None

    def action_global_forward(self) -> None:
        """Salta avanti di 10 secondi sul player attivo."""
        if self.active_engine == "mocp":
            subprocess.run(["mocp", "-k", "+10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.active_engine == "mpv":
            try:
                # MPV gestisce il seek relativo nativamente (valore in secondi)
                self.mpv_player.seek(10)
            except Exception:
                pass

    def action_global_rewind(self) -> None:
        """Torna indietro di 10 secondi sul player attivo."""
        if self.active_engine == "mocp":
            subprocess.run(["mocp", "-k", "-10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.active_engine == "mpv":
            try:
                self.mpv_player.seek(-10)
            except Exception:
                pass

    def action_avanti_veloce(self) -> None:
        """Spostamento grosso in avanti di 40 secondi (Tasto F)"""
        if self.active_engine == "mpv":
            try:
                pos = self.mpv_player.time_pos or 0
                dur = self.mpv_player.duration or 0
                # Salta avanti di 40s senza sforare la durata massima
                self.mpv_player.time_pos = min(pos + 40, max(0, dur - 1))
            except Exception:
                pass
        elif self.active_engine == "mocp":
            # Comando nativo mocp per saltare avanti di N secondi
            subprocess.run(["mocp", "-k", "40"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def action_indietro_veloce(self) -> None:
        """Spostamento grosso all'indietro di 40 secondi (Tasto R)"""
        if self.active_engine == "mpv":
            try:
                pos = self.mpv_player.time_pos or 0
                # Salta indietro di 40s senza andare sotto zero
                self.mpv_player.time_pos = max(0, pos - 40)
            except Exception:
                pass
        elif self.active_engine == "mocp":
            # Comando nativo mocp per saltare indietro di N secondi (valore negativo)
            subprocess.run(["mocp", "-k", "-40"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def action_global_volume_up(self) -> None:
        if self.active_engine == "mocp" or self.active_engine is None:
            subprocess.run(["mocp", "-v", "+5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.active_engine == "mpv":
            self.mpv_volume = min(100, self.mpv_volume + 5)
            self.mpv_player.volume = self.mpv_volume

    def action_global_volume_down(self) -> None:
        if self.active_engine == "mocp" or self.active_engine is None:
            subprocess.run(["mocp", "-v", "-5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.active_engine == "mpv":
            self.mpv_volume = max(0, self.mpv_volume - 5)
            self.mpv_player.volume = self.mpv_volume

    def action_global_next(self) -> None:
        if self.sorgente_riproduzione == "playlist":
            prossimo = self.indice_corrente + 1
            if prossimo < len(self.playlist_interna):
                self.riproduci_indice_playlist(prossimo)

    def action_global_previous(self) -> None:
        if self.sorgente_riproduzione == "playlist":
            precedente = self.indice_corrente - 1
            if precedente >= 0:
                self.riproduci_indice_playlist(precedente)

    def engine_supervisor(self) -> None:
        if self.active_engine == "mpv":
            state = "[PAUSE]" if self.mpv_player.pause else "[PLAY]"
            pos = int(self.mpv_player.time_pos or 0)
            dur = int(self.mpv_player.duration or 0)
            time_str = f"{pos//60:02d}:{pos%60:02d} / {dur//60:02d}:{dur%60:02d}"
            info_string = f"[red]{state} (MPV/YT)[/red] | Vol: {self.mpv_volume}% | Streaming: ...{self.web_title} | Tempo: {time_str}"
            self.query_one("#mocp-info-bar", Label).update(info_string)
            # CORREZIONE: Migliore rilevamento fine brano per MPV
            if dur > 0 and not self.mpv_player.pause:
                # Rileva fine brano con una tolleranza di 0.5 secondi
                if pos >= dur - 0.5:
                    self.action_global_next()            
#           if dur > 0 and pos >= dur - 1:
#               self.action_global_next()



        else:
            try:
                result = subprocess.run(["mocp", "-i"], capture_output=True, text=True, check=True)
                lines = result.stdout.splitlines()
                info = {"State": "STOP", "CurrentTime": "00:00", "TotalTime": "00:00", "Bitrate": "N/A", "Rate": "N/A", "Volume": "N/A"}
                for line in lines:
                    if ":" in line:
                        chiave, valore = line.split(":", 1)
                        chiave = chiave.strip()
                        if chiave in info:
                            info[chiave] = valore.strip()
                
                # CORREZIONE LOGICA QUI: imposta il motore attivo se MOCP sta suonando/pausa
                if info["State"] in ("PLAY", "PAUSE"):
                    self.active_engine = "mocp"
                    state_dec = "[green][PLAY][/green]" if info["State"] == "PLAY" else "[yellow][PAUSE][/yellow]"
                    info_string = f"{state_dec} (MOCP) | Vol: {info['Volume']} | Audio: {info['Rate']} @ {info['Bitrate']} | Tempo: {info['CurrentTime']} / {info['TotalTime']}"
                    self.query_one("#mocp-info-bar", Label).update(info_string)
                
                # Se è in STOP ed era attiva la riproduzione da playlist, avanza!
                elif info["State"] == "STOP" and self.active_engine == "mocp":
                    self.active_engine = None  # Resetta il motore prima di cambiare
                    if self.sorgente_riproduzione == "playlist":
                        self.action_global_next()
                    else:
                        self.sorgente_riproduzione = None
                        self.query_one("#mocp-info-bar", Label).update("[gray][STOP][/gray] (MOCP)")

            except (subprocess.CalledProcessError, FileNotFoundError):
                self.query_one("#mocp-info-bar", Label).update("[red]MOCP Server disconnesso[/red]")

    # =========================================================================
    # FUNZIONE DI ECO MODE REALE (0% CPU - RISVEGLIO ISTANTANEO)
    # =========================================================================
    async def action_sospendi_e_riproduci(self) -> None:
        """Sgancia Textual, ottimizza il polling e gestisce correttamente il passaggio automatico dei brani."""
        import sys
        import tty
        import termios
        import select
        import time

        if hasattr(self, "update_timer"):
            self.update_timer.pause()

        with self.suspend():
            # Pulizia radicale dello schermo
            sys.stdout.write("\033[H\033[J")
            sys.stdout.flush()
            
            sys.stdout.write("=" * 65 + "\r\n")
            sys.stdout.write(" 🎧 TEXTUAL SGANCIATO - MODALITÀ ECO CON SMART POLLING (0% CPU)\r\n")
            sys.stdout.write("=" * 65 + "\r\n")
            sys.stdout.write(" -> [SPAZIO] Play/Pausa  |  [n] Successivo  |  [b] Precedente\r\n")
            sys.stdout.write(" -> [f] Avanti 10s  |  [r] Indietro 10s  |  [q] Sveglia Interfaccia\r\n\r\n")
            sys.stdout.flush()

            fd = sys.stdin.fileno()
            vecchi_settings = termios.tcgetattr(fd)
            
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
                tty.setraw(fd)
                
                # Variabili per tracciare lo stato
                ultimo_stato_loggato = ""
                stato_precedente_mocp = None
                stato_precedente_mpv = None
                brano_in_corso = True
                
                while True:
                    # ============================================================
                    # 1. LEGGI LO STATO REALE DI ENTRAMBI I PLAYER
                    # ============================================================
                    stato_mocp = "UNKNOWN"
                    stato_mpv = "UNKNOWN"
                    mpv_pos = 0
                    mpv_dur = 0
                    
                    # Leggi stato MOCP
                    try:
                        res = subprocess.run(["mocp", "-i"], capture_output=True, text=True, timeout=0.5)
                        if "State: PLAY" in res.stdout:
                            stato_mocp = "PLAY"
                        elif "State: PAUSE" in res.stdout:
                            stato_mocp = "PAUSE"
                        elif "State: STOP" in res.stdout:
                            stato_mocp = "STOP"
                        else:
                            stato_mocp = "UNKNOWN"
                    except Exception:
                        stato_mocp = "ERROR"
                    
                    # Leggi stato MPV
                    if self.active_engine == "mpv":
                        try:
                            mpv_pos = self.mpv_player.time_pos or 0
                            mpv_dur = self.mpv_player.duration or 0
                            is_paused = self.mpv_player.pause
                            
                            if mpv_dur == 0 and not is_paused and mpv_pos == 0:
                                stato_mpv = "BUFFERING"
                            elif not is_paused and mpv_dur > 0:
                                stato_mpv = "PLAY"
                            elif is_paused:
                                stato_mpv = "PAUSE"
                            else:
                                stato_mpv = "STOP"
                        except Exception:
                            stato_mpv = "ERROR"
                    
                    # ============================================================
                    # 2. RILEVA FINE BRANO (TRANSIZIONE PLAY -> STOP)
                    # ============================================================
                    brano_finito = False
                    
                    # Per MOCP: rileva transizione da PLAY a STOP
                    if self.active_engine == "mocp":
                        if stato_precedente_mocp == "PLAY" and stato_mocp == "STOP":
                            brano_finito = True
                            sys.stdout.write(f"\r🎵 Transizione PLAY->STOP rilevata")
                            sys.stdout.flush()
                        elif stato_precedente_mocp == None and stato_mocp == "PLAY":
                            # Primo rilevamento, il brano è partito
                            brano_in_corso = True
                        elif stato_mocp == "PLAY":
                            # Il brano sta ancora suonando
                            brano_in_corso = True
                    
                    # Per MPV: rileva fine brano tramite time_pos
                    elif self.active_engine == "mpv":
                        if stato_precedente_mpv == "PLAY" and (stato_mpv == "STOP" or stato_mpv == "BUFFERING"):
                            brano_finito = True
                            sys.stdout.write(f"\r🎵 Fine brano MPV intercettata")
                            sys.stdout.flush()
                        elif stato_mpv == "PLAY" and mpv_dur > 0 and mpv_pos >= mpv_dur - 0.5:
                            brano_finito = True
                    
                    # Aggiorna stati precedenti
                    if stato_mocp != "UNKNOWN":
                        stato_precedente_mocp = stato_mocp
                    if stato_mpv != "UNKNOWN":
                        stato_precedente_mpv = stato_mpv
                    
                    # ============================================================
                    # 3. GESTIONE FINE BRANO
                    # ============================================================
                    if brano_finito and self.sorgente_riproduzione == "playlist" and len(self.playlist_interna) > 0:
                        prossimo_indice = self.indice_corrente + 1
                        
                        if prossimo_indice < len(self.playlist_interna):
                            traccia_succ = self.playlist_interna[prossimo_indice]
                            sys.stdout.write(f"\r🎵 Passo al brano successivo: '{traccia_succ['title'][:50]}'")
                            sys.stdout.flush()
                            
                            self.indice_corrente = prossimo_indice
                            traccia = self.playlist_interna[self.indice_corrente]
                            target = traccia["path"]
                            
                            # Avvia il brano successivo
                            if target.startswith(("http://", "https://")) or "youtube.com" in target or "youtu.be" in target:
                                self.active_engine = "mpv"
                                self.web_title = traccia["title"]
                                self.mpv_player.play(target)
                                time.sleep(1.5)  # ^_^ Aggiunto
                                # Forza il riallineamento di tutte le variabili del ciclo while
                                stato_precedente_mpv = "BUFFERING" 
                                stato_mpv = "BUFFERING"
                                stato_precedente_mocp = None
                                brano_in_corso = True
                                ultimo_stato_loggato = ""
                            else:
                                self.active_engine = "mocp"
                                subprocess.run(["mocp", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                subprocess.run(["mocp", "-a", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                subprocess.run(["mocp", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                time.sleep(0.4) # ^_^ Aggiunto
                                stato_mocp = "PLAY"          # ^_^
                                ultimo_stato_loggato = ""    # ^_^

                            # Resetta stati precedenti per il nuovo brano
#                           stato_precedente_mocp = None
#                           stato_precedente_mpv = None
#                           brano_in_corso = True

                            # Sincronizza la visuale playlist
                            try:
                                playlist_widget = self.query_one("#playlist", MocpPlaylist)
                                playlist_widget.index = self.indice_corrente
                            except Exception:
                                pass
                            
                            time.sleep(0.5)
                            ultimo_stato_loggato = ""
                        else:
                            sys.stdout.write("\r🏁 Fine playlist raggiunta")
                            sys.stdout.flush()
                            self.stop_all_engines()
                            self.sorgente_riproduzione = None
                            brano_in_corso = False
                    
                    # ============================================================
                    # 4. AGGIORNAMENTO STATO A VIDEO
                    # ============================================================
                    if self.active_engine == "mocp" and stato_mocp == "UNKNOWN" and stato_precedente_mocp == "PLAY":
                        stato_mocp = "PLAY"

                    if self.active_engine == "mpv":
                        if stato_mpv == "PLAY":
                            stato_desc = f"PLAY (MPV) {int(mpv_pos//60)}:{int(mpv_pos%60):02d}/{int(mpv_dur//60)}:{int(mpv_dur%60):02d}"
                        elif stato_mpv == "PAUSE":
                            stato_desc = "PAUSE (MPV)"
                        elif stato_mpv == "BUFFERING":
                            stato_desc = "⏳ BUFFERING (MPV)"
                        elif stato_mpv == "STOP":
                            stato_desc = "STOP (MPV)"
                        else:
                            stato_desc = f"? (MPV/{stato_mpv})"
                    else:  # MOCP
                        if stato_mocp == "PLAY":
                            try:
                                formato = "%a|%t|%cs|%ts"
                                output = subprocess.check_output(["mocp", "-Q", formato], stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore').strip()
                                artista, titolo, curr, total = output.split('|')
                                c_min, c_sec = divmod(int(curr), 60)
                                t_min, t_sec = divmod(int(total), 60)
                                nome_brano = f"{artista} - {titolo}" if artista and titolo else "Brano Locale"
                                nome_brano_accorciato = nome_brano[:45] + "..." if len(nome_brano) > 45 else nome_brano
                                stato_desc = f"PLAY (MOCP) {nome_brano_accorciato} [{c_min:02d}:{c_sec:02d}/{t_min:02d}:{t_sec:02d}]"
                            except Exception:
                                # Fallback se MOCP sta cambiando brano o fallisce la lettura al volo
                                stato_desc = "PLAY (MOCP) Lettura info..."

                        elif stato_mocp == "PAUSE":
                            stato_desc = "PAUSE (MOCP)"
                        elif stato_mocp == "STOP":
                            stato_desc = "STOP (MOCP)"
                        else:
                            stato_desc = f"? (MOCP/{stato_mocp})"

                    forza_refresh = ("PLAY (MOCP)" in stato_desc or "PLAY (MPV)" in stato_desc or ultimo_stato_loggato == "")

                    if stato_desc != ultimo_stato_loggato or forza_refresh:
                        sys.stdout.write("\r" + " " * 95 + "\r")  # Pulizia riga aumentata a 95 spazi
                        sys.stdout.write(f"\r🎵 {stato_desc}")
                        sys.stdout.flush()
                        ultimo_stato_loggato = stato_desc

                    # ============================================================
                    # 5. TIMEOUT DINAMICO PER SELECT
                    # ============================================================
                    is_playing = (stato_mpv == "PLAY" or stato_mocp == "PLAY")
                    is_buffering = (stato_mpv == "BUFFERING")
                    
                    if is_playing or is_buffering:
                        timeout_select = 3.0
                    else:
                        timeout_select = None
                    
                    time.sleep(0.2)

                    # ============================================================
                    # 6. GESTIONE TASTIERA
                    # ============================================================
                    pronto, _, _ = select.select([sys.stdin], [], [], timeout_select)
                    
                    if pronto:
                        ch = sys.stdin.read(1)
                        
                        if ch == 'q' or ch == '\x03':
                            break
                        elif ch == ' ':
                            self.action_global_toggle_pause()
                            time.sleep(0.1)
                            termios.tcflush(fd, termios.TCIFLUSH)
                            sys.stdout.write("\r⏯️  Play/Pausa")
                            sys.stdout.flush()
                            ultimo_stato_loggato = ""
                        elif ch == 'n':
                            if self.sorgente_riproduzione == "playlist" and len(self.playlist_interna) > 0:
                                prossimo = self.indice_corrente + 1
                                if prossimo < len(self.playlist_interna):
                                    sys.stdout.write("\r⏭️  Brano successivo")
                                    sys.stdout.flush()
                                    self.indice_corrente = prossimo
                                    self.riproduci_indice_playlist(self.indice_corrente)
                                    stato_precedente_mocp = None
                                    stato_precedente_mpv = None
                                    ultimo_stato_loggato = ""
                                    time.sleep(0.3)
                        elif ch == 'b':
                            if self.sorgente_riproduzione == "playlist" and len(self.playlist_interna) > 0:
                                precedente = self.indice_corrente - 1
                                if precedente >= 0:
                                    sys.stdout.write("\r⏮️  Brano precedente")
                                    sys.stdout.flush()
                                    self.indice_corrente = precedente
                                    self.riproduci_indice_playlist(self.indice_corrente)
                                    stato_precedente_mocp = None
                                    stato_precedente_mpv = None
                                    ultimo_stato_loggato = ""
                                    time.sleep(0.3)
                        elif ch == 'f':
                            self.action_global_forward()
                            sys.stdout.write("\r⏩ Avanti 10s")
                            sys.stdout.flush()
                        elif ch == 'r':
                            self.action_global_rewind()
                            sys.stdout.write("\r⏪ Indietro 10s")
                            sys.stdout.flush()
                        elif ch == 'F':
                            self.action_avanti_veloce()
                            sys.stdout.write("\r⏩ Avanti 40s")
                            sys.stdout.flush()
                        elif ch == 'R':
                            self.action_indietro_veloce()
                            sys.stdout.write("\r⏪ Indietro 40s")
                            sys.stdout.flush()
                    else:
                        if is_playing:
                            # Questo garantisce che il ciclo non faccia più di un giro al secondo!
                            time.sleep(0.9)						

                sys.stdout.write("\r\n\r\n[Ripristino interfaccia grafica...]\r\n")
                sys.stdout.flush()
                
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, vecchi_settings)
                time.sleep(0.2)
        
        if hasattr(self, "update_timer"):
            self.update_timer.resume()
        
        self.refresh(layout=True)
        try:
    #       self.query_one("#m3x-viewer" if "hidden" not in self.query_one("#m3x-viewer").classes else "#file-browser").focus()
            self.query_one("#playlist").focus()
        except:
            pass

if __name__ == "__main__":
    MocpUltimateCombo().run()
