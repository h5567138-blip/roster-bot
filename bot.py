#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
德德機器人 - 玩家自助 / 排班組隊 / 班表產圖 / 抽獎 整合復原版
依 2026-08-14 現有 roster_data.json / lottery_data.json 結構復原。
"""

import os
import json
import threading
import uuid
import asyncio
import io
import random
from pathlib import Path
from datetime import datetime

import discord
from discord import ui
from discord.ext import commands
from flask import Flask

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

try:
    from lottery import LotteryCreateView, LotteryEntryView, lottery_history_embed
except Exception:
    LotteryCreateView = None

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "roster_data.json"
LOTTERY_FILE = BASE_DIR / "lottery_data.json"
TIPS_FILE = BASE_DIR / "tips_data.json"
TIPS_IMAGE_DIR = BASE_DIR / "tips_images"
TIPS_IMAGE_DIR.mkdir(exist_ok=True)
GENERATED_DIR = BASE_DIR / "generated"
GENERATED_DIR.mkdir(exist_ok=True)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    token_file = BASE_DIR / ".token"
    if token_file.exists():
        TOKEN = token_file.read_text(encoding="utf-8").strip()

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ---------- JSON ----------
def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def load_data():
    if DATA_FILE.exists():
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("guilds", {})
    data.setdefault("members", {})
    data.setdefault("bound_members", {})
    data.setdefault("unbound_members", {})
    return data

def save_data(data):
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)

def get_guild_data(data, guild_id):
    gid = str(guild_id)
    g = data["guilds"].setdefault(gid, {})
    g.setdefault("bound_members", {})
    g.setdefault("unbound_members", {})
    g.setdefault("teams", {})
    g.setdefault("leave", [])
    g.setdefault("left_members", {})
    g.setdefault("team_board_layout", [])
    return g

def is_admin(user):
    return user.guild_permissions.administrator or user.guild_permissions.manage_guild

def resolve_slot(g, slot):
    if not slot or not isinstance(slot, dict):
        return None
    typ = slot.get("type")
    key = str(slot.get("key", ""))
    if typ == "bound":
        info = g.get("bound_members", {}).get(key)
        if not info:
            return None
        return {
            "type": typ, "key": key,
            "game_id": info.get("game_id", "未設定"),
            "job": info.get("manual_job") or info.get("job") or "未設定",
            "power": int(info.get("power", 0) or 0)
        }
    if typ == "unbound":
        info = g.get("unbound_members", {}).get(key)
        if not info:
            return None
        return {
            "type": typ, "key": key,
            "game_id": info.get("game_id", key),
            "job": info.get("manual_job") or info.get("job") or "未設定",
            "power": int(info.get("power", 0) or 0)
        }
    return None

# ---------- 玩家 ----------
JOB_ROLE_PREFIX = "職業-"


def get_server_job_roles(guild: discord.Guild):
    """
    動態取得伺服器內所有「職業-XX」身分組。
    依 Discord 身分組位置由高到低排序。
    """
    roles = [
        role for role in guild.roles
        if role.name.startswith(JOB_ROLE_PREFIX)
        and role.name != JOB_ROLE_PREFIX
        and not role.is_default()
    ]
    roles.sort(key=lambda r: r.position, reverse=True)
    return roles


class DynamicJobRoleSelect(ui.Select):
    def __init__(self, guild: discord.Guild, page: int = 0, per_page: int = 25):
        self.guild_id = guild.id
        self.page = page
        self.per_page = per_page

        roles = get_server_job_roles(guild)
        self.total = len(roles)
        self.pages = max(1, (self.total + per_page - 1) // per_page)
        self.page = max(0, min(page, self.pages - 1))

        start = self.page * per_page
        current = roles[start:start + per_page]

        if current:
            options = [
                discord.SelectOption(
                    label=role.name,
                    value=str(role.id),
                    description=f"領取 / 更新為 {role.name}"[:100]
                )
                for role in current
            ]
            disabled = False
        else:
            options = [
                discord.SelectOption(
                    label="目前沒有職業身分組",
                    value="__none__"
                )
            ]
            disabled = True

        super().__init__(
            placeholder="選擇職業身分",
            min_values=1,
            max_values=1,
            options=options,
            disabled=disabled,
            custom_id=f"player_dynamic_job_select_{self.page}"
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await interaction.response.send_message(
                "❌ 目前伺服器沒有 `職業-XX` 身分組。",
                ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ 此功能只能在伺服器內使用。",
                ephemeral=True
            )
            return

        selected_role = guild.get_role(int(self.values[0]))
        if selected_role is None or not selected_role.name.startswith(JOB_ROLE_PREFIX):
            await interaction.response.send_message(
                "❌ 找不到選擇的職業身分組，請重新開啟玩家選單。",
                ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            try:
                member = await guild.fetch_member(interaction.user.id)
            except Exception:
                await interaction.response.send_message(
                    "❌ 無法取得你的伺服器成員資料。",
                    ephemeral=True
                )
                return

        # Bot 需要 Manage Roles，且 Bot 最高身分組必須高於所有「職業-XX」。
        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ 機器人缺少「管理身分組」權限。",
                ephemeral=True
            )
            return

        if selected_role >= bot_member.top_role:
            await interaction.response.send_message(
                f"❌ 無法操作 **{selected_role.name}**。\n"
                "請把「德德機器人」的身分組移到職業身分組上方。",
                ephemeral=True
            )
            return

        current_job_roles = [
            role for role in member.roles
            if role.name.startswith(JOB_ROLE_PREFIX)
            and role != selected_role
        ]

        # 只移除 Bot 有權限操作的舊職業身分組。
        removable_roles = [
            role for role in current_job_roles
            if role < bot_member.top_role
        ]

        try:
            if removable_roles:
                await member.remove_roles(
                    *removable_roles,
                    reason="玩家自助更新職業身分"
                )

            if selected_role not in member.roles:
                await member.add_roles(
                    selected_role,
                    reason="玩家自助領取職業身分"
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Discord 拒絕修改身分組。\n"
                "請確認機器人有「管理身分組」權限，且機器人身分組位於所有職業身分組上方。",
                ephemeral=True
            )
            return
        except Exception as ex:
            print(f"[JobRole] {type(ex).__name__}: {ex}")
            await interaction.response.send_message(
                f"❌ 更新職業失敗：{type(ex).__name__}: {ex}",
                ephemeral=True
            )
            return

        # 同步保存到原本 roster_data.json，沿用既有資料結構。
        try:
            data = load_data()
            g = get_guild_data(data, guild.id)
            uid = str(member.id)

            info = g["bound_members"].setdefault(uid, {
                "discord_name": member.name,
                "game_id": "未設定",
                "power": 0,
                "created_at": now_text()
            })

            info["discord_name"] = member.name
            info["manual_job"] = selected_role.name[len(JOB_ROLE_PREFIX):].strip()
            info["job_role_id"] = selected_role.id
            info["updated_at"] = now_text()
            save_data(data)
        except Exception as ex:
            # Discord 身分已成功，不因 JSON 儲存失敗回滾身分。
            print(f"[JobRole][save] {type(ex).__name__}: {ex}")

        removed_text = ""
        if removable_roles:
            removed_text = "\n已移除舊職業：" + "、".join(
                role.name for role in removable_roles
            )

        await interaction.response.edit_message(
            content=(
                f"✅ 職業身分已更新為 **{selected_role.name}**"
                f"{removed_text}"
            ),
            view=None
        )


class DynamicJobRoleView(ui.View):
    def __init__(self, guild: discord.Guild, page: int = 0, per_page: int = 25):
        super().__init__(timeout=180)
        self.guild = guild
        self.page = page
        self.per_page = per_page

        roles = get_server_job_roles(guild)
        self.pages = max(1, (len(roles) + per_page - 1) // per_page)

        self.add_item(
            DynamicJobRoleSelect(
                guild,
                page=page,
                per_page=per_page
            )
        )

        if self.pages > 1:
            prev_button = ui.Button(
                label="◀ 上一頁",
                style=discord.ButtonStyle.secondary,
                disabled=page <= 0
            )
            next_button = ui.Button(
                label="下一頁 ▶",
                style=discord.ButtonStyle.secondary,
                disabled=page >= self.pages - 1
            )

            prev_button.callback = self.go_prev
            next_button.callback = self.go_next
            self.add_item(prev_button)
            self.add_item(next_button)

    async def open_voice_draw(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceChannelDrawModal())

    async def go_prev(self, interaction: discord.Interaction):
        new_page = max(0, self.page - 1)
        await interaction.response.edit_message(
            content=f"🪪 請選擇職業（第 {new_page + 1}/{self.pages} 頁）：",
            view=DynamicJobRoleView(
                interaction.guild,
                page=new_page,
                per_page=self.per_page
            )
        )

    async def go_next(self, interaction: discord.Interaction):
        new_page = min(self.pages - 1, self.page + 1)
        await interaction.response.edit_message(
            content=f"🪪 請選擇職業（第 {new_page + 1}/{self.pages} 頁）：",
            view=DynamicJobRoleView(
                interaction.guild,
                page=new_page,
                per_page=self.per_page
            )
        )



def load_admin_lottery_data():
    if LOTTERY_FILE.exists():
        with LOTTERY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"lotteries": {}}
    data.setdefault("lotteries", {})
    return data


def get_admin_lottery_records(guild_id):
    data = load_admin_lottery_data()
    rows = []

    for lottery_id, info in data.get("lotteries", {}).items():
        if str(info.get("guild_id", "")) not in ("", str(guild_id)):
            continue

        prizes = info.get("prizes") or []
        if prizes and not all(isinstance(p, dict) for p in prizes):
            continue

        dt = None
        for key in ("created_at", "end_time"):
            try:
                if info.get(key):
                    dt = datetime.fromisoformat(str(info[key]))
                    break
            except Exception:
                pass

        rows.append((lottery_id, info, dt or datetime.min))

    rows.sort(key=lambda x: x[2], reverse=True)
    return rows


def admin_lottery_history_embed(guild_id, page=0, per_page=5):
    rows = get_admin_lottery_records(guild_id)

    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))

    start = page * per_page
    current = rows[start:start + per_page]

    embed = discord.Embed(
        title="🎉 抽獎管理｜全部歷史紀錄",
        description=f"共 **{total}** 筆｜第 **{page + 1}/{pages}** 頁",
        color=discord.Color.orange()
    )

    if not current:
        embed.add_field(name="目前沒有抽獎紀錄", value="—", inline=False)
    else:
        for local_index, (lottery_id, info, _) in enumerate(current, start=1):
            title = info.get("title", "未命名抽獎")
            prizes = info.get("prizes") or []
            prize_text = "、".join(
                f"{p.get('name', '獎品')}×{p.get('quantity', 1)}"
                for p in prizes
                if isinstance(p, dict)
            ) or "未設定"

            status = info.get("status", "unknown")
            status_text = "🟢 進行中" if status in ("active", "running") else "⚪ 已結束"

            embed.add_field(
                name=f"{local_index}. {status_text}｜{title}",
                value=(
                    f"🎁 {prize_text}\n"
                    f"截止：{info.get('end_time', '—')}\n"
                    f"參加人數：{len(info.get('participants', []))}/{info.get('max_participants', 150)}\n"
                    f"ID：`{lottery_id}`"
                ),
                inline=False
            )

    return embed, current, page, pages


def delete_lottery_record(lottery_id):
    data = load_admin_lottery_data()
    lotteries = data.setdefault("lotteries", {})

    if lottery_id not in lotteries:
        return False

    del lotteries[lottery_id]

    tmp = LOTTERY_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(LOTTERY_FILE)
    return True


class AdminLotteryDeleteButton(ui.Button):
    def __init__(self, lottery_id, display_index, row):
        self.lottery_id = lottery_id
        super().__init__(
            label=f"🗑 刪除 {display_index}",
            style=discord.ButtonStyle.danger,
            custom_id=f"admin_lottery_delete_{lottery_id}"[:100],
            row=row
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if delete_lottery_record(self.lottery_id):
            await interaction.followup.send(
                f"✅ 已刪除抽獎歷史紀錄：`{self.lottery_id}`",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "ℹ️ 這筆紀錄已不存在。",
                ephemeral=True
            )

        if interaction.message:
            try:
                view = AdminLotteryHistoryView(interaction.guild_id, page=0)
                embed, _, _, _ = admin_lottery_history_embed(
                    interaction.guild_id,
                    page=view.page,
                    per_page=view.per_page
                )
                await interaction.message.edit(embed=embed, view=view)
            except Exception as ex:
                print(f"[Lottery] refresh after delete failed: {type(ex).__name__}: {ex}")


class AdminLotteryHistoryView(ui.View):
    def __init__(self, guild_id, page=0, per_page=4):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.page = page
        self.per_page = per_page

        _, current, normalized_page, pages = admin_lottery_history_embed(
            guild_id, page=page, per_page=per_page
        )
        self.page = normalized_page
        self.pages = pages

        voice_draw_button = ui.Button(
            label="🎯 抽目前語音頻道成員",
            style=discord.ButtonStyle.success,
            custom_id="admin_lottery_voice_draw",
            row=4
        )
        voice_draw_button.callback = self.open_voice_draw
        self.add_item(voice_draw_button)

        for idx, (lottery_id, _, _) in enumerate(current, start=1):
            self.add_item(
                AdminLotteryDeleteButton(
                    lottery_id=lottery_id,
                    display_index=idx,
                    row=idx - 1
                )
            )

        if pages > 1:
            prev_button = ui.Button(
                label="◀ 上一頁",
                style=discord.ButtonStyle.secondary,
                custom_id=f"admin_lottery_prev_{self.page}",
                row=4,
                disabled=self.page <= 0
            )
            next_button = ui.Button(
                label="下一頁 ▶",
                style=discord.ButtonStyle.secondary,
                custom_id=f"admin_lottery_next_{self.page}",
                row=4,
                disabled=self.page >= pages - 1
            )
            prev_button.callback = self.go_prev
            next_button.callback = self.go_next
            self.add_item(prev_button)
            self.add_item(next_button)

    async def go_prev(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        new_page = max(0, self.page - 1)
        view = AdminLotteryHistoryView(self.guild_id, page=new_page, per_page=self.per_page)
        embed, _, _, _ = admin_lottery_history_embed(
            self.guild_id, page=new_page, per_page=self.per_page
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_next(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        new_page = min(self.pages - 1, self.page + 1)
        view = AdminLotteryHistoryView(self.guild_id, page=new_page, per_page=self.per_page)
        embed, _, _, _ = admin_lottery_history_embed(
            self.guild_id, page=new_page, per_page=self.per_page
        )
        await interaction.response.edit_message(embed=embed, view=view)


class PlayerSelfServiceView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🪪 領取 / 更新職業身份", style=discord.ButtonStyle.success, custom_id="player_self_job", row=0)
    async def job(self, interaction, button):
        roles = get_server_job_roles(interaction.guild)

        if not roles:
            await interaction.response.send_message(
                "❌ 目前伺服器沒有任何 `職業-XX` 身分組。",
                ephemeral=True
            )
            return

        pages = max(1, (len(roles) + 24) // 25)
        await interaction.response.send_message(
            f"🪪 請選擇職業（共 {len(roles)} 個職業，第 1/{pages} 頁）：",
            view=DynamicJobRoleView(interaction.guild),
            ephemeral=True
        )

    @ui.button(label="🎮 綁定 / 更新遊戲ID", style=discord.ButtonStyle.primary, custom_id="player_self_gameid", row=0)
    async def gameid(self, interaction, button):
        await interaction.response.send_modal(GameIdModal())

    @ui.button(label="🌙 請假", style=discord.ButtonStyle.secondary, custom_id="player_self_leave", row=1)
    async def leave(self, interaction, button):
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        ref = f"bound:{interaction.user.id}"
        if ref not in g["leave"]:
            g["leave"].append(ref)
            save_data(data)
        await interaction.response.send_message("✅ 已登記請假。", ephemeral=True)

    @ui.button(label="🌙 取消請假", style=discord.ButtonStyle.secondary, custom_id="player_self_cancel_leave", row=1)
    async def cancel_leave(self, interaction, button):
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        ref = f"bound:{interaction.user.id}"
        if ref in g["leave"]:
            g["leave"].remove(ref)
            save_data(data)
            msg = "✅ 已取消請假。"
        else:
            msg = "ℹ️ 目前沒有請假紀錄。"
        await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(label="👤 查看我的資料", style=discord.ButtonStyle.secondary, custom_id="player_self_profile", row=1)
    async def profile(self, interaction, button):
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        info = g["bound_members"].get(str(interaction.user.id))
        if not info:
            await interaction.response.send_message("尚未綁定資料。", ephemeral=True)
            return
        e = discord.Embed(title="👤 我的資料", color=discord.Color.blurple())
        e.add_field(name="遊戲ID", value=info.get("game_id", "未設定"), inline=True)
        e.add_field(name="職業", value=info.get("manual_job") or info.get("job") or "未設定", inline=True)
        e.add_field(name="戰力", value=f"{int(info.get('power',0) or 0):,}", inline=True)
        e.add_field(name="請假", value="🌙 是" if f"bound:{interaction.user.id}" in g["leave"] else "✅ 否", inline=True)
        e.add_field(name="最後更新", value=info.get("updated_at", "—"), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ui.button(label="🎉 抽獎", style=discord.ButtonStyle.primary, custom_id="player_self_lottery", row=2)
    async def lottery(self, interaction, button):
        # 先立即 ACK，避免 Discord 顯示「應用程式未回應」
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await interaction.followup.send(
                embed=lottery_history_embed(interaction.guild_id),
                view=LotteryEntryView(),
                ephemeral=True
            )
        except Exception as ex:
            print(f"[Lottery] open menu failed: {type(ex).__name__}: {ex}")
            await interaction.followup.send(
                f"❌ 抽獎入口載入失敗：{type(ex).__name__}: {ex}",
                ephemeral=True
            )

class PlayerMenuLauncher(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🎮 開啟玩家自助選單", style=discord.ButtonStyle.success, custom_id="player_menu_launcher")
    async def open_menu(self, interaction, button):
        await interaction.response.send_message(
            "📌 **玩家自助區**\n請選擇你要使用的功能：",
            view=PlayerSelfServiceView(),
            ephemeral=True
        )

# ---------- 管理員排班 ----------
def team_summary_embed(guild_id):
    data = load_data()
    g = get_guild_data(data, guild_id)
    e = discord.Embed(title="📅 排班 / 組隊總覽", color=discord.Color.blue())
    if not g["teams"]:
        e.description = "目前沒有隊伍資料。"
        return e
    for team_name, slots in g["teams"].items():
        rows = []
        for idx, slot in enumerate(slots):
            r = resolve_slot(g, slot)
            if r:
                leave = " 🌙" if r["type"] == "bound" and f"bound:{r['key']}" in g["leave"] else ""
                rows.append(f"{idx+1}. {r['game_id']}｜{r['job']}｜{r['power']:,}{leave}")
        e.add_field(name=team_name, value=("\n".join(rows[:25]) or "（空）")[:1024], inline=False)
    return e

class AddUnboundModal(ui.Modal, title="新增未綁定玩家"):
    game_id = ui.TextInput(label="遊戲ID", max_length=30)
    job = ui.TextInput(label="職業", max_length=20)
    power = ui.TextInput(label="戰力", max_length=12)

    async def on_submit(self, interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return
        try:
            p = int(str(self.power).replace(",", ""))
        except ValueError:
            await interaction.response.send_message("❌ 戰力需為整數。", ephemeral=True)
            return
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        key = str(self.game_id).strip()
        g["unbound_members"][key] = {
            "game_id": key,
            "manual_job": str(self.job).strip(),
            "power": p,
            "created_by": str(interaction.user.id),
            "updated_at": now_text()
        }
        save_data(data)
        await interaction.response.send_message(f"✅ 已新增 {key}", ephemeral=True)

class TeamSlotModal(ui.Modal):
    slot_no = ui.TextInput(label="格位", placeholder="1~25", max_length=2)
    member_key = ui.TextInput(label="Discord ID 或遊戲ID", max_length=40, required=False)

    def __init__(self, team_name, mode):
        super().__init__(title=f"{team_name}｜{mode}")
        self.team_name = team_name
        self.mode = mode

    async def on_submit(self, interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return
        try:
            idx = int(str(self.slot_no)) - 1
            if not 0 <= idx < 25:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ 格位請輸入 1~25。", ephemeral=True)
            return
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        slots = g["teams"].setdefault(self.team_name, [None] * 25)
        while len(slots) < 25:
            slots.append(None)
        if self.mode == "清空":
            slots[idx] = None
            save_data(data)
            await interaction.response.send_message(f"✅ 已清空 {self.team_name} #{idx+1}", ephemeral=True)
            return
        key = str(self.member_key).strip()
        ref = None
        if key in g["bound_members"]:
            ref = {"type": "bound", "key": key}
        elif key in g["unbound_members"]:
            ref = {"type": "unbound", "key": key}
        else:
            for uid, info in g["bound_members"].items():
                if info.get("game_id") == key:
                    ref = {"type": "bound", "key": uid}
                    break
        if not ref:
            await interaction.response.send_message("❌ 找不到玩家。", ephemeral=True)
            return
        slots[idx] = ref
        save_data(data)
        await interaction.response.send_message(f"✅ 已放入 {self.team_name} #{idx+1}", ephemeral=True)

class TeamSelect(ui.Select):
    def __init__(self, guild_id, mode):
        data = load_data()
        g = get_guild_data(data, guild_id)
        opts = [discord.SelectOption(label=n, value=n) for n in list(g["teams"].keys())[:25]]
        if not opts:
            opts = [discord.SelectOption(label="目前沒有隊伍", value="__none__")]
        self.mode = mode
        super().__init__(placeholder="選擇隊伍", options=opts, custom_id=f"team_select_{mode}")

    async def callback(self, interaction):
        if self.values[0] == "__none__":
            await interaction.response.send_message("目前沒有隊伍。", ephemeral=True)
            return
        await interaction.response.send_modal(TeamSlotModal(self.values[0], self.mode))

class TeamSelectView(ui.View):
    def __init__(self, guild_id, mode):
        super().__init__(timeout=180)
        self.add_item(TeamSelect(guild_id, mode))

class SwapModal(ui.Modal, title="調換組隊"):
    team_a = ui.TextInput(label="來源隊伍", placeholder="主戰1")
    slot_a = ui.TextInput(label="來源格", placeholder="1~25", max_length=2)
    team_b = ui.TextInput(label="目標隊伍", placeholder="主戰2")
    slot_b = ui.TextInput(label="目標格", placeholder="1~25", max_length=2)

    async def on_submit(self, interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return
        try:
            a = int(str(self.slot_a)) - 1
            b = int(str(self.slot_b)) - 1
            if not (0 <= a < 25 and 0 <= b < 25):
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ 格位需為 1~25。", ephemeral=True)
            return
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        ta, tb = str(self.team_a).strip(), str(self.team_b).strip()
        if ta not in g["teams"] or tb not in g["teams"]:
            await interaction.response.send_message("❌ 找不到隊伍。", ephemeral=True)
            return
        while len(g["teams"][ta]) < 25: g["teams"][ta].append(None)
        while len(g["teams"][tb]) < 25: g["teams"][tb].append(None)
        g["teams"][ta][a], g["teams"][tb][b] = g["teams"][tb][b], g["teams"][ta][a]
        save_data(data)
        await interaction.response.send_message(f"✅ 已交換 {ta}#{a+1} ↔ {tb}#{b+1}", ephemeral=True)

class RosterManageView(ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @ui.button(label="➕ 放入隊伍", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_message("選擇隊伍：", view=TeamSelectView(interaction.guild_id, "加入"), ephemeral=True)

    @ui.button(label="➖ 清空格位", style=discord.ButtonStyle.danger)
    async def clear(self, interaction, button):
        await interaction.response.send_message("選擇隊伍：", view=TeamSelectView(interaction.guild_id, "清空"), ephemeral=True)

    @ui.button(label="👤 新增未綁定玩家", style=discord.ButtonStyle.secondary)
    async def add_unbound(self, interaction, button):
        await interaction.response.send_modal(AddUnboundModal())


class ChatMemberLotteryModal(ui.Modal, title="🎯 抽目前聊天室成員"):
    count = ui.TextInput(
        label="抽出人數",
        placeholder="例如：5",
        required=True,
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ 只有管理員可以使用此功能。",
                ephemeral=True
            )
            return

        try:
            draw_count = int(self.count.value)
            if draw_count <= 0:
                raise ValueError()
        except Exception:
            await interaction.response.send_message(
                "❌ 請輸入正確人數。",
                ephemeral=True
            )
            return

        members = []
        async for member in interaction.guild.fetch_members(limit=None):
            if member.bot:
                continue
            members.append(member)

        if len(members) < draw_count:
            await interaction.response.send_message(
                f"❌ 可抽成員不足，目前可抽 {len(members)} 人。",
                ephemeral=True
            )
            return

        winners = random.sample(members, draw_count)

        text = (
            "🎉 **聊天室抽獎結果**\n\n"
            f"📍 頻道：{interaction.channel.mention}\n"
            f"👥 參與人數：{len(members)}\n\n"
            "🏆 得主：\n"
        )

        for i, member in enumerate(winners, 1):
            text += f"{i}. {member.mention}\n"

        await interaction.response.send_message(text)


class ChatMemberLotteryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🎯 抽目前聊天室成員",
        style=discord.ButtonStyle.success,
        custom_id="chat_member_lottery_button"
    )
    async def draw(self, interaction: discord.Interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ 管理員專用。",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            ChatMemberLotteryModal()
        )


class VoiceChannelDrawModal(ui.Modal, title="🎯 語音頻道即時抽獎"):
    draw_count = ui.TextInput(
        label="抽出人數",
        placeholder="例如：5",
        max_length=3,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        try:
            count = int(self.draw_count.value)
            if count <= 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message(
                "❌ 抽出人數必須是大於 0 的整數。",
                ephemeral=True
            )
            return

        member = interaction.user
        voice = getattr(member, "voice", None)
        voice_channel = getattr(voice, "channel", None)

        if voice_channel is None:
            await interaction.response.send_message(
                "❌ 你目前不在任何語音頻道內。\n請先加入要抽獎的語音頻道，再執行此功能。",
                ephemeral=True
            )
            return

        candidates = [
            m for m in voice_channel.members
            if not m.bot
        ]

        if not candidates:
            await interaction.response.send_message(
                "❌ 目前語音頻道內沒有可抽取的成員。",
                ephemeral=True
            )
            return

        if count > len(candidates):
            await interaction.response.send_message(
                f"❌ 抽出人數超過目前語音頻道人數。\n"
                f"目前可抽：**{len(candidates)}** 人。",
                ephemeral=True
            )
            return

        winners = random.sample(candidates, count)

        result = (
            "🎉 **語音頻道即時抽獎結果**\n\n"
            f"🔊 語音頻道：**{voice_channel.name}**\n"
            f"👥 參與人數：**{len(candidates)}**\n"
            f"🎯 抽出人數：**{count}**\n\n"
            "🏆 **得主：**\n"
        )

        result += "\n".join(
            f"{idx}. {winner.mention}"
            for idx, winner in enumerate(winners, start=1)
        )

        # 公開發送，所有人都看得到
        await interaction.channel.send(result)

        await interaction.response.send_message(
            f"✅ 已從 **{voice_channel.name}** 抽出 {count} 位成員。",
            ephemeral=True
        )


class VoiceChannelDrawView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🎯 抽目前語音頻道成員",
        style=discord.ButtonStyle.success,
        custom_id="admin_voice_channel_draw"
    )
    async def draw_voice_members(self, interaction: discord.Interaction, button: ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        await interaction.response.send_modal(VoiceChannelDrawModal())


class AdminMenuView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="📅 排班", style=discord.ButtonStyle.primary, custom_id="admin_roster", row=0)
    async def roster(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True); return
        await interaction.response.send_message(embed=team_summary_embed(interaction.guild_id), view=RosterManageView(), ephemeral=True)

    @ui.button(label="🤝 交握視圖", style=discord.ButtonStyle.secondary, custom_id="admin_handshake", row=0)
    async def handshake(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True); return
        data = load_data()
        g = get_guild_data(data, interaction.guild_id)
        teams = list(g["teams"].keys())
        layout = [x for x in g.get("team_board_layout", []) if x in g["teams"]] or teams
        if len(layout) > 1:
            layout = layout[1:] + layout[:1]
        g["team_board_layout"] = layout
        save_data(data)
        await interaction.response.send_message("🤝 已切換交握視圖：\n" + (" → ".join(layout) if layout else "無隊伍"), ephemeral=True)

    @ui.button(label="🔁 調換組隊", style=discord.ButtonStyle.primary, custom_id="admin_swap_team", row=0)
    async def swap(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True); return
        await interaction.response.send_modal(SwapModal())

    @ui.button(label="🎯 抽目前聊天室成員", style=discord.ButtonStyle.success, custom_id="admin_chat_member_lottery", row=1)
    async def chat_member_lottery(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        await interaction.response.send_message(
            "🎯 請點擊下方按鈕開始抽取：",
            view=ChatMemberLotteryView(),
            ephemeral=True
        )

    @ui.button(label="🖼 產圖", style=discord.ButtonStyle.success, custom_id="admin_generate_image", row=2)
    async def image(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True); return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            p = generate_roster_image(interaction.guild_id, interaction.guild.name)
            await interaction.followup.send("✅ 班表圖片已產生。", file=discord.File(p), ephemeral=True)
        except Exception as ex:
            await interaction.followup.send(f"❌ 產圖失敗：{ex}", ephemeral=True)

    @ui.button(label="🎯 語音即時抽獎", style=discord.ButtonStyle.success, custom_id="admin_voice_draw_main", row=2)
    async def voice_draw(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return
        await interaction.response.send_modal(VoiceChannelDrawModal())

    @ui.button(label="🎉 抽獎管理", style=discord.ButtonStyle.secondary, custom_id="admin_lottery", row=1)
    async def lottery(self, interaction, button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ 管理員專用。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            view = AdminLotteryHistoryView(interaction.guild_id, page=0)
            embed, _, _, _ = admin_lottery_history_embed(
                interaction.guild_id,
                page=0,
                per_page=view.per_page
            )
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True
            )
        except Exception as ex:
            print(f"[Lottery] admin menu failed: {type(ex).__name__}: {ex}")
            await interaction.followup.send(
                f"❌ 抽獎管理載入失敗：{type(ex).__name__}: {ex}",
                ephemeral=True
            )

# ---------- 圖片 ----------
def find_font(size):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/msjh.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()

def generate_roster_image(guild_id, guild_name=""):
    if Image is None:
        raise RuntimeError("缺少 Pillow")
    data = load_data()
    g = get_guild_data(data, guild_id)
    bg_file = BASE_DIR / "backgrounds" / f"league_bg_{guild_id}.png"
    W, H = 1600, 2200

    if bg_file.exists():
        bg = Image.open(bg_file).convert("RGB")
        crop_h = min(bg.height, int(bg.width * H / W))
        top = max(0, (bg.height - crop_h) // 2)
        bg = bg.crop((0, top, bg.width, top + crop_h)).resize((W, H))
    else:
        bg = Image.new("RGB", (W, H), (245, 241, 232))

    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((45,45,W-45,H-45), radius=38, fill=(255,255,255,218))
    d.rounded_rectangle((80,80,W-80,245), radius=28, fill=(255,255,255,235))

    title_f = find_font(54); sub_f = find_font(28); team_f = find_font(34); row_f = find_font(24); tiny_f = find_font(20)
    d.text((110,105), f"{guild_name or '公會'}｜排班組隊表", font=title_f, fill=(35,35,35,255))
    d.text((112,180), f"產生時間：{datetime.now():%Y-%m-%d %H:%M}", font=sub_f, fill=(80,80,80,255))

    teams = g["teams"]
    order = [x for x in g.get("team_board_layout", []) if x in teams]
    order += [x for x in teams.keys() if x not in order]

    left, top0, gap = 80, 290, 30
    card_w = (W - 2*left - gap) // 2
    card_h = 560

    if not order:
        d.text((120,320), "目前沒有隊伍資料", font=team_f, fill=(40,40,40,255))

    for i, team_name in enumerate(order[:6]):
        col, row = i % 2, i // 2
        x0 = left + col * (card_w + gap)
        y0 = top0 + row * (card_h + gap)
        x1, y1 = x0 + card_w, y0 + card_h
        d.rounded_rectangle((x0,y0,x1,y1), radius=24, fill=(255,255,255,232), outline=(175,155,120,180), width=3)
        d.text((x0+26,y0+18), team_name, font=team_f, fill=(70,55,35,255))
        yy, count = y0+76, 0
        for idx, slot in enumerate(teams[team_name]):
            r = resolve_slot(g, slot)
            if not r:
                continue
            count += 1
            leave = "  [請假]" if r["type"] == "bound" and f"bound:{r['key']}" in g["leave"] else ""
            txt = f"{idx+1:02d}. {r['game_id']} ｜ {r['job']} ｜ {r['power']:,}{leave}"
            d.text((x0+30,yy), txt, font=row_f, fill=(45,45,45,255))
            yy += 38
            if yy > y1 - 55:
                break
        d.text((x1-150,y0+25), f"{count} 人", font=tiny_f, fill=(110,90,60,255))

    img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    p = GENERATED_DIR / f"roster_{guild_id}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    img.save(p, quality=92)
    return p


# ---------- Tips 圖片提示系統 ----------

def load_tips_data():
    if TIPS_FILE.exists():
        try:
            with TIPS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            print(f"[Tips] load failed: {type(ex).__name__}: {ex}")
            data = {}
    else:
        data = {}

    data.setdefault("guilds", {})
    return data


def save_tips_data(data):
    tmp = TIPS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(TIPS_FILE)


def get_guild_tips(data, guild_id):
    gid = str(guild_id)
    guild_data = data["guilds"].setdefault(gid, {})
    guild_data.setdefault("tips", [])
    return guild_data["tips"]


def is_image_attachment(attachment: discord.Attachment):
    content_type = (attachment.content_type or "").lower()
    if content_type.startswith("image/"):
        return True
    suffix = Path(attachment.filename or "").suffix.lower()
    return suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def get_tip_record(guild_id, tip_id):
    data = load_tips_data()
    for tip in get_guild_tips(data, guild_id):
        if tip.get("id") == tip_id:
            return tip
    return None


def tip_has_text(tip):
    return bool((tip.get("text_content") or "").strip())


def tip_has_image(tip):
    return bool(tip.get("file_name") or tip.get("source_message_id"))


async def send_tip_publicly(interaction: discord.Interaction, tip):
    """公開顯示 Tip；支援純文字、URL、圖片，或文字+圖片。"""
    title = tip.get("title", "未命名 Tips")
    text_content = (tip.get("text_content") or "").strip()

    embed = discord.Embed(
        title=f"💡 {title}",
        description=text_content if text_content else None,
        color=discord.Color.teal()
    )
    embed.set_footer(text=f"由 {tip.get('created_by_name', '成員')} 新增")

    file_name = tip.get("file_name")
    local_path = TIPS_IMAGE_DIR / file_name if file_name else None

    # 有圖片時優先夾帶圖片。
    if local_path and local_path.exists():
        attachment_name = local_path.name
        embed.set_image(url=f"attachment://{attachment_name}")
        await interaction.channel.send(
            embed=embed,
            file=discord.File(local_path, filename=attachment_name)
        )
        return

    # 本機檔案不存在時，嘗試從原始 Discord 訊息復原圖片。
    source_channel_id = tip.get("source_channel_id")
    source_message_id = tip.get("source_message_id")

    if source_channel_id and source_message_id:
        try:
            source_channel = interaction.guild.get_channel(int(source_channel_id))
            if source_channel is None:
                source_channel = await interaction.guild.fetch_channel(int(source_channel_id))

            source_message = await source_channel.fetch_message(int(source_message_id))
            image_attachment = next(
                (a for a in source_message.attachments if is_image_attachment(a)),
                None
            )

            if image_attachment:
                image_bytes = await image_attachment.read()
                recovered_name = image_attachment.filename or f"{tip['id']}.png"

                try:
                    (TIPS_IMAGE_DIR / recovered_name).write_bytes(image_bytes)
                except Exception:
                    pass

                embed.set_image(url=f"attachment://{recovered_name}")
                await interaction.channel.send(
                    embed=embed,
                    file=discord.File(io.BytesIO(image_bytes), filename=recovered_name)
                )
                return
        except Exception as ex:
            print(f"[Tips] fallback fetch failed: {type(ex).__name__}: {ex}")

    # 純文字/URL Tip：直接公開 embed。
    if text_content:
        await interaction.channel.send(embed=embed)
        return

    # 理論上不應發生：沒有文字也沒有圖片。
    await interaction.channel.send(
        embed=discord.Embed(
            title=f"💡 {title}",
            description="❌ 這筆 Tips 沒有可顯示的內容。",
            color=discord.Color.red()
        )
    )


class AddTipTitleModal(ui.Modal, title="➕ 新增提示"):
    tip_title = ui.TextInput(
        label="標題",
        placeholder="例如：副本王機制、裝備強化順序",
        max_length=80,
        required=True
    )

    text_content = ui.TextInput(
        label="文字 / URL（可留空）",
        placeholder="可輸入說明、網址，或純文字內容。若要只上傳圖片可留空。",
        style=discord.TextStyle.paragraph,
        max_length=1800,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        title = self.tip_title.value.strip()
        text_content = self.text_content.value.strip()

        if not title:
            await interaction.response.send_message("❌ 請輸入標題。", ephemeral=True)
            return

        # 先建立一個 pending 流程：
        # 有文字時可直接儲存，也可選擇再補圖片；
        # 沒文字時則必須上傳圖片。
        view = TipContentChoiceView(title, text_content)
        await interaction.response.send_message(
            "💡 **請選擇 Tips 內容方式：**\n"
            "• 只存文字 / URL\n"
            "• 上傳圖片（可搭配剛剛輸入的文字）",
            view=view,
            ephemeral=True
        )


class TipContentChoiceView(ui.View):
    def __init__(self, title, text_content):
        super().__init__(timeout=180)
        self.title = title
        self.text_content = text_content

        # 沒有文字時，不允許只存文字。
        self.save_text_only.disabled = not bool(text_content)

    async def _save_tip(self, interaction, image_message=None):
        tip_id = uuid.uuid4().hex[:12]

        file_name = None
        source_channel_id = None
        source_message_id = None

        if image_message is not None:
            image_attachment = next(
                (a for a in image_message.attachments if is_image_attachment(a)),
                None
            )
            if image_attachment is None:
                await interaction.followup.send("❌ 沒有找到有效圖片。", ephemeral=True)
                return

            suffix = Path(image_attachment.filename or "").suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
                suffix = ".png"

            file_name = f"{tip_id}{suffix}"
            local_path = TIPS_IMAGE_DIR / file_name

            try:
                await image_attachment.save(local_path)
            except Exception as ex:
                await interaction.followup.send(
                    f"❌ 圖片保存失敗：{type(ex).__name__}: {ex}",
                    ephemeral=True
                )
                return

            source_channel_id = image_message.channel.id
            source_message_id = image_message.id

        data = load_tips_data()
        tips = get_guild_tips(data, interaction.guild_id)
        tips.append({
            "id": tip_id,
            "title": self.title,
            "text_content": self.text_content,
            "file_name": file_name,
            "created_by": str(interaction.user.id),
            "created_by_name": interaction.user.display_name,
            "created_at": datetime.now().isoformat(),
            "source_channel_id": source_channel_id,
            "source_message_id": source_message_id
        })
        save_tips_data(data)

        content_type = []
        if self.text_content:
            content_type.append("文字")
        if file_name:
            content_type.append("圖片")

        await interaction.followup.send(
            f"✅ Tips **{self.title}** 已新增完成（{' + '.join(content_type)}）。\n"
            "重新輸入 `/tips` 就會看到新的按鈕。",
            ephemeral=True
        )

    @ui.button(label="📝 只存文字 / URL", style=discord.ButtonStyle.primary)
    async def save_text_only(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self._save_tip(interaction, image_message=None)

    @ui.button(label="🖼 上傳圖片", style=discord.ButtonStyle.success)
    async def upload_image(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "🖼 **請在目前這個頻道上傳 1 張圖片。**\n"
            "我會等待 **2 分鐘**；剛才輸入的文字 / URL 也會一起保存。",
            ephemeral=True
        )

        def check(message: discord.Message):
            return (
                message.author.id == interaction.user.id
                and message.channel.id == interaction.channel_id
                and any(is_image_attachment(a) for a in message.attachments)
            )

        try:
            message = await interaction.client.wait_for(
                "message",
                check=check,
                timeout=120
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⌛ 已超過 2 分鐘，新增 Tips 已取消。",
                ephemeral=True
            )
            return

        await self._save_tip(interaction, image_message=message)


class TipShowButton(ui.Button):
    def __init__(self, tip, row):
        self.tip_id = tip["id"]
        super().__init__(
            label=tip.get("title", "未命名 Tips")[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"tips_show_{self.tip_id}"[:100],
            row=row
        )

    async def callback(self, interaction: discord.Interaction):
        tip = get_tip_record(interaction.guild_id, self.tip_id)
        if not tip:
            await interaction.response.send_message("❌ 這筆 Tips 已不存在。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await send_tip_publicly(interaction, tip)
        await interaction.followup.send(
            f"✅ 已顯示 **{tip.get('title', 'Tips')}**。",
            ephemeral=True
        )


def can_delete_tip(interaction: discord.Interaction, tip):
    """Tip 建立者本人或管理員可以刪除。"""
    if not tip:
        return False
    if str(tip.get("created_by")) == str(interaction.user.id):
        return True
    perms = interaction.user.guild_permissions
    return perms.administrator or perms.manage_guild


def delete_tip_record(guild_id, tip_id):
    data = load_tips_data()
    tips = get_guild_tips(data, guild_id)

    target = None
    for tip in tips:
        if tip.get("id") == tip_id:
            target = tip
            break

    if not target:
        return False, None

    tips.remove(target)
    save_tips_data(data)

    # 同步刪除本機圖片檔，Discord 原始上傳訊息不刪除。
    file_name = target.get("file_name")
    if file_name:
        local_path = TIPS_IMAGE_DIR / file_name
        try:
            if local_path.exists():
                local_path.unlink()
        except Exception as ex:
            print(f"[Tips] image delete failed: {type(ex).__name__}: {ex}")

    return True, target


class DeleteTipSelect(ui.Select):
    def __init__(self, guild_id, user_id, is_admin_user):
        data = load_tips_data()
        tips = list(reversed(get_guild_tips(data, guild_id)))

        deletable = []
        for tip in tips:
            if is_admin_user or str(tip.get("created_by")) == str(user_id):
                deletable.append(tip)

        if deletable:
            options = [
                discord.SelectOption(
                    label=tip.get("title", "未命名 Tips")[:100],
                    value=tip["id"],
                    description=f"建立者：{tip.get('created_by_name', '未知')}"[:100]
                )
                for tip in deletable[:25]
            ]
            disabled = False
        else:
            options = [
                discord.SelectOption(
                    label="沒有可刪除的 Tips",
                    value="__none__"
                )
            ]
            disabled = True

        super().__init__(
            placeholder="選擇要刪除的 Tips",
            options=options,
            min_values=1,
            max_values=1,
            disabled=disabled,
            custom_id="tips_delete_select"
        )

    async def callback(self, interaction: discord.Interaction):
        tip_id = self.values[0]
        if tip_id == "__none__":
            await interaction.response.send_message(
                "目前沒有你可以刪除的 Tips。",
                ephemeral=True
            )
            return

        tip = get_tip_record(interaction.guild_id, tip_id)
        if not can_delete_tip(interaction, tip):
            await interaction.response.send_message(
                "❌ 你只能刪除自己建立的 Tips；管理員可刪除全部。",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"⚠️ 確定要刪除 **{tip.get('title', '未命名 Tips')}** 嗎？",
            view=ConfirmDeleteTipView(tip_id),
            ephemeral=True
        )


class DeleteTipSelectView(ui.View):
    def __init__(self, guild_id, user_id, is_admin_user):
        super().__init__(timeout=180)
        self.add_item(DeleteTipSelect(guild_id, user_id, is_admin_user))


class ConfirmDeleteTipView(ui.View):
    def __init__(self, tip_id):
        super().__init__(timeout=60)
        self.tip_id = tip_id

    @ui.button(label="🗑 確認刪除", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: ui.Button):
        tip = get_tip_record(interaction.guild_id, self.tip_id)
        if not can_delete_tip(interaction, tip):
            await interaction.response.send_message(
                "❌ 你沒有權限刪除這筆 Tips。",
                ephemeral=True
            )
            return

        ok, deleted = delete_tip_record(interaction.guild_id, self.tip_id)
        if not ok:
            await interaction.response.send_message(
                "ℹ️ 這筆 Tips 已不存在。",
                ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"✅ 已刪除 Tips：**{deleted.get('title', '未命名 Tips')}**",
            view=None
        )

    @ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content="✅ 已取消刪除。",
            view=None
        )


class TipsView(ui.View):
    def __init__(self, guild_id, page=0, per_page=20):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.page = page
        self.per_page = per_page

        data = load_tips_data()
        all_tips = list(reversed(get_guild_tips(data, guild_id)))
        self.total = len(all_tips)
        self.pages = max(1, (self.total + per_page - 1) // per_page)
        self.page = max(0, min(page, self.pages - 1))

        start = self.page * per_page
        current = all_tips[start:start + per_page]

        for idx, tip in enumerate(current):
            self.add_item(TipShowButton(tip, row=idx // 5))

        add_button = ui.Button(
            label="➕ 新增提示",
            style=discord.ButtonStyle.success,
            custom_id=f"tips_add_{self.page}",
            row=4
        )
        add_button.callback = self.add_tip
        self.add_item(add_button)

        delete_button = ui.Button(
            label="🗑 刪除提示",
            style=discord.ButtonStyle.danger,
            custom_id=f"tips_delete_{self.page}",
            row=4
        )
        delete_button.callback = self.delete_tip
        self.add_item(delete_button)

        if self.pages > 1:
            prev_button = ui.Button(
                label="◀ 上一頁",
                style=discord.ButtonStyle.secondary,
                custom_id=f"tips_prev_{self.page}",
                row=4,
                disabled=self.page <= 0
            )
            next_button = ui.Button(
                label="下一頁 ▶",
                style=discord.ButtonStyle.secondary,
                custom_id=f"tips_next_{self.page}",
                row=4,
                disabled=self.page >= self.pages - 1
            )
            prev_button.callback = self.go_prev
            next_button.callback = self.go_next
            self.add_item(prev_button)
            self.add_item(next_button)

    async def add_tip(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddTipTitleModal())

    async def delete_tip(self, interaction: discord.Interaction):
        perms = interaction.user.guild_permissions
        is_admin_user = perms.administrator or perms.manage_guild

        await interaction.response.send_message(
            "🗑 請選擇要刪除的 Tips：",
            view=DeleteTipSelectView(
                interaction.guild_id,
                interaction.user.id,
                is_admin_user
            ),
            ephemeral=True
        )

    async def go_prev(self, interaction: discord.Interaction):
        new_page = max(0, self.page - 1)
        await interaction.response.edit_message(
            embed=build_tips_embed(self.guild_id, new_page, self.per_page),
            view=TipsView(self.guild_id, new_page, self.per_page)
        )

    async def go_next(self, interaction: discord.Interaction):
        new_page = min(self.pages - 1, self.page + 1)
        await interaction.response.edit_message(
            embed=build_tips_embed(self.guild_id, new_page, self.per_page),
            view=TipsView(self.guild_id, new_page, self.per_page)
        )


def build_tips_embed(guild_id, page=0, per_page=20):
    data = load_tips_data()
    tips = list(reversed(get_guild_tips(data, guild_id)))

    total = len(tips)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))

    embed = discord.Embed(
        title="💡 成員 Tips",
        description=(
            "上方是已新增的 Tips，點擊後會把內容公開顯示在目前頻道。\n"
            "支援 **文字、URL、圖片、文字+圖片**。\n"
            "要新增內容請按最下方的 **➕ 新增提示**。"
        ),
        color=discord.Color.teal()
    )
    embed.set_footer(text=f"共 {total} 筆｜第 {page + 1}/{pages} 頁")
    return embed


@bot.tree.command(name="tips", description="開啟成員圖片 Tips")
async def tips_command(interaction: discord.Interaction):
    # Slash command 必須在 Discord 的時限內 ACK。
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        embed = build_tips_embed(interaction.guild_id)
        view = TipsView(interaction.guild_id)

        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True
        )
    except Exception as ex:
        print(f"[Slash:/tips] {type(ex).__name__}: {ex}")
        await interaction.followup.send(
            f"❌ /tips 執行失敗：{type(ex).__name__}: {ex}",
            ephemeral=True
        )


# ---------- 指令 ----------
@bot.tree.command(name="playermenu", description="開啟玩家自助入口")
async def playermenu(interaction: discord.Interaction):
    # 先 ACK，再建立公開永久入口，避免任何 JSON/I/O 問題造成「該申請未受回應」。
    await interaction.response.defer(thinking=True)

    try:
        e = discord.Embed(
            title="📌 玩家自助入口",
            description=(
                "點下方按鈕開啟個人功能選單。\n"
                "功能包含：職業領取、綁定 / 更新遊戲ID、請假、取消請假、查看我的資料。"
            ),
            color=discord.Color.green()
        )

        msg = await interaction.followup.send(
            embed=e,
            view=PlayerMenuLauncher(),
            wait=True
        )

        try:
            data = load_data()
            g = get_guild_data(data, interaction.guild_id)
            g["player_menu_channel_id"] = interaction.channel_id
            g["player_menu_message_id"] = msg.id
            save_data(data)
        except Exception as save_ex:
            # 入口已成功送出，資料記錄失敗不應讓 Slash command 看起來失敗。
            print(f"[Slash:/playermenu][save] {type(save_ex).__name__}: {save_ex}")

    except Exception as ex:
        print(f"[Slash:/playermenu] {type(ex).__name__}: {ex}")
        await interaction.followup.send(
            f"❌ /playermenu 執行失敗：{type(ex).__name__}: {ex}",
            ephemeral=True
        )

@bot.command()
async def menu(ctx):
    if not is_admin(ctx.author):
        await ctx.send("❌ 管理員專用。"); return
    e = discord.Embed(title="🛠 管理員功能", description="排班｜交握視圖｜調換組隊｜產圖｜抽獎管理", color=discord.Color.orange())
    msg = await ctx.send(embed=e, view=AdminMenuView())
    data = load_data(); g = get_guild_data(data, ctx.guild.id)
    g["menu_channel_id"] = ctx.channel.id
    g["menu_message_id"] = msg.id
    save_data(data)

@bot.command()
async def lottery(ctx):
    await ctx.send(embed=lottery_history_embed(ctx.guild.id), view=LotteryEntryView())

@bot.command()
async def roster(ctx):
    await ctx.send(embed=team_summary_embed(ctx.guild.id))

@bot.command()
async def output(ctx):
    if not is_admin(ctx.author):
        await ctx.send("❌ 管理員專用。"); return
    p = generate_roster_image(ctx.guild.id, ctx.guild.name)
    await ctx.send(file=discord.File(p))

@bot.command()
async def help(ctx):
    e = discord.Embed(title="德德機器人指令", color=discord.Color.blurple())
    e.add_field(name="玩家", value="`!playermenu` 玩家永久入口\n`!lottery` 抽獎紀錄", inline=False)
    e.add_field(name="管理員", value="`!menu` 管理功能\n`!roster` 隊伍總覽\n`!output` 直接產圖", inline=False)
    await ctx.send(embed=e)

@bot.command()
async def clearmenu(ctx):
    if not ctx.author.guild_permissions.administrator:
        return
    data = load_data(); g = get_guild_data(data, ctx.guild.id)
    cid, mid = g.get("menu_channel_id"), g.get("menu_message_id")
    if cid and mid:
        try:
            ch = ctx.guild.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
            await (await ch.fetch_message(int(mid))).delete()
        except Exception:
            pass
    g["menu_channel_id"] = None; g["menu_message_id"] = None
    save_data(data)
    await ctx.send("✅ 已清除管理員入口紀錄。", delete_after=5)

@bot.command()
async def clearplayermenu(ctx):
    if not ctx.author.guild_permissions.administrator:
        return
    data = load_data(); g = get_guild_data(data, ctx.guild.id)
    cid, mid = g.get("player_menu_channel_id"), g.get("player_menu_message_id")
    if cid and mid:
        try:
            ch = ctx.guild.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
            await (await ch.fetch_message(int(mid))).delete()
        except Exception:
            pass
    g["player_menu_channel_id"] = None; g["player_menu_message_id"] = None
    save_data(data)
    await ctx.send("✅ 已清除玩家入口紀錄。", delete_after=5)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """所有 Slash Command 的最後保護，避免 Discord 只顯示『該申請未受回應』。"""
    print(f"[Slash Error] {type(error).__name__}: {error}")

    message = f"❌ 指令執行發生錯誤：{type(error).__name__}: {error}"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as notify_ex:
        print(f"[Slash Error][notify failed] {type(notify_ex).__name__}: {notify_ex}")


@bot.event
async def on_ready():
    if not getattr(bot, "_persistent_registered", False):
        persistent_views = [
            ("PlayerMenuLauncher", PlayerMenuLauncher()),
            ("PlayerSelfServiceView", PlayerSelfServiceView()),
            ("AdminMenuView", AdminMenuView()),
            ("LotteryEntryView", LotteryEntryView()),
            ("VoiceChannelDrawView", VoiceChannelDrawView()),
            ("ChatMemberLotteryView", ChatMemberLotteryView()),
        ]

        for view_name, view in persistent_views:
            try:
                bot.add_view(view)
                print(f"Persistent view registered: {view_name}")
            except Exception as ex:
                print(f"[Persistent View:{view_name}] {type(ex).__name__}: {ex}")

        try:
            synced = await bot.tree.sync()
            print(f"Slash commands synced: {len(synced)}")
            print("Slash command names:", ", ".join(cmd.name for cmd in synced))
        except Exception as ex:
            print(f"[Slash Sync] {type(ex).__name__}: {ex}")

        bot._persistent_registered = True

    print(f"Bot ready: {bot.user}")

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("找不到 DISCORD_TOKEN 或 .token")
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
