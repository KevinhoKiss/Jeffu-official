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

# 🔑 IA (usa variável de ambiente)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

    texto = message.content.lower()

    # ==================== BLOQUEIO DE CONVITES ====================
    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        print("🚨 Convite detectado!")

        try:
            invite_link = re.search(invite_pattern, message.content).group(0)
            invite = await bot.fetch_invite(invite_link)

            if invite.guild.id != message.guild.id:

                # DM usuário
                try:
                    await message.author.send(
                        f"🚫 Você foi banido de **{message.guild.name}**\nMotivo: {MOTIVO}"
                    )
                except:
                    print("❌ Não consegui mandar DM para o usuário")

                await message.delete()
                await message.guild.ban(message.author, reason=MOTIVO)

                print(f"🚫 {message.author} banido")

                # DM dono
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
            print("⚠️ Erro ao verificar convite")

            await message.delete()
            await message.guild.ban(message.author, reason="Convite suspeito")

            return

    # ==================== RESPOSTA RÁPIDA (SENHA / SUPORTE) ====================
    palavras_chave = [
        "login", "senha", "esqueci", "não consigo", "acesso",
        "nao consigo", "problema", "ajuda", "ticket", "suporte"
    ]

    if any(p in texto for p in palavras_chave):
        await message.reply(
            "🔐 Para suporte, vá em <#1479642544429076500>",
            mention_author=False
        )
        return

    # ==================== IA (CHATGPT) ====================
    try:
        resposta = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Você é um bot de suporte de Discord. Responda de forma curta, clara e útil."
                },
                {
                    "role": "user",
                    "content": message.content
                }
            ]
        )

        texto_resposta = resposta.choices[0].message.content

        await message.reply(texto_resposta, mention_author=False)

    except Exception as e:
        print("❌ Erro na IA:", e)

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
