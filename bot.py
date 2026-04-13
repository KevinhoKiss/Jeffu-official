import discord
from discord.ext import commands
import os
import traceback
import re

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔴 COLOQUE SEU ID CERTO
DONO_ID = 123456789012345678

# ==================== EVENTO READY ====================
@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} conectado com sucesso!')

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        motivo = "Divulgação de servidor (convite)"

        try:
            invite_link = re.search(invite_pattern, message.content).group(0)
            invite = await bot.fetch_invite(invite_link)

            if invite.guild.id != message.guild.id:

                # 🔴 TENTA AVISAR O USUÁRIO
                try:
                    await message.author.send(
                        f"🚫 Você foi banido de **{message.guild.name}**\n"
                        f"Motivo: {motivo}"
                    )
                except:
                    print("❌ Não consegui mandar DM para o usuário")

                await message.delete()

                # 🔴 BAN
                await message.guild.ban(
                    message.author,
                    reason=motivo
                )

                print(f"🚫 {message.author} banido")

                # 🔴 AVISO AO DONO (FORMA CORRETA)
                dono = bot.get_user(DONO_ID)

                if dono is None:
                    dono = await bot.fetch_user(DONO_ID)

                try:
                    await dono.send(
                        f"🚨 BANIMENTO\n\n"
                        f"👤 Usuário: {message.author}\n"
                        f"🆔 ID: {message.author.id}\n"
                        f"📌 Motivo: {motivo}\n"
                        f"💬 Mensagem: {message.content}\n"
                        f"🌐 Servidor: {message.guild.name}"
                    )
                except:
                    print("❌ Não consegui enviar DM para você")

                return

        except Exception:
            motivo = "Convite suspeito ou não verificado"

            try:
                await message.author.send(
                    f"🚫 Você foi banido de **{message.guild.name}**\n"
                    f"Motivo: {motivo}"
                )
            except:
                pass

            await message.delete()
            await message.guild.ban(message.author, reason=motivo)

            dono = bot.get_user(DONO_ID)
            if dono is None:
                dono = await bot.fetch_user(DONO_ID)

            try:
                await dono.send(
                    f"🚨 BANIMENTO\n\n"
                    f"👤 Usuário: {message.author}\n"
                    f"🆔 ID: {message.author.id}\n"
                    f"📌 Motivo: {motivo}\n"
                    f"💬 Mensagem: {message.content}\n"
                    f"🌐 Servidor: {message.guild.name}"
                )
            except:
                pass

            return

    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ Token não encontrado!")
else:
    bot.run(TOKEN)
