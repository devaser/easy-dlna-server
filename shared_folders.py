# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)


import json
import os
import subprocess
import sys
import tkinter as tk

from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_settings import (
    DEFAULT_HTTP_PORT,
    MAX_PORT,
    MIN_PORT,
    get_http_port,
    set_http_port,
    validate_port
)
from platform_paths import get_data_dir
from scan_lock import SCAN_BUSY_EXIT_CODE


DATA_DIR = get_data_dir()

CONFIG_DIR = DATA_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "shared_folders.json"

LOG_DIR = DATA_DIR / "logs"
SCAN_LOG_FILE = LOG_DIR / "scan.log"


# ---------------------------------------------------------
# LIBRARY SCANNER
# ---------------------------------------------------------

FROZEN = getattr(sys, "frozen", False)

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent
)

SCAN_EXECUTABLE = BASE_DIR / "EasyDLNAScanner.exe"
SCAN_SCRIPT = BASE_DIR / "scan_library.py"

SCAN_POLL_INTERVAL_MS = 1000

SERVICE_NAME = "EasyDLNAServer"

CREATE_NO_WINDOW = getattr(
    subprocess,
    "CREATE_NO_WINDOW",
    0
)


class SharedFoldersApp:

    def __init__(self, root):

        self.root = root
        self.root.title("Easy DLNA Server - Settings")
        self.root.geometry("760x620")
        self.root.minsize(680, 520)
        self.root.resizable(True, True)

        self.folders = []

        self.scan_process = None
        self.scan_message = ""

        self.create_interface()
        self.load_folders()

    # -----------------------------------------------------
    # INTERFACE
    # -----------------------------------------------------

    def create_interface(self):

        main_frame = ttk.Frame(
            self.root,
            padding=18
        )
        main_frame.pack(fill="both", expand=True)

        ttk.Label(
            main_frame,
            text="Easy DLNA Server",
            font=("Segoe UI", 18, "bold")
        ).pack(anchor="w", pady=(0, 5))

        ttk.Label(
            main_frame,
            text="Manage your shared media folders."
        ).pack(anchor="w", pady=(0, 14))

        folders_frame = ttk.LabelFrame(
            main_frame,
            text="Shared folders",
            padding=12
        )
        folders_frame.pack(fill="both", expand=True)

        list_frame = ttk.Frame(folders_frame)
        list_frame.pack(fill="both", expand=True)

        self.folder_list = tk.Listbox(
            list_frame,
            font=("Segoe UI", 10),
            selectmode=tk.SINGLE,
            activestyle="none",
            borderwidth=1,
            relief="solid"
        )

        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.folder_list.yview
        )

        self.folder_list.configure(
            yscrollcommand=scrollbar.set
        )

        self.folder_list.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        buttons_frame = ttk.Frame(folders_frame)
        buttons_frame.pack(
            fill="x",
            pady=(12, 0)
        )

        ttk.Button(
            buttons_frame,
            text="Add Folder",
            command=self.add_folder
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            buttons_frame,
            text="Remove Selected",
            command=self.remove_folder
        ).pack(side="left")

        server_frame = ttk.LabelFrame(
            main_frame,
            text="Server",
            padding=12
        )
        server_frame.pack(fill="x", pady=(12, 0))

        ttk.Label(
            server_frame,
            text="HTTP port:"
        ).pack(side="left")

        self.port_var = tk.StringVar(value=str(get_http_port()))

        ttk.Spinbox(
            server_frame,
            from_=MIN_PORT,
            to=MAX_PORT,
            textvariable=self.port_var,
            width=8
        ).pack(side="left", padx=(8, 12))

        ttk.Label(
            server_frame,
            text=(
                f"Default {DEFAULT_HTTP_PORT}. "
                "The server restarts to apply a new port."
            ),
            foreground="gray"
        ).pack(side="left")

        self.status_label = ttk.Label(
            main_frame,
            text="0 shared folders",
            foreground="gray"
        )
        self.status_label.pack(
            anchor="w",
            pady=(10, 0)
        )

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(
            fill="x",
            pady=(18, 0)
        )

        ttk.Button(
            bottom_frame,
            text="Save Changes",
            command=self.save_settings
        ).pack(side="right")

        ttk.Button(
            bottom_frame,
            text="Close",
            command=self.root.destroy
        ).pack(side="right", padx=(0, 8))

    # -----------------------------------------------------
    # LOAD FOLDERS
    # -----------------------------------------------------

    def load_folders(self):

        CONFIG_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        if not CONFIG_FILE.exists():
            self.refresh_list()
            return

        try:

            with open(
                CONFIG_FILE,
                "r",
                encoding="utf-8"
            ) as file:
                data = json.load(file)

            if not isinstance(data, list):
                raise ValueError(
                    "Invalid shared folders configuration."
                )

            for folder in data:

                if not isinstance(folder, str):
                    continue

                path = Path(folder).expanduser()

                if path.is_dir() and not self.is_duplicate(path):
                    self.folders.append(path.resolve())

            self.refresh_list()

        except (
            OSError,
            json.JSONDecodeError,
            ValueError
        ) as error:

            messagebox.showerror(
                "Configuration Error",
                f"Could not load shared folders:\n\n{error}"
            )

    # -----------------------------------------------------
    # FOLDER MANAGEMENT
    # -----------------------------------------------------

    def is_duplicate(self, folder):

        normalized = str(folder.resolve()).casefold()

        return any(
            str(existing.resolve()).casefold() == normalized
            for existing in self.folders
        )

    def add_folder(self):

        selected_folder = filedialog.askdirectory(
            title="Select a folder to share",
            mustexist=True
        )

        if not selected_folder:
            return

        folder = Path(selected_folder).resolve()

        if self.is_duplicate(folder):

            messagebox.showinfo(
                "Already Added",
                "This folder is already in the shared folders list."
            )
            return

        self.folders.append(folder)
        self.refresh_list()

    def remove_folder(self):

        selection = self.folder_list.curselection()

        if not selection:

            messagebox.showwarning(
                "No Selection",
                "Select a folder first."
            )
            return

        index = selection[0]
        folder = self.folders[index]

        if not messagebox.askyesno(
            "Remove Folder",
            f"Remove this shared folder?\n\n{folder}"
        ):
            return

        self.folders.pop(index)
        self.refresh_list()

    def refresh_list(self):

        self.folder_list.delete(0, tk.END)

        for folder in self.folders:

            self.folder_list.insert(
                tk.END,
                f"{folder.name}    ({folder})"
            )

        self.update_status()

    def scan_is_running(self):

        return (
            self.scan_process is not None
            and self.scan_process.poll() is None
        )

    def update_status(self):

        text = f"{len(self.folders)} shared folder(s)"

        if self.scan_is_running():
            text += " - scanning library..."

        elif self.scan_message:
            text += f" - {self.scan_message}"

        self.status_label.config(text=text)

    # -----------------------------------------------------
    # LIBRARY SCAN
    # -----------------------------------------------------

    def scan_command(self):

        # Linux and macOS: the scanner is the "scan" command of easydlna.
        if os.name != "nt":

            if FROZEN:
                return [sys.executable, "scan"]

            return [
                sys.executable,
                str(BASE_DIR / "easydlna.py"),
                "scan"
            ]

        if FROZEN:

            if not SCAN_EXECUTABLE.is_file():
                raise FileNotFoundError(
                    f"Scanner application not found: "
                    f"{SCAN_EXECUTABLE}"
                )

            return [str(SCAN_EXECUTABLE)]

        if not SCAN_SCRIPT.is_file():
            raise FileNotFoundError(
                f"Scanner script not found: {SCAN_SCRIPT}"
            )

        return [sys.executable, str(SCAN_SCRIPT)]

    def start_scan(self):

        """
        Starts the library scanner in the background and returns a message
        that says what happened. The scanner keeps running if this window is
        closed; its output goes to scan.log.
        """

        if not self.folders:
            return "There are no shared folders, so no scan was started."

        if self.scan_is_running():
            return "A library scan is already running."

        try:

            command = self.scan_command()

            SCAN_LOG_FILE.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            with open(
                SCAN_LOG_FILE,
                "a",
                encoding="utf-8"
            ) as log:

                log.write("\n--- Scan started from Settings ---\n")
                log.flush()

                self.scan_process = subprocess.Popen(
                    command,
                    cwd=str(BASE_DIR),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW
                )

        except OSError as error:

            self.scan_process = None
            self.scan_message = "Library scan could not be started."
            self.update_status()

            return f"Could not start the library scan:\n{error}"

        self.scan_message = ""
        self.update_status()

        self.root.after(
            SCAN_POLL_INTERVAL_MS,
            self.check_scan
        )

        return (
            "The library scan started in the background. "
            "You can close this window."
        )

    def check_scan(self):

        process = self.scan_process

        if process is None:
            return

        code = process.poll()

        if code is None:

            self.root.after(
                SCAN_POLL_INTERVAL_MS,
                self.check_scan
            )
            return

        self.scan_process = None

        if code == 0:
            self.scan_message = "library scan finished."
        elif code == SCAN_BUSY_EXIT_CODE:
            self.scan_message = "another library scan is already running."
        else:
            self.scan_message = (
                f"library scan failed (exit code {code}). "
                f"See scan.log."
            )

        self.update_status()

    # -----------------------------------------------------
    # SAVE FOLDERS
    # -----------------------------------------------------

    def save_folders(self):

        CONFIG_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        folders_data = [
            str(folder.resolve())
            for folder in self.folders
        ]

        temporary_file = CONFIG_FILE.with_suffix(".tmp")

        with open(
            temporary_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                folders_data,
                file,
                indent=4,
                ensure_ascii=False
            )

        os.replace(
            temporary_file,
            CONFIG_FILE
        )

    def apply_port(self, port):

        """
        Saves a new HTTP port and restarts the service so it uses it.
        Returns a message for the user, or "" if the port did not change.
        """

        if port == get_http_port():
            return ""

        try:
            set_http_port(port)

        except OSError as error:
            return f"The new HTTP port could not be saved: {error}"

        if os.name != "nt":
            return (
                f"The HTTP port is now {port}. Restart the service to "
                "apply it: easydlna restart"
            )

        try:
            subprocess.Popen(
                [
                    "cmd.exe", "/c",
                    f"net stop {SERVICE_NAME} & net start {SERVICE_NAME}"
                ],
                creationflags=CREATE_NO_WINDOW
            )

        except OSError:
            return (
                f"The HTTP port is now {port}. Restart the server to "
                "apply it."
            )

        return (
            f"The HTTP port is now {port}. The server is restarting to "
            "apply it."
        )

    def save_settings(self):

        try:
            port = validate_port(self.port_var.get())

        except ValueError as error:

            messagebox.showerror(
                "Invalid Port",
                str(error)
            )
            return

        try:

            self.save_folders()

        except OSError as error:

            messagebox.showerror(
                "Save Error",
                f"Could not save shared folders:\n\n{error}"
            )
            return

        port_message = self.apply_port(port)

        scan_message = self.start_scan()

        details = [
            "Shared folders saved successfully.",
            port_message,
            scan_message
        ]

        messagebox.showinfo(
            "Settings Saved",
            "\n\n".join(text for text in details if text)
        )


def main():

    root = tk.Tk()
    SharedFoldersApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
