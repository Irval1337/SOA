#!/bin/sh
[ -f /tmp/sentinel.conf ] || cp /etc/redis/sentinel.conf /tmp/sentinel.conf
redis-sentinel /tmp/sentinel.conf &
pid=$!
trap 'kill -TERM $pid' TERM INT

if [ -n "$SENTINEL_TILT_PERIOD_MS" ]; then
    until redis-cli -p 26379 ping >/dev/null 2>&1; do sleep 0.2; done
    redis-cli -p 26379 sentinel debug tilt-period "$SENTINEL_TILT_PERIOD_MS" >/dev/null
fi

wait $pid
