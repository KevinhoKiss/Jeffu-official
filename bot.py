import discord
from discord.ext import commands
import os
import traceback
import re

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔴 COLOQUE SEU ID AQUI
DONO_ID = 123456789012345678

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
                await message.delete()

                # 🔴 BANIMENTO
                await message.guild.ban(
                    message.author,
                    reason="Divulgação de servidor (convite)"
                )

                print(f"🚫 {message.author} foi banido por divulgar convite")

                # 🔴 AVISO NA SUA DM
                try:
                    dono = await bot.fetch_user(DONO_ID)
                    await dono.send(
                        f"🚨 Usuário banido\n"
                        f"👤: {message.author} ({message.author.id})\n"
                        f"📌 Motivo: Divulgação de servidor\n"
                        f"🌐 Servidor: {message.guild.name}"
                    )
                except Exception:
                    print("❌ Não consegui enviar DM para o dono")

                return

        except Exception:
            # Se não conseguir verificar, ainda bane por segurança
            await message.delete()

            await message.guild.ban(
                message.author,
                reason="Convite suspeito/não verificado"
            )

            print(f"⚠️ {message.author} banido (convite não verificado)")

            try:
                dono = await bot.fetch_user(DONO_ID)
                await dono.send(
                    f"🚨 Usuário banido\n"
                    f"👤: {message.author} ({message.author.id})\n"
                    f"📌 Motivo: Convite não verificado\n"
                    f"🌐 Servidor: {message.guild.name}"
                )
            except Exception:
                print("❌ Não consegui enviar DM para o dono")

            return

    # ==================== RESPOSTAS AUTOMÁTICAS ====================
    texto = message.content.lower()

    palavras_chave = [
        "login", "senha", "esqueci", "não consigo",
        "nao consigo", "problema", "ticket", "suporte"
    ]

    if any(p in texto for p in palavras_chave):
        await message.channel.send("O <#1479642544429076500> foi criado justamente para isso")
        print("✅ Resposta enviada")
    else:
        print("❌ Nenhuma palavra-chave encontrada")

    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ Token não encontrado! Defina DISCORD_TOKEN")
else:
    try:
        bot.run(TOKEN)
    except Exception:
        print("❌ Erro ao iniciar o bot:")
        traceback.print_exc()
