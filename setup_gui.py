"""
setup_gui.py — Live Translation Setup Wizard (GUI)
===================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Standalone Tkinter configuration wizard for setting up church identity,
local network hostname, Google Gemini API validation, and Cloudflare public access.

Builds into `SKC_setup.exe` with PyInstaller.
"""
import os
import shutil
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Import configuration helpers from app.config
from app.config import (
    church_cfg,
    gemini_api_key,
    gemini_model,
    get_app_root,
    mask_api_key,
    network_cfg,
    save_church_identity,
    save_public_url,
    update_gemini_api_key,
)
from app.cloudflared_service import CloudflaredService

# Colors matching church branding
COLOR_NAVY = "#1a2a42"
COLOR_NAVY_LIGHT = "#243a5e"
COLOR_GOLD = "#b89445"
COLOR_GOLD_HOVER = "#9e7d36"
COLOR_BG = "#f4f6f9"
COLOR_CARD_BG = "#ffffff"
COLOR_BORDER = "#d9e2ec"
COLOR_TEXT_MAIN = "#102a43"
COLOR_TEXT_MUTED = "#627d98"
COLOR_SUCCESS = "#2b8a3e"
COLOR_ERROR = "#c92a2a"


class SetupApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Live Translation Setup")
        self.root.geometry("1060x670")
        self.root.minsize(980, 620)
        self.root.configure(bg=COLOR_BG)

        self.app_root = get_app_root()
        self.selected_logo_source: Path | None = None
        self.testing = False
        self.validation_passed = False

        # Load existing configuration
        self.current_church = church_cfg()
        self.current_network = network_cfg()
        self.configured_model = gemini_model()
        self.cloudflared_service = CloudflaredService()

        # Check existing key in environment / .env
        self.existing_key = ""
        try:
            self.existing_key = gemini_api_key()
        except RuntimeError:
            self.existing_key = ""

        self.active_key_to_save = self.existing_key

        self._setup_styles()
        self._build_ui()
        self._update_readiness_pills()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD_BG, relief="solid", borderwidth=1)
        style.configure(
            "Header.TLabel",
            background=COLOR_NAVY,
            foreground="#ffffff",
            font=("Segoe UI", 15, "bold"),
            padding=(14, 10, 14, 2),
        )
        style.configure(
            "SubHeader.TLabel",
            background=COLOR_NAVY,
            foreground="#d9e2ec",
            font=("Segoe UI", 9),
            padding=(14, 0, 14, 10),
        )
        style.configure(
            "SectionTitle.TLabel",
            background=COLOR_CARD_BG,
            foreground=COLOR_NAVY,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "FieldLabel.TLabel",
            background=COLOR_CARD_BG,
            foreground=COLOR_TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=COLOR_CARD_BG,
            foreground=COLOR_TEXT_MUTED,
            font=("Segoe UI", 8),
        )
        style.configure(
            "Action.TButton",
            font=("Segoe UI", 9, "bold"),
            padding=4,
        )
        style.configure(
            "Primary.TButton",
            background=COLOR_NAVY,
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            padding=(12, 6),
        )
        style.map(
            "Primary.TButton",
            background=[("active", COLOR_NAVY_LIGHT), ("disabled", "#bcccdc")],
        )

    def _build_ui(self):
        # ── 1. Top Header Banner ──────────────────────────────────────────────
        f_hdr = tk.Frame(self.root, bg=COLOR_NAVY)
        f_hdr.pack(fill="x")
        ttk.Label(f_hdr, text="Live Translation Setup", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            f_hdr,
            text="Configure church identity, Google Gemini API credentials, local Wi-Fi address, and Cloudflare public access.",
            style="SubHeader.TLabel",
        ).pack(anchor="w")

        # ── 2. Fixed Bottom Action & Readiness Bar ────────────────────────────
        bottom_frame = tk.Frame(self.root, bg="#eaedf1", bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        bottom_frame.pack(fill="x", side="bottom")

        p_bottom = tk.Frame(bottom_frame, bg="#eaedf1", padx=16, pady=10)
        p_bottom.pack(fill="x")

        # Setup Readiness (Left side of footer)
        f_readiness = tk.Frame(p_bottom, bg="#eaedf1")
        f_readiness.pack(side="left", fill="y")
        tk.Label(f_readiness, text="SETUP READINESS:", font=("Segoe UI", 9, "bold"), bg="#eaedf1", fg=COLOR_NAVY).pack(side="left", padx=(0, 8))

        self.lbl_pill_local = tk.Label(f_readiness, text="🟢 Local Network", font=("Segoe UI", 8, "bold"), bg="#dcfce7", fg=COLOR_SUCCESS, padx=6, pady=2, bd=1, relief="solid")
        self.lbl_pill_local.pack(side="left", padx=4)

        self.lbl_pill_gemini = tk.Label(f_readiness, text="⚪ Gemini API", font=("Segoe UI", 8, "bold"), bg="#f1f5f9", fg=COLOR_TEXT_MUTED, padx=6, pady=2, bd=1, relief="solid")
        self.lbl_pill_gemini.pack(side="left", padx=4)

        self.lbl_pill_cloudflare = tk.Label(f_readiness, text="⚪ Public HTTPS", font=("Segoe UI", 8, "bold"), bg="#f1f5f9", fg=COLOR_TEXT_MUTED, padx=6, pady=2, bd=1, relief="solid")
        self.lbl_pill_cloudflare.pack(side="left", padx=4)

        # Action Buttons (Right side of footer)
        f_actions = tk.Frame(p_bottom, bg="#eaedf1")
        f_actions.pack(side="right")

        btn_cancel = ttk.Button(f_actions, text="Cancel / Exit", command=self.root.destroy)
        btn_cancel.pack(side="left", padx=6)

        self.btn_save = ttk.Button(
            f_actions,
            text="💾 Save & Finish",
            style="Primary.TButton",
            command=self._save_and_finish,
        )
        self.btn_save.pack(side="left", padx=4)

        # ── 3. Main 2-Column Content Area ─────────────────────────────────────
        content = tk.Frame(self.root, bg=COLOR_BG, padx=14, pady=10)
        content.pack(fill="both", expand=True)

        content.grid_columnconfigure(0, weight=1, uniform="upper_col")
        content.grid_columnconfigure(1, weight=1, uniform="upper_col")
        content.grid_rowconfigure(0, weight=3)
        content.grid_rowconfigure(1, weight=2)

        # ── CARD 1: Church & Local Network (Top Left) ─────────────────────────
        card_church = tk.Frame(content, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_church.grid(row=0, column=0, padx=(0, 6), pady=(0, 8), sticky="nsew")

        p_church = tk.Frame(card_church, bg=COLOR_CARD_BG, padx=14, pady=12)
        p_church.pack(fill="both", expand=True)

        ttk.Label(p_church, text="1. CHURCH & LOCAL NETWORK", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 8))

        # Church Name
        f_name = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_name.pack(fill="x", pady=3)
        ttk.Label(f_name, text="Church Name:", width=13, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_church_name = ttk.Entry(f_name, font=("Segoe UI", 9))
        self.entry_church_name.insert(0, self.current_church.get("name", "Starkville Korean Church"))
        self.entry_church_name.pack(side="left", fill="x", expand=True)

        # Short Name
        f_sname = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_sname.pack(fill="x", pady=3)
        ttk.Label(f_sname, text="Short Name:", width=13, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_short_name = ttk.Entry(f_sname, font=("Segoe UI", 9), width=12)
        self.entry_short_name.insert(0, self.current_church.get("short_name", "SKC"))
        self.entry_short_name.pack(side="left")
        ttk.Label(f_sname, text="(compact mobile header)", style="Muted.TLabel").pack(side="left", padx=6)

        # Local Hostname & Port
        f_host = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_host.pack(fill="x", pady=3)
        ttk.Label(f_host, text="Local URL:", width=13, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(f_host, text="http://", style="Muted.TLabel").pack(side="left")
        self.entry_hostname = ttk.Entry(f_host, font=("Segoe UI", 9), width=12)
        self.entry_hostname.insert(0, self.current_network.get("hostname", "skc"))
        self.entry_hostname.pack(side="left", padx=2)
        port_num = self.current_network.get("port", 8080)
        port_suffix = f".local:{port_num}" if port_num != 80 else ".local"
        ttk.Label(f_host, text=port_suffix, style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(f_host, text=f"(e.g. skc → http://skc{port_suffix})", style="Muted.TLabel").pack(side="left", padx=6)

        # Church Logo
        f_logo = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_logo.pack(fill="x", pady=3)
        ttk.Label(f_logo, text="Church Logo:", width=13, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_logo_path = ttk.Label(
            f_logo,
            text=self.current_church.get("logo", "branding/church-logo.png"),
            style="Muted.TLabel",
        )
        self.lbl_logo_path.pack(side="left", fill="x", expand=True, padx=4)
        btn_logo = ttk.Button(f_logo, text="Choose Logo...", command=self._choose_logo)
        btn_logo.pack(side="right")

        # ── CARD 2: Google Gemini API (Top Right) ─────────────────────────────
        card_gemini = tk.Frame(content, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_gemini.grid(row=0, column=1, padx=(6, 0), pady=(0, 8), sticky="nsew")

        p_gemini = tk.Frame(card_gemini, bg=COLOR_CARD_BG, padx=14, pady=12)
        p_gemini.pack(fill="both", expand=True)

        ttk.Label(p_gemini, text="2. GOOGLE GEMINI API", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 6))

        # External Links Row (Compact)
        f_links = tk.Frame(p_gemini, bg=COLOR_CARD_BG)
        f_links.pack(fill="x", pady=(0, 6))
        btn_studio = ttk.Button(
            f_links,
            text="🌐 Open Google AI Studio",
            command=lambda: webbrowser.open("https://aistudio.google.com/app/apikey"),
        )
        btn_studio.pack(side="left", padx=(0, 6))
        btn_billing = ttk.Button(
            f_links,
            text="💳 Billing Setup Guide",
            command=lambda: webbrowser.open("https://ai.google.dev/gemini-api/docs/billing"),
        )
        btn_billing.pack(side="left")

        # API Key container (Single Row: LABEL | FIELD | ACTION)
        self.key_container = tk.Frame(p_gemini, bg=COLOR_CARD_BG)
        self.key_container.pack(fill="x", pady=2)

        if self.existing_key:
            self._render_existing_key_view()
        else:
            self._render_new_key_view()

        # Compact Validation Status Rows
        self.val_box = tk.Frame(p_gemini, bg="#f8fafc", bd=1, relief="solid", highlightbackground=COLOR_BORDER, padx=8, pady=6)
        self.val_box.pack(fill="x", pady=(6, 4))

        f_val_k = tk.Frame(self.val_box, bg="#f8fafc")
        f_val_k.pack(fill="x")
        ttk.Label(f_val_k, text="API Key Status:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_val_key = tk.Label(
            f_val_k,
            text="● Configured" if self.existing_key else "⚪ Key required",
            font=("Segoe UI", 9, "bold"),
            bg="#f8fafc",
            fg=COLOR_SUCCESS if self.existing_key else COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_val_key.pack(side="left")

        f_val_m = tk.Frame(self.val_box, bg="#f8fafc")
        f_val_m.pack(fill="x", pady=(2, 0))
        ttk.Label(f_val_m, text="Model Target:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_val_model = tk.Label(
            f_val_m,
            text=f"● {self.configured_model}",
            font=("Segoe UI", 9),
            bg="#f8fafc",
            fg=COLOR_NAVY,
            anchor="w",
        )
        self.lbl_val_model.pack(side="left")

        # Test Connection button
        self.btn_test = ttk.Button(
            p_gemini,
            text="⚡ Test Connection & Model Availability",
            command=self._start_connection_test,
        )
        self.btn_test.pack(fill="x", pady=(4, 0))

        # ── CARD 3: Public HTTPS / Cloudflare Named Tunnel (Bottom Full-Width) 
        card_tunnel = tk.Frame(content, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_tunnel.grid(row=1, column=0, columnspan=2, pady=(0, 0), sticky="nsew")

        p_tunnel = tk.Frame(card_tunnel, bg=COLOR_CARD_BG, padx=14, pady=10)
        p_tunnel.pack(fill="both", expand=True)

        ttk.Label(p_tunnel, text="3. PUBLIC HTTPS / CLOUDFLARE NAMED TUNNEL", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 6))

        # Row 1: Public URL
        f_puburl = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_puburl.pack(fill="x", pady=2)
        ttk.Label(f_puburl, text="Public URL:", width=16, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_public_url = ttk.Entry(f_puburl, font=("Segoe UI", 9))
        self.entry_public_url.insert(0, self.current_network.get("public_url", "https://live.starkvillekoreanchurch.org"))
        self.entry_public_url.pack(side="left", fill="x", expand=True)

        # Row 2: Service Status + Refresh
        f_svc = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_svc.pack(fill="x", pady=3)
        ttk.Label(f_svc, text="Windows Service:", width=16, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_svc_status = tk.Label(f_svc, text="Checking...", font=("Segoe UI", 9, "bold"), bg=COLOR_CARD_BG, fg=COLOR_TEXT_MUTED)
        self.lbl_svc_status.pack(side="left", padx=4)
        btn_refresh_svc = ttk.Button(f_svc, text="🔄 Refresh", width=10, command=self._refresh_service_status)
        btn_refresh_svc.pack(side="right")

        # Row 3: Tunnel Token + Start / Install
        f_tok = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_tok.pack(fill="x", pady=3)
        ttk.Label(f_tok, text="Tunnel Token:", width=16, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_tunnel_token = ttk.Entry(f_tok, font=("Consolas", 9), show="•")
        self.entry_tunnel_token.pack(side="left", fill="x", expand=True, padx=(0, 8))
        btn_install_svc = ttk.Button(f_tok, text="⚙️ Start / Install Service", command=self._start_or_install_service)
        btn_install_svc.pack(side="right")

        ttk.Label(
            p_tunnel,
            text="• Token is only needed once to install or repair the Windows service. Runtime translation monitors the service automatically.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self._refresh_service_status()

    def _render_existing_key_view(self):
        for w in self.key_container.winfo_children():
            w.destroy()

        f_row = tk.Frame(self.key_container, bg=COLOR_CARD_BG)
        f_row.pack(fill="x", pady=2)

        ttk.Label(f_row, text="API Key:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        masked = mask_api_key(self.existing_key)
        lbl_masked = tk.Label(
            f_row,
            text=masked,
            font=("Consolas", 9, "bold"),
            bg="#edf2f7",
            fg=COLOR_TEXT_MAIN,
            padx=8,
            pady=2,
            relief="solid",
            bd=1,
        )
        lbl_masked.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_replace = ttk.Button(f_row, text="Replace Key...", command=self._render_new_key_view)
        btn_replace.pack(side="right")

    def _render_new_key_view(self):
        for w in self.key_container.winfo_children():
            w.destroy()

        f_entry = tk.Frame(self.key_container, bg=COLOR_CARD_BG)
        f_entry.pack(fill="x", pady=2)

        ttk.Label(f_entry, text="API Key:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_key = ttk.Entry(f_entry, font=("Consolas", 9), show="•")
        self.entry_key.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.is_key_visible = False

        def _toggle_visibility():
            self.is_key_visible = not self.is_key_visible
            self.entry_key.configure(show="" if self.is_key_visible else "•")
            btn_show.configure(text="Hide" if self.is_key_visible else "Show")

        btn_show = ttk.Button(f_entry, text="Show", width=5, command=_toggle_visibility)
        btn_show.pack(side="left", padx=2)

        def _paste_clipboard():
            try:
                clip = self.root.clipboard_get().strip()
                if clip:
                    self.entry_key.delete(0, tk.END)
                    self.entry_key.insert(0, clip)
            except Exception:
                pass

        btn_paste = ttk.Button(f_entry, text="Paste", width=5, command=_paste_clipboard)
        btn_paste.pack(side="left", padx=2)

        if self.existing_key:
            btn_cancel_replace = ttk.Button(
                f_entry,
                text="Cancel",
                width=6,
                command=self._render_existing_key_view,
            )
            btn_cancel_replace.pack(side="left", padx=2)

    def _get_active_key(self) -> str:
        if hasattr(self, "entry_key") and self.entry_key.winfo_exists():
            return self.entry_key.get().strip()
        return self.existing_key.strip()

    def _choose_logo(self):
        file_types = [
            ("Image Files", "*.png *.jpg *.jpeg *.webp"),
            ("PNG Images", "*.png"),
            ("JPEG Images", "*.jpg *.jpeg"),
            ("WebP Images", "*.webp"),
            ("All Files", "*.*"),
        ]
        chosen = filedialog.askopenfilename(title="Select Church Logo", filetypes=file_types)
        if chosen:
            self.selected_logo_source = Path(chosen)
            self.lbl_logo_path.configure(text=f"Selected: {self.selected_logo_source.name}")

    def _start_connection_test(self):
        if self.testing:
            return

        key = self._get_active_key()
        if not key:
            messagebox.showwarning("API Key Required", "Please enter or paste your Gemini API key before testing.")
            return

        self.testing = True
        self.validation_passed = False
        self.btn_test.configure(state="disabled")
        self.lbl_val_key.configure(text="● Testing authentication...", fg=COLOR_TEXT_MUTED)
        self.lbl_val_model.configure(text=f"● Querying {self.configured_model}...", fg=COLOR_TEXT_MUTED)

        thread = threading.Thread(target=self._run_connection_test, args=(key,), daemon=True)
        thread.start()

    def _run_connection_test(self, key: str):
        key_valid = False
        model_valid = False
        error_msg = ""
        model_note = ""

        try:
            from google import genai
            client = genai.Client(api_key=key)
            models_iter = client.models.list()
            available_models = [
                m.name.removeprefix("models/")
                for m in models_iter
                if hasattr(m, "name")
            ]
            key_valid = True

            # Separate model check
            cfg_model = self.configured_model.strip()
            if cfg_model in available_models:
                model_valid = True
                model_note = f"● {cfg_model} available"
            else:
                live_models = [m for m in available_models if "live" in m or "translate" in m]
                if live_models:
                    model_note = f"● Found alternative: {live_models[0]}"
                    model_valid = True
                else:
                    model_note = f"✗ Model '{cfg_model}' not found in project"
                    model_valid = False

        except Exception as e:
            err_str = str(e)
            if key in err_str:
                err_str = err_str.replace(key, "••••••••")
            error_msg = err_str

        self.root.after(0, self._on_test_complete, key_valid, model_valid, error_msg, model_note)

    def _on_test_complete(self, key_valid: bool, model_valid: bool, error_msg: str, model_note: str):
        self.testing = False
        self.btn_test.configure(state="normal")

        if key_valid:
            self.lbl_val_key.configure(text="● Authenticated", fg=COLOR_SUCCESS)
            if model_valid:
                self.lbl_val_model.configure(text=model_note, fg=COLOR_SUCCESS)
                self.validation_passed = True
            else:
                self.lbl_val_model.configure(text=model_note, fg=COLOR_ERROR)
        else:
            self.lbl_val_key.configure(text="✗ Auth Failed", fg=COLOR_ERROR)
            self.lbl_val_model.configure(text=f"✗ {error_msg[:30]}", fg=COLOR_ERROR)

        self._update_readiness_pills()

    def _refresh_service_status(self):
        state = self.cloudflared_service._query()
        if state == "running":
            self.lbl_svc_status.configure(text="🟢 RUNNING (Active)", fg=COLOR_SUCCESS)
        elif state == "stopped":
            self.lbl_svc_status.configure(text="🟡 STOPPED (Installed, not running)", fg=COLOR_GOLD)
        elif state == "missing":
            self.lbl_svc_status.configure(text="⚪ NOT INSTALLED", fg=COLOR_TEXT_MUTED)
        else:
            self.lbl_svc_status.configure(text=f"• {state.upper()}", fg=COLOR_TEXT_MUTED)

        self._update_readiness_pills()

    def _update_readiness_pills(self):
        # 1. Local Network readiness
        if hasattr(self, "entry_hostname") and self.entry_hostname.get().strip():
            self.lbl_pill_local.configure(text="🟢 Local Network", bg="#dcfce7", fg=COLOR_SUCCESS)
        else:
            self.lbl_pill_local.configure(text="⚪ Local Network", bg="#f1f5f9", fg=COLOR_TEXT_MUTED)

        # 2. Gemini readiness
        active_key = self._get_active_key()
        if self.validation_passed:
            self.lbl_pill_gemini.configure(text="🟢 Gemini API", bg="#dcfce7", fg=COLOR_SUCCESS)
        elif active_key:
            self.lbl_pill_gemini.configure(text="🟡 Gemini Configured", bg="#fef9c3", fg=COLOR_GOLD)
        else:
            self.lbl_pill_gemini.configure(text="⚪ Gemini API", bg="#f1f5f9", fg=COLOR_TEXT_MUTED)

        # 3. Cloudflare readiness
        state = self.cloudflared_service._query()
        if state == "running":
            self.lbl_pill_cloudflare.configure(text="🟢 Public HTTPS", bg="#dcfce7", fg=COLOR_SUCCESS)
        elif state == "stopped":
            self.lbl_pill_cloudflare.configure(text="🟡 Service Stopped", bg="#fef9c3", fg=COLOR_GOLD)
        else:
            self.lbl_pill_cloudflare.configure(text="⚪ Public HTTPS", bg="#f1f5f9", fg=COLOR_TEXT_MUTED)

    def _start_or_install_service(self):
        state = self.cloudflared_service._query()
        token = self.entry_tunnel_token.get().strip() if hasattr(self, "entry_tunnel_token") else ""

        if state == "running":
            messagebox.showinfo("Cloudflared Service", "Cloudflared service is already RUNNING and active.")
            return

        if state == "stopped":
            success = self.cloudflared_service._run("start")
            self._refresh_service_status()
            if success:
                messagebox.showinfo("Cloudflared Service", "Cloudflared service started successfully!")
            else:
                messagebox.showwarning(
                    "Service Start Elevation Required",
                    "Could not start the service directly due to Windows permission constraints.\n\nPlease start it via Windows Services (services.msc) or run 'net start cloudflared' in an Administrator Command Prompt.",
                )
            return

        if state == "missing":
            if not token:
                messagebox.showwarning(
                    "Tunnel Token Required",
                    "To install the Cloudflared Windows service, please paste your Cloudflare Tunnel Token into the field above.\n\nPossession of that token permits the connector to run the tunnel.",
                )
                return

            try:
                import subprocess
                res = subprocess.run(["cloudflared.exe", "service", "install", token], capture_output=True, text=True, timeout=15)
                self._refresh_service_status()
                out_lower = (res.stdout + " " + res.stderr).lower()
                if res.returncode == 0 or "installed" in out_lower:
                    if hasattr(self, "entry_tunnel_token"):
                        self.entry_tunnel_token.delete(0, tk.END)
                    messagebox.showinfo("Installation Complete", "Cloudflared Windows service installed successfully!\n\nThe token has been provisioned into the Windows service.")
                elif "access is denied" in out_lower or "permission" in out_lower:
                    messagebox.showerror(
                        "Administrator Elevation Required",
                        "Installing a Windows service requires Administrator privileges.\n\nPlease right-click SKC_setup.exe and select 'Run as administrator', or execute:\n\ncloudflared.exe service install <TOKEN>\n\nin an Administrator Command Prompt.",
                    )
                else:
                    messagebox.showinfo(
                        "Installation Result",
                        f"Command output:\n{res.stdout or res.stderr or 'Check services.msc'}\n\nTip: Installing Windows services requires Administrator privileges.",
                    )
            except FileNotFoundError:
                messagebox.showerror("cloudflared.exe Not Found", "cloudflared.exe was not found on your system PATH.\n\nPlease install cloudflared or place cloudflared.exe in the application folder.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to run cloudflared.exe:\n{e}\n\nMake sure you have Administrator privileges.")

    def _save_and_finish(self):
        church_name = self.entry_church_name.get().strip() or "Starkville Korean Church"
        short_name = self.entry_short_name.get().strip() or "SKC"
        hostname = self.entry_hostname.get().strip() or "skc"
        active_key = self._get_active_key()

        if not active_key:
            if not messagebox.askyesno(
                "No API Key",
                "No Gemini API key has been configured.\n\nThe live translation service will not function without an API key.\n\nSave settings anyway?",
            ):
                return

        # 1. Handle logo copy
        logo_rel_path = self.current_church.get("logo", "branding/church-logo.png")
        if self.selected_logo_source and self.selected_logo_source.exists():
            branding_dir = self.app_root / "branding"
            branding_dir.mkdir(parents=True, exist_ok=True)
            dest_ext = self.selected_logo_source.suffix.lower() or ".png"
            dest_logo = branding_dir / f"church-logo{dest_ext}"

            try:
                shutil.copy2(self.selected_logo_source, dest_logo)
                logo_rel_path = f"branding/church-logo{dest_ext}"
            except Exception as e:
                messagebox.showerror("Logo Error", f"Failed to copy logo file: {e}")
                return

        # 2. Save church identity and public URL to config.yaml atomically
        try:
            save_church_identity(
                name=church_name,
                short_name=short_name,
                hostname=hostname,
                logo_rel_path=logo_rel_path,
            )
            public_url = self.entry_public_url.get().strip() if hasattr(self, "entry_public_url") else ""
            if public_url:
                save_public_url(public_url=public_url, enable_tunnel=True)
        except Exception as e:
            messagebox.showerror("Configuration Error", f"Failed to save config.yaml: {e}")
            return

        # 3. Save API key to .env atomically if provided
        if active_key:
            try:
                update_gemini_api_key(active_key)
            except Exception as e:
                messagebox.showerror(".env Error", f"Failed to save .env file: {e}")
                return

        # 4. Confirmation and optional launch
        port_num = self.current_network.get("port", 8080)
        port_suffix = f":{port_num}" if port_num != 80 else ""
        resp = messagebox.askyesno(
            "Setup Complete",
            "Configuration saved successfully!\n\n"
            f"• Church: {church_name} ({short_name})\n"
            f"• Local URL: http://{hostname}.local{port_suffix}\n"
            f"• API Key: {mask_api_key(active_key)}\n\n"
            "Would you like to launch Live Translation now?",
        )

        if resp:
            self._launch_main_app()

        self.root.destroy()

    def _launch_main_app(self):
        import subprocess

        # If frozen executable, launch SKC_translation.exe next to setup
        if getattr(sys, "frozen", False):
            exe_target = self.app_root / "SKC_translation.exe"
            if exe_target.exists():
                subprocess.Popen([str(exe_target)], cwd=str(self.app_root))
                return

        # Development fallback
        main_py = self.app_root / "main.py"
        if main_py.exists():
            subprocess.Popen([sys.executable, str(main_py)], cwd=str(self.app_root))


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = SetupApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
