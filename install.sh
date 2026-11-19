#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p "$HOME/.local/bin"
if [ -e "$HOME/.local/bin/vps-migrator" ] || [ -L "$HOME/.local/bin/vps-migrator" ]; then
    echo 'Команда уже существует. Используйте python3 -m vps_migrator или проверьте её вручную.' >&2
    exit 1
fi
chmod u+x vps-migrator
ln -s "$(pwd)/vps-migrator" "$HOME/.local/bin/vps-migrator"
echo 'Готово: ~/.local/bin/vps-migrator --help'
