import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
import sqlite3
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

from vps_migrator import cli, remote as r
from vps_migrator.transport import SSH, host

CERT = "/root/cert/sakha-ai.ru"
UNIT_TEXT = "[Unit]\nDescription=x-ui\n[Service]\nType=simple\nWorkingDirectory=/usr/local/x-ui/\nExecStart=/usr/local/x-ui/x-ui\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n"


class Isolation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mapping = patch.object(r, "ROOT", self.root)
        self.mapping.start()
        self.addCleanup(self.mapping.stop)

    def put(self, name, content=b"x", mode=0o600):
        p = r.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        p.chmod(mode)
        return p

    def staged(self):
        job = r.job_path(str(uuid.uuid4()))
        stage = job / "stage"
        stage.mkdir(parents=True)
        records = {}
        for base in r.roots(CERT):
            p = stage / base
            p.parent.mkdir(parents=True, exist_ok=True)
            if base == r.UNIT.lstrip("/"):
                p.write_text(UNIT_TEXT)
            else:
                p.mkdir()
                (p / "data").write_text("original")
        for base in r.roots(CERT):
            p = stage / base
            for member in [p] + (list(p.rglob("*")) if p.is_dir() else []):
                record = {"mode": stat.S_IMODE(member.stat().st_mode), "dir": member.is_dir(),
                          "size": 0 if member.is_dir() else member.stat().st_size}
                if member.is_file():
                    record["sha256"] = r.sha(member)
                records[member.relative_to(stage).as_posix()] = record
        state = {"job": job.name, "state": "staged", "cert": CERT, "intent": [], "source": {}}
        r.save(job / "state.json", state)
        r.save(job / "manifest.json", {"files": records})
        return job, state


class SQLiteTests(Isolation):
    def test_live_wal_snapshot_contains_committed_rows(self):
        source = self.root / "live.db"
        connection = sqlite3.connect(source)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE clients (id INTEGER, token TEXT)")
        connection.execute("INSERT INTO clients VALUES (1, 'secret')")
        connection.commit()
        self.assertTrue(Path(str(source) + "-wal").exists())
        target = self.root / "snapshot.db"
        r.backup_sqlite(source, target)
        with contextlib.closing(sqlite3.connect(target)) as check:
            self.assertEqual(check.execute("SELECT * FROM clients").fetchall(), [(1, "secret")])
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        connection.execute("INSERT INTO clients VALUES (2, 'later')")
        connection.commit()
        with contextlib.closing(sqlite3.connect(target)) as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM clients").fetchone(), (1,))
        with contextlib.closing(sqlite3.connect(source)) as check:
            self.assertEqual(check.execute("SELECT COUNT(*) FROM clients").fetchone(), (2,))

    def test_corrupt_database_rejected(self):
        source = self.put("bad.db", b"not a sqlite db")
        with self.assertRaises(sqlite3.DatabaseError):
            r.backup_sqlite(source, self.root / "out.db")


