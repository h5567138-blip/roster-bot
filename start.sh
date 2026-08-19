#!/bin/bash
cd "$(dirname "$0")"
export DISCORD_TOKEN=$DISCORD_TOKEN  # 從環境變數讀取
nohup python3 bot.py > bot.log 2>&1 &
echo "Discord Bot 已啟動"