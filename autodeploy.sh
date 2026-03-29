#!/bin/bash
# autodeploy.sh — каждые 2 минуты стягивает изменения с GitHub и перезапускает сервис
#
# Запуск в фоне:  nohup ./autodeploy.sh > autodeploy.log 2>&1 &
# Остановка:      pkill -f autodeploy.sh
#
# Ветку для отслеживания задай переменной:
#   AUTODEPLOY_BRANCH=main nohup ./autodeploy.sh > autodeploy.log 2>&1 &

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${AUTODEPLOY_BRANCH:-main}"
INTERVAL=120  # секунд между проверками

cd "$SCRIPT_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [autodeploy] $1"
}

restart_service() {
    log "Перезапускаю openclaw..."

    # Останавливаем текущий процесс
    OLD_PID=$(pgrep -f "python.*main\.py" | head -1)
    if [ -n "$OLD_PID" ]; then
        kill "$OLD_PID" 2>/dev/null
        sleep 3
        # Принудительно если не остановился
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi

    # Запускаем снова
    source "$SCRIPT_DIR/venv/bin/activate"
    nohup python "$SCRIPT_DIR/main.py" >> "$SCRIPT_DIR/openclaw.log" 2>&1 &
    NEW_PID=$!
    log "openclaw запущен (PID: $NEW_PID)"
}

log "Автодеплой запущен. Ветка: $BRANCH, интервал: ${INTERVAL}с"
log "Директория: $SCRIPT_DIR"

while true; do
    # Фетчим удалённые изменения
    git fetch origin "$BRANCH" --quiet 2>/dev/null

    LOCAL=$(git rev-parse HEAD 2>/dev/null)
    REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null)

    if [ -z "$REMOTE" ]; then
        log "WARN: ветка origin/$BRANCH не найдена"
    elif [ "$LOCAL" != "$REMOTE" ]; then
        log "Новые изменения: ${LOCAL:0:7} → ${REMOTE:0:7}"

        git pull origin "$BRANCH" --quiet
        if [ $? -eq 0 ]; then
            log "Pull успешен"
            restart_service
        else
            log "ОШИБКА: git pull завершился с ошибкой"
        fi
    else
        log "Нет изменений (HEAD: ${LOCAL:0:7})"
    fi

    sleep $INTERVAL
done
