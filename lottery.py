#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽獎系統模組"""

import os
import json
import random
from datetime import datetime, timedelta

import discord
from discord import ui


class LotteryEntryView(ui.View):
    """
    抽獎入口 Persistent View。
    用於舊訊息 / 玩家入口重啟後恢復按鈕。
    """

    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🎉 抽獎",
        style=discord.ButtonStyle.primary,
        custom_id="lottery_entry_button"
    )
    async def lottery_entry(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ):
        await interaction.response.send_message(
            "🎉 抽獎功能入口",
            ephemeral=True
        )

# 抽獎數據檔案
LOTTERY_FILE = "lottery_data.json"

def load_lottery_data():
    """載入抽獎數據"""
    if os.path.exists(LOTTERY_FILE):
        with open(LOTTERY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"lotteries": {}}

def save_lottery_data(data):
    """儲存抽獎數據"""
    with open(LOTTERY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def lottery_history_embed(guild_id):
    """建立玩家用抽獎紀錄 Embed，只處理新版 prizes 格式。"""
    data = load_lottery_data()
    now = datetime.now()
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

        # 玩家頁只顯示近 10 天
        if dt and abs((now - dt).days) > 10:
            continue

        rows.append((lottery_id, info, dt or now))

    rows.sort(key=lambda x: x[2], reverse=True)

    embed = discord.Embed(
        title="🎉 抽獎紀錄（10天內）",
        color=discord.Color.gold()
    )

    if not rows:
        embed.description = "目前沒有 10 天內的抽獎紀錄。"
        return embed

    for lottery_id, info, _ in rows[:10]:
        title = info.get("title", "未命名抽獎")
        status = info.get("status", "unknown")
        running = status in ("active", "running")

        prizes = info.get("prizes") or []
        prize_text = "、".join(
            f"{p.get('name', '獎品')}×{p.get('quantity', 1)}"
            for p in prizes
            if isinstance(p, dict)
        ) or "未設定"

        embed.add_field(
            name=f"{'🟢 進行中' if running else '⚪ 已結束'}｜{title}",
            value=(
                f"🎁 {prize_text}\n"
                f"截止：{info.get('end_time', '—')}\n"
                f"參加人數：{len(info.get('participants', []))}/{info.get('max_participants', 150)}\n"
                f"ID：`{lottery_id}`"
            ),
            inline=False
        )

    return embed




class EditTimeModal(ui.Modal, title="✏️ 修改截止時間"):
    """修改抽獎截止時間"""
    def __init__(self, lottery_id: str):
        super().__init__()
        self.lottery_id = lottery_id
        
        # 載入當前截止時間
        data = load_lottery_data()
        lottery = data["lotteries"].get(lottery_id)
        if lottery:
            end_time = datetime.fromisoformat(lottery["end_time"])
            default_time = end_time.strftime("%Y-%m-%d %H:%M")
        else:
            default_time = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        
        self.end_datetime = ui.TextInput(
            label="截止日期時間",
            placeholder="格式: 2026-08-15 21:00",
            default=default_time,
            required=True,
            row=0
        )
        
        self.add_item(self.end_datetime)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            end_time = datetime.strptime(self.end_datetime.value.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                end_time = datetime.strptime(self.end_datetime.value.strip(), "%Y/%m/%d %H:%M")
            except ValueError:
                await interaction.response.send_message(
                    "❌ 日期格式錯誤！請使用格式：YYYY-MM-DD HH:MM",
                    ephemeral=True
                )
                return

        if end_time <= datetime.now():
            await interaction.response.send_message("❌ 截止時間必須是未來的時間！", ephemeral=True)
            return

        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)
        if lottery:
            lottery["end_time"] = end_time.isoformat()
            save_lottery_data(data)
            
            await interaction.response.send_message(
                f"✅ 已修改截止時間為：<t:{int(end_time.timestamp())}:f>",
                ephemeral=True
            )
            
            # 更新 embed
            end_time = datetime.fromisoformat(lottery["end_time"])
            prizes = lottery.get("prizes", [])
            if prizes:
                prize_text = "\n".join([f"{i+1}. {p['name']} x{p['quantity']}" for i, p in enumerate(prizes)])
            else:
                prize_text = lottery.get("prize", "")
            
            embed = discord.Embed(title=f"🎉 {lottery.get('title', '未命名抽獎')}", color=discord.Color.gold())
            embed.add_field(name="🎁 禮物", value=prize_text, inline=False)
            embed.add_field(name="👤 發起人", value=f"<@{lottery['creator_id']}>", inline=True)
            embed.add_field(name="⏰ 截止時間", value=f"<t:{int(end_time.timestamp())}:f>", inline=True)
            embed.add_field(name="👥 已參加", value=f"{len(lottery['participants'])}", inline=True)
            embed.add_field(name="📊 名額", value=f"{lottery['max_participants']}", inline=True)
            embed.set_footer(text=f"抽獎ID: {self.lottery_id}")
            await interaction.message.edit(embed=embed)
        else:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)


