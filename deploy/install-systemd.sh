#!/usr/bin/env bash
# systemd にボットをサービスとして登録するスクリプト。
# /opt/shibosei-bot に配置済みで、.env を設定済みであることが前提。
set -euo pipefail

APP_DIR=/opt/shibosei-bot
SERVICE_NAME=shibosei-bot

if [[ $EUID -ne 0 ]]; then
  echo "root で実行してください: sudo $0"
  exit 1
fi

if ! id shibosei &>/dev/null; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin shibosei
fi

chown -R shibosei:shibosei "$APP_DIR"
touch /var/log/${SERVICE_NAME}.log
chown shibosei:shibosei /var/log/${SERVICE_NAME}.log

install -m 644 "$APP_DIR/deploy/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

systemctl status "$SERVICE_NAME" --no-pager