class ArchiveTests(Isolation):
    def archive(self, extra=None, corrupt=False):
        members = []
        records = {}
        for name in r.roots(CERT):
            is_dir = name != r.UNIT.lstrip("/")
            members.append((name, b"" if is_dir else UNIT_TEXT.encode(), is_dir))
        members.append(("etc/x-ui/x-ui.db", b"database", False))
        for name, content, is_dir in members:
            records[name] = {"mode": 0o700 if is_dir else 0o600, "dir": is_dir, "size": len(content)}
            if not is_dir:
                records[name]["sha256"] = hashlib.sha256(content).hexdigest()
        archive = self.root / (str(uuid.uuid4()) + ".tar")
        manifest = {"schema": 1, "cert": CERT, "files": records}
        with tarfile.open(archive, "w") as tar:
            for name, content, is_dir in members:
                m = tarfile.TarInfo(name)
                m.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
                m.mode = records[name]["mode"]
                m.size = len(content)
                if corrupt and name.endswith(".db"):
                    content = b"modified"
                tar.addfile(m, None if is_dir else io.BytesIO(content))
            if extra:
                m = tarfile.TarInfo(extra[0])
                m.type = extra[1]
                m.linkname = "/etc/passwd"
                m.mode = 0o600
                tar.addfile(m)
                if m.name != "manifest.json":
                    manifest["files"][m.name] = {"mode": m.mode, "dir": False, "size": 0}
            data = json.dumps(manifest).encode()
            m = tarfile.TarInfo("manifest.json")
            m.size = len(data)
            tar.addfile(m, io.BytesIO(data))
        return archive

    def test_valid_archive_and_permissions(self):
        dest = self.root / "dest"
        dest.mkdir()
        with patch.object(r.os, "chown", create=True):
            r.unpack(self.archive(), dest, CERT)
        self.assertEqual((dest / "etc/x-ui/x-ui.db").read_bytes(), b"database")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((dest / "etc/x-ui/x-ui.db").stat().st_mode), 0o600)

    def test_traversal_absolute_links_devices_and_duplicates_rejected(self):
        cases = [("../escape", tarfile.REGTYPE), ("/etc/passwd", tarfile.REGTYPE),
                 ("etc/x-ui/link", tarfile.SYMTYPE), ("etc/x-ui/hard", tarfile.LNKTYPE),
                 ("etc/x-ui/dev", tarfile.CHRTYPE), ("etc/x-ui/fifo", tarfile.FIFOTYPE),
                 ("etc/x-ui/../evil", tarfile.REGTYPE), ("manifest.json", tarfile.REGTYPE),
                 ("etc/shadow", tarfile.REGTYPE)]
        for name, kind in cases:
            with self.subTest(name=name):
                dest = self.root / str(uuid.uuid4())
                dest.mkdir()
                with self.assertRaises(ValueError):
                    r.unpack(self.archive((name, kind)), dest, CERT)
                self.assertEqual(list(dest.iterdir()), [])

    def test_checksum_mismatch(self):
        dest = self.root / "bad"
        dest.mkdir()
        with patch.object(r.os, "chown", create=True), self.assertRaisesRegex(ValueError, "Checksum"):
            r.unpack(self.archive(corrupt=True), dest, CERT)


class TransactionTests(Isolation):
    def test_discard_protects_installed_and_incomplete_transactions(self):
        for value in ("active", "committed", "committing", "activating", "rolling-back"):
            with self.subTest(state=value):
                job, state = self.staged()
                state["state"] = value
                r.save(job / "state.json", state)
                with self.assertRaises(ValueError):
                    r.discard(job)
                self.assertTrue(job.is_dir())

    def test_discard_staged_and_partial_preparation(self):
        job, state = self.staged()
        r.discard(job)
        self.assertFalse(job.exists())
        partial = r.job_path(str(uuid.uuid4()))
        partial.mkdir()
        (partial / "payload.tar").write_bytes(b"partial")
        r.discard(partial)
        self.assertFalse(partial.exists())

    def test_commit_and_rollback_keep_data(self):
        job, state = self.staged()
        with patch.object(r, "preflight"), patch.object(r, "validate_payload"), patch.object(r, "run", return_value="inactive"):
            r.commit(job, state)
            self.assertEqual(state["state"], "committed")
            for name in r.roots(CERT):
                self.assertTrue(r.path(name).exists())
            r.path(r.DBDIR + "/data").write_text("new writes")
            r.rollback_job(job, state)
            self.assertEqual(state["state"], "rolled-back")
            self.assertEqual((job / "stage/etc/x-ui/data").read_text(), "new writes")
            for name in r.roots(CERT):
                self.assertFalse(r.path(name).exists())

    def test_mid_commit_exception_rolls_back(self):
        job, state = self.staged()
        original = os.rename
        calls = []
        def fail_second(src, dst):
            calls.append(str(src))
            if len(calls) == 2:
                raise OSError("injected failure")
            return original(src, dst)
        with patch.object(r, "preflight"), patch.object(r, "validate_payload"), patch.object(r, "run", return_value="inactive"):
            with patch.object(r.os, "rename", side_effect=fail_second), self.assertRaises(OSError):
                r.commit(job, state)
        self.assertEqual(state["state"], "rolled-back")
        self.assertTrue((job / "stage/etc/x-ui/data").exists())
        self.assertFalse(r.path(r.DBDIR).exists())

    def test_crash_after_intent_before_or_after_rename(self):
        for moved in (False, True):
            with self.subTest(moved=moved):
                job, state = self.staged()
                name = "etc/x-ui"
                state.update(state="committing", intent=[name])
                if moved:
                    r.path("etc").mkdir(exist_ok=True)
                    os.rename(job / "stage" / name, r.path(name))
                with patch.object(r, "run", return_value="inactive"):
                    r.rollback_job(job, state)
                self.assertTrue((job / "stage" / name / "data").exists())
                self.assertFalse(r.path(name).exists())

    def test_tamper_fails_before_any_install(self):
        job, state = self.staged()
        (job / "stage/etc/x-ui/data").write_text("tampered")
        with patch.object(r, "preflight"), self.assertRaisesRegex(ValueError, "checksum"):
            r.commit(job, state)
        self.assertEqual(state["state"], "staged")

    def test_unhealthy_activation_rolls_back(self):
        job, state = self.staged()
        with patch.object(r, "preflight"), patch.object(r, "validate_payload"), patch.object(r, "run", return_value="inactive"):
            r.commit(job, state)
            with patch.object(r, "listening", return_value={"tcp": [], "udp": []}), patch.object(r, "health", return_value={"ok": False}), patch.object(r.time, "monotonic", side_effect=[0, 100]):
                with self.assertRaisesRegex(ValueError, "Health"):
                    r.activate(job, state)
        self.assertEqual(state["state"], "rolled-back")
        self.assertFalse(r.path(r.APP).exists())