class AddPrizeModal(ui.Modal, title="🎁 新增禮物"):
    """新增禮物的表單"""
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        
        self.prize_name = ui.TextInput(
            label="禮物名稱",
            placeholder="例如: 月卡、Steam遊戲、點數",
            required=True,
            row=0
        )
        
        self.prize_quantity = ui.TextInput(
            label="數量（數字）",
            placeholder="例如: 1、3、10",
            default="1",
            required=True,
            row=1
        )
        
        self.add_item(self.prize_name)
        self.add_item(self.prize_quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.prize_quantity.value)
            if quantity < 1:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ 數量必須是大於0的整數！", ephemeral=True)
            return

        prize_name = self.prize_name.value.strip()
        if not prize_name:
            await interaction.response.send_message("❌ 請輸入禮物名稱！", ephemeral=True)
            return

        # 調用回調函數
        await self.callback(interaction, prize_name, quantity)


class LotteryCreateModal(ui.Modal, title="🎉 發起抽獎"):
    """發起抽獎的表單"""
    def __init__(self):
        super().__init__()
        self.prizes = []  # 禮物列表
        
        # 最高人數
        self.max_participants = ui.TextInput(
            label="最高人數",
            placeholder="例如: 3",
            default="1",
            required=True,
            row=0
        )
        
        # 截止日期時間
        default_time = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        self.end_datetime = ui.TextInput(
            label="截止日期時間",
            placeholder="格式: 2026-08-15 21:00",
            default=default_time,
            required=True,
            row=1
        )
        
        self.add_item(self.max_participants)
        self.add_item(self.end_datetime)

    async def on_submit(self, interaction: discord.Interaction):
        # 這個只是暫時的，最終會被替換
        pass


class LotteryCreateView(ui.View):
    """發起抽獎按鈕"""
    @ui.button(label="🎉 發起抽獎", style=discord.ButtonStyle.primary, row=0)
    async def create_lottery(self, interaction: discord.Interaction, button: ui.Button):
        # 直接發送 Modal
        await interaction.response.send_modal(CreateLotteryFullModal())


