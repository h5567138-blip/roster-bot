#!/usr/bin/env python3
"""
Discord 排班機器人
"""

import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime

# ===== 設定 =====
TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = "roster_data.json"

intents = discord.Intents(message_content=True)
bot = commands.Bot(command_prefix="!", intents=intents)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"members": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@bot.command()
async def register(ctx, id: str, 職業: str, 戰力: int):
    data = load_data()
    data["members"][id] = {"職業": 職業, "戰力": 戰力, "狀態": "報名", "隊伍": None}
    save_data(data)
    await ctx.send(f"OK {id} - {職業} 戰力{戰力}")

@bot.command()
async def leave(ctx, id: str):
    data = load_data()
    if id in data["members"]:
        data["members"][id]["狀態"] = "請假"
        save_data(data)
        await ctx.send(f"OK {id} 已請假")
    else:
        await ctx.send(f"Error 找不到 {id}")

@bot.command()
async def add(ctx, id: str, 職業: str, 戰力: int):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("Error 幹部專用")
        return
    data = load_data()
    data["members"][id] = {"職業": 職業, "戰力": 戰力, "狀態": "報名", "隊伍": None}
    save_data(data)
    await ctx.send(f"OK 已加入 {id}")

@bot.command()
async def team(ctx, id: str, 隊伍: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("Error 幹部專用")
        return
    data = load_data()
    if id in data["members"]:
        data["members"][id]["隊伍"] = 隊伍
        save_data(data)
        await ctx.send(f"OK {id} -> 第{隊伍}隊")
    else:
        await ctx.send(f"Error 找不到 {id}")

@bot.command()
async def remove(ctx, id: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("Error 幹部專用")
        return
    data = load_data()
    if id in data["members"]:
        del data["members"][id]
        save_data(data)
        await ctx.send(f"OK 已移除 {id}")
    else:
        await ctx.send(f"Error 找不到 {id}")

@bot.command()
async def list_members(ctx):
    data = load_data()
    msg = "成員列表:\n"
    for id, info in data["members"].items():
        隊 = info.get("隊伍") or "未"
        msg += f"- {id} | {info['職業']} | 戰{info['戰力']} | {info['狀態']} | [{隊}]\n"
    await ctx.send(msg)

@bot.command()
async def output(ctx):
    data = load_data()
    today = datetime.now().strftime("%Y/%m/%d")
    msg = f"=== 隊伍名單 {today} ===\n\n"
    
    teams = {}
    請假 = []
    for id, info in data["members"].items():
        if info["狀態"] == "請假":
            請假.append(f"{id}({info['職業']})")
        elif info.get("隊伍"):
            t = info["隊伍"]
            if t not in teams:
                teams[t] = []
            teams[t].append(f"{id} | {info['職業']} | 戰{info['戰力']}")
    
    for t in sorted(teams.keys()):
        msg += f"【第{t}隊】\n"
        for m in teams[t]:
            msg += f"  {m}\n"
        msg += "\n"
    
    if 請假:
        msg += "請假: " + ", ".join(請假)
    
    await ctx.send(msg)

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)