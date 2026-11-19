import argparse
import contextlib
import json
import sqlite3
import stat
from unittest.mock import patch

from test_migrator import Isolation, CERT, UNIT_TEXT
from vps_migrator import cli, remote as r


class EnvironmentTests(Isolation):
    def test_standard_optional_environment_file_is_supported(self):
        unit = self.put("unit", (UNIT_TEXT + "EnvironmentFile=-/etc/default/x-ui\n").encode())
        # v0.1 rejects this known dependency instead of migrating it.
        try:
            r.validate_unit(unit)
        except ValueError as error:
            self.fail("Стандартный env должен поддерживаться: " + str(error))
        self.assertEqual(r.unit_environment(unit), ("/etc/default/x-ui", True))

    def test_whitespace_around_environment_assignment_cannot_bypass_validation(self):
        p = self.put("unit", (UNIT_TEXT + "EnvironmentFile = /etc/secrets\n").encode())
        with self.assertRaises(ValueError):
            r.validate_unit(p)

    def test_required_missing_environment_is_refused_and_optional_is_allowed(self):
        p = self.put("unit", (UNIT_TEXT + "EnvironmentFile=/etc/default/x-ui\n").encode())
        with self.assertRaisesRegex(ValueError, "обязательный"):
            r.source_environment(p)
        p.write_text(UNIT_TEXT + "EnvironmentFile = -/etc/default/x-ui\n")
        self.assertIsNone(r.source_environment(p))

    def test_multiple_environment_files_are_refused(self):
        p = self.put("unit", (UNIT_TEXT + "EnvironmentFile=/etc/default/x-ui\nEnvironmentFile=/etc/default/x-ui\n").encode())
        with self.assertRaises(ValueError):
            r.validate_unit(p)

    def test_parse_sqlite_values_without_evaluating_shell(self):
        p = self.put("env", b'# settings\nXUI_DB_TYPE="sqlite"\nXUI_DB_FOLDER=/etc/x-ui\nXUI_LOG_LEVEL=info\n')
        self.assertEqual(r.read_environment(p), {"XUI_DB_TYPE": "sqlite", "XUI_DB_FOLDER": "/etc/x-ui", "XUI_LOG_LEVEL": "info"})
        marker = self.root / "never-created"
        for text in ("XUI_DB_TYPE=postgres", "XUI_DB_FOLDER=/tmp/other", "XUI_DB_DSN=secret-token",
                     "XUI_DB_TYPE=$(touch " + str(marker) + ")", "XUI_LOG_LEVEL=`id`", "LD_PRELOAD=/tmp/evil"):
            with self.subTest(text=text.split("=")[0]):
                p.write_text(text)
                with self.assertRaises(ValueError) as err:
                    r.read_environment(p)
                self.assertNotIn("secret-token", str(err.exception))
                self.assertFalse(marker.exists())

    def test_environment_root_is_whitelisted(self):
        self.assertIn("etc/default/x-ui", r.roots(CERT, "/etc/default/x-ui"))
        self.assertTrue(r.allowed("etc/default/x-ui", CERT, "/etc/default/x-ui"))
        self.assertFalse(r.allowed("etc/default/x-ui/child", CERT, "/etc/default/x-ui"))
        with self.assertRaises(ValueError):
            r.roots(CERT, "/etc/shadow")

    def test_existing_target_environment_is_never_overwritten(self):
        p = self.put("/etc/default/x-ui", b"existing")
        with patch.object(r, "run", return_value="inactive"):
            with self.assertRaisesRegex(ValueError, "etc/default/x-ui"):
                r.fresh(CERT)
        self.assertEqual(p.read_bytes(), b"existing")

    def test_environment_participates_in_commit_and_rollback(self):
        job, state = self.staged()
        state["environment"] = "/etc/default/x-ui"
        env = job / "stage/etc/default/x-ui"
        env.parent.mkdir(parents=True)
        env.write_text("XUI_DB_TYPE=sqlite\n")
        manifest = r.read(job / "manifest.json")
        manifest["files"]["etc/default/x-ui"] = {"dir": False, "size": env.stat().st_size,
            "mode": stat.S_IMODE(env.stat().st_mode), "sha256": r.sha(env)}
        r.save(job / "manifest.json", manifest)
        with patch.object(r, "preflight"), patch.object(r, "validate_payload"), patch.object(r, "verify_summary"), patch.object(r, "run", return_value="inactive"):
            r.commit(job, state)
            self.assertEqual(r.path("/etc/default/x-ui").read_text(), "XUI_DB_TYPE=sqlite\n")
            r.rollback_job(job, state)
        self.assertFalse(r.path("/etc/default/x-ui").exists())
        self.assertTrue(env.is_file())


