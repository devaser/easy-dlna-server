# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Checks that the version is the same everywhere before a release:

  app_version.py        (Linux and macOS packages, command line)
  EasyDLNASetup.iss     (Windows installer)
  the git tag           (optional, for example v0.3.2)

  python packaging/check_version.py [TAG]
"""

import re
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main(argv):

    sys.path.insert(0, str(ROOT))

    import app_version

    iss = (ROOT / "EasyDLNASetup.iss").read_text(encoding="utf-8")
    match = re.search(r'^#define MyAppVersion "([^"]+)"', iss, re.MULTILINE)

    if not match:
        print("error: MyAppVersion not found in EasyDLNASetup.iss")
        return 1

    versions = {
        "app_version.py": app_version.__version__,
        "EasyDLNASetup.iss": match.group(1),
    }

    tag = argv[1].strip() if len(argv) > 1 else ""

    if tag:
        versions["git tag"] = tag[1:] if tag.startswith("v") else tag

    for name, version in versions.items():
        print(f"{name:20} {version}")

    if len(set(versions.values())) != 1:
        print("error: the versions do not match")
        return 1

    print("OK: all versions match")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
