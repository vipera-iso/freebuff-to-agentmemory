#!/usr/bin/env bash
# bifrost-stack — quản lý Bifrost gateway (:8080) + codex-ns-shim (:8081) bằng 1 lệnh.
# Usage: bifrost-stack.sh {up|status|down|logs}
#   up     : bật service nào CHƯA chạy (idempotent — chạy lại vô hại)
#   status : in trạng thái + check health endpoints
#   down   : dừng cả 2 service
#   logs   : tail -f cả 2 file log (Ctrl+C để thoát)
#
# Ưu tiên systemd --user units (bifrost.service, codex-ns-shim.service —
# tự start lúc bật máy nhờ linger). Fallback: chạy tay bằng setsid nohup.
set -u

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM_SCRIPT="$STACK_DIR/codex-ns-shim/start-shim.sh"
LOG_BIFROST="/tmp/bifrost.log"
LOG_SHIM="/tmp/codex-ns-shim.log"
BIFROST_UNIT="bifrost.service"
SHIM_UNIT="codex-ns-shim.service"

c_green=$'\033[32m'; c_red=$'\033[31m'; c_yellow=$'\033[33m'; c_off=$'\033[0m'
ok()   { printf '%s[OK]%s %s\n'   "$c_green"  "$c_off" "$1"; }
bad()  { printf '%s[DOWN]%s %s\n' "$c_red"    "$c_off" "$1"; }
warn() { printf '%s[WARN]%s %s\n' "$c_yellow" "$c_off" "$1"; }

health() { curl -s -m 5 "$1" >/dev/null 2>&1; }
have_systemd() { command -v systemctl >/dev/null && systemctl --user cat "$BIFROST_UNIT" >/dev/null 2>&1; }

unit_up() { systemctl --user is-active "$1" &>/dev/null; }

bifrost_up() {
  if health http://localhost:8080/health; then
    ok "Bifrost gateway :8080 (đang chạy)"
    return 0
  fi
  echo "... Đang bật Bifrost gateway :8080"
  if have_systemd; then
    systemctl --user enable --now "$BIFROST_UNIT" >/dev/null 2>&1
    for _ in $(seq 1 30); do
      health http://localhost:8080/health && { ok "Bifrost gateway :8080 (systemd)"; return 0; }
      sleep 1
    done
  else
    setsid nohup bifrost > "$LOG_BIFROST" 2>&1 &
    for _ in $(seq 1 30); do
      health http://localhost:8080/health && { ok "Bifrost gateway :8080 (manual)"; return 0; }
      sleep 1
    done
  fi
  bad "Bifrost không lên được — xem log: $LOG_BIFROST / journalctl --user -u $BIFROST_UNIT"
  return 1
}

shim_up() {
  if health http://127.0.0.1:8081/; then
    ok "codex-ns-shim :8081 (đang chạy)"
    return 0
  fi
  echo "... Đang bật codex-ns-shim :8081"
  if have_systemd; then
    systemctl --user enable --now "$SHIM_UNIT" >/dev/null 2>&1
    for _ in $(seq 1 10); do
      health http://127.0.0.1:8081/ && { ok "codex-ns-shim :8081 (systemd)"; return 0; }
      sleep 1
    done
  else
    bash "$SHIM_SCRIPT" start >/dev/null 2>&1 || true
    for _ in $(seq 1 10); do
      health http://127.0.0.1:8081/ && { ok "codex-ns-shim :8081 (manual)"; return 0; }
      sleep 1
    done
  fi
  bad "shim không lên được — xem log: $LOG_SHIM"
  return 1
}

status() {
  if have_systemd; then
    unit_up "$BIFROST_UNIT" && health http://localhost:8080/health \
      && ok "Bifrost gateway :8080 (systemd: active)" \
      || bad "Bifrost gateway :8080 (systemd: $(systemctl --user is-active $BIFROST_UNIT))"
    unit_up "$SHIM_UNIT" && health http://127.0.0.1:8081/ \
      && ok "codex-ns-shim :8081 (systemd: active)" \
      || bad "codex-ns-shim :8081 (systemd: $(systemctl --user is-active $SHIM_UNIT))"
  else
    health http://localhost:8080/health && ok "Bifrost gateway :8080" || bad "Bifrost gateway :8080"
    health http://127.0.0.1:8081/      && ok "codex-ns-shim :8081" || bad "codex-ns-shim :8081"
  fi
}

case "${1:-up}" in
  up)
    bifrost_up
    shim_up
    ;;
  status) status ;;
  down)
    if have_systemd; then
      systemctl --user stop "$SHIM_UNIT" "$BIFROST_UNIT"
      warn "Đã dừng cả 2 unit (systemd)"
    else
      bash "$SHIM_SCRIPT" stop >/dev/null 2>&1 || true
      pkill -f "[b]in/bifrost" 2>/dev/null && warn "Đã dừng Bifrost" || warn "Bifrost không chạy"
      warn "Đã dừng shim"
    fi
    ;;
  logs) tail -f "$LOG_BIFROST" "$LOG_SHIM" ;;
  *) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