class DatabaseSummaryTests(Isolation):
    def database(self, settings='{"clients":[{"id":"private-uuid"},{"email":"private-email"}]}', nodes=0):
        p = self.root / "snapshot.db"
        with contextlib.closing(sqlite3.connect(p)) as conn:
            conn.executescript("CREATE TABLE inbounds(id INTEGER, settings TEXT); CREATE TABLE users(id INTEGER); CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE nodes(id INTEGER);")
            conn.execute("INSERT INTO inbounds VALUES(1,?)", (settings,))
            conn.execute("INSERT INTO users VALUES(1)")
            conn.execute("INSERT INTO settings VALUES('secret','never-print')")
            for i in range(nodes):
                conn.execute("INSERT INTO nodes VALUES(?)", (i,))
            conn.commit()
        return p

    def test_counts_include_json_clients_but_no_secrets(self):
        result = r.database_summary(self.database())
        self.assertEqual(result, {"inbounds": 1, "users": 1, "settings": 1, "clients": 2, "nodes": 0})
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("never-print", json.dumps(result))

    def test_multi_node_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "узл"):
            r.database_summary(self.database(nodes=1))

    def test_broken_client_json_is_not_silently_counted_as_zero(self):
        with self.assertRaises(ValueError) as error:
            r.database_summary(self.database(settings="private-broken-json"))
        self.assertNotIn("private-broken-json", str(error.exception))

    def test_mismatched_snapshot_summary_blocks_restore(self):
        p = self.database()
        with self.assertRaisesRegex(ValueError, "совпад"):
            r.verify_summary(p, {"inbounds": 900, "users": 1, "settings": 1, "clients": 2, "nodes": 0})


class WizardTests(Isolation):
    def arguments(self):
        return argparse.Namespace(identity=None, known_hosts=None)

    def test_wizard_command_is_available(self):
        try:
            result = cli.parser().parse_args(["wizard"])
        except SystemExit:
            self.fail("Нужна команда wizard")
        self.assertEqual(result.command, "wizard")

    def drive(self, replies):
        from vps_migrator.wizard import run
        commands = []
        def execute(args):
            commands.append(args)
            return "11111111-1111-4111-8111-111111111111" if args.command == "migrate" and args.apply else None
        with patch("builtins.input", side_effect=replies), contextlib.redirect_stdout(__import__("io").StringIO()):
            run(self.arguments(), execute)
        return commands

    def test_empty_first_confirmation_performs_only_dry_run(self):
        commands = self.drive(["192.0.2.1", "192.0.2.2", "", "", "", ""])
        self.assertEqual([a.command for a in commands], ["migrate"])
        self.assertFalse(commands[0].apply)

    def test_stop_after_staging_does_not_commit(self):
        commands = self.drive(["192.0.2.1", "192.0.2.2", "", "", "", "192.0.2.2", ""])
        self.assertEqual([a.command for a in commands], ["migrate", "migrate"])
        self.assertTrue(commands[-1].apply)

    def test_full_flow_requires_separate_start_permission(self):
        commands = self.drive(["192.0.2.1", "192.0.2.2", "", "", "", "192.0.2.2", "192.0.2.2", "192.0.2.2", "ЗАПУСТИТЬ"])
        self.assertEqual([a.command for a in commands], ["migrate", "migrate", "commit", "activate", "health"])
        self.assertTrue(commands[3].accept_source_code)
        self.assertTrue(all(a.confirm == "192.0.2.2" for a in commands[1:4]))

    def test_eof_never_advances_to_staging(self):
        commands = self.drive(["192.0.2.1", "192.0.2.2", "", "", "", EOFError()])
        self.assertEqual([a.command for a in commands], ["migrate"])

    def test_declining_code_execution_never_starts_service(self):
        commands = self.drive(["192.0.2.1", "192.0.2.2", "", "", "", "192.0.2.2", "192.0.2.2", "192.0.2.2", ""])
        self.assertEqual([a.command for a in commands], ["migrate", "migrate", "commit"])
