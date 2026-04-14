import discord
from discord.ext import commands
import os
import traceback
import re
import json

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔴 CONFIG FIXA
DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

CARGOS_AUTORIZADOS = [
    1464361173305655389,
    1409338610854920374,
    1409306638548209826
]

ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"

# ==================== BANCO ====================
def carregar():
    if not os.path.exists(ARQUIVO):
        return {}
    with open(ARQUIVO, "r") as f:
        return json.load(f)

def salvar(data):
    with open(ARQUIVO, "w") as f:
        json.dump(data, f, indent=4)

def carregar_autorizados():
    if not os.path.exists(AUTORIZADOS_FILE):
        return []
    with open(AUTORIZADOS_FILE, "r") as f:
        return json.load(f)

def salvar_autorizados(lista):
    with open(AUTORIZADOS_FILE, "w") as f:
        json.dump(lista, f, indent=4)

# ==================== SLASH ====================
@bot.tree.command(name="autorizar", description="Autorizar usuário")
async def autorizar(interaction: discord.Interaction, user: discord.Member):

    if not (interaction.user.id == DONO_ID or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)

    autorizados = carregar_autorizados()

    if user.id not in autorizados:
        autorizados.append(user.id)
        salvar_autorizados(autorizados)

    await interaction.response.send_message(f"✅ {user.mention} autorizado!", ephemeral=True)

# ==================== READY ====================
@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} conectado!')

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="Suporte - Tickets")
        )

        # 🔥 SYNC FORÇADO (resolve slash)
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos sincronizados")

    except Exception:
        traceback.print_exc()

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"📨 {message.author}: {message.content}")
    texto = message.content.lower()
    texto_limpo = texto.strip()

    # ==================== SAUDAÇÕES ====================
    saudacoes = {
        "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
        "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
        "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
    }

    for chave in saudacoes:
        if texto_limpo.startswith(chave):
            await message.reply(saudacoes[chave], mention_author=False)

    # ==================== INTERAÇÕES ====================
    if re.search(r"(agradecido|obg|obrigado).*(jeffu)?", texto):
        await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)

    if re.search(r"(te amo|amo vc|amo você).*(jeffu)?", texto):
        await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)

    if re.search(r"(cala boca|calaboca|clbc|cbc|fica quieto|quieto).*(jeffu)?", texto):
        await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)

    # ==================== BLOQUEIO ====================
    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        try:
            await message.delete()
            await message.guild.ban(message.author, reason=MOTIVO)
            return
        except:
            pass

    # ==================== COMANDOS ====================
    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
