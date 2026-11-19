from pathlib import Path

V = Path(r"D:\Obsidian\AI")
P = V / "Проекты" / "VPS Migrator"


def patch(p, old, new):
    t = p.read_bytes().decode("utf-8")
    assert t.count(old) == 1, (p.name, old[:60])
    p.write_bytes(t.replace(old, new).encode("utf-8"))


def append(p, text):
    t = p.read_bytes().decode("utf-8")
    p.write_bytes((t.rstrip("\n") + "\n" + text).encode("utf-8"))


append(P / "Переписка" / "Разработка VPS Migrator.md", """
## 2026-11-09 12:40 (+09:00, время записи) — Claude Code

Да, исправь

Контекст: ответ на вопрос «Исправить поддержку стандартного unit 3x-ui?».
""")

patch(P / "О проекте.md",
      """## Стадия
MVP 0.2.0: реализовано, юнит-тесты проходят (43, 1 пропущен на Windows). Проверка на реальных VPS по `TESTING.md` не выполнялась. Не опубликовано.

## Главная проблема (2026-11-09)
Текущий стандартный unit 3x-ui (`x-ui.service.debian` в MHSanaei/3x-ui) не проходит проверку утилиты: в нём есть `ExecReload`, `Environment=`, `KillMode=mixed`, а install.sh добавляет drop-in `x-ui.service.d/10-xui-sandbox.conf`. Свежая установка 3x-ui будет отклонена. См. [[Проекты/VPS Migrator/Задачи]].

## Следующий шаг
Решить, как поддержать стандартный unit 3x-ui, затем первый коммит и проверка на двух тестовых VPS.""",
      """## Стадия
MVP 0.2.0 плюс невыпущенная доработка: поддержан стандартный unit 3x-ui и его sandbox drop-in (2026-11-09). Реализовано и проверено тестами: 55 юнит-тестов (Windows и WSL Ubuntu, Python 3.12/3.14) и root-smoke в WSL для двух сценариев. Проверка на реальных VPS по `TESTING.md` не выполнялась. Коммитов нет, не опубликовано, новая сборка `.pyz/.zip` не делалась.

## Следующий шаг
Первый коммит, затем проверка на двух тестовых VPS со свежей установкой 3x-ui.""")

patch(P / "Техническая спецификация.md",
      """## Ограничения unit (remote.py `validate_unit`)
Только один `ExecStart=/usr/local/x-ui/x-ui`, без других `Exec*`; без `Environment=`; `KillMode` только `control-group`; без drop-ins; `EnvironmentFile` только `/etc/default/x-ui`. Стандартный unit 3x-ui сейчас этим условиям не соответствует.""",
      """## Ограничения unit (remote.py `validate_unit`, `validate_dropin`, `dropin_source`)
- Один `ExecStart=/usr/local/x-ui/x-ui`; из прочих `Exec*` допустим только `ExecReload=/bin/kill -USR1 $MAINPID` (или `/usr/bin/kill`).
- `Environment=` только `"XRAY_VMESS_AEAD_FORCED=false"`; `EnvironmentFile` только `/etc/default/x-ui`.
- `KillMode` — `control-group` или `mixed`; `SendSIGKILL=yes`; пользователь root.
- Drop-in только `/etc/systemd/system/x-ui.service.d/10-xui-sandbox.conf` (его генерирует install.sh/update.sh 3x-ui): секция `[Service]`, ключи `ReadWritePaths`/`ReadWriteDirectories` со стандартными каталогами, `SystemCallFilter=@system-service`, `SystemCallErrorNumber=EPERM`. Переносится как отдельный корень, участвует в commit/rollback; при `SystemCallFilter` preflight требует systemd 239+ на цели (`facts()["systemd"]`).
- Стандартный upstream `x-ui.service.debian` проходит проверку (сверено 2026-11-09).""")

patch(P / "Техническая спецификация.md",
      """- `python -m unittest discover -s tests` — 43 теста, OK (Python 3.12 и 3.14 с `-W error`, Windows; 1 тест пропущен).""",
      """- `python -m unittest discover -s tests` — 55 тестов, OK: Windows (Python 3.12 и 3.14 с `-W error`, 1 тест пропущен) и WSL Ubuntu от root (без пропусков).
- `sudo python3 tools/integration_smoke.py` в WSL Ubuntu — PASS для сценариев «минимальный unit» и «стандартный unit 3x-ui + drop-in».""")

patch(P / "Задачи.md",
      """- [ ] Решить, поддерживать ли стандартный unit 3x-ui: `ExecReload=/bin/kill -USR1 $MAINPID`, `Environment="XRAY_VMESS_AEAD_FORCED=false"`, `KillMode=mixed`, drop-in `x-ui.service.d/10-xui-sandbox.conf`. Сейчас такой источник отклоняется (подтверждено проверкой `validate_unit` 2026-11-09).
""", "")
patch(P / "Задачи.md",
      """- [ ] Протокол из `TESTING.md` на двух одноразовых VPS.""",
      """- [ ] Протокол из `TESTING.md` на двух одноразовых VPS, включая свежую установку 3x-ui с drop-in.
- [ ] После проверки — версия 0.2.1/0.3.0 и новая сборка `.pyz/.zip`.""")
patch(P / "Задачи.md",
      """## Завершено
""", """## Завершено
- [x] 2026-11-09 — поддержан стандартный unit 3x-ui и drop-in `10-xui-sandbox.conf`; тесты 55 OK, smoke в WSL PASS. Не закоммичено.
""")

append(P / "Решения.md", """- 2026-11-09 — Стандартный unit 3x-ui поддерживается по точному белому списку строк, а не ослаблением проверок: разрешены только известные `ExecReload`, `Environment`, `KillMode=mixed` и один сгенерированный drop-in с проверяемым содержимым. Причина: иначе утилита отклоняет любую свежую установку 3x-ui, а общий разбор systemd слишком рискован.
""")

j = V / "Журнал изменений.md"
t = j.read_bytes().decode("utf-8")
head = "# Журнал изменений\n\n"
assert t.startswith(head)
line = ("- 2026-11-09 12:40 +09:00 — Claude Code — VPS Migrator: поддержан стандартный unit 3x-ui и sandbox drop-in; "
        "55 тестов OK, root-smoke в WSL PASS; не закоммичено. [[Проекты/VPS Migrator/Техническая спецификация]]\n\n")
j.write_bytes((head + line + t[len(head):]).encode("utf-8"))
print("ok")
