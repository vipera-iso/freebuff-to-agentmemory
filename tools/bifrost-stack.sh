#!/usr/bin/env bash
# bifrost-stack — quản lý Bifrost gateway (:8080) + codex-ns-shim (:8081) bằng 1 lệnh.
# Usage: bifrost-stack.sh {up|status|down|logs}
#   up     : bật service nào CHƯA chạy (idempotent — chạy lại vô hại)
#   status : in trạng thái + check health endpoints
#   down   : dừng cả 2 service
#   logs   : tail -f cả 2 file log (Ctrl+C để thoát)
set -u

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM_SCRIPT="$STACK_DIR/codex-ns-shim/start-shim.sh"
LOG_BIFROST="/tmp/bifrost.log"
LOG_SHIM="/tmp/codex-ns-shim.log"

c_green=$'\033[32m'; c_red=$'\033[31m'; c_yellow=$'\033[33m'; c_off=$'\033[0m'
ok()   { printf '%s[OK]%s %s\n'   "$c_green"  "$c_off" "$1"; }
bad()  { printf '%s[DOWN]%s %s\n' "$c_red"    "$c_off" "$1"; }
warn() { printf '%s[WARN]%s %s\n' "$c_yellow" "$c_off" "$1"; }

health() { curl -s -m 5 "$1" >/dev/null 2>&1; }

bifrost_up() {
  if health http://localhost:8080/health; then
    ok "Bifrost gateway :8080 (đã chạy sẵn)"
    return 0
  fi
  echo "..." "Đang bật Bifrost gateway :8080"
  setsid nohup bifrost > "$LOG_BIFROST" 2>&1 &
  for _ in $(seq 1 30); do
    health http://localhost:8080/health && { ok "Bifrost gateway :8080"; return 0; }
    sleep 1
  done
  bad "Bifrost không lên được — xem log: $LOG_BIFROST"
  return 1
}

shim_up() {
  if health http://127.0.0.1:8081/; then
    ok "codex-ns-shim :8081 (đã chạy sẵn)"
    return 0
  fi
  bash "$SHIM_SCRIPT" start >/dev/null 2>&1 || true
  for _ in $(seq 1 10); do
    health http://127.0.0.1:8081/ && { ok "codex-ns-shim :8081"; return 0; }
    sleep 1
  done
  bad "shim không lên được — xem log: $LOG_SHIM"
  return 1
}

status() {
  health http://localhost:8080/health && ok "Bifrost gateway :8080" || bad "Bifrost gateway :8080"
  health http://127.0.0.1:8081/      && ok "codex-ns-shim :8081" || bad "codex-ns-shim :8081"
}

case "${1:-up}" in
  up)
    bifrost_up
    shim_up
    ;;
  status) status ;;
  down)
    bash "$SHIM_SCRIPT" stop >/dev/null 2>&1 || true
    pkill -f "bin/bifrost" 2>/dev/null && warn "Đã dừng Bifrost" || warn "Bifrost không chạy"
    warn "Đã dừng shim"
    ;;
  logs) tail -f "$LOG_BIFROST" "$LOG_SHIM" ;;
  *) sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
