import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from .transport import SSH


def parser():
    p = argparse.ArgumentParser(description="VPS Migrator — безопасный перенос 3x-ui/Xray")
    p.add_argument("--identity", help="SSH private key (пароль ключа спросит OpenSSH)")
    p.add_argument("--known-hosts", help="Файл заранее проверенных SSH host keys")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("wizard", help="Пошаговый мастер на русском")
    d = sub.add_parser("discover", help="Инвентаризация без изменения конфигурации")
    d.add_argument("host")
    d.add_argument("--port", type=int, default=22)
    m = sub.add_parser("migrate", help="Проверить план; --apply только готовит staging")
    m.add_argument("--source", required=True)
    m.add_argument("--target", required=True)
    m.add_argument("--source-port", type=int, default=22)
    m.add_argument("--target-port", type=int, default=22)
    m.add_argument("--cert-dir", default="/root/cert/sakha-ai.ru")
    m.add_argument("--apply", action="store_true")
    m.add_argument("--confirm", help="Повторите IP новой VPS для записи staging")
    for command in ("commit", "activate", "rollback", "health", "status", "discard"):
        a = sub.add_parser(command)
        a.add_argument("host")
        a.add_argument("job", help="UUID задания, показанный migrate")
        a.add_argument("--port", type=int, default=22)
        if command in ("commit", "activate", "rollback", "discard"):
            a.add_argument("--apply", action="store_true")
            a.add_argument("--confirm", help="Повторите IP целевой VPS")
        if command == "activate":
            a.add_argument("--accept-source-code", action="store_true",
                           help="Разрешить выполнение перенесённого root-кода и открытие его слушателей")
    return p


def show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def execute(a):
    if a.command == "wizard":
        from .wizard import run
        return run(a, execute)
    def ssh(address, port):
        return SSH(address, port, a.identity, a.known_hosts)

    if a.command == "discover":
        show(ssh(a.host, a.port).call("discover"))
        return
    if a.command == "migrate":
        src, dst = ssh(a.source, a.source_port), ssh(a.target, a.target_port)
        if src.address == dst.address:
            raise ValueError("Источник и назначение должны быть разными VPS")
        left = src.call("discover", {"cert": a.cert_dir})
        right = dst.call("preflight", {"cert": a.cert_dir, "source": left})
        show({"source": left, "target": right, "plan": [
            "SQLite Online Backup API (.backup), integrity_check",
            "Копия установки, TLS и unit; SHA-256 каждого файла",
            "SSH → закрытый временный файл → SSH → staging",
            "Отдельно commit, затем activate; DNS/маршруты/firewall не меняются"],
            "dry_run": not a.apply})
        if not a.apply:
            return
        if a.confirm != dst.address:
            raise ValueError("Для staging укажите --confirm с IP новой VPS")
        job = str(uuid.uuid4())
        print("Задание: " + job, flush=True)
        # TemporaryDirectory and mkstemp are private, including archive with TLS keys.
        with tempfile.TemporaryDirectory(prefix="vps-migrator-") as folder:
            archive = os.path.join(folder, "payload.tar")
            exported = src.call("export", {"job": job, "cert": a.cert_dir})
            try:
                fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as out:
                    src.call("download", {"job": job}, stdout=out)
                digest = hashlib.sha256()
                with open(archive, "rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != exported["sha256"]:
                    raise RuntimeError("SHA-256 архива не совпадает")
                with open(archive, "rb") as stream:
                    show(dst.call("stage", {"job": job, "cert": a.cert_dir,
                                           "sha256": exported["sha256"],
                                           "size": exported["size"], "source": left}, stdin=stream))
            finally:
                src.call("cleanup-export", {"job": job})
        print("Staging готов. Сохраните UUID. Далее: commit → activate (см. README).")
        return job
    target = ssh(a.host, a.port)
    args = {"job": a.job}
    if a.command in ("commit", "activate", "rollback", "discard"):
        if not a.apply:
            show({"dry_run": True, "action": a.command, "state": target.call("status", args)})
            return
        if a.confirm != target.address:
            raise ValueError("Требуется --confirm с IP целевой VPS")
        if a.command == "activate" and not a.accept_source_code:
            raise ValueError("Для запуска root-кода требуется --accept-source-code")
    report = target.call(a.command, args)
    show(report)
    if a.command == "health" and not report["ok"]:
        raise RuntimeError("Health check не пройден; отсутствующие слушатели показаны выше")


def main():
    try:
        execute(parser().parse_args())
    except (ValueError, RuntimeError, OSError) as exc:
        print("Ошибка: " + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Прервано. Проверьте status; при незавершённом commit используйте rollback.", file=sys.stderr)
        sys.exit(130)
