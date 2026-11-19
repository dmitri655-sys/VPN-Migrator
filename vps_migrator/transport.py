"""OpenSSH owns authentication; passwords never enter Python."""
import base64
import ipaddress
from importlib import resources
import json
from pathlib import Path
import shlex
import subprocess


def host(value):
    # IP literals only: no shell text, SSH options, aliases or DNS ambiguity.
    return str(ipaddress.ip_address(value))


class SSH:
    def __init__(self, address, port=22, identity=None, known_hosts=None):
        self.address = host(address)
        if not 1 <= port <= 65535:
            raise ValueError("Некорректный порт SSH")
        self.options = ["ssh", "-F", "/dev/null", "-T", "-p", str(port),
                        "-o", "StrictHostKeyChecking=yes",
                        "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
                        "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no",
                        "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no",
                        "-o", "ControlMaster=no", "-o", "ControlPath=none",
                        "-o", "GlobalKnownHostsFile=/dev/null"]
        if identity:
            self.options += ["-i", str(Path(identity).resolve()), "-o", "IdentitiesOnly=yes"]
        if known_hosts:
            self.options += ["-o", "UserKnownHostsFile=" + str(Path(known_hosts).resolve())]

    def command(self, action, args):
        source = resources.files("vps_migrator").joinpath("remote.py").read_bytes()
        payload = base64.b64encode(json.dumps([action, args]).encode()).decode()
        program = ("import base64;exec(compile(base64.b64decode(" +
                   repr(base64.b64encode(source).decode()) + "),'<migrator>','exec'))")
        return self.options + ["root@" + self.address,
                               "python3 -c " + shlex.quote(program) + " " + shlex.quote(payload)]

    def call(self, action, args=None, stdin=None, stdout=None):
        # stderr intentionally inherited: OpenSSH can ask via /dev/tty.
        result = subprocess.run(self.command(action, args or {}),
                                stdin=stdin or subprocess.DEVNULL,
                                stdout=stdout or subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("SSH/удалённая операция завершилась ошибкой; см. сообщение выше")
        if stdout is None:
            return json.loads(result.stdout)