class GuardTests(Isolation):
    def test_bad_input(self):
        for value in ("-oProxyCommand=evil", "1.2.3.4;id", "root@1.2.3.4", "example.org"):
            with self.assertRaises(ValueError):
                host(value)
        self.assertEqual(host("2001:db8::1"), "2001:db8::1")
        for value in ("/root/cert/../etc", "/etc/ssl", "/root/cert/.."):
            with self.assertRaises(ValueError):
                r.cert_path(value)
        with self.assertRaises(ValueError):
            r.job_path("../../etc")

    def test_host_key_and_no_forwarding(self):
        ssh = SSH("192.0.2.1")
        args = ssh.command("discover", {})
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("ForwardAgent=no", args)
        self.assertIn("ClearAllForwardings=yes", args)
        self.assertNotIn("StrictHostKeyChecking=no", args)

    def test_unit_hooks_and_external_dependencies_rejected(self):
        p = self.put("unit", UNIT_TEXT.encode())
        r.validate_unit(p)
        for extra in ("ExecStartPre=/bin/evil", "EnvironmentFile=/etc/secrets", "User=nobody",
                      "KillMode=process", "SendSIGKILL=no"):
            p.write_text(UNIT_TEXT + extra + "\n")
            with self.assertRaises(ValueError):
                r.validate_unit(p)

    def test_existing_target_refused(self):
        self.put(r.DBDIR + "/existing", b"keep")
        with self.assertRaisesRegex(ValueError, "не чистая"):
            r.fresh(CERT)
        self.assertEqual(r.path(r.DBDIR + "/existing").read_bytes(), b"keep")

    def test_tcp_udp_listener_parser(self):
        def command(*args, **kwargs):
            return "LISTEN 0 128 [::]:443 [::]:*\nLISTEN 0 128 0.0.0.0:8443 0.0.0.0:*" if args[1] == "-lntH" else "UNCONN 0 0 0.0.0.0:443 0.0.0.0:*"
        with patch.object(r, "run", side_effect=command):
            self.assertEqual(r.listening(), {"tcp": [443, 8443], "udp": [443]})

    def test_migrate_default_never_exports(self):
        source = {"machine_id": "old"}
        calls = []
        class FakeSSH:
            def __init__(self, address, *args):
                self.address = address
            def call(self, action, args=None):
                calls.append(action)
                return source
        args = cli.parser().parse_args(["migrate", "--source", "192.0.2.1", "--target", "192.0.2.2"])
        with patch.object(cli, "SSH", FakeSSH), patch.object(cli, "show"):
            cli.execute(args)
        self.assertEqual(calls, ["discover", "preflight"])

    def test_confirmation_before_mutation(self):
        args = cli.parser().parse_args(["commit", "192.0.2.2", str(uuid.uuid4()), "--apply"])
        with patch.object(SSH, "call") as call, self.assertRaises(ValueError):
            cli.execute(args)
        call.assert_not_called()

    def test_activate_requires_code_acceptance(self):
        args = cli.parser().parse_args(["activate", "192.0.2.2", str(uuid.uuid4()),
                                        "--apply", "--confirm", "192.0.2.2"])
        with patch.object(SSH, "call") as call, self.assertRaisesRegex(ValueError, "accept-source-code"):
            cli.execute(args)
        call.assert_not_called()

    def test_negative_health_is_visible_and_fails(self):
        args = cli.parser().parse_args(["health", "192.0.2.2", str(uuid.uuid4())])
        result = {"ok": False, "missing_listeners": {"tcp": [443]}}
        with patch.object(SSH, "call", return_value=result), patch.object(cli, "show") as show:
            with self.assertRaises(RuntimeError):
                cli.execute(args)
            show.assert_called_once_with(result)

    @unittest.skipUnless(os.name == "posix", "Linux filesystem guard")
    def test_symlink_parent_refused(self):
        (self.root / "linked").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            r.no_links(self.root / "linked/anything")


