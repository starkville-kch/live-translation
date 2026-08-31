"""
setup_gui.py — Live Translation Setup Wizard (GUI)
===================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Standalone Tkinter configuration wizard for setting up church identity,
network hostname, and Google Gemini API key validation.

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
        self.root.geometry("620x760")
        self.root.minsize(580, 680)
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

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD_BG, relief="solid", borderwidth=1)
        style.configure(
            "Header.TLabel",
            background=COLOR_NAVY,
            foreground="#ffffff",
            font=("Segoe UI", 16, "bold"),
            padding=12,
        )
        style.configure(
            "SubHeader.TLabel",
            background=COLOR_NAVY,
            foreground="#d9e2ec",
            font=("Segoe UI", 9),
            padding=(12, 0, 12, 12),
        )
        style.configure(
            "SectionTitle.TLabel",
            background=COLOR_CARD_BG,
            foreground=COLOR_NAVY,
            font=("Segoe UI", 11, "bold"),
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
            font=("Segoe UI", 9),
        )
        style.configure(
            "Action.TButton",
            font=("Segoe UI", 9, "bold"),
            padding=6,
        )
        style.configure(
            "Primary.TButton",
            background=COLOR_NAVY,
            foreground="#ffffff",
            font=("Segoe UI", 11, "bold"),
            padding=8,
        )
        style.map(
            "Primary.TButton",
            background=[("active", COLOR_NAVY_LIGHT), ("disabled", "#bcccdc")],
        )

    def _build_ui(self):
        # ── Header Banner ──────────────────────────────────────────────────────
        hdr_frame = tk.Frame(self.root, bg=COLOR_NAVY)
        hdr_frame.pack(fill="x", side="top")

        lbl_title = ttk.Label(hdr_frame, text="Live Translation Setup", style="Header.TLabel")
        lbl_title.pack(anchor="w")
        lbl_subtitle = ttk.Label(
            hdr_frame,
            text="Configure church identity, local network address, and Google Gemini API credentials.",
            style="SubHeader.TLabel",
        )
        lbl_subtitle.pack(anchor="w")

        # ── Scrollable Body ───────────────────────────────────────────────────
        canvas = tk.Canvas(self.root, bg=COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        self.scroll_frame = ttk.Frame(canvas)

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas_window = canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")

        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="top", fill="both", expand=True, padx=16, pady=12)
        scrollbar.pack(side="right", fill="y")

        # ── Section 1: Church Identity ────────────────────────────────────────
        card_church = tk.Frame(self.scroll_frame, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_church.pack(fill="x", pady=(0, 12), padx=4)

        p_church = tk.Frame(card_church, bg=COLOR_CARD_BG, padx=16, pady=14)
        p_church.pack(fill="x")

        ttk.Label(p_church, text="1. CHURCH IDENTITY", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 10))

        # Church Name
        f_name = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_name.pack(fill="x", pady=4)
        ttk.Label(f_name, text="Church Name:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_church_name = ttk.Entry(f_name, font=("Segoe UI", 9))
        self.entry_church_name.insert(0, self.current_church.get("name", "Starkville Korean Church"))
        self.entry_church_name.pack(side="left", fill="x", expand=True)

        # Short Name
        f_sname = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_sname.pack(fill="x", pady=4)
        ttk.Label(f_sname, text="Short Name:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_short_name = ttk.Entry(f_sname, font=("Segoe UI", 9), width=15)
        self.entry_short_name.insert(0, self.current_church.get("short_name", "SKC"))
        self.entry_short_name.pack(side="left")
        ttk.Label(f_sname, text="(used on compact mobile headers)", style="Muted.TLabel").pack(side="left", padx=8)

        # Local Hostname
        f_host = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_host.pack(fill="x", pady=4)
        ttk.Label(f_host, text="Local URL:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(f_host, text="http://", style="Muted.TLabel").pack(side="left")
        self.entry_hostname = ttk.Entry(f_host, font=("Segoe UI", 9), width=16)
        self.entry_hostname.insert(0, self.current_network.get("hostname", "skc"))
        self.entry_hostname.pack(side="left", padx=2)
        ttk.Label(f_host, text=".local", style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(f_host, text="(e.g. skc → http://skc.local)", style="Muted.TLabel").pack(side="left", padx=8)

        # Church Logo
        f_logo = tk.Frame(p_church, bg=COLOR_CARD_BG)
        f_logo.pack(fill="x", pady=4)
        ttk.Label(f_logo, text="Church Logo:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_logo_path = ttk.Label(
            f_logo,
            text=self.current_church.get("logo", "branding/church-logo.png"),
            style="Muted.TLabel",
        )
        self.lbl_logo_path.pack(side="left", fill="x", expand=True, padx=4)
        btn_logo = ttk.Button(f_logo, text="Choose Logo...", command=self._choose_logo)
        btn_logo.pack(side="right")

        # ── Section 2: Google Gemini Setup Instructions ───────────────────────
        card_google = tk.Frame(self.scroll_frame, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_google.pack(fill="x", pady=(0, 12), padx=4)

        p_google = tk.Frame(card_google, bg=COLOR_CARD_BG, padx=16, pady=14)
        p_google.pack(fill="x")

        ttk.Label(p_google, text="2. GOOGLE GEMINI SETUP", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 6))

        # Step A: Create API Key
        ttk.Label(
            p_google,
            text="Step A: Create an API key in Google AI Studio",
            style="FieldLabel.TLabel",
        ).pack(anchor="w", pady=(4, 2))
        ttk.Label(
            p_google,
            text="• Create a new API key in Google AI Studio rather than reusing an old key.\n  (Older keys before the Auth key migration may stop working by Sept 2026).",
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 4))
        btn_studio = ttk.Button(
            p_google,
            text="🌐 Open Google AI Studio",
            command=lambda: webbrowser.open("https://aistudio.google.com/app/apikey"),
        )
        btn_studio.pack(anchor="w", padx=8, pady=(2, 8))

        # Step B: Billing Setup
        ttk.Label(
            p_google,
            text="Step B: Set up billing for Google Cloud / Gemini API",
            style="FieldLabel.TLabel",
        ).pack(anchor="w", pady=(4, 2))
        ttk.Label(
            p_google,
            text="• Gemini Live translation requires a paid tier account.\n  Google may require a minimum $10 prepaid credit during initial billing setup.",
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 4))
        btn_billing = ttk.Button(
            p_google,
            text="💳 Open Billing Setup Guide",
            command=lambda: webbrowser.open("https://ai.google.dev/gemini-api/docs/billing"),
        )
        btn_billing.pack(anchor="w", padx=8, pady=(2, 6))

        # ── Section 3: API Key & Validation ───────────────────────────────────
        card_key = tk.Frame(self.scroll_frame, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_key.pack(fill="x", pady=(0, 12), padx=4)

        p_key = tk.Frame(card_key, bg=COLOR_CARD_BG, padx=16, pady=14)
        p_key.pack(fill="x")

        ttk.Label(p_key, text="3. API KEY CONFIGURATION & VALIDATION", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 8))

        self.key_container = tk.Frame(p_key, bg=COLOR_CARD_BG)
        self.key_container.pack(fill="x", pady=4)

        if self.existing_key:
            self._render_existing_key_view()
        else:
            self._render_new_key_view()

        # Validation status box
        self.val_box = tk.Frame(p_key, bg="#f8f9fa", bd=1, relief="solid", highlightbackground=COLOR_BORDER, padx=12, pady=10)
        self.val_box.pack(fill="x", pady=(10, 4))

        self.lbl_val_key = tk.Label(
            self.val_box,
            text="• API Key: Not tested yet",
            font=("Segoe UI", 9),
            bg="#f8f9fa",
            fg=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_val_key.pack(fill="x")

        self.lbl_val_model = tk.Label(
            self.val_box,
            text=f"• Model ({self.configured_model}): Not tested yet",
            font=("Segoe UI", 9),
            bg="#f8f9fa",
            fg=COLOR_TEXT_MUTED,
            anchor="w",
        )
        self.lbl_val_model.pack(fill="x", pady=(2, 0))

        # Test Connection button
        self.btn_test = ttk.Button(
            p_key,
            text="🔍 Test Connection & Model Availability",
            command=self._start_connection_test,
        )
        self.btn_test.pack(pady=8)

        # ── Section 4: Cloudflare Named Tunnel (Public HTTPS) ────────────────
        card_tunnel = tk.Frame(self.scroll_frame, bg=COLOR_CARD_BG, bd=1, relief="solid", highlightbackground=COLOR_BORDER)
        card_tunnel.pack(fill="x", pady=(0, 12), padx=4)

        p_tunnel = tk.Frame(card_tunnel, bg=COLOR_CARD_BG, padx=16, pady=14)
        p_tunnel.pack(fill="x")

        ttk.Label(p_tunnel, text="4. CLOUDFLARE NAMED TUNNEL (PUBLIC HTTPS)", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 8))

        # Public URL
        f_puburl = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_puburl.pack(fill="x", pady=4)
        ttk.Label(f_puburl, text="Public URL:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_public_url = ttk.Entry(f_puburl, font=("Segoe UI", 9))
        self.entry_public_url.insert(0, self.current_network.get("public_url", "https://live.starkvillekoreanchurch.org"))
        self.entry_public_url.pack(side="left", fill="x", expand=True)

        # Service Status
        f_svc = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_svc.pack(fill="x", pady=(8, 4))
        ttk.Label(f_svc, text="Windows Service:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.lbl_svc_status = tk.Label(f_svc, text="Checking...", font=("Segoe UI", 9, "bold"), bg=COLOR_CARD_BG, fg=COLOR_TEXT_MUTED)
        self.lbl_svc_status.pack(side="left", padx=4)
        btn_refresh_svc = ttk.Button(f_svc, text="Refresh", width=8, command=self._refresh_service_status)
        btn_refresh_svc.pack(side="right")

        # Tunnel Token (Optional Setup/Install)
        f_tok = tk.Frame(p_tunnel, bg=COLOR_CARD_BG)
        f_tok.pack(fill="x", pady=(8, 4))
        ttk.Label(f_tok, text="Tunnel Token:", width=14, anchor="w", style="FieldLabel.TLabel").pack(side="left")
        self.entry_tunnel_token = ttk.Entry(f_tok, font=("Consolas", 9), show="•")
        self.entry_tunnel_token.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_install_svc = ttk.Button(p_tunnel, text="⚙️ Start / Install Cloudflared Service", command=self._start_or_install_service)
        btn_install_svc.pack(anchor="w", padx=8, pady=(4, 2))
        ttk.Label(
            p_tunnel,
            text="• Token is only needed once to install the Windows service. Runtime translation monitors the service automatically.",
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 2))

        self._refresh_service_status()

        # ── Bottom Action Bar ─────────────────────────────────────────────────
        bottom_frame = tk.Frame(self.root, bg=COLOR_BG, pady=12)
        bottom_frame.pack(fill="x", side="bottom", padx=20)

        self.btn_save = ttk.Button(
            bottom_frame,
            text="💾 Save & Finish",
            style="Primary.TButton",
            command=self._save_and_finish,
        )
        self.btn_save.pack(side="right", padx=4)

        btn_cancel = ttk.Button(bottom_frame, text="Cancel / Exit", command=self.root.destroy)
        btn_cancel.pack(side="right", padx=4)

    def _render_existing_key_view(self):
        for w in self.key_container.winfo_children():
            w.destroy()

        ttk.Label(self.key_container, text="Configured Key:", style="FieldLabel.TLabel").pack(anchor="w")
        f_row = tk.Frame(self.key_container, bg=COLOR_CARD_BG)
        f_row.pack(fill="x", pady=4)

        masked = mask_api_key(self.existing_key)
        lbl_masked = tk.Label(
            f_row,
            text=masked,
            font=("Consolas", 10, "bold"),
            bg="#edf2f7",
            fg=COLOR_TEXT_MAIN,
            padx=8,
            pady=4,
            relief="solid",
            bd=1,
        )
        lbl_masked.pack(side="left", padx=(0, 8))

        btn_replace = ttk.Button(f_row, text="Replace Key...", command=self._render_new_key_view)
        btn_replace.pack(side="left")

    def _render_new_key_view(self):
        for w in self.key_container.winfo_children():
            w.destroy()

        ttk.Label(self.key_container, text="Paste Google Gemini API Key:", style="FieldLabel.TLabel").pack(anchor="w")

        f_entry = tk.Frame(self.key_container, bg=COLOR_CARD_BG)
        f_entry.pack(fill="x", pady=4)

        self.entry_key = ttk.Entry(f_entry, font=("Consolas", 9), show="•")
        self.entry_key.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.is_key_visible = False

        def _toggle_visibility():
            self.is_key_visible = not self.is_key_visible
            self.entry_key.configure(show="" if self.is_key_visible else "•")
            btn_show.configure(text="Hide" if self.is_key_visible else "Show")

        btn_show = ttk.Button(f_entry, text="Show", width=6, command=_toggle_visibility)
        btn_show.pack(side="left", padx=2)

        def _paste_clipboard():
            try:
                clip = self.root.clipboard_get().strip()
                if clip:
                    self.entry_key.delete(0, tk.END)
                    self.entry_key.insert(0, clip)
            except Exception:
                pass

        btn_paste = ttk.Button(f_entry, text="Paste", width=6, command=_paste_clipboard)
        btn_paste.pack(side="left", padx=2)

        if self.existing_key:
            btn_cancel_replace = ttk.Button(
                self.key_container,
                text="← Keep Existing Key",
                command=self._render_existing_key_view,
            )
            btn_cancel_replace.pack(anchor="w", pady=(2, 0))

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
        self.lbl_val_key.configure(text="• API Key: Testing authentication...", fg=COLOR_TEXT_MUTED)
        self.lbl_val_model.configure(text=f"• Model ({self.configured_model}): Querying available models...", fg=COLOR_TEXT_MUTED)

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
                model_note = f"✓ Configured model available: {cfg_model}"
            else:
                # Check if any live translate model is available as alternative
                live_models = [m for m in available_models if "live" in m or "translate" in m]
                if live_models:
                    model_note = f"⚠️ {cfg_model} not in list, but found: {', '.join(live_models[:2])}"
                    model_valid = True
                else:
                    model_note = f"✗ Configured model '{cfg_model}' was not found in your project."
                    model_valid = False

        except Exception as e:
            err_str = str(e)
            # Defensive sanitation: NEVER show or leak raw key
            if key in err_str:
                err_str = err_str.replace(key, "••••••••")
            error_msg = err_str

        # Update GUI on main thread
        self.root.after(0, self._on_test_complete, key_valid, model_valid, error_msg, model_note)

    def _on_test_complete(self, key_valid: bool, model_valid: bool, error_msg: str, model_note: str):
        self.testing = False
        self.btn_test.configure(state="normal")

        if key_valid:
            self.lbl_val_key.configure(text="✓ API key accepted and authenticated", fg=COLOR_SUCCESS)
            if model_valid:
                self.lbl_val_model.configure(text=f"✓ {model_note}", fg=COLOR_SUCCESS)
                self.validation_passed = True
            else:
                self.lbl_val_model.configure(text=model_note, fg=COLOR_ERROR)
        else:
            self.lbl_val_key.configure(text=f"✗ API key authentication failed: {error_msg}", fg=COLOR_ERROR)
            self.lbl_val_model.configure(text=f"• Model ({self.configured_model}): Could not test", fg=COLOR_TEXT_MUTED)

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
        resp = messagebox.askyesno(
            "Setup Complete",
            "Configuration saved successfully!\n\n"
            f"• Church: {church_name} ({short_name})\n"
            f"• Local URL: http://{hostname}.local\n"
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
    # Windows taskbar icon/styling helper if available
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = SetupApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
