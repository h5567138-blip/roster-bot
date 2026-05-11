#!/usr/bin/env python3

import threading
from flask import Flask
import discord
from discord.ext import commands
import json
import os
from datetime import datetime

# Flask for keeping port open
app = Flask(__name__)
@app.route('/')
def home():
    return 'Bot is running!'

def run_flask():
    app.run(host='0.0.0.0', port=10000)

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

# Test hook - bot should respond to any message starting with !
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith("!"):
        await message.channel.send("🤖 排班機器人回應中...")
    await bot.process_commands(message)

@bot.command()
async def help_cmd(message):
    embed = discord.Embed(title="📋 排班機器人指令", color=0x00ff00)
    embed.add_field(name="成員指令", value="!register [ID] [職業] [戰力] - 報名\n!leave [ID] - 請假", inline=False)
    embed.add_field(name="幹部指令", value="!add [ID] [職業] [戰力] - 手動加入\n!team [ID] [隊伍] - 分隊\n!remove [ID] - 移除", inline=False)
    embed.add_field(name="查看指令", value="!list - 成員列表\n!output - 產出名單", inline=False)
    await message.channel.send(embed=embed)

@bot.command()
async def register(ctx, id: str, job: str, power: int):
    data = load_data()
    data["members"][id] = {"job": job, "power": power, "status": "報名", "team": None}
    save_data(data)
    await ctx.send(f"✅ OK {id} - {job} 戰力{power}")

@bot.command()
async def register_cn(ctx, id: str, job: str, power: int):
    data = load_data()
    data["members"][id] = {"job": job, "power": power, "status": "報名", "team": None}
    save_data(data)
    await ctx.send(f"✅ OK {id} - {job} 戰力{power}")

@bot.command()
async def leave(ctx, id: str):
    data = load_data()
    if id in data["members"]:
        data["members"][id]["status"] = "請假"
        save_data(data)
        await ctx.send(f"✅ OK {id} 已請假")
    else:
        await ctx.send(f"❌ 找不到 {id}")

@bot.command()
async def add(ctx, id: str, job: str, power: int):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 幹部專用")
        return
    data = load_data()
    data["members"][id] = {"job": job, "power": power, "status": "報名", "team": None}
    save_data(data)
    await ctx.send(f"✅ 已加入 {id}")

@bot.command()
async def team(ctx, id: str, team: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 幹部專用")
        return
    data = load_data()
    if id in data["members"]:
        data["members"][id]["team"] = team
        save_data(data)
        await ctx.send(f"✅ {id} -> 第{team}隊")
    else:
        await ctx.send(f"❌ 找不到 {id}")

@bot.command()
async def remove(ctx, id: str):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 幹部專用")
        return
    data = load_data()
    if id in data["members"]:
        del data["members"][id]
        save_data(data)
        await ctx.send(f"✅ 已移除 {id}")
    else:
        await ctx.send(f"❌ 找不到 {id}")

@bot.command()
async def list_members(ctx):
    data = load_data()
    msg = "📋 成員列表:\n"
    for id, info in data["members"].items():
        team = info.get("team") or "未"
        msg += f"- {id} | {info['job']} | 戰{info['power']} | {info['status']} | [{team}]\n"
    await ctx.send(msg)

@bot.command()
async def output(ctx):
    data = load_data()
    today = datetime.now().strftime("%Y/%m/%d")
    msg = f"=== 隊伍名單 {today} ===\n\n"
    
    teams = {}
    leave_list = []
    for id, info in data["members"].items():
        if info["status"] == "請假":
            leave_list.append(f"{id}({info['job']})")
        elif info.get("team"):
            t = info["team"]
            if t not in teams:
                teams[t] = []
            teams[t].append(f"{id} | {info['job']} | 戰{info['power']}")
    
    for t in sorted(teams.keys()):
        msg += f"【第{t}隊】\n"
        for m in teams[t]:
            msg += f"  {m}\n"
        msg += "\n"
    
    if leave_list:
        msg += "請假: " + ", ".join(leave_list)
    
    await ctx.send(msg)

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)

if __name__ == "__main__":
    # Start Flask in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    # Run bot
    bot.run(TOKEN)
