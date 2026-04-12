import discord
from discord.ext import commands
import os
import traceback

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True  # precisa estar ativado no portal também!

bot = commands.Bot(command_prefix="!", intents=intents)

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

# ==================== TOKEN SEGURO ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN")
else:
    try:
        bot.run(TOKEN)
    except Exception:
        print("❌ Erro ao iniciar o bot:")
        traceback.print_exc()
