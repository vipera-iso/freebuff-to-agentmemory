#!/usr/bin/env bash
# codex-ns-shim launcher — start/stop/status the local namespace-flattening proxy.
# Codex config points at http://localhost:8081 ; this forwards to Bifrost on 8080.
# Usage: start-shim.sh [start|stop|status|restart]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="/tmp/codex-ns-shim.pid"
LOG="/tmp/codex-ns-shim.log"
UPSTREAM="${SHIM_UPSTREAM:-http://127.0.0.1:8080/openai/v1}"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-start}" in
  start)
    if running; then echo "shim already running (pid $(cat "$PIDFILE"))"; exit 0; fi
    # setsid: tách session riêng để shim không bị giết cùng process group của shell
    setsid nohup python3 "$DIR/shim.py" >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 0.5
    if running; then
      echo "shim started: pid $(cat "$PIDFILE") (log: $LOG)"
    else
      echo "shim failed to start; log tail:"; tail -5 "$LOG"; exit 1
    fi
    ;;
  stop)
    if running; then
      kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE" && echo "shim stopped"
    else
      echo "shim not running"; rm -f "$PIDFILE"
    fi
    ;;
  status)
    if running; then
      echo "shim running (pid $(cat "$PIDFILE"))"
      curl -s -X HEALTH "http://127.0.0.1:8081/" && echo
    else
      echo "shim not running"; exit 1
    fi
    ;;
  restart) "$0" stop; "$0" start ;;
  *) echo "usage: $0 [start|stop|status|restart]"; exit 2 ;;
esac
