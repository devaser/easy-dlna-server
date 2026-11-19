## ☕ Support

I build and maintain these tools in my free time for free. If this project saved you some time or you find it useful, consider buying me a coffee to support future updates!

<a href="https://www.buymeacoffee.com/devaser" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" style="height: 60px !important;width: 217px !important;" ></a>




# Easy DLNA Server

A lightweight DLNA/UPnP media server for Windows. It runs as a Windows service, shares the folders you choose with DLNA clients (TVs, game consoles, media players) on your local network, and comes with a system tray app to control it.

## Components

| Executable | What it does |
|---|---|
| `EasyDLNAService.exe` | The Windows service: SSDP discovery (UDP 1900) and the HTTP media server (TCP 8200 by default, configurable). |
| `EasyDLNATray.exe` | System tray app: start/stop the service, scan the library, open settings, open the service log. |
| `EasyDLNASettings.exe` | Settings window (shared folders). |
| `EasyDLNAScanner.exe` | Scans the shared folders and updates the media library. |

## Install

1. Download `EasyDLNASetup.exe` from the [Releases](https://github.com/devaser/easy-dlna-server/releases) page.
2. Run it as administrator.
3. Leave **Allow Easy DLNA Server through Windows Firewall** selected so other devices on your network can find the server. This creates two inbound rules, only for the Easy DLNA service:
   - UDP 1900 (SSDP discovery)
   - TCP 8200 (HTTP, the default port; it can be changed in Settings)

   Both rules accept connections from the local subnet only, on the Private, Domain and Public profiles.

To install silently and skip the firewall rules:

```
EasyDLNASetup.exe /VERYSILENT /TASKS="!firewall"
```

Uninstalling removes the service, the firewall rules and the program files. The data stored in `%ProgramData%` is not touched.

## Install on Linux (Ubuntu and Debian)

1. Download `easydlna_<version>_<architecture>.deb` from the Releases page.
2. Install it:

   ```
   sudo apt install ./easydlna_*.deb
   ```

3. Choose how to run it:

   ```
   easydlna setup
   ```

   - **User service:** runs as you, starts when you log in. The simplest choice on a desktop. To keep it running after you log out, run `loginctl enable-linger $USER`.
   - **System service:** runs as the `easydlna` user and starts at boot. Commands that change anything need `sudo`. The user `easydlna` must be able to read your media folders.

   `setup` can also open the ports in `ufw` (UDP 1900 and the HTTP port, local network only).

4. Add your folders and start sharing:

   ```
   easydlna folders add ~/Videos
   easydlna status
   ```

#### System service and media folders

The system service runs as the `easydlna` user, so it must be able to read your media folders. A folder outside your home, such as `/srv/media`, works directly if everybody can read it. A folder in your home usually does not, because your home is private. Give the service access without opening the folder to everybody (this needs the `acl` package):

```
sudo apt install acl
sudo setfacl -m u:easydlna:x /home/YOUR_USER
sudo setfacl -R -m u:easydlna:rX /home/YOUR_USER/Videos
sudo easydlna folders add /home/YOUR_USER/Videos
```

`easydlna folders add` prints these commands for you when access is denied. With the user service none of this is needed, because it runs as you.

#### Keeping the computer awake

While a video is playing, Easy DLNA Server keeps the computer from going to sleep (you can see it with `systemd-inhibit --list`). It stays awake for 5 minutes after the last video ends.

- **User service:** the program asks the system for a sleep lock itself.
- **System service:** the `easydlna` user has no login session, and the system does not give it a sleep lock ("Access denied"). So `easydlna install-service --system` also installs two small helper units, `easydlna-awake.path` and `easydlna-awake.service`. The service creates the file `/run/easydlna/streaming` while it streams, and the helper, which runs as root, holds the sleep lock for as long as that file exists. `easydlna uninstall-service` removes them.

Other commands: `start`, `stop`, `restart`, `logs -f`, `scan`, `folders list|remove`, `port [NUMBER]`, `firewall open|close` and `uninstall-service`. Run `easydlna --help` for the details.

The HTTP port is 8200 by default. Change it with `easydlna port NUMBER`; the service restarts and the `ufw` rules are updated when you run the command as root.

To remove the package: `sudo apt remove easydlna` (or `purge` to also delete the data of the system service).

## Install on macOS (experimental, not tested yet)

1. Download `easydlna-<version>-<architecture>.pkg` and open it. The package is not signed, so macOS asks you to confirm it (right-click the package, then Open).
2. Run `easydlna setup` to start it when you log in, then `easydlna folders add ~/Movies`.

macOS supports the user service only. The first time, the macOS firewall asks for permission: choose Allow.

## Build from source

Requirements: Windows and Python 3.13.

```
py -3.13 -m pip install -r requirements-dev.txt
```

Run the tray app from source:

```
py -3.13 tray.py
```

Build the executables (one `.spec` file per executable) and the installer:

```
py -3.13 -m PyInstaller EasyDLNAService.spec
py -3.13 -m PyInstaller EasyDLNATray.spec
py -3.13 -m PyInstaller EasyDLNAScanner.spec
py -3.13 -m PyInstaller EasyDLNASettings.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" EasyDLNASetup.iss
```

### Linux and macOS packages

The packages are built with PyInstaller on the system they are meant for (a package cannot be built for another system or processor).

```
# Ubuntu or Debian (use the oldest Ubuntu you want to support, for example 22.04)
sudo apt install python3 python3-venv
bash packaging/linux/build-deb.sh          # creates dist/easydlna_<version>_<architecture>.deb

# macOS
bash packaging/macos/build-pkg.sh          # creates dist/easydlna-<version>-<architecture>.pkg
```

To run from source on Linux or macOS: `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt` and `.venv/bin/python easydlna.py setup`.

### Windows installer

The installer is written for [Inno Setup](https://jrsoftware.org/isinfo.php) 6.3 or later. It needs the four executables in `dist\`, plus `LICENSE` and `THIRD_PARTY_NOTICES.md`.

### Releasing (maintainers)

GitHub Actions builds and tests every package. To make a release:

1. Raise the version in `app_version.py` and in `EasyDLNASetup.iss` (`MyAppVersion`). The workflow stops if they differ from each other or from the tag.
2. Commit, then create and push the tag:

   ```
   git tag v0.3.3
   git push origin main v0.3.3
   ```

3. The **Release** workflow builds the Windows installer, the `.deb` packages for amd64 and arm64, and the macOS `.pkg` packages for Apple silicon and Intel. It installs each package and checks that the server answers. If everything passes, it creates a **draft** release with the files and a `SHA256SUMS.txt`. Review it on GitHub and publish it.

The **CI** workflow checks every push and pull request: Python and shell syntax, matching versions, and that the server runs from source. The **Release** workflow can also be started by hand from the Actions tab to test the builds without making a release.

## License

Copyright (C) 2026 [devaser](https://github.com/devaser)

Easy DLNA Server is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See the [LICENSE](LICENSE) file for the full text.

Third-party components and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
