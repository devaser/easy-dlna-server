# Third-party notices

Easy DLNA Server is licensed under the GNU General Public License, version 3 or (at your option) any later version. See `LICENSE`.

It uses the third-party components listed below. The licenses are summarized from each project's repository; check them again before each release.

| Component | Use | License | Project |
|---|---|---|---|
| aiohttp | HTTP server and UPnP requests | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| pystray | System tray icon (Windows) | LGPL-3.0-or-later | https://github.com/moses-palmer/pystray |
| Pillow | Draws the tray icon image | HPND (MIT-CMU style) | https://github.com/python-pillow/Pillow |
| pywin32 | Windows service control and service host | Python Software Foundation License (PSF-2.0) | https://github.com/mhammond/pywin32 |
| Python | Runtime and standard library, bundled in the executables | Python Software Foundation License (PSF-2.0) | https://www.python.org/ |
| PyInstaller | Build tool that packages the executables (not distributed) | GPL-2.0-or-later with an exception that allows the generated executables to use any license | https://github.com/pyinstaller/pyinstaller |
| Inno Setup | Builds the installer (not distributed) | Inno Setup License | https://jrsoftware.org/isinfo.php |

## pystray (LGPL-3.0-or-later)

The executables are built with PyInstaller and include pystray. The complete corresponding source code of Easy DLNA Server is available at https://github.com/devaser/easy-dlna-server. You can replace the bundled pystray with another version by rebuilding the executables from source (see `README.md`).

The LGPL-3.0 license text is available at https://www.gnu.org/licenses/lgpl-3.0.txt.
