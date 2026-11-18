# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 devaser (https://github.com/devaser)

"""
Keeps the Windows firewall rule of the HTTP server in step with the configured
port. The service (which runs as SYSTEM) calls this every time it starts, so a
port changed in Settings is applied when the service restarts.

The rule is deleted and created again with the new port, using the same
settings as the installer (this program and this service only, local subnet
only, private, domain and public profiles).

Only an existing rule is replaced: if the user did not choose to create the
firewall rules at install time, nothing is created here.
"""

import os
import subprocess


HTTP_RULE_NAME = "EasyDLNA_HTTP"
SERVICE_NAME = "EasyDLNAServer"


def netsh_path():

    return os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "netsh.exe"
    )


def run_netsh(run, arguments):

    return run(
        [netsh_path(), "advfirewall", "firewall"] + arguments,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def sync_http_rule(
    port,
    program=None,
    service_name=SERVICE_NAME,
    run=subprocess.run,
    platform=None
):
    """
    Delete the existing HTTP rule and create it again for the given TCP port.
    program is the path of the service executable. Without it the existing
    rule is only modified. Returns a short message that says what happened.
    """

    if (platform or os.name) != "nt":
        return "Not on Windows: nothing to do."

    try:

        exists = run_netsh(
            run,
            ["show", "rule", f"name={HTTP_RULE_NAME}"]
        ).returncode == 0

        if not exists:
            return (
                f"Firewall rule {HTTP_RULE_NAME} was not found, so it was "
                "left alone (normal if the firewall rules were not installed)."
            )

        if not program:

            result = run_netsh(
                run,
                [
                    "set", "rule", f"name={HTTP_RULE_NAME}",
                    "new", "protocol=TCP", f"localport={port}"
                ]
            )

            if result.returncode == 0:
                return (
                    f"Firewall rule {HTTP_RULE_NAME} now uses TCP port {port}."
                )

            return f"Could not change the firewall rule {HTTP_RULE_NAME}."

        run_netsh(run, ["delete", "rule", f"name={HTTP_RULE_NAME}"])

        result = run_netsh(
            run,
            [
                "add", "rule",
                f"name={HTTP_RULE_NAME}",
                "dir=in",
                "action=allow",
                f"program={program}",
                f"service={service_name}",
                "protocol=TCP",
                f"localport={port}",
                "remoteip=localsubnet",
                "profile=private,domain,public"
            ]
        )

    except OSError as error:
        return f"Could not run netsh: {error}"

    if result.returncode == 0:
        return (
            f"Firewall rule {HTTP_RULE_NAME} was deleted and created again "
            f"for TCP port {port}."
        )

    # The old rule is already gone; the next start of the service tries again.
    return (
        f"The old firewall rule {HTTP_RULE_NAME} was deleted but the new one "
        "could not be created. It will be tried again when the service "
        "starts."
    )