class PreflightTests(Isolation):
    def data(self):
        return {"machine_id": "target", "arch": "x86_64", "libc": ["glibc", "2.36"],
                "listeners": {"tcp": [], "udp": []}, "free_bytes": 2 * 1024**3}

    def source(self):
        data = self.data()
        data.update(machine_id="source", bytes=1024)
        return data

    def test_preflight_accepts_clean_same_arch(self):
        r.path("/var/lib").mkdir(parents=True)
        with patch.object(r, "facts", return_value=self.data()), patch.object(r, "fresh"):
            self.assertEqual(r.preflight({"cert": CERT, "source": self.source()})["machine_id"], "target")

    def test_preflight_rejects_identity_arch_old_libc_ports_and_disk(self):
        cases = [{"machine_id": "source"}, {"arch": "aarch64"},
                 {"libc": ["glibc", "2.28"]}, {"libc": ["musl", "1.2"]},
                 {"listeners": {"tcp": [443], "udp": []}},
                 {"listeners": {"tcp": [], "udp": [443]}}, {"free_bytes": 1}]
        for change in cases:
            with self.subTest(change=change):
                data = self.data()
                data.update(change)
                with patch.object(r, "facts", return_value=data), patch.object(r, "fresh"), self.assertRaises(ValueError):
                    r.preflight({"cert": CERT, "source": self.source()})


class TLSValidationTests(Isolation):
    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL not on PATH")
    def test_real_tls_pair_and_mismatch(self):
        stage = self.root / "tls-stage"
        cert_dir = stage / CERT.lstrip("/")
        cert_dir.mkdir(parents=True)
        unit = stage / r.UNIT.lstrip("/")
        unit.parent.mkdir(parents=True)
        unit.write_text(UNIT_TEXT)
        app = stage / "usr/local/x-ui"
        (app / "bin").mkdir(parents=True)
        for p in (app / "x-ui", app / "bin/xray-linux-amd64"):
            p.write_bytes(b"fake executable; never run")
            p.chmod(0o700)
        db = stage / "etc/x-ui/x-ui.db"
        db.parent.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE clients (id INTEGER)")
            conn.commit()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(cert_dir / "key.pem"), "-out", str(cert_dir / "cert.pem"),
                        "-days", "1", "-subj", "/CN=sakha-ai.ru"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r.validate_payload(stage, CERT)
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(cert_dir / "key.pem")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.assertRaisesRegex(ValueError, "PEM"):
            r.validate_payload(stage, CERT)


if __name__ == "__main__":
    unittest.main()
