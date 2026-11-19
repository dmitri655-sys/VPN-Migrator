# Guided migration implementation plan

> **For agentic workers:** use superpowers:executing-plans; implementation remains in this session.

**Goal:** русский мастер и проверяемый перенос стандартного SQLite env-файла и данных.
**Architecture:** существующий SSH worker остаётся самостоятельным; новый wizard вызывает существующий CLI execute, а не дублирует опасные операции.
**Tech Stack:** Python >=3.9, stdlib, OpenSSH, SQLite, OpenSSL.
**Spec:** ../specs/2026-10-30-guided-migration-design.md

## Global constraints

Dry-run по умолчанию; отдельное подтверждение для записи и запуска; источник работает; никаких сетевых правок, PostgreSQL или AWG2. Секреты не выводятся. Существующие пути цели не перезаписываются.

## Review focus

* EOF/пустой ответ в мастере никогда не разрешает следующую запись.
* Парсер env не исполняет подстановки и не выдаёт секретные значения при ошибке.
* Env участвует в rollback после сбоя между перемещениями корней.
* Counts получены из снимка, а не живой меняющейся БД.
* Linux tests работают в Linux tmpfs/ext4, а не DrvFS Windows с иными правами.

## Tasks

- [x] 1. Env support: tests/test_guided.py — failing tests для optional/required EnvironmentFile, валидации значений, target conflict и rollback пятого корня; remote.py — env_settings, unit_environment, optional roots/environment. Run unittest.
- [x] 2. Snapshot checks: failing tests для counts, JSON client list, nodes и несоответствия; remote.py — database_summary, verify_summary; export/stage/commit — проверка manifest. Run unittest.
- [x] 3. Wizard: failing tests для отказов на каждом этапе и полного сценария; vps_migrator/wizard.py — run(namespace, execute); cli.py — wizard command, return migration UUID; tests — реальные CLI args с подменой только SSH boundary. Run unittest.
- [x] 4. Linux verification: tools/integration_smoke.py создаёт синтетическую установку под tempfile, выполняет export/unpack/validation/commit/rollback, systemctl заменён узкой функцией. Запустить Windows+Ubuntu tests и smoke; честно отразить неиспытанные реальные VPS.
- [x] 5. Release 0.2.0: README, CHANGELOG, VALIDATION, pyproject, tools/build_release.py, быстрый старт. Повторить тесты, zip checksums/resources/help, воспроизводимость. Независимое review и исправление существенных замечаний.

## Проверка 30 октября 2026

Windows: 43 теста, один Linux-only пропуск. Ubuntu WSL/root: 43 без пропусков и полный синтетический export→stage→commit→rollback. Независимый reviewer: env-whitespace исправлен, Windows controller вне согласованного Linux-only scope. Исходные VPS не затрагивались. ZIP/PYZ проверяются отдельным завершающим прогоном.
