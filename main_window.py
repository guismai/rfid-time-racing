"""
Main Tkinter application window for RFID Time Racing.

Covers:
  - Reader connection (USB/HID)
  - RF Power / WorkMode / OutInterface get&set
  - Frequency band / region / start-end channel get&set
  - Race timing workflow: Start line / Finish line / Round, teams.csv
    import, real-time CSV export, and a Results cross-reference view
  - Tag capture list (ttk.Treeview) + Start/Stop/Export/Clear
  - Log window

Tkinter is not thread-safe: widgets must only be touched from the
main thread. Calls from the inventory worker thread are marshalled
back onto the UI thread via a thread-safe `queue.Queue` that the
worker thread posts to, drained periodically on the main thread via
`root.after(...)` (see `_pump_queue`).
"""
import csv
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

from channel_region import ChannelRegion, ChannelRegionItem, ChannelItem, ChannelCount
from freq_info import FreqInfo
from reader_api import Reader
from reader_exception import ReaderException
from tag_item import Devicepara, ShowTagItem
import util
from gsheet_export import GoogleSheetExporter, GoogleSheetError


STOP_INVENTORY_TIMEOUT_MS = 10000
PAGE_LINES = 30  # rows shown at a time (kept for parity with the original paging fields)

_APP_DIR = (sys._MEIPASS if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
            else os.path.dirname(os.path.abspath(__file__)))


