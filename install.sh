#!/usr/bin/env bash
# First-time setup. Run from the project folder: bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
sed -e "s|^User=.*|User=$(id -un)|" -e "s|^ExecStart=.*|ExecStart=/usr/bin/python3 $DIR/companion.py|" \
  "$DIR/klipper-companion.service" | sudo tee /etc/systemd/system/klipper-companion.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now klipper-companion
ASVC="$HOME/printer_data/moonraker.asvc"
if [ -f "$ASVC" ] && ! grep -qx klipper-companion "$ASVC"; then echo klipper-companion >> "$ASVC"; fi
echo
echo "Running at http://$(hostname -I | awk '{print $1}'):8099"
echo "For one-click updates, add this to moonraker.conf, then restart Moonraker:"
cat <<CONF

[update_manager klipper-companion]
type: git_repo
path: $DIR
origin: https://github.com/WTFisjuice/klipper-companion.git
primary_branch: main
managed_services: klipper-companion
CONF
