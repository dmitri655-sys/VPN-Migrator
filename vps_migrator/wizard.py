"""Guided UI over the existing CLI. No credentials or separate mutation path."""
from .transport import host


def run(options, execute):
    from .cli import parser

    def command(*parts):
        common = []
        if options.identity:
            common += ["--identity", options.identity]
        if options.known_hosts:
            common += ["--known-hosts", options.known_hosts]
        return execute(parser().parse_args(common + list(parts)))

    def confirmation(target, text):
        return input(text + "\nПовторите IP новой VPS (Enter — остановиться): ").strip() == target

    print("VPS Migrator — пошаговый перенос 3x-ui")
    print("Подготовьте проверенные SSH host keys. Пароль при необходимости спросит OpenSSH.")
    print("Источник продолжит работать. AWG2, DNS и firewall не меняются.")
    try:
        source = host(input("IP старой VPS: ").strip())
        target = host(input("IP новой VPS: ").strip())
        if source == target:
            raise ValueError("Нужны разные VPS")
        source_port = str(int(input("SSH-порт старой VPS [22]: ").strip() or "22"))
        target_port = str(int(input("SSH-порт новой VPS [22]: ").strip() or "22"))
        if not all(1 <= int(p) <= 65535 for p in (source_port, target_port)):
            raise ValueError("SSH-порты должны быть в диапазоне 1–65535")
        cert = input("TLS-каталог [/root/cert/sakha-ai.ru]: ").strip() or "/root/cert/sakha-ai.ru"
        migration = ["migrate", "--source", source, "--target", target,
                     "--source-port", source_port, "--target-port", target_port, "--cert-dir", cert]
        print("\nШаг 1. Проверка и план без записи")
        command(*migration)
        if not confirmation(target, "\nШаг 2. Создать резервную копию и staging на новой VPS?"):
            return
        job = command(*migration, "--apply", "--confirm", target)
        if not job:
            raise RuntimeError("Не получен UUID задания; проверьте status")
        print("\nСохраните UUID задания: " + job)
        print("Продолжение: commit/activate/status/rollback " + target + " " + job + " --port " + target_port)
        if not confirmation(target, "\nШаг 3. Разместить файлы? Служба ещё не запускается."):
            return
        command("commit", target, job, "--port", target_port, "--apply", "--confirm", target)
        print("Проверьте перенесённые настройки: старый bind IP, TLS-пути, внешние зависимости.")
        if not confirmation(target, "\nШаг 4. Запустить код исходной установки от root и её слушатели?"):
            return
        if input("Для разрешения запуска напишите ЗАПУСТИТЬ (Enter — остановиться): ").strip() != "ЗАПУСТИТЬ":
            return
        command("activate", target, job, "--port", target_port, "--apply", "--confirm", target, "--accept-source-code")
        command("health", target, job, "--port", target_port)
        print("Проверьте каждый протокол внешним клиентом. Автозапуск и DNS включаются отдельно.")
    except EOFError:
        print("Ввод закрыт. Следующий этап не выполняется; сохранённое задание можно продолжить через CLI.")