class CreateLotteryFullModal(ui.Modal, title="🎉 發起抽獎"):
    """完整的發起抽獎表單"""
    def __init__(self):
        super().__init__()
        self.prizes = []

        self.lottery_title = ui.TextInput(
            label="抽獎標題",
            placeholder="例如：8月公會月卡抽獎",
            max_length=80,
            required=True,
            row=0
        )

        self.max_participants = ui.TextInput(
            label="最高人數",
            placeholder="例如: 150",
            default="150",
            required=True,
            row=1
        )

        default_time = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        self.end_datetime = ui.TextInput(
            label="截止日期時間",
            placeholder="格式: 2026-08-15 21:00",
            default=default_time,
            required=True,
            row=2
        )

        self.add_item(self.lottery_title)
        self.add_item(self.max_participants)
        self.add_item(self.end_datetime)

    async def on_submit(self, interaction: discord.Interaction):
        prize_text = "尚未新增禮物"
        if self.prizes:
            prize_text = "\n".join(
                [f"{i+1}. {p['name']} x{p['quantity']}" for i, p in enumerate(self.prizes)]
            )

        embed = discord.Embed(
            title=f"🎉 {self.lottery_title.value.strip()}",
            description="請新增禮物",
            color=discord.Color.gold()
        )
        embed.add_field(name="🎁 禮物列表", value=prize_text, inline=False)

        view = LotteryAddPrizeView(self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class LotteryAddPrizeView(ui.View):
    """新增禮物視圖"""
    def __init__(self, modal):
        super().__init__(timeout=600)
        self.modal = modal
        
        # 顯示當前禮物列表
        self.prize_text = "尚未新增禮物"
        
        # 新增禮物按鈕
        self.add_prize_btn = ui.Button(label="➕ 新增禮物", style=discord.ButtonStyle.success, row=0)
        self.add_prize_btn.callback = self.add_prize
        self.add_item(self.add_prize_btn)
        
        # 發起抽獎按鈕
        self.submit_btn = ui.Button(label="✅ 發起抽獎", style=discord.ButtonStyle.primary, row=0)
        self.submit_btn.callback = self.submit_lottery
        self.add_item(self.submit_btn)

    async def add_prize(self, interaction: discord.Interaction):
        async def on_prize_added(interaction: discord.Interaction, name: str, qty: int):
            self.modal.prizes.append({"name": name, "quantity": qty})
            # 更新顯示
            prize_text = "\n".join([f"{i+1}. {p['name']} x{p['quantity']}" for i, p in enumerate(self.modal.prizes)])
            embed = discord.Embed(
                title=f"🎉 {self.modal.lottery_title.value.strip()}",
                description="請新增禮物",
                color=discord.Color.gold()
            )
            embed.add_field(name="🎁 禮物列表", value=prize_text, inline=False)
            embed.add_field(name="📝 說明", value="點擊「新增禮物」繼續添加，確認後點擊「發起抽獎」", inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        
        await interaction.response.send_modal(AddPrizeModal(on_prize_added))

    async def submit_lottery(self, interaction: discord.Interaction):
        if not self.modal.prizes:
            await interaction.response.send_message("❌ 請先新增至少一個禮物！", ephemeral=True)
            return

        try:
            max_pax = int(self.modal.max_participants.value)
            if max_pax < 1:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ 最高人數必須是大於 0 的整數！", ephemeral=True)
            return

        lottery_title = self.modal.lottery_title.value.strip()
        if not lottery_title:
            await interaction.response.send_message("❌ 請輸入抽獎標題！", ephemeral=True)
            return

        # 解析日期時間
        try:
            end_time = datetime.strptime(self.modal.end_datetime.value.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                end_time = datetime.strptime(self.modal.end_datetime.value.strip(), "%Y/%m/%d %H:%M")
            except ValueError:
                await interaction.response.send_message(
                    "❌ 日期格式錯誤！請使用格式：YYYY-MM-DD HH:MM",
                    ephemeral=True
                )
                return

        if end_time <= datetime.now():
            await interaction.response.send_message("❌ 截止時間必須是未來的時間！", ephemeral=True)
            return

        lottery_id = f"{interaction.guild_id}_{int(datetime.now().timestamp())}"

        # 展開禮物數量
        expanded_prizes = []
        for p in self.modal.prizes:
            for _ in range(p["quantity"]):
                expanded_prizes.append(p["name"])
        
        data = load_lottery_data()
        data["lotteries"][lottery_id] = {
            "id": lottery_id,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "creator_id": interaction.user.id,
            "creator_name": interaction.user.display_name,
            "created_at": datetime.now().isoformat(),
            "title": lottery_title,
            "prizes": self.modal.prizes,
            "prize": "\n".join(expanded_prizes),
            "max_participants": max_pax,
            "end_time": end_time.isoformat(),
            "participants": [],
            "status": "active",
            "winner_ids": []
        }
        save_lottery_data(data)

        # 顯示禮物列表
        prize_text = "\n".join([f"{i+1}. {p['name']} x{p['quantity']}" for i, p in enumerate(self.modal.prizes)])
        
        embed = discord.Embed(title=f"🎉 {lottery_title}", color=discord.Color.gold())
        embed.add_field(name="🎁 禮物", value=prize_text, inline=False)
        embed.add_field(name="👤 發起人", value=interaction.user.mention, inline=True)
        embed.add_field(name="⏰ 截止時間", value=f"<t:{int(end_time.timestamp())}:f>", inline=True)
        embed.add_field(name="👥 已參加", value="0", inline=True)
        embed.add_field(name="📊 名額", value=f"{max_pax}", inline=True)
        embed.set_footer(text=f"抽獎ID: {lottery_id}")

        view = LotteryJoinView(lottery_id)

        # 最終抽獎卡片一定要發成「頻道公開訊息」。
        # 先前若從 ephemeral 管理流程回覆，Discord jump_url 可能只指向
        # 私密互動訊息，其他人點「前往抽獎」會無法開啟。
        try:
            public_message = await interaction.channel.send(embed=embed, view=view)

            data = load_lottery_data()
            lottery = data["lotteries"].get(lottery_id)
            if lottery:
                lottery["message_id"] = public_message.id
                lottery["channel_id"] = public_message.channel.id
                save_lottery_data(data)

            await interaction.response.send_message(
                f"✅ 抽獎已建立並發布：{public_message.jump_url}",
                ephemeral=True
            )
        except Exception as ex:
            print(f"[Lottery] publish public lottery failed: {type(ex).__name__}: {ex}")
            await interaction.response.send_message(
                f"❌ 抽獎建立失敗：{type(ex).__name__}: {ex}",
                ephemeral=True
            )



class ConfirmDrawView(ui.View):
    """確認開獎視圖"""
    def __init__(self, lottery_id: str):
        super().__init__(timeout=60)
        self.lottery_id = lottery_id

    @ui.button(label="✅ 確認開獎", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)

        if not lottery:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return

        if lottery.get("status") != "active":
            await interaction.response.send_message("❌ 此抽獎已經開過了！", ephemeral=True)
            return

        participants = lottery.get("participants", [])
        if not participants:
            lottery["status"] = "drawn"
            lottery["winner_ids"] = []
            save_lottery_data(data)

            try:
                await refresh_published_lottery_messages(
                    interaction.guild,
                    self.lottery_id
                )
            except Exception as ex:
                print(f"[Lottery] refresh announcements after empty draw failed: {type(ex).__name__}: {ex}")

            await interaction.response.send_message(
                "❌ 沒有人參加抽獎，本次抽獎已結束。",
                ephemeral=True
            )
            return

        # 新版唯一支援格式：
        # "prizes": [{"name": "月卡", "quantity": 5}, ...]
        prizes = lottery.get("prizes") or []
        if not prizes or not all(isinstance(p, dict) for p in prizes):
            await interaction.response.send_message(
                "❌ 這筆抽獎不是新版獎品格式，無法開獎。",
                ephemeral=True
            )
            return

        # 展開真正的獎項名額。
        # 月卡 x5 => 5 個獨立中獎名額。
        prize_list = []
        for prize in prizes:
            name = str(prize.get("name", "")).strip()
            try:
                quantity = int(prize.get("quantity", 0))
            except (TypeError, ValueError):
                quantity = 0

            if not name or quantity <= 0:
                continue

            prize_list.extend([name] * quantity)

        if not prize_list:
            await interaction.response.send_message(
                "❌ 沒有有效的獎品名額，無法開獎。",
                ephemeral=True
            )
            return

        try:
            max_participants = int(lottery.get("max_participants", len(participants)))
        except (TypeError, ValueError):
            max_participants = len(participants)

        # 同一場每個玩家最多中一次。
        # random.sample() 不放回抽樣，因此不會重複抽到同一個人。
        winner_count = min(
            len(participants),
            max_participants,
            len(prize_list)
        )

        winners = random.sample(participants, winner_count)

        lottery["winner_ids"] = winners
        lottery["status"] = "drawn"
        lottery["drawn_at"] = datetime.now().isoformat()
        save_lottery_data(data)

        # 把所有已發布公告更新成「已結束 / 查看抽獎結果」
        try:
            await refresh_published_lottery_messages(
                interaction.guild,
                self.lottery_id
            )
        except Exception as ex:
            print(f"[Lottery] refresh announcements after draw failed: {type(ex).__name__}: {ex}")

        result = f"🎉 **{lottery.get('title', '抽獎結果')}** 🎉\n\n"
        result += "**幸運得主：**\n"

        for i, wid in enumerate(winners):
            result += f"{i+1}. <@{wid}> → {prize_list[i]}\n"

        # 若獎品數大於參加人數，清楚標示剩餘未抽出的獎項。
        remaining = len(prize_list) - winner_count
        if remaining > 0:
            result += f"\nℹ️ 因參加人數不足，尚有 **{remaining}** 個獎項未送出。"

        await interaction.response.send_message(result)

    @ui.button(label="❌ 取消", style=discord.ButtonStyle.gray, row=0)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("✅ 已取消開獎", ephemeral=True)


class LotteryParticipantListView(ui.View):
    def __init__(self, lottery_id: str, page: int = 0, per_page: int = 30):
        super().__init__(timeout=300)
        self.lottery_id = lottery_id
        self.page = page
        self.per_page = per_page

        data = load_lottery_data()
        lottery = data["lotteries"].get(lottery_id, {})
        participants = lottery.get("participants", [])
        self.pages = max(1, (len(participants) + per_page - 1) // per_page)
        self.page = max(0, min(self.page, self.pages - 1))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1

    def build_embed(self):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)
        if not lottery:
            return discord.Embed(title="❌ 抽獎不存在")

        participants = lottery.get("participants", [])
        start = self.page * self.per_page
        current = participants[start:start + self.per_page]
        title = lottery.get("title", "未命名抽獎")

        embed = discord.Embed(
            title=f"👥 已參加人員｜{title}",
            description=(
                f"共 **{len(participants)} / {lottery.get('max_participants', 150)}** 人"
                f"｜第 **{self.page + 1}/{self.pages}** 頁"
            ),
            color=discord.Color.blurple()
        )

        if not current:
            embed.add_field(name="目前尚無參加者", value="—", inline=False)
        else:
            lines = [f"{start + i + 1}. <@{uid}>" for i, uid in enumerate(current)]
            embed.add_field(name="名單", value="\n".join(lines), inline=False)
        return embed

    @ui.button(label="◀ 上一頁", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = max(0, self.page - 1)
        data = load_lottery_data()
        participants = data["lotteries"].get(self.lottery_id, {}).get("participants", [])
        self.pages = max(1, (len(participants) + self.per_page - 1) // self.per_page)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @ui.button(label="下一頁 ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.pages - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


def build_public_lottery_embed(lottery_id: str):
    data = load_lottery_data()
    lottery = data["lotteries"].get(lottery_id)
    if not lottery:
        return None

    prizes = lottery.get("prizes") or []
    prize_text = "\n".join(
        f"{i+1}. {p.get('name', '獎品')} x{p.get('quantity', 1)}"
        for i, p in enumerate(prizes)
        if isinstance(p, dict)
    ) or "未設定"

    end_time = datetime.fromisoformat(lottery["end_time"])
    ended = lottery.get("status") not in ("active", "running")
    embed = discord.Embed(
        title=f"🎉 {lottery.get('title', '未命名抽獎')}",
        description=(
            "⚪ **抽獎已結束**\n點擊下方連結查看原始抽獎與開獎結果。"
            if ended else
            "🟢 **抽獎進行中**\n點擊下方連結前往原始抽獎訊息參加。"
        ),
        color=discord.Color.gold()
    )
    embed.add_field(name="🎁 禮物", value=prize_text, inline=False)
    embed.add_field(name="⏰ 截止時間", value=f"<t:{int(end_time.timestamp())}:f>", inline=True)
    embed.add_field(
        name="👥 最終參加" if ended else "👥 目前人數",
        value=f"{len(lottery.get('participants', []))}/{lottery.get('max_participants', 150)}",
        inline=True
    )
    if ended:
        winners = lottery.get("winner_ids", [])
        embed.add_field(
            name="🏆 開獎結果",
            value=("\n".join(f"{i+1}. <@{uid}>" for i, uid in enumerate(winners[:20])) if winners else "抽獎已結束"),
            inline=False
        )
    embed.add_field(name="👤 發起人", value=f"<@{lottery['creator_id']}>", inline=True)
    return embed


class PublicLotteryLinkView(ui.View):
    def __init__(self, jump_url: str, ended: bool = False):
        super().__init__(timeout=None)
        self.add_item(
            ui.Button(
                label="🏆 查看抽獎結果" if ended else "🎯 前往抽獎",
                style=discord.ButtonStyle.link,
                url=jump_url
            )
        )


def get_owner_lotteries(guild_id: int, user_id: int):
    data = load_lottery_data()
    rows = []
    for lottery_id, info in data.get("lotteries", {}).items():
        if str(info.get("guild_id", "")) not in ("", str(guild_id)):
            continue
        if int(info.get("creator_id", 0) or 0) != int(user_id):
            continue

        # 只處理新版 prizes 格式
        prizes = info.get("prizes") or []
        if prizes and not all(isinstance(p, dict) for p in prizes):
            continue

        try:
            created = datetime.fromisoformat(info.get("created_at", ""))
        except Exception:
            created = datetime.min
        rows.append((lottery_id, info, created))

    rows.sort(key=lambda x: x[2], reverse=True)
    return rows


def owner_lottery_list_embed(guild_id: int, user_id: int):
    rows = get_owner_lotteries(guild_id, user_id)
    embed = discord.Embed(
        title="📋 我發起的抽獎",
        description=f"共 **{len(rows)}** 筆，請從下方選單選擇要管理的抽獎。",
        color=discord.Color.blurple()
    )

    if not rows:
        embed.add_field(name="目前沒有紀錄", value="你還沒有發起過抽獎。", inline=False)
        return embed

    for idx, (lottery_id, info, _) in enumerate(rows[:20], start=1):
        title = info.get("title", "未命名抽獎")
        status = info.get("status", "unknown")
        status_text = "🟢 進行中" if status in ("active", "running") else "⚪ 已結束"
        embed.add_field(
            name=f"{idx}. {status_text}｜{title}",
            value=(
                f"👥 {len(info.get('participants', []))}/{info.get('max_participants', 150)}\n"
                f"⏰ {info.get('end_time', '—')}\n"
                f"ID：`{lottery_id}`"
            ),
            inline=False
        )
    return embed


class OwnerLotterySelect(ui.Select):
    def __init__(self, guild_id: int, user_id: int):
        self.guild_id = guild_id
        self.user_id = user_id
        rows = get_owner_lotteries(guild_id, user_id)[:25]

        if rows:
            options = []
            for lottery_id, info, _ in rows:
                title = info.get("title", "未命名抽獎")
                status = "進行中" if info.get("status") in ("active", "running") else "已結束"
                options.append(
                    discord.SelectOption(
                        label=title[:100],
                        value=lottery_id,
                        description=f"{status}｜{len(info.get('participants', []))}/{info.get('max_participants', 150)}"[:100]
                    )
                )
            disabled = False
        else:
            options = [discord.SelectOption(label="目前沒有抽獎", value="__none__")]
            disabled = True

        super().__init__(
            placeholder="選擇要管理的抽獎",
            min_values=1,
            max_values=1,
            options=options,
            disabled=disabled,
            custom_id="owner_lottery_select"
        )

    async def callback(self, interaction: discord.Interaction):
        lottery_id = self.values[0]
        if lottery_id == "__none__":
            await interaction.response.send_message("目前沒有可以管理的抽獎。", ephemeral=True)
            return

        data = load_lottery_data()
        info = data["lotteries"].get(lottery_id)
        if not info or int(info.get("creator_id", 0) or 0) != interaction.user.id:
            await interaction.response.send_message("❌ 你不是這筆抽獎的發起人。", ephemeral=True)
            return

        view = OwnerLotteryManageView(lottery_id)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True
        )


class OwnerLotteryListView(ui.View):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.add_item(OwnerLotterySelect(guild_id, user_id))


class OwnerLotteryManageView(ui.View):
    def __init__(self, lottery_id: str):
        super().__init__(timeout=300)
        self.lottery_id = lottery_id

        data = load_lottery_data()
        info = data["lotteries"].get(lottery_id, {})
        ended = info.get("status") not in ("active", "running")

        self.edit_time.disabled = ended
        self.draw_now.disabled = ended

    def build_embed(self):
        data = load_lottery_data()
        info = data["lotteries"].get(self.lottery_id)
        if not info:
            return discord.Embed(title="❌ 抽獎不存在")

        prizes = info.get("prizes") or []
        prize_text = "\n".join(
            f"{i+1}. {p.get('name', '獎品')} x{p.get('quantity', 1)}"
            for i, p in enumerate(prizes)
            if isinstance(p, dict)
        ) or "未設定"

        status = "🟢 進行中" if info.get("status") in ("active", "running") else "⚪ 已結束"
        embed = discord.Embed(
            title=f"🎉 {info.get('title', '未命名抽獎')}",
            description=f"{status}\nOwner 管理介面",
            color=discord.Color.gold()
        )
        embed.add_field(name="🎁 獎品", value=prize_text, inline=False)
        embed.add_field(
            name="👥 參加人數",
            value=f"{len(info.get('participants', []))}/{info.get('max_participants', 150)}",
            inline=True
        )
        embed.add_field(name="⏰ 截止時間", value=info.get("end_time", "—"), inline=True)
        return embed

    def _is_owner(self, interaction, info):
        return info and int(info.get("creator_id", 0) or 0) == interaction.user.id

    @ui.button(label="👥 已參加人員", style=discord.ButtonStyle.secondary, row=0)
    async def participants(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        info = data["lotteries"].get(self.lottery_id)
        if not self._is_owner(interaction, info):
            await interaction.response.send_message("❌ 你不是這筆抽獎的發起人。", ephemeral=True)
            return
        view = LotteryParticipantListView(self.lottery_id)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @ui.button(label="✏️ 修改截止時間", style=discord.ButtonStyle.secondary, row=0)
    async def edit_time(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        info = data["lotteries"].get(self.lottery_id)
        if not self._is_owner(interaction, info):
            await interaction.response.send_message("❌ 你不是這筆抽獎的發起人。", ephemeral=True)
            return
        if info.get("status") not in ("active", "running"):
            await interaction.response.send_message("ℹ️ 已結束的抽獎不能修改時間。", ephemeral=True)
            return
        await interaction.response.send_modal(EditTimeModal(self.lottery_id))

    @ui.button(label="🔔 提前開獎", style=discord.ButtonStyle.danger, row=0)
    async def draw_now(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        info = data["lotteries"].get(self.lottery_id)
        if not self._is_owner(interaction, info):
            await interaction.response.send_message("❌ 你不是這筆抽獎的發起人。", ephemeral=True)
            return
        if info.get("status") not in ("active", "running"):
            await interaction.response.send_message("ℹ️ 這筆抽獎已經結束。", ephemeral=True)
            return
        await interaction.response.send_message(
            "⚠️ 確定要提前開獎嗎？",
            view=ConfirmDrawView(self.lottery_id),
            ephemeral=True
        )

    @ui.button(label="📣 發布抽獎資訊", style=discord.ButtonStyle.primary, row=1)
    async def publish(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        info = data["lotteries"].get(self.lottery_id)
        if not self._is_owner(interaction, info):
            await interaction.response.send_message("❌ 你不是這筆抽獎的發起人。", ephemeral=True)
            return

        channel_id = info.get("channel_id")
        message_id = info.get("message_id")
        if not channel_id or not message_id:
            await interaction.response.send_message("❌ 找不到原始抽獎訊息。", ephemeral=True)
            return

        # 確認原始公開抽獎訊息仍存在，避免發布一個失效連結
        try:
            target_channel = interaction.guild.get_channel(int(channel_id))
            if target_channel is None:
                target_channel = await interaction.guild.fetch_channel(int(channel_id))
            target_message = await target_channel.fetch_message(int(message_id))
            jump_url = target_message.jump_url
        except Exception:
            await interaction.response.send_message(
                "❌ 原始抽獎訊息不存在或無法存取，請重新建立/發布抽獎。",
                ephemeral=True
            )
            return

        embed = build_public_lottery_embed(self.lottery_id)
        if embed is None:
            await interaction.response.send_message("❌ 抽獎不存在。", ephemeral=True)
            return

        published_message = await interaction.channel.send(
            content=f"🔗 直接連結：{jump_url}",
            embed=embed,
            view=PublicLotteryLinkView(
                jump_url,
                ended=info.get("status") not in ("active", "running")
            )
        )
        data = load_lottery_data()
        saved = data["lotteries"].get(self.lottery_id)
        if saved is not None:
            refs = saved.setdefault("published_messages", [])
            ref = {"channel_id": published_message.channel.id, "message_id": published_message.id}
            if ref not in refs:
                refs.append(ref)
            save_lottery_data(data)
        await interaction.response.send_message("✅ 已發布抽獎資訊到目前頻道。", ephemeral=True)



async def refresh_published_lottery_messages(guild, lottery_id: str):
    data = load_lottery_data()
    lottery = data["lotteries"].get(lottery_id)
    if not lottery:
        return
    channel_id = lottery.get("channel_id")
    message_id = lottery.get("message_id")
    if not channel_id or not message_id:
        return
    jump_url = f"https://discord.com/channels/{guild.id}/{channel_id}/{message_id}"
    embed = build_public_lottery_embed(lottery_id)
    for ref in lottery.get("published_messages", []):
        try:
            channel = guild.get_channel(int(ref["channel_id"])) or await guild.fetch_channel(int(ref["channel_id"]))
            message = await channel.fetch_message(int(ref["message_id"]))
            await message.edit(
                content=f"🔗 直接連結：{jump_url}",
                embed=embed,
                view=PublicLotteryLinkView(jump_url, ended=True)
            )
        except Exception as ex:
            print(f"[Lottery] refresh published message failed: {type(ex).__name__}: {ex}")


class LotteryJoinView(ui.View):
    """抽獎參與視圖"""
    def __init__(self, lottery_id: str):
        super().__init__(timeout=None)
        self.lottery_id = lottery_id

    @ui.button(label="🎯 加入抽獎", style=discord.ButtonStyle.success, row=0)
    async def join_lottery(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)

        if not lottery:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return
        if lottery["status"] != "active":
            await interaction.response.send_message("❌ 此抽獎已結束！", ephemeral=True)
            return
        if len(lottery["participants"]) >= lottery["max_participants"]:
            await interaction.response.send_message("❌ 名額已滿！", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        if user_id in lottery["participants"]:
            await interaction.response.send_message("❌ 你已經參加過了！", ephemeral=True)
            return

        lottery["participants"].append(user_id)
        save_lottery_data(data)

        await interaction.response.send_message(
            f"✅ 加入成功！目前 {len(lottery['participants'])}/{lottery['max_participants']} 人",
            ephemeral=True
        )
        await self.update_embed(interaction.message)

    @ui.button(label="👥 已參加人員", style=discord.ButtonStyle.secondary, row=1)
    async def participant_list(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)
        if not lottery:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return

        view = LotteryParticipantListView(self.lottery_id)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True
        )

    @ui.button(label="📣 發布抽獎資訊", style=discord.ButtonStyle.primary, row=1)
    async def publish_lottery(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)

        if not lottery:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return

        if lottery.get("creator_id") != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ 只有發起人或管理員可以發布抽獎資訊！", ephemeral=True)
            return

        channel_id = lottery.get("channel_id")
        message_id = lottery.get("message_id")
        jump_url = None

        if channel_id and message_id:
            jump_url = f"https://discord.com/channels/{interaction.guild_id}/{channel_id}/{message_id}"

        if not jump_url:
            await interaction.response.send_message(
                "❌ 找不到可公開存取的原始抽獎訊息。請重新建立這筆抽獎。",
                ephemeral=True
            )
            return

        embed = build_public_lottery_embed(self.lottery_id)
        if embed is None:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return

        published_message = await interaction.channel.send(
            content=f"🔗 直接連結：{jump_url}",
            embed=embed,
            view=PublicLotteryLinkView(
                jump_url,
                ended=lottery.get("status") not in ("active", "running")
            )
        )
        data = load_lottery_data()
        saved = data["lotteries"].get(self.lottery_id)
        if saved is not None:
            refs = saved.setdefault("published_messages", [])
            ref = {"channel_id": published_message.channel.id, "message_id": published_message.id}
            if ref not in refs:
                refs.append(ref)
            save_lottery_data(data)
        await interaction.response.send_message("✅ 已發布抽獎資訊到目前頻道。", ephemeral=True)

    @ui.button(label="✏️ 修改時間", style=discord.ButtonStyle.secondary, row=0)
    async def edit_time(self, interaction: discord.Interaction, button: ui.Button):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)

        if not lottery:
            await interaction.response.send_message("❌ 抽獎不存在！", ephemeral=True)
            return
        if lottery["creator_id"] != interaction.user.id:
            await interaction.response.send_message("❌ 只有發起人可以修改時間！", ephemeral=True)
            return
        if lottery["status"] != "active":
            await interaction.response.send_message("❌ 此抽獎已結束，無法修改時間！", ephemeral=True)
            return

        await interaction.response.send_modal(EditTimeModal(self.lottery_id))

    @ui.button(label="🔔 提前開獎", style=discord.ButtonStyle.primary, row=0)
    async def draw_now(self, interaction: discord.Interaction, button: ui.Button):
        # 顯示確認視窗
        view = ConfirmDrawView(self.lottery_id)
        await interaction.response.send_message(
            "⚠️ 確定要提前開獎嗎？此操作無法復原！",
            view=view,
            ephemeral=True
        )
    async def update_embed(self, message):
        data = load_lottery_data()
        lottery = data["lotteries"].get(self.lottery_id)
        if not lottery:
            return

        end_time = datetime.fromisoformat(lottery["end_time"])
        
        prizes = lottery.get("prizes", [])
        if prizes:
            prize_text = "\n".join([f"{i+1}. {p['name']} x{p['quantity']}" for i, p in enumerate(prizes)])
        else:
            prize_text = lottery.get("prize", "")
        
        embed = discord.Embed(title=f"🎉 {lottery.get('title', '未命名抽獎')}", color=discord.Color.gold())
        embed.add_field(name="🎁 禮物", value=prize_text, inline=False)
        embed.add_field(name="👤 發起人", value=f"<@{lottery['creator_id']}>", inline=True)
        embed.add_field(name="⏰ 截止時間", value=f"<t:{int(end_time.timestamp())}:f>", inline=True)
        embed.add_field(name="👥 已參加", value=f"{len(lottery['participants'])}", inline=True)
        embed.add_field(name="📊 名額", value=f"{lottery['max_participants']}", inline=True)
        embed.set_footer(text=f"抽獎ID: {self.lottery_id}")
        await message.edit(embed=embed)


def create_lottery_embed():
    """創建抽獎系統說明 embed"""
    embed = discord.Embed(
        title="🎉 抽獎系統",
        description="點擊下方按鈕發起抽獎！",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="📝 功能",
        value="發起抽獎 → 新增禮物(含數量) → 讓成員加入 → 開獎",
        inline=False
    )
    return embed
