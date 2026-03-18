#!/bin/bash
# Restart flow_server.py - called by deploy
pkill -f flow_server.py 2>/dev/null || true
sleep 1
export DISPLAY=:99
cd /root
nohup /root/flow_env/bin/python3 /root/flow_server.py > /root/flow_server.log 2>&1 &
echo "Server PID: $!"
