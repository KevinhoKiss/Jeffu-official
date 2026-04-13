import discord
from discord.ext import commands
import os
import traceback
import re
from openai import OpenAI

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔴 CONFIG FIXA
DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

# 🔑 IA
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ==================== READY ====================
@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} conectado!')

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="Suporte - Tickets")
        )
    except Exception:
        traceback.print_exc()

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"📨 {message.author}: {message.content}")
    texto = message.content.lower()

    # ==================== BLOQUEIO DE CONVITES ====================
    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        try:
            invite_link = re.search(invite_pattern, message.content).group(0)
            invite = await bot.fetch_invite(invite_link)

            if invite.guild.id != message.guild.id:

                try:
                    await message.author.send(
                        f"🚫 Você foi banido de **{message.guild.name}**\nMotivo: {MOTIVO}"
                    )
                except:
                    pass

                await message.delete()
                await message.guild.ban(message.author, reason=MOTIVO)

                dono = bot.get_user(DONO_ID) or await bot.fetch_user(DONO_ID)

                try:
                    await dono.send(
                        f"🚨 BANIMENTO\n\n"
                        f"👤 {message.author} ({message.author.id})\n"
                        f"📌 Motivo: {MOTIVO}\n"
                        f"💬 {message.content}\n"
                        f"🌐 {message.guild.name}"
                    )
                except:
                    pass

                return

        except:
            await message.delete()
            await message.guild.ban(message.author, reason="Convite suspeito")
            return

    # ==================== SUPORTE ====================
    palavras_chave = [
        "login", "senha", "esqueci", "não consigo", "acesso",
        "nao consigo", "ajuda", "ticket", "suporte"
    ]

    if any(p in texto for p in palavras_chave):
        await message.reply(
            "🔐 Para suporte, vá em <#1479642544429076500>",
            mention_author=False
        )
        return

    # ==================== QUEDA DO SITE ====================
    frases_site = [
        "o site caiu",
        "site caiu",
        "site tá fora",
        "site ta fora",
        "site offline",
        "site não funciona",
        "site nao funciona",
        "site saiu do ar"
    ]

    if any(frase in texto for frase in frases_site):
        await message.reply(
            "🌐 Veja em <#1409296003034644542>",
            mention_author=False
        )
        return

    # ==================== IA ====================
    try:
        resposta = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Você é um bot de suporte de Discord. Responda de forma curta, clara e útil."},
                {"role": "user", "content": message.content}
            ]
        )

        await message.reply(
            resposta.choices[0].message.content,
            mention_author=False
        )

    except Exception as e:
        print("❌ Erro IA:", e)

    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