class MessageType:
    Info = 0
    Warning = 1
    Error = 2


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RFID Time racing")
        self.geometry("1020x700")
        self.minsize(1020, 700)  # can't be shrunk below this, but can still be maximized
        self._set_window_icon()

        # --- state -----------------------
        self.reader = Reader()
        self.devicepara = Devicepara()
        self.in_inventory = False
        self.stop_inventory = True
        self.inv_thread: threading.Thread | None = None
        self.is_closed = False

        self.tags: dict[bytes, ShowTagItem] = {}     # by code, for de-dup (m_tags)
        self.tags_ordered: list[ShowTagItem] = []      # in received order (m_tags2)
        self.inv_tag_count = 0
        self.inv_time_ms = 1
        self.inv_start_tick = 0.0

        self.allowed_codes: dict[bytes, dict] | None = None  # None = no filter loaded ; code -> {"bib","team"}
        self.first_seen_allowed: dict[bytes, str] = {}  # code -> time of first authorized passage

        # Real-time CSV export of the current race (see _start_race_csv / _stop_race_csv)
        self.race_csv_file = None
        self.race_csv_writer = None
        self.race_csv_path: str | None = None
        self._last_export_dir: str | None = None

        # Live Google Sheet export (see gsheet_export.py)
        self.gsheet_exporter: GoogleSheetExporter | None = None
        self.gsheet_ready = False  # True once connected + spreadsheet confirmed
        self.gsheet_connecting = False

        self.ui_queue: "queue.Queue" = queue.Queue()
        self._tags_lock = threading.Lock()

        # Separate log window (created on demand, see open_log_window)
        self.log_lines: list[str] = []
        self.log_window: tk.Toplevel | None = None
        self.log_text: tk.Text | None = None

        self._build_ui()
        self.after(80, self._pump_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.cmb_region.set("Europe")  # default region
        self.on_region_changed()

        self.after(200, self._auto_connect_usb)

    # ------------------------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------------------------
    def _set_window_icon(self):
        """Sets the app icon (title bar / taskbar). Best-effort: never blocks
        startup if the .ico file is missing or the platform doesn't support it."""
        try:
            icon_path = os.path.join(_APP_DIR, "icon.ico")
            if os.path.isfile(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass  # e.g. non-Windows platform where .ico isn't supported

    def _create_hidden_popup(self, title: str, resizable: bool = False) -> tk.Toplevel:
        """Creates a Toplevel window hidden by default (closing it hides it, doesn't destroy it)."""
        win = tk.Toplevel(self)
        win.title(title)
        win.resizable(resizable, resizable)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        win.withdraw()
        return win

    @staticmethod
    def _toggle_popup(win: tk.Toplevel):
        if win.state() == "withdrawn":
            win.deiconify()
            win.lift()
        else:
            win.withdraw()

    def _set_button_color(self, button: tk.Button, active: bool):
        if active:
            button.configure(bg="#4caf50", fg="white", activebackground="#43a047")
        else:
            button.configure(bg=self._default_btn_bg, fg=self._default_btn_fg,
                              activebackground=self._default_btn_bg)

    def _update_usb_button_color(self):
        self._set_button_color(self.btn_toggle_usb, self.reader.IsOpened)

    def _set_start_button_color(self, active: bool):
        """Keeps the main-window Start button and the Capture RFID popup's
        Start button in sync (both control the same inventory)."""
        self._set_button_color(self.btn_inventory, active)
        self._set_button_color(self.btn_inventory_capture, active)

    def _bind_start_hover(self, button: tk.Button):
        """Turns a Start button green on hover; on leave, falls back to green
        if inventory is actually running, or the default color otherwise."""
        button.bind("<Enter>", lambda e: button.configure(bg="#4caf50", fg="white"))
        button.bind("<Leave>", lambda e: self._set_start_button_color(self.in_inventory))

    # ------------------------------------------------------------------------------------
    # Live Google Sheet export
    # ------------------------------------------------------------------------------------
    def _ensure_gsheet_round_tab(self):
        """Best-effort: if the Google Sheet export is active, make sure this
        round's tab exists before the race starts (surfaces problems early
        instead of only on the first passage). Never blocks the race from
        starting — failures just log a warning."""
        if not (self.gsheet_ready and self.gsheet_exporter is not None):
            return
        try:
            self.gsheet_exporter.ensure_round_tab(self._get_round_number())
        except Exception as ex:
            self.write_log(MessageType.Warning, "Could not prepare the Google Sheet round tab", ex)

    def on_gsheet_button_click(self):
        if self.gsheet_ready:
            messagebox.showinfo(self.title(), "Google Sheet export is already configured and active.")
            return
        if self.gsheet_connecting:
            return

        self.gsheet_connecting = True
        self.btn_gsheet.configure(state="disabled", text="Connecting to Google...")
        self.write_log(MessageType.Info,
                        "Connecting to Google Sheets (a browser window may open for sign-in)...")

        def worker():
            try:
                exporter = GoogleSheetExporter(_APP_DIR)
                exporter.connect()  # blocking: opens the browser for sign-in if needed
                self.gsheet_exporter = exporter
                self.ui_queue.put(("gsheet_connected",))
            except GoogleSheetError as ex:
                self.ui_queue.put(("gsheet_failed", str(ex)))
            except Exception as ex:
                self.ui_queue.put(("gsheet_failed", f"Unexpected error: {ex}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_gsheet_connected_ui(self):
        self.gsheet_connecting = False
        self.gsheet_ready = True
        self.btn_gsheet.configure(state="normal", text="Export live Google Sheet")
        self._set_button_color(self.btn_gsheet, True)
        self.write_log(MessageType.Info,
                        "Google Sheet ready: 'RFID Time Racing' — passages will be pushed live.")

    def _on_gsheet_failed_ui(self, message: str):
        self.gsheet_connecting = False
        self.gsheet_exporter = None
        self.btn_gsheet.configure(state="normal", text="Export live Google Sheet")
        self._set_button_color(self.btn_gsheet, False)
        self.write_log(MessageType.Error, "Google Sheet connection failed: ", message)
        messagebox.showinfo(self.title(), f"Google Sheet connection failed:\n{message}")

    def _build_ui(self):
        root_pad = {"padx": 6, "pady": 6}

        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, **root_pad)

        # --- Popup windows (hidden by default) for USB / Device Parameters / Frequency --
        self.usb_window = self._create_hidden_popup("USB Connect")
        group_usb = ttk.Frame(self.usb_window, padding=8)
        group_usb.pack(fill=tk.BOTH, expand=True)

        self.dev_window = self._create_hidden_popup("Device Parameters")
        group_dev = ttk.Frame(self.dev_window, padding=8)
        group_dev.pack(fill=tk.BOTH, expand=True)

        self.freq_window = self._create_hidden_popup("Frequency")
        group_freq = ttk.Frame(self.freq_window, padding=8)
        group_freq.pack(fill=tk.BOTH, expand=True)

        # --- Top row buttons (all the same height) --------------------------
        self.btn_toggle_usb = tk.Button(top, text="Connection", width=14,
                                         command=lambda: self._toggle_popup(self.usb_window))
        self.btn_toggle_usb.grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self._default_btn_bg = self.btn_toggle_usb.cget("bg")
        self._default_btn_fg = self.btn_toggle_usb.cget("fg")

        # --- Start line / Finish line / Round ------------------------------------------------
        race_frame = ttk.Frame(top)
        race_frame.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.race_mode_var = tk.StringVar(value="")  # "" = no choice made yet (required before Start)
        self.rb_start_line = ttk.Radiobutton(race_frame, text="Start line", value="start_line",
                                              variable=self.race_mode_var)
        self.rb_start_line.pack(side=tk.LEFT, padx=(0, 6))
        self.rb_finish_line = ttk.Radiobutton(race_frame, text="Finish line", value="finish_line",
                                               variable=self.race_mode_var)
        self.rb_finish_line.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(race_frame, text="Round:").pack(side=tk.LEFT)
        self.round_var = tk.StringVar(value="1")
        self.spn_round = ttk.Spinbox(race_frame, from_=1, to=10, width=3,
                                      textvariable=self.round_var, state="readonly")
        self.spn_round.pack(side=tk.LEFT)

        self.btn_inventory = tk.Button(top, text="Start", width=10,
                                        command=self.on_inventory_click)
        self.btn_inventory.grid(row=0, column=2, padx=4, pady=4, sticky="w")
        self._bind_start_hover(self.btn_inventory)

        self.btn_inv_stop = tk.Button(top, text="Stop", width=10,
                                       command=self.on_inv_stop_click)
        self.btn_inv_stop.grid(row=0, column=3, padx=4, pady=4, sticky="w")
        self.btn_inv_stop.bind("<Enter>", lambda e: self.btn_inv_stop.configure(bg="#e53935", fg="white"))
        self.btn_inv_stop.bind("<Leave>", lambda e: self.btn_inv_stop.configure(
            bg=self._default_btn_bg, fg=self._default_btn_fg))

        self.btn_toggle_dev = tk.Button(top, text="Device Parameters", width=18,
                                         command=lambda: self._toggle_popup(self.dev_window))
        self.btn_toggle_dev.grid(row=0, column=4, padx=4, pady=4, sticky="w")

        self.btn_toggle_freq = tk.Button(top, text="Frequency", width=14,
                                          command=lambda: self._toggle_popup(self.freq_window))
        self.btn_toggle_freq.grid(row=0, column=5, padx=4, pady=4, sticky="w")

        self.btn_open_log = tk.Button(top, text="Log", width=10, command=self.open_log_window)
        self.btn_open_log.grid(row=0, column=6, padx=4, pady=4, sticky="w")

        self.btn_toggle_capture = tk.Button(top, text="Capture RFID", width=14,
                                             command=lambda: self._toggle_popup(self.capture_window))
        self.btn_toggle_capture.grid(row=0, column=7, padx=4, pady=4, sticky="w")

        self.btn_toggle_results = tk.Button(top, text="Results", width=14,
                                             command=lambda: self._toggle_popup(self.results_window))
        self.btn_toggle_results.grid(row=0, column=8, padx=(30, 4), pady=4, sticky="w")

        self.btn_gsheet = tk.Button(top, text="Export live Google Sheet", width=24,
                                     command=self.on_gsheet_button_click)
        self.btn_gsheet.grid(row=0, column=9, padx=4, pady=4, sticky="w")

        # --- USB Connect (popup window content) --------------------------------------

        ttk.Label(group_usb, text="USB PATH:").grid(row=0, column=0, columnspan=2, sticky="w")
        self.cbx_usb_path = ttk.Combobox(group_usb, width=16, state="readonly")
        self.cbx_usb_path.grid(row=1, column=0, columnspan=2, padx=4, pady=2)

        self.btn_scan_usb = ttk.Button(group_usb, text="ScanUSB", command=self.on_scan_usb)
        self.btn_scan_usb.grid(row=2, column=0, padx=2, pady=4)
        self.btn_usb_open = ttk.Button(group_usb, text="OPEN", command=self.on_usb_open)
        self.btn_usb_open.grid(row=2, column=1, padx=2, pady=4)
        self.btn_usb_close = ttk.Button(group_usb, text="CLOSE", command=self.on_usb_close)
        self.btn_usb_close.grid(row=3, column=0, columnspan=2, pady=(0, 4))

        # --- RF Power / WorkMode / OutInterface (popup window content) -------------
        ttk.Label(group_dev, text="RfPower (dBm):").grid(row=0, column=0, sticky="w")
        self.cmb_tx_power = ttk.Combobox(group_dev, width=8, state="readonly",
                                          values=[str(i) for i in range(34)])
        self.cmb_tx_power.grid(row=0, column=1, padx=4, pady=2)
        ttk.Button(group_dev, text="Get", command=self.on_get_tx_power, width=6).grid(row=0, column=2, padx=2)
        ttk.Button(group_dev, text="Set", command=self.on_set_tx_power, width=6).grid(row=0, column=3, padx=2)

        ttk.Label(group_dev, text="WorkMode:").grid(row=1, column=0, sticky="w")
        self.cmb_workmode = ttk.Combobox(group_dev, width=12, state="readonly",
                                          values=["AnswerMode", "ActiveMode"])
        self.cmb_workmode.grid(row=1, column=1, padx=4, pady=2)
        ttk.Button(group_dev, text="Get", command=self.on_get_workmode, width=6).grid(row=1, column=2, padx=2)
        ttk.Button(group_dev, text="Set", command=self.on_set_workmode, width=6).grid(row=1, column=3, padx=2)

        ttk.Label(group_dev, text="OutInterface:").grid(row=2, column=0, sticky="w")
        self.cmb_out_interface = ttk.Combobox(
            group_dev, width=12, state="readonly",
            values=["RS232", "RS485", "RJ45", "Wieggand", "WiFi", "USB", "KeyBoard", "CDC_COM"])
        self.cmb_out_interface.grid(row=2, column=1, padx=4, pady=2)
        ttk.Button(group_dev, text="Get", command=self.on_get_out_interface, width=6).grid(row=2, column=2, padx=2)
        ttk.Button(group_dev, text="Set", command=self.on_set_out_interface, width=6).grid(row=2, column=3, padx=2)

        ttk.Label(group_dev, text="Buzzer:").grid(row=3, column=0, sticky="w")
        ttk.Button(group_dev, text="Turn buzzer off", command=self.on_disable_buzzer).grid(
            row=3, column=1, columnspan=2, sticky="we", padx=2, pady=2)
        ttk.Button(group_dev, text="Turn on", command=self.on_enable_buzzer, width=6).grid(
            row=3, column=3, padx=2)

        # --- Frequency (popup window content) --------------------------------------
        ttk.Label(group_freq, text="Freq Band:").grid(row=0, column=0, sticky="w", padx=4)
        self.cmb_region = ttk.Combobox(
            group_freq, width=10, state="readonly",
            values=["Custom", "USA", "Korea", "Europe", "Japan", "Malaysia", "Europe3", "China_1", "China_2"])
        self.cmb_region.grid(row=0, column=1, padx=4, pady=4)
        self.cmb_region.bind("<<ComboboxSelected>>", self.on_region_changed)

        ttk.Label(group_freq, text="Freq Start:").grid(row=0, column=2, sticky="w", padx=4)
        self.cmb_freq_start = ttk.Combobox(group_freq, width=10, state="readonly")
        self.cmb_freq_start.grid(row=0, column=3, padx=4)

        ttk.Label(group_freq, text="End:").grid(row=0, column=4, sticky="w", padx=4)
        self.cmb_freq_end = ttk.Combobox(group_freq, width=10, state="readonly")
        self.cmb_freq_end.grid(row=0, column=5, padx=4)

        ttk.Button(group_freq, text="Get", command=self.on_get_freq, width=6).grid(row=0, column=6, padx=4)
        ttk.Button(group_freq, text="Set", command=self.on_set_freq, width=6).grid(row=0, column=7, padx=4)

        # keep parallel Python-side lists for the frequency combos (VB stored objects as Items)
        self._freq_start_items: list[ChannelItem] = []
        self._freq_end_items: list[ChannelCount] = []

        # --- Inventory controls + tag list + log ---------------------------------------------
        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, **root_pad)

        left = ttk.Frame(mid)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("no", "code", "len", "count", "rssi", "channel")
        headers = {"no": "No.", "code": "Data", "len": "Len",
                   "count": "Cnt(Ant1/2/3/4)", "rssi": "RSSI(dBm)", "channel": "Channel"}
        widths = {"no": 40, "code": 320, "len": 50, "count": 110, "rssi": 90, "channel": 70}

        # --- Capture RFID popup: the raw detected-tags list lives here, with only
        # Export and Clear as actions. ------------------------------------------------
        self.capture_window = self._create_hidden_popup("Capture RFID", resizable=True)
        capture_frame = ttk.Frame(self.capture_window, padding=8)
        capture_frame.pack(fill=tk.BOTH, expand=True)

        capture_btn_row = ttk.Frame(capture_frame)
        capture_btn_row.pack(side=tk.TOP, fill=tk.X, pady=4)
        self.btn_inventory_capture = tk.Button(capture_btn_row, text="Start", width=10,
                                                command=self.on_capture_start_click)
        self.btn_inventory_capture.pack(side=tk.LEFT, padx=4)
        self._bind_start_hover(self.btn_inventory_capture)
        self.btn_inv_stop_capture = tk.Button(capture_btn_row, text="Stop", width=10,
                                               command=self.on_inv_stop_click)
        self.btn_inv_stop_capture.pack(side=tk.LEFT, padx=4)
        self.btn_inv_stop_capture.bind("<Enter>", lambda e: self.btn_inv_stop_capture.configure(
            bg="#e53935", fg="white"))
        self.btn_inv_stop_capture.bind("<Leave>", lambda e: self.btn_inv_stop_capture.configure(
            bg=self._default_btn_bg, fg=self._default_btn_fg))
        self.btn_clear = ttk.Button(capture_btn_row, text="Clear", command=self.on_clear)
        self.btn_clear.pack(side=tk.RIGHT, padx=4)
        self.btn_export_tags = ttk.Button(capture_btn_row, text="Export", command=self.on_export_tags)
        self.btn_export_tags.pack(side=tk.RIGHT, padx=4)

        tags_frame = ttk.Frame(capture_frame)
        tags_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.lsv_tags = ttk.Treeview(tags_frame, columns=columns, show="headings", height=4)
        for c in columns:
            self.lsv_tags.heading(c, text=headers[c])
            self.lsv_tags.column(c, width=widths[c], anchor="center")
        self.lsv_tags.tag_configure("allowed", background="#c8f7c5")
        self.lsv_tags.tag_configure("denied", background="#f7c5c5")
        tags_vsb = ttk.Scrollbar(tags_frame, orient="vertical", command=self.lsv_tags.yview)
        self.lsv_tags.configure(yscrollcommand=tags_vsb.set)
        self.lsv_tags.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tags_vsb.pack(side=tk.LEFT, fill=tk.Y)

        # --- Results popup: cross-reference a start_line file and a finish_line file
        # (same round) by bib number, and compute start/finish/duration per bib. -------
        self.results_window = self._create_hidden_popup("Results", resizable=True)
        results_frame = ttk.Frame(self.results_window, padding=8)
        results_frame.pack(fill=tk.BOTH, expand=True)

        self._results_start_path: str | None = None
        self._results_finish_path: str | None = None
        self._results_rows: list[dict] = []

        file_row = ttk.Frame(results_frame)
        file_row.pack(side=tk.TOP, fill=tk.X, pady=4)
        ttk.Button(file_row, text="Select start_line file",
                   command=self.on_select_start_file).pack(side=tk.LEFT, padx=4)
        self.lbl_start_file = ttk.Label(file_row, text="(none)")
        self.lbl_start_file.pack(side=tk.LEFT, padx=4)

        file_row2 = ttk.Frame(results_frame)
        file_row2.pack(side=tk.TOP, fill=tk.X, pady=4)
        ttk.Button(file_row2, text="Select finish_line file",
                   command=self.on_select_finish_file).pack(side=tk.LEFT, padx=4)
        self.lbl_finish_file = ttk.Label(file_row2, text="(none)")
        self.lbl_finish_file.pack(side=tk.LEFT, padx=4)

        results_btn_row = ttk.Frame(results_frame)
        results_btn_row.pack(side=tk.TOP, fill=tk.X, pady=4)
        ttk.Button(results_btn_row, text="Export CSV",
                   command=self.on_export_results).pack(side=tk.RIGHT, padx=4)

        results_list_frame = ttk.Frame(results_frame)
        results_list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        results_columns = ("bib", "team", "start", "finish", "duration")
        self.lsv_results = ttk.Treeview(results_list_frame, columns=results_columns, show="headings")
        self.lsv_results.heading("bib", text="Bib")
        self.lsv_results.heading("team", text="Team")
        self.lsv_results.heading("start", text="Start")
        self.lsv_results.heading("finish", text="Finish")
        self.lsv_results.heading("duration", text="Duration")
        self.lsv_results.column("bib", width=70, anchor="center")
        self.lsv_results.column("team", width=140, anchor="center")
        self.lsv_results.column("start", width=150, anchor="center")
        self.lsv_results.column("finish", width=150, anchor="center")
        self.lsv_results.column("duration", width=110, anchor="center")
        results_vsb = ttk.Scrollbar(results_list_frame, orient="vertical", command=self.lsv_results.yview)
        self.lsv_results.configure(yscrollcommand=results_vsb.set)
        self.lsv_results.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_vsb.pack(side=tk.LEFT, fill=tk.Y)

        # --- Authorized tags: only the first passage of each one (main window) --------
        log_container = ttk.Frame(left)
        log_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        log_header = ttk.Frame(log_container)
        log_header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(log_header, text="Authorized tags (first passage)").pack(side=tk.LEFT, anchor="w")
        log_frame = ttk.Frame(log_container)
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        log_columns = ("no", "bib", "code", "team", "time")
        self.lsv_allowed_log = ttk.Treeview(log_frame, columns=log_columns, show="headings")
        self.lsv_allowed_log.heading("no", text="Pass #")
        self.lsv_allowed_log.heading("bib", text="Bib")
        self.lsv_allowed_log.heading("code", text="RFID")
        self.lsv_allowed_log.heading("team", text="Team")
        self.lsv_allowed_log.heading("time", text="Passage time")
        self.lsv_allowed_log.column("no", width=60, anchor="center")
        self.lsv_allowed_log.column("bib", width=80, anchor="center")
        self.lsv_allowed_log.column("code", width=260, anchor="center")
        self.lsv_allowed_log.column("team", width=140, anchor="center")
        self.lsv_allowed_log.column("time", width=160, anchor="center")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.lsv_allowed_log.yview)
        self.lsv_allowed_log.configure(yscrollcommand=log_vsb.set)
        self.lsv_allowed_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_vsb.pack(side=tk.LEFT, fill=tk.Y)

    # ------------------------------------------------------------------------------------
    # Logging — safe to call from any thread
    # ------------------------------------------------------------------------------------
    def write_log(self, msg_type: int, msg: str, ex: Exception = None):
        self.ui_queue.put(("log", msg_type, msg, ex))

    def _write_log_ui(self, msg_type: int, msg: str, ex: Exception):
        prefix = {MessageType.Info: "info: ", MessageType.Warning: "warning: ",
                  MessageType.Error: "error: "}.get(msg_type, "")
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}, {prefix}"
        if msg:
            line += msg
        if ex is not None:
            line += str(ex)
        line += "\n"
        self.log_lines.append(line)
        if self.log_text is not None:
            self.log_text.insert("end", line)
            self.log_text.see("end")

    def open_log_window(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            self.log_window.focus_force()
            return

        self.log_window = tk.Toplevel(self)
        self.log_window.title("Logs")
        self.log_window.geometry("560x420")
        self.log_text = tk.Text(self.log_window, wrap="word")
        self.log_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.log_text.insert("end", "".join(self.log_lines))
        self.log_text.see("end")
        self.log_window.protocol("WM_DELETE_WINDOW", self._on_log_window_close)

    def _on_log_window_close(self):
        if self.log_window is not None:
            self.log_window.destroy()
        self.log_window = None
        self.log_text = None

    def _pump_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._write_log_ui(item[1], item[2], item[3])
                elif kind == "show_tags":
                    self._show_tag_ui()
                elif kind == "inventory_end":
                    self._on_inventory_end_ui()
                elif kind == "gsheet_connected":
                    self._on_gsheet_connected_ui()
                elif kind == "gsheet_failed":
                    self._on_gsheet_failed_ui(item[1])
        except queue.Empty:
            pass
        self.after(80, self._pump_queue)

    # ------------------------------------------------------------------------------------
    # Post-connect initialization
    # ------------------------------------------------------------------------------------
    def _init_reader(self):
        try:
            if self.reader.IsOpened:
                self.devicepara = self.reader.get_device_para()
                self.cmb_tx_power.set(str(self.devicepara.Power))
                if self.devicepara.Workmode > 1:
                    self.cmb_workmode.current(0)
                else:
                    self.cmb_workmode.current(self.devicepara.Workmode)
                self.cmb_region.current(self.devicepara.Region)
                self.on_region_changed()
                self.on_get_out_interface()
        except Exception as ex:
            try:
                self.reader.close()
            except Exception:
                pass
            self.write_log(MessageType.Error, "Failed to get Power", ex)
            messagebox.showinfo(self.title(), f"Failed to get power: {ex}")
        finally:
            self._update_usb_button_color()

    # ------------------------------------------------------------------------------------
    # USB / HID connect
    # ------------------------------------------------------------------------------------
    def on_scan_usb(self):
        self.cbx_usb_path["values"] = []
        count = self.reader.cfhid_get_usb_count()
        values = []
        for index in range(count):
            buffer = bytearray(256)
            self.reader.cfhid_get_usb_info(index, buffer)
            sn = buffer.decode("utf-8", errors="ignore").replace("\x00", "")
            flag = sn[-3:] if len(sn) >= 3 else sn
            label = "\\Keyboard-can'topen" if flag == "kbd" else "\\USB-open"
            values.append(label)
            print(f"[DEBUG] ScanUSB: index={index} raw_sn={sn!r} -> {label}", file=sys.stderr)
        self.cbx_usb_path["values"] = values
        if count > 0:
            self.cbx_usb_path.current(0)
        print(f"[DEBUG] ScanUSB: {count} device(s) found, values={values}", file=sys.stderr)

    def on_usb_open(self):
        try:
            if self.reader.IsOpened:
                messagebox.showinfo(self.title(), "The reader is already open, please close the reader first")
                return
            index = self.cbx_usb_path.current()
            self.reader.open_usb(index if index >= 0 else 0)
            self.write_log(MessageType.Info, f"The reader is opened successfully, the usb number: {index}")
            self.btn_scan_usb.configure(state="disabled")
            self.btn_usb_open.configure(state="disabled")
        except Exception as ex:
            try:
                if self.reader.IsOpened:
                    self.reader.close()
            except Exception:
                pass
            self.write_log(MessageType.Error, "Reader failed to open", ex)
            messagebox.showinfo(self.title(), f"Reader failed to open: {ex}")
        self._init_reader()  # (also updates the Connection button color)

    def on_usb_close(self):
        self.write_log(MessageType.Info, "Reader Disconnected")
        self.btn_scan_usb.configure(state="normal")
        self.btn_usb_open.configure(state="normal")
        if self.reader is not None:
            self.reader.close()
        self._update_usb_button_color()

    def _auto_connect_usb(self):
        """Automatic connection at startup on the '\\USB-open' interface."""
        try:
            self.on_scan_usb()
            values = list(self.cbx_usb_path["values"])
            if "\\USB-open" in values:
                idx = values.index("\\USB-open")
            elif values:
                idx = 0
            else:
                self.write_log(MessageType.Warning, "Auto-connect USB: no device found")
                return
            self.cbx_usb_path.current(idx)
            self.on_usb_open()
        except Exception as ex:
            self.write_log(MessageType.Error, "Auto-connect USB failed", ex)


    # ------------------------------------------------------------------------------------
    # RF power / work mode / out interface
    # ------------------------------------------------------------------------------------
    def on_set_tx_power(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Set device RfPower")
            power = util.dec_from_string(self.cmb_tx_power.get())
            self.devicepara.Power = power
            self.reader.set_device_para(self.devicepara)
            self.write_log(MessageType.Info, "Set device RfPower Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_get_tx_power(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Get device RfPower")
            self.devicepara = self.reader.get_device_para()
            self.cmb_tx_power.set(str(self.devicepara.Power))
            self.write_log(MessageType.Info, "Get device RfPower Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_set_workmode(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Set device Workmode")
            self.devicepara.Workmode = self.cmb_workmode.current()
            self.write_log(MessageType.Info, "Set device Workmode Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_get_workmode(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Get device Workmode")
            self.devicepara = self.reader.get_device_para()
            self.cmb_workmode.current(self.devicepara.Workmode)
            self.write_log(MessageType.Info, "Get device Workmode Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_get_out_interface(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Get device interface")
            self.devicepara = self.reader.get_device_para()
            interport = self.devicepara.port
            mapping = {0x80: "RS232", 0x40: "RS485", 0x20: "RJ45", 0x10: "WiFi",
                       0x01: "USB", 0x02: "KeyBoard", 0x04: "CDC_COM"}
            if interport in mapping:
                self.cmb_out_interface.set(mapping[interport])
            self.write_log(MessageType.Info, "Get device interface Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_set_out_interface(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Set device OutInterface")
            self.devicepara.wieggand = 0x0
            idx = self.cmb_out_interface.current()
            port_map = {0: 0x80, 1: 0x40, 2: 0x20, 4: 0x10, 5: 0x01, 6: 0x02, 7: 0x04}
            self.devicepara.port = port_map.get(idx, 0x80)
            self.reader.set_device_para(self.devicepara)
            self.write_log(MessageType.Info, "Set device OutInterface Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def _set_buzzer(self, enabled: bool):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Set device Buzzer " + ("ON" if enabled else "OFF"))
            self.devicepara.Buzzertime = 1 if enabled else 0
            self.reader.set_device_para(self.devicepara)
            self.write_log(MessageType.Info, "Set device Buzzer Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_disable_buzzer(self):
        self._set_buzzer(False)

    def on_enable_buzzer(self):
        self._set_buzzer(True)

    # ------------------------------------------------------------------------------------
    # Frequency (region selection + get/set)
    # ------------------------------------------------------------------------------------
    def on_region_changed(self, _event=None):
        try:
            self.cmb_freq_start["values"] = []
            self.cmb_freq_end["values"] = []
            self._freq_start_items = []
            self._freq_end_items = []

            region = ChannelRegionItem.option_from_value(
                ChannelRegionItem.string_to_region(self.cmb_region.get()), True)
            if region is None:
                return

            if region.value == ChannelRegion.Custom:
                self.cmb_freq_start.configure(state="normal")
                self.cmb_freq_end.configure(state="normal")
            else:
                self.cmb_freq_start.configure(state="readonly")
                self.cmb_freq_end.configure(state="readonly")

                self._freq_start_items = region.get_channel_items()
                self._freq_end_items = region.get_channel_counts()
                self.cmb_freq_start["values"] = [str(i) for i in self._freq_start_items]
                self.cmb_freq_end["values"] = [str(i) for i in self._freq_end_items]

                if self._freq_start_items:
                    self.cmb_freq_start.current(0)
                if self._freq_end_items:
                    self.cmb_freq_end.current(len(self._freq_end_items) - 1)
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_set_freq(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Set device Freq")

            self.devicepara.Region = self.cmb_region.current()
            region = ChannelRegionItem.option_from_value(
                ChannelRegionItem.string_to_region(self.cmb_region.get()), True)
            if region is None:
                return

            if region.value == ChannelRegion.Custom:
                start_text = self.cmb_freq_start.get().strip()
                if not start_text:
                    raise Exception("Please enter the starting frequency")
                step_text = "500"
                end_text = self.cmb_freq_end.get().strip()

                try:
                    freq2 = float(start_text)
                except ValueError:
                    freq2 = None
                if freq2 is None or freq2 < 840 or freq2 > 960:
                    raise Exception('The entered "frequency" value must be a number between 840 and 960 '
                                     '(including 840 and 960) (can be a decimal)')
                try:
                    f_endfreq = float(end_text)
                except ValueError:
                    f_endfreq = None
                if f_endfreq is None or f_endfreq < 840 or f_endfreq > 960:
                    raise Exception('The entered "frequency" value must be a number between 840 and 960 '
                                     '(including 840 and 960) (can be a decimal)')

                step2 = util.number_from_string(step_text)
                if step2 < 0 or step2 > 2000:
                    raise Exception('The entered "Channel Spacing Frequency" value must be an integer '
                                     'between 0 and 500 (Include an integer between 1 and 500)')

                count2 = int(f_endfreq - freq2) // step2
                if count2 < 1 or count2 > 50:
                    raise Exception('The entered "Channel Spacing Frequency" value must be an integer '
                                     'between 1 and 50 (Include an integer between 1 and 50)')
            else:
                start_idx = self.cmb_freq_start.current()
                end_idx = self.cmb_freq_end.current()
                if start_idx < 0 or start_idx >= len(self._freq_start_items):
                    raise Exception("Please select a starting frequency")
                if end_idx < 0 or end_idx >= len(self._freq_end_items):
                    raise Exception("Please select a ending frequency")
                item = self._freq_start_items[start_idx]
                freq2 = item.freq
                count2 = end_idx - start_idx + 1
                f_endfreq = freq2 + region.freq_step * count2

            i_val = freq2 * 1000
            self.devicepara.StartFreq = int(freq2)
            self.devicepara.StartFreqde = int(i_val - int(freq2) * 1000)
            self.devicepara.Stepfreq = region.freq_step
            self.devicepara.Channel = count2

            self.reader.set_device_para(self.devicepara)
            self.write_log(MessageType.Info, "Set device Freq Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_get_freq(self):
        try:
            if self.reader is None:
                raise RuntimeError("Reader not connected")
            self.write_log(MessageType.Info, "Get device Freq")
            self.devicepara = self.reader.get_device_para()

            freq = FreqInfo()
            freq.region = self.devicepara.Region
            freq.start_freq = float(self.devicepara.StartFreq) + float(self.devicepara.StartFreqde) / 1000.0
            freq.step_freq = self.devicepara.Stepfreq
            freq.count = self.devicepara.Channel

            region = ChannelRegionItem.option_from_value(ChannelRegion(freq.region), True)

            self.cmb_freq_start.set(f"{freq.start_freq:.3f}")
            self.cmb_freq_end.set(f"{(freq.start_freq + freq.step_freq * freq.count):.3f}")

            region_names = {
                ChannelRegion.USA: "USA", ChannelRegion.China_1: "China_1",
                ChannelRegion.China_2: "China_2", ChannelRegion.Europe3: "Europe3",
                ChannelRegion.Korea: "Korea", ChannelRegion.Europe: "Europe",
                ChannelRegion.Japan: "Japan", ChannelRegion.Malaysia: "Malaysia",
            }
            if region is None or region.value not in region_names:
                self.write_log(MessageType.Info, "Get device Freq Success")
                return
            self.cmb_region.set(region_names[region.value])

            item1_idx = None
            for i, item in enumerate(self._freq_start_items):
                if abs(item.freq - freq.start_freq) < 0.01:
                    item1_idx = i
                    break
            item2_idx = None
            if item1_idx is not None:
                for i, item in enumerate(self._freq_end_items):
                    if item.count == freq.count:
                        item2_idx = i
                        break
            if item1_idx is not None:
                self.cmb_freq_start.current(item1_idx)
            if item2_idx is not None:
                self.cmb_freq_end.current(item2_idx)

            self.write_log(MessageType.Info, "Get device Freq Success")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    # ------------------------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------------------------
    def _close_inventory_thread(self):
        try:
            self.stop_inventory = True
            if self.inv_thread is not None:
                self.inv_thread.join(4.0)
        except Exception:
            pass

    def _lock_race_controls(self, locked: bool):
        state = "disabled" if locked else "readonly"
        self.rb_start_line.configure(state="disabled" if locked else "normal")
        self.rb_finish_line.configure(state="disabled" if locked else "normal")
        self.spn_round.configure(state=state)

    def _get_round_number(self) -> int:
        try:
            n = int(self.round_var.get())
        except (TypeError, ValueError):
            n = 1
        return max(1, min(10, n))

    def _start_race_csv(self):
        """Opens the real-time CSV export file for the race that is starting.
        Raises an exception if the user cancels or if opening the file fails."""
        mode = self.race_mode_var.get()
        round_num = self._get_round_number()
        directory = filedialog.askdirectory(
            title="Choose the CSV export folder for this race",
            initialdir=self._last_export_dir or os.getcwd(),
        )
        if not directory:
            raise RuntimeError("CSV export cancelled: no folder chosen")
        self._last_export_dir = directory

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{mode}_round_{round_num}_{timestamp}.csv"
        path = os.path.join(directory, filename)

        self.race_csv_file = open(path, "w", newline="", encoding="utf-8")
        self.race_csv_writer = csv.writer(self.race_csv_file, delimiter=";")
        self.race_csv_writer.writerow(["bib", "rfid", "team", "passage_time"])
        self.race_csv_file.flush()
        self.race_csv_path = path
        self.write_log(MessageType.Info, f"Real-time export started: {path}")

    def _stop_race_csv(self):
        if self.race_csv_file is not None:
            try:
                self.race_csv_file.close()
            except Exception:
                pass
            self.write_log(MessageType.Info, f"Real-time export finished: {self.race_csv_path}")
        self.race_csv_file = None
        self.race_csv_writer = None
        self.race_csv_path = None

    def _on_inventory_end_ui(self):
        self.in_inventory = False
        self.stop_inventory = True
        self.btn_inventory.configure(state="normal")
        self.btn_inventory_capture.configure(state="normal")
        self._set_start_button_color(False)
        self._lock_race_controls(False)
        self._stop_race_csv()
        self.write_log(MessageType.Info, "Inventory completed")

    def _do_stop_inventory(self):
        try:
            self.in_inventory = False
            self.stop_inventory = True
            try:
                if self.reader is not None:
                    self.reader.inventory_stop(STOP_INVENTORY_TIMEOUT_MS)
            except Exception:
                pass
        except Exception:
            pass
        self.ui_queue.put(("inventory_end",))

    def _update_page_index(self):
        pass  # paging is not used in this port; the whole tag list is shown at once

    def _show_tag(self):
        self.ui_queue.put(("show_tags",))

    def _tag_status_and_style(self, sitem: ShowTagItem):
        """Returns (status_text, ttk_tag) based on the currently loaded authorization filter."""
        if self.allowed_codes is None:
            return "", ()
        if sitem.Code in self.allowed_codes:
            return "Authorized", ("allowed",)
        return "Denied", ("denied",)

    def _show_tag_ui(self):
        with self._tags_lock:
            rows = list(self.tags_ordered)

        existing = self.lsv_tags.get_children()
        if len(existing) != len(rows):
            self.lsv_tags.delete(*existing)
            for idx, sitem in enumerate(rows):
                _, row_tags = self._tag_status_and_style(sitem)
                self.lsv_tags.insert("", "end", iid=str(idx), tags=row_tags, values=(
                    idx + 1,
                    util.hex_array_to_string(sitem.Code),
                    sitem.LEN,
                    sitem.counts_to_string(),
                    sitem.Rssi // 10,
                    sitem.Channel,
                ))
        else:
            for idx, sitem in enumerate(rows):
                iid = str(idx)
                _, row_tags = self._tag_status_and_style(sitem)
                self.lsv_tags.set(iid, "count", sitem.counts_to_string())
                self.lsv_tags.set(iid, "rssi", sitem.Rssi // 10)
                self.lsv_tags.set(iid, "channel", sitem.Channel)
                self.lsv_tags.item(iid, tags=row_tags)

        self._refresh_allowed_log_ui()

    def _refresh_allowed_log_ui(self):
        """Appends new entries to the log (append-only, never updates an existing row)."""
        with self._tags_lock:
            entries = list(self.first_seen_allowed.items())
            allowed_codes = self.allowed_codes

        existing_log = self.lsv_allowed_log.get_children()
        for idx in range(len(existing_log), len(entries)):
            code, ts = entries[idx]
            bib = ""
            team = ""
            if allowed_codes is not None and code in allowed_codes:
                bib = allowed_codes[code].get("bib", "")
                team = allowed_codes[code].get("team", "")
            self.lsv_allowed_log.insert("", "end", iid=str(idx), values=(
                idx + 1, bib, util.hex_array_to_string(code), team, ts,
            ))

    def _inventory_thread_main(self):
        reader = self.reader
        if reader is None:
            self._do_stop_inventory()
            return

        with self._tags_lock:
            self.tags.clear()
            self.tags_ordered.clear()
        self._show_tag()

        self.inv_tag_count = 0
        self.inv_start_tick = time.time()

        try:
            while not self.stop_inventory:
                try:
                    item = reader.get_tag_uii(1000)
                except ReaderException as ex:
                    if ex.error_code in (ReaderException.ERROR_CMD_COMM_TIMEOUT,
                                          ReaderException.ERROR_CMD_RESP_FORMAT_ERROR):
                        if reader is not None and self.is_closed:
                            self._do_stop_inventory()
                            return
                        continue
                    raise

                if item is None:  # no tag around / command finished
                    break
                if item.Antenna == 0 or item.Antenna > 4:
                    continue

                with self._tags_lock:
                    sitem = self.tags.get(item.Code)
                    if sitem is not None:
                        sitem.inc_count(item)
                    else:
                        sitem = ShowTagItem(item=item)
                        self.tags[item.Code] = sitem
                        self.tags_ordered.append(sitem)
                    self.inv_tag_count += 1
                    self.inv_time_ms = int((time.time() - self.inv_start_tick) * 1000) + 1

                    # Log of authorized tags: only the very first passage
                    if (self.allowed_codes is not None
                            and item.Code in self.allowed_codes
                            and item.Code not in self.first_seen_allowed):
                        ts = time.strftime("%Y-%m-%d %H:%M:%S")
                        self.first_seen_allowed[item.Code] = ts
                        if self.race_csv_writer is not None:
                            info = self.allowed_codes[item.Code]
                            try:
                                self.race_csv_writer.writerow([
                                    info.get("bib", ""),
                                    util.hex_array_to_string(item.Code),
                                    info.get("team", ""),
                                    ts,
                                ])
                                self.race_csv_file.flush()
                            except Exception as ex:
                                self.write_log(MessageType.Error, "Real-time CSV write failed", ex)

                        if self.gsheet_ready and self.gsheet_exporter is not None:
                            info = self.allowed_codes[item.Code]
                            try:
                                self.gsheet_exporter.push_passage(
                                    round_num=self._get_round_number(),
                                    mode=self.race_mode_var.get(),
                                    bib=info.get("bib", ""),
                                    team=info.get("team", ""),
                                    timestamp=ts,
                                )
                            except Exception as ex:
                                self.write_log(MessageType.Error, "Google Sheet push failed", ex)
                self._show_tag()
            self._show_tag()
            self.ui_queue.put(("inventory_end",))
        except Exception as ex:
            try:
                self.write_log(MessageType.Error, "Inventory label failed: ", ex)
            except Exception:
                pass
            self._do_stop_inventory()

    def on_capture_start_click(self):
        """Autonomous Start for the Capture RFID popup: just runs the raw
        inventory scan, no Start line/Finish line requirement, no teams.csv
        prompt, no CSV export directory, no locking of the race controls."""
        try:
            reader = self.reader
            if reader is None:
                raise RuntimeError("Reader not connected")

            if self.in_inventory:
                # Already running (from either Start button): stop through the
                # normal path so any race-related cleanup still happens safely.
                self.on_inv_stop_click()
                return

            self.devicepara.Workmode = self.cmb_workmode.current()
            reader.set_device_para(self.devicepara)

            self.btn_inventory.configure(state="disabled")
            self.btn_inventory_capture.configure(state="disabled")
            self.in_inventory = True
            self.stop_inventory = False
            self._set_start_button_color(True)

            if self.cmb_workmode.current() == 0:  # Answer mode: must send a command first
                self.write_log(MessageType.Info, "Set parameters successfully:")
                time.sleep(0.1)
                reader.inventory(0, 0)

            self.inv_thread = threading.Thread(target=self._inventory_thread_main, daemon=True)
            self.inv_thread.start()
        except Exception as ex:
            self.in_inventory = False
            self.stop_inventory = True
            self.btn_inventory.configure(state="normal")
            self.btn_inventory_capture.configure(state="normal")
            self._set_start_button_color(False)
            self.write_log(MessageType.Error, "Capture failed: ", ex)
            messagebox.showinfo("Tips", f"Capture failed: {ex}")

    def on_inventory_click(self):
        try:
            reader = self.reader
            if reader is None:
                raise RuntimeError("Reader not connected")

            if self.cmb_workmode.current() == 0:  # Answer mode: must send a command first
                if self.in_inventory:
                    self.stop_inventory = True
                    self._close_inventory_thread()
                    reader.inventory_stop(STOP_INVENTORY_TIMEOUT_MS)
                    self.btn_inventory.configure(state="normal")
                    self.btn_inventory_capture.configure(state="normal")
                    self._set_start_button_color(False)
                    self._lock_race_controls(False)
                    self._stop_race_csv()
                    return

                if self.race_mode_var.get() not in ("start_line", "finish_line"):
                    messagebox.showinfo(self.title(),
                                         "Select 'Start line' or 'Finish line' before starting")
                    return
                self._prompt_teams_file()  # may raise (cancelled, invalid file)
                self._start_race_csv()  # may raise (cancelled, write error)
                self._ensure_gsheet_round_tab()
                self._lock_race_controls(True)
                self.on_clear_allowed_log()

                self.devicepara.Workmode = self.cmb_workmode.current()
                reader.set_device_para(self.devicepara)
                self.write_log(MessageType.Info, "Set parameters successfully:")

                self.btn_inventory.configure(state="disabled")
                self.btn_inventory_capture.configure(state="disabled")
                self.write_log(MessageType.Info, "Start  inventory")
                self.in_inventory = True
                self.stop_inventory = False
                self._set_start_button_color(True)
                time.sleep(0.1)

                reader.inventory(0, 0)
                self.inv_thread = threading.Thread(target=self._inventory_thread_main, daemon=True)
                self.inv_thread.start()
            else:  # Active mode
                if self.in_inventory:
                    self.stop_inventory = True
                    self._close_inventory_thread()
                    self.btn_inventory.configure(state="normal")
                    self.btn_inventory_capture.configure(state="normal")
                    self._set_start_button_color(False)
                    self._lock_race_controls(False)
                    self._stop_race_csv()
                    return

                if self.race_mode_var.get() not in ("start_line", "finish_line"):
                    messagebox.showinfo(self.title(),
                                         "Select 'Start line' or 'Finish line' before starting")
                    return
                self._prompt_teams_file()  # may raise (cancelled, invalid file)
                self._start_race_csv()
                self._ensure_gsheet_round_tab()
                self._lock_race_controls(True)
                self.on_clear_allowed_log()

                self.devicepara.Workmode = self.cmb_workmode.current()
                reader.set_device_para(self.devicepara)

                self.btn_inventory.configure(state="disabled")
                self.btn_inventory_capture.configure(state="disabled")
                self.in_inventory = True
                self.stop_inventory = False
                self._set_start_button_color(True)

                self.inv_thread = threading.Thread(target=self._inventory_thread_main, daemon=True)
                self.inv_thread.start()
        except Exception as ex:
            self.in_inventory = False
            self.stop_inventory = True
            self.btn_inventory.configure(state="normal")
            self.btn_inventory_capture.configure(state="normal")
            self._set_start_button_color(False)
            self._lock_race_controls(False)
            self._stop_race_csv()
            self.write_log(MessageType.Error, "Inventory label failed: ", ex)
            messagebox.showinfo("Tips", f"Inventory label failed: {ex}")

    def on_inv_stop_click(self):
        try:
            reader = self.reader
            if reader is None:
                raise RuntimeError("Reader not connected")
            if self.in_inventory:
                self.stop_inventory = True
                self._close_inventory_thread()
                if self.devicepara.Workmode == 0:
                    reader.inventory_stop(STOP_INVENTORY_TIMEOUT_MS)
                self.btn_inventory.configure(state="normal")
                self.btn_inventory_capture.configure(state="normal")
                self._set_start_button_color(False)
                self._lock_race_controls(False)
                self._stop_race_csv()
                return
            self.write_log(MessageType.Error, "Inventory Stoped")
        except Exception as ex:
            messagebox.showinfo(self.title(), str(ex))

    def on_clear(self):
        self.lsv_tags.delete(*self.lsv_tags.get_children())
        with self._tags_lock:
            self.tags.clear()
            self.tags_ordered.clear()

    def on_clear_allowed_log(self):
        self.lsv_allowed_log.delete(*self.lsv_allowed_log.get_children())
        with self._tags_lock:
            self.first_seen_allowed.clear()

    # ------------------------------------------------------------------------------------
    # Export / import of the authorized tag list
    # ------------------------------------------------------------------------------------
    # ------------------------------------------------------------------------------------
    # Results: cross-reference a start_line file and a finish_line file by bib
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _extract_round_from_filename(path: str) -> int | None:
        m = re.search(r"round_(\d+)_", os.path.basename(path))
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_race_csv(path: str) -> dict:
        """Parses a start_line/finish_line real-time export CSV
        (header: bib;rfid;team;passage_time). Returns {bib: {"team", "time"}},
        keeping the first row seen for a given bib."""
        result: dict[str, dict] = {}
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(f, dialect))

        start_idx = 0
        if rows and rows[0] and rows[0][0].strip().lower() == "bib":
            start_idx = 1

        for row in rows[start_idx:]:
            if not row or len(row) < 4:
                continue
            bib = row[0].strip()
            team = row[2].strip()
            ts = row[3].strip()
            if not bib or bib in result:
                continue
            result[bib] = {"team": team, "time": ts}
        return result

    def _compute_results(self, start_data: dict, finish_data: dict) -> list:
        bibs = sorted(set(start_data) | set(finish_data))
        results = []
        for bib in bibs:
            s = start_data.get(bib)
            f = finish_data.get(bib)
            team = (s or {}).get("team") or (f or {}).get("team") or ""
            start_time = (s or {}).get("time") or ""
            finish_time = (f or {}).get("time") or ""
            duration_text = ""
            duration_seconds = None
            if start_time and finish_time:
                try:
                    t1 = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(finish_time, "%Y-%m-%d %H:%M:%S")
                    delta = t2 - t1
                    duration_seconds = delta.total_seconds()
                    duration_text = str(delta)
                except ValueError:
                    pass
            results.append({
                "bib": bib, "team": team, "start": start_time, "finish": finish_time,
                "duration": duration_text, "duration_seconds": duration_seconds,
            })
        # Fastest (valid) durations first; incomplete rows (missing start or finish) at the end.
        results.sort(key=lambda r: (r["duration_seconds"] is None,
                                     r["duration_seconds"] if r["duration_seconds"] is not None else 0))
        return results

    def _refresh_results_ui(self):
        self.lsv_results.delete(*self.lsv_results.get_children())
        for idx, r in enumerate(self._results_rows):
            self.lsv_results.insert("", "end", iid=str(idx), values=(
                r["bib"], r["team"], r["start"], r["finish"], r["duration"],
            ))

    def _maybe_compute_results(self):
        if not (self._results_start_path and self._results_finish_path):
            return
        try:
            start_round = self._extract_round_from_filename(self._results_start_path)
            finish_round = self._extract_round_from_filename(self._results_finish_path)
            if start_round is not None and finish_round is not None and start_round != finish_round:
                messagebox.showwarning(
                    self.title(),
                    f"Warning: the two files are not the same round "
                    f"(start = round {start_round}, finish = round {finish_round})")
            start_data = self._parse_race_csv(self._results_start_path)
            finish_data = self._parse_race_csv(self._results_finish_path)
            self._results_rows = self._compute_results(start_data, finish_data)
            self._refresh_results_ui()
            self.write_log(MessageType.Info,
                            f"Results computed: {len(self._results_rows)} bib(s)")
        except Exception as ex:
            messagebox.showinfo(self.title(), f"Failed to compute results: {ex}")

    def on_select_start_file(self):
        path = filedialog.askopenfilename(
            title="Select the start_line CSV file",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self._results_start_path = path
        self.lbl_start_file.configure(text=os.path.basename(path))
        self._maybe_compute_results()

    def on_select_finish_file(self):
        path = filedialog.askopenfilename(
            title="Select the finish_line CSV file",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self._results_finish_path = path
        self.lbl_finish_file.configure(text=os.path.basename(path))
        self._maybe_compute_results()

    def on_export_results(self):
        if not self._results_rows:
            messagebox.showinfo(self.title(), "No results to export yet")
            return
        path = filedialog.asksaveasfilename(
            title="Export results",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["bib", "team", "start", "finish", "duration"])
                for r in self._results_rows:
                    writer.writerow([r["bib"], r["team"], r["start"], r["finish"], r["duration"]])
            self.write_log(MessageType.Info, f"Results exported to {path}")
        except Exception as ex:
            messagebox.showinfo(self.title(), f"Export failed: {ex}")

    def on_export_tags(self):
        with self._tags_lock:
            rows = list(self.tags_ordered)
        if not rows:
            messagebox.showinfo(self.title(), "No tag detected to export yet")
            return
        path = filedialog.asksaveasfilename(
            title="Export the list of detected tags",
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# List of detected RFID tags — one per line (hex code)\n")
                for sitem in rows:
                    f.write(util.hex_array_to_string(sitem.Code) + "\n")
            self.write_log(MessageType.Info, f"List exported ({len(rows)} tag(s)) to {path}")
        except Exception as ex:
            messagebox.showinfo(self.title(), f"Export failed: {ex}")

    @staticmethod
    def _parse_teams_file(path: str) -> dict:
        """Parses a teams.csv file: columns bib, rfid, team (optional).

        Accepts ; or , as delimiter, with or without a header row.
        Returns a dict {code_bytes: {"bib": str, "team": str}}.
        """
        teams: dict[bytes, dict] = {}
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(f, dialect))

        start_idx = 0
        if rows and len(rows[0]) >= 2:
            try:
                util.hex_array_from_string(rows[0][1])
            except ValueError:
                start_idx = 1  # first line = header (not a valid hex code), skip it

        for row in rows[start_idx:]:
            if not row or all(not (c or "").strip() for c in row):
                continue
            dossard = row[0].strip() if len(row) > 0 else ""
            rfid_raw = row[1].strip() if len(row) > 1 else ""
            equipe = row[2].strip() if len(row) > 2 else ""
            if not rfid_raw:
                continue
            try:
                code = util.hex_array_from_string(rfid_raw)
            except ValueError:
                continue  # unreadable line, skipped
            teams[code] = {"bib": dossard, "team": equipe}
        return teams

    def _prompt_teams_file(self):
        """Asks the user to pick a teams.csv file and loads it as the authorization
        filter for the race about to start. Raises if cancelled or invalid."""
        path = filedialog.askopenfilename(
            title="Select teams.csv (bib, rfid, team) for this race",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            raise RuntimeError("Start cancelled: no teams.csv file selected")
        teams = self._parse_teams_file(path)
        if not teams:
            raise RuntimeError("No valid tag found in the selected teams.csv file")
        self.allowed_codes = teams
        self.write_log(MessageType.Info, f"Teams loaded: {len(teams)} tag(s) from {path}")
        self._show_tag()

    # ------------------------------------------------------------------------------------
    def _on_close(self):
        self.is_closed = True
        self.stop_inventory = True
        self._stop_race_csv()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
