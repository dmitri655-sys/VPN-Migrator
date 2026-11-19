# История изменений

## Не выпущено

* Поддержка стандартного unit 3x-ui (`x-ui.service.debian`): по точному белому списку разрешены `ExecReload=/bin/kill -USR1 $MAINPID`, `Environment="XRAY_VMESS_AEAD_FORCED=false"`, `KillMode=mixed`. Раньше такая установка отклонялась.
* Перенос сгенерированного 3x-ui drop-in `x-ui.service.d/10-xui-sandbox.conf`: проверка содержимого, отдельный корень в manifest, commit и rollback. Другие drop-ins по-прежнему запрещены. При `SystemCallFilter` preflight требует на цели systemd 239+.
* Linux smoke дополнен сценарием «стандартный unit + drop-in».

## 0.2.0 — 30 октября 2026

* Русская команда `wizard`: план, staging, commit, запуск с отдельными подтверждениями; Enter/EOF не разрешает запись.
* Поддержка явно указанного в unit `/etc/default/x-ui` (включая необязательный вариант). Только безопасный поднабор SQLite-переменных, без исполнения shell. Env-файл переносится с 0600 и участвует в общем rollback.
* Количества inbounds/users/settings/JSON-клиентов берутся из самого SQLite snapshot. Проверка после передачи и перед commit; непустая таблица nodes блокирует неподдерживаемый multi-node.
* Исправлена ошибка 0.1: manifest сохранял биты типа файла из gettarinfo, хотя tar сериализует только mode permissions. Linux export→stage smoke воспроизвёл ошибку и подтвердил исправление.
* Закрыт обход проверки EnvironmentFile через пробелы вокруг `=`.
* Добавлен Linux smoke-тест с настоящими SQLite WAL, OpenSSL, tar, root ownership, 0600, flock и rename. Systemd и факты о VPS в smoke подменяются; реальные службы не запускаются.

## 0.1.0 — 24 октября 2026

Первоначальный CLI с discovery, SQLite backup, SSH-передачей, staging и обратимым размещением на чистой цели. Реального прогона Linux pipeline в той сборке не было. Для нового переноса используйте 0.2.0.
