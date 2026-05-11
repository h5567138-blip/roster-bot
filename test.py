import os
import discord

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_message(message):
    print(f"Got message: {message.content} from {message.author}")
    if message.author.bot:
        return
    await message.channel.send(f"收到: {message.content}")

@client.event
async def on_ready():
    print(f"Bot is online: {client.user}")

client.run(TOKEN)
