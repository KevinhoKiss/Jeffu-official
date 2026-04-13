import discord
from discord.ext import commands
import os
import traceback
import re

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔴 CONFIG FIXA (sem painel)
DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

# ==================== EVENTO READY ====================
@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} conectado com sucesso!')
    
    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="Suporte - Tickets")
        )
        print('✅ Status definido!')
    except Exception:
        print('❌ Erro ao definir status:')
        traceback.print_exc()

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"📨 {message.author}: {message.content}")

    # ==================== BLOQUEIO DE CONVITES ====================
    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        try:
            invite_link = re.search(invite_pattern, message.content).group(0)
            invite = await bot.fetch_invite(invite_link)

            if invite.guild.id != message.guild.id:

                # 🔴 AVISA O USUÁRIO
                try:
                    await message.author.send(
                        f"🚫 Você foi banido de **{message.guild.name}**\nMotivo: {MOTIVO}"
                    )
                except:
                    print("❌ Não consegui mandar DM para o usuário")

                await message.delete()

                # 🔴 BAN
                await message.guild.ban(
                    message.author,
                    reason=MOTIVO
                )

                print(f"🚫 {message.author} banido")

                # 🔴 AVISA O DONO
                dono = bot.get_user(DONO_ID) or await bot.fetch_user(DONO_ID)

                try:
                    await dono.send(
                        f"🚨 BANIMENTO\n\n"
                        f"👤 Usuário: {message.author}\n"
                        f"🆔 ID: {message.author.id}\n"
                        f"📌 Motivo: {MOTIVO}\n"
                        f"💬 Mensagem: {message.content}\n"
                        f"🌐 Servidor: {message.guild.name}"
                    )
                except:
                    print("❌ Não consegui enviar DM para você")

                return

        except Exception:
            motivo_erro = "Convite suspeito ou não verificado"

            try:
                await message.author.send(
                    f"🚫 Você foi banido de **{message.guild.name}**\nMotivo: {motivo_erro}"
                )
            except:
                pass

            await message.delete()
            await message.guild.ban(message.author, reason=motivo_erro)

            dono = bot.get_user(DONO_ID) or await bot.fetch_user(DONO_ID)

            try:
                await dono.send(
                    f"🚨 BANIMENTO\n\n"
                    f"👤 Usuário: {message.author}\n"
                    f"🆔 ID: {message.author.id}\n"
                    f"📌 Motivo: {motivo_erro}\n"
                    f"💬 Mensagem: {message.content}\n"
                    f"🌐 Servidor: {message.guild.name}"
                )
            except:
                pass

            return

    # ==================== RESPOSTA AUTOMÁTICA ====================
    texto = message.content.lower()

    palavras_chave = [
        "login", "senha", "esqueci",
        "ajuda", "ticket", "suporte"
    ]

    if any(p in texto for p in palavras_chave):
        await message.channel.send("<#1479642544429076500>")
        print("✅ Resposta enviada")
    else:
        print("❌ Nenhuma palavra-chave encontrada")

    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ Token não encontrado!")
else:
    try:
        bot.run(TOKEN)
    except Exception:
        print("❌ Erro ao iniciar o bot:")
        traceback.print_exc()
