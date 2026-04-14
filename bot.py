import discord
from discord.ext import commands
import os
import traceback
import re
import json
try:
    from pymongo import MongoClient
except:
    MongoClient = None

# ==================== CONFIG ====================
LOG_CHANNEL_ID = 1466542559730991164  # seu canal de log

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

CARGOS_AUTORIZADOS = [
    1464361173305655389,
    1409338610854920374,
    1409306638548209826
]

ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"

convites = {}

# ==================== FUNÇÃO DE LOG ====================
async def log(guild, mensagem):
    canal = guild.get_channel(LOG_CHANNEL_ID)
    if canal:
        await canal.send(mensagem)

# ==================== BOTÃO ====================
class AceitarView(discord.ui.View):
    def __init__(self, dono_id):
        super().__init__(timeout=60)
        self.dono_id = dono_id

    @discord.ui.button(label="✅ Aceitar convite", style=discord.ButtonStyle.green)
    async def aceitar(self, interaction: discord.Interaction, button: discord.ui.Button):

        user_id = str(interaction.user.id)
        dono_id = str(self.dono_id)

        data = carregar()

        if dono_id not in data:
            return await interaction.response.send_message("❌ Família não existe", ephemeral=True)

        if user_id in data[dono_id]["membros"]:
            return await interaction.response.send_message("❌ Você já está na família", ephemeral=True)

        data[dono_id]["membros"].append(user_id)
        salvar(data)

        cargo = discord.utils.get(interaction.guild.roles, name="Família")
        if cargo:
            await interaction.user.add_roles(cargo)

        await interaction.response.send_message("✅ Você entrou na família!", ephemeral=True)
        
# ==================== MONGO (NOVO) ====================
mongo = None
try:
    mongo = MongoClient(os.getenv("MONGO_URI"))
    db = mongo["bot"]
    familias_db = db["familias"]
except:
    mongo = None

# ==================== BANCO ====================
def carregar():
    if not os.path.exists(ARQUIVO):
        return {}
    try:
        with open(ARQUIVO, "r") as f:
            return json.load(f)
    except:
        return {}

def salvar(data):
    with open(ARQUIVO, "w") as f:
        json.dump(data, f, indent=4)

def carregar_autorizados():
    if not os.path.exists(AUTORIZADOS_FILE):
        return []
    try:
        with open(AUTORIZADOS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def salvar_autorizados(lista):
    with open(AUTORIZADOS_FILE, "w") as f:
        json.dump(lista, f, indent=4)
# ==================== SISTEMA FAMÍLIA COMPLETO ====================

import time

convites = {}

# ==================== COMANDO FAMÍLIA ====================

@bot.command()
async def familia(ctx):

    autorizados = carregar_autorizados()

    if not (
        any(role.id in CARGOS_AUTORIZADOS for role in ctx.author.roles)
        or ctx.author.guild_permissions.administrator
        or ctx.author.id == DONO_ID
        or ctx.author.id in autorizados
    ):
        return await ctx.reply("❌ Você não tem permissão!", mention_author=False)

    data = carregar()
    user_id = str(ctx.author.id)

    if user_id not in data:
        data[user_id] = {
            "nome": "Minha Família",
            "dono": user_id,
            "membros": [user_id]
        }
        salvar(data)

    membros = "\n".join(f"<@{m}>" for m in data[user_id]["membros"])

    embed = discord.Embed(
        title=f"👥 {data[user_id]['nome']}",
        description=membros,
        color=0x5865F2
    )

    await ctx.reply(embed=embed, mention_author=False)


# ==================== BOTÃO ====================

class AceitarView(discord.ui.View):
    def __init__(self, dono_id):
        super().__init__(timeout=60)
        self.dono_id = dono_id

    @discord.ui.button(label="✅ Aceitar convite", style=discord.ButtonStyle.green)
    async def aceitar(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id not in convites:
            return await interaction.response.send_message("❌ Convite inválido", ephemeral=True)

        convite = convites[interaction.user.id]

        if time.time() - convite["tempo"] > 60:
            del convites[interaction.user.id]
            return await interaction.response.send_message("⏰ Convite expirou", ephemeral=True)

        dono_id = str(convite["dono"])
        user_id = str(interaction.user.id)

        data = carregar()

        if dono_id not in data:
            return await interaction.response.send_message("❌ Família não existe", ephemeral=True)

        if user_id in data[dono_id]["membros"]:
            return await interaction.response.send_message("❌ Você já está na família", ephemeral=True)

        data[dono_id]["membros"].append(user_id)
        salvar(data)

        del convites[interaction.user.id]

        cargo = discord.utils.get(interaction.guild.roles, name="Família")
        if cargo:
            await interaction.user.add_roles(cargo)

        await interaction.response.send_message("✅ Você entrou na família!", ephemeral=True)


# ==================== CONVIDAR ====================

@bot.command()
async def convidar(ctx, membro: discord.Member):

    autorizados = carregar_autorizados()

    if not (
        any(role.id in CARGOS_AUTORIZADOS for role in ctx.author.roles)
        or ctx.author.guild_permissions.administrator
        or ctx.author.id == DONO_ID
        or ctx.author.id in autorizados
    ):
        return await ctx.reply("❌ Você não tem permissão!", mention_author=False)

    convites[membro.id] = {
        "dono": ctx.author.id,
        "tempo": time.time()
    }

    view = AceitarView(ctx.author.id)

    try:
        await membro.send(
            f"📩 Convite para a família de {ctx.author.mention} (expira em 60s)",
            view=view
        )

        await ctx.reply(f"✅ Convite enviado para {membro.mention}")

    except:
        await ctx.reply("❌ Não consegui enviar DM para esse usuário")


# ==================== SAIR ====================

@bot.command()
async def sair(ctx):
    data = carregar()
    user_id = str(ctx.author.id)

    for dono, info in data.items():
        if user_id in info["membros"]:

            if dono == user_id:
                return await ctx.reply("❌ Você é o dono! Não pode sair.")

            info["membros"].remove(user_id)
            salvar(data)

            return await ctx.reply("👋 Você saiu da família!")

    await ctx.reply("❌ Você não está em nenhuma família")


# ==================== EXPULSAR ====================

@bot.command()
async def expulsar(ctx, membro: discord.Member):
    data = carregar()
    dono_id = str(ctx.author.id)

    if dono_id not in data:
        return await ctx.reply("❌ Você não tem família")

    if data[dono_id]["dono"] != dono_id:
        return await ctx.reply("❌ Apenas o dono pode expulsar")

    user_id = str(membro.id)

    if user_id not in data[dono_id]["membros"]:
        return await ctx.reply("❌ Esse usuário não está na família")

    data[dono_id]["membros"].remove(user_id)
    salvar(data)

    await ctx.reply(f"🚫 {membro.mention} foi expulso")


# ==================== PAINEL ====================

@bot.command()
async def painel(ctx):
    data = carregar()
    user_id = str(ctx.author.id)

    for dono, info in data.items():
        if user_id in info["membros"]:

            membros = "\n".join(f"<@{m}>" for m in info["membros"])

            embed = discord.Embed(
                title=f"🏠 {info['nome']}",
                description=membros,
                color=0x5865F2
            )

            embed.add_field(name="👑 Dono", value=f"<@{info['dono']}>")

            return await ctx.reply(embed=embed)

    await ctx.reply("❌ Você não está em nenhuma família")

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

        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos sincronizados")

    except Exception:
        traceback.print_exc()

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    try:
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

            if (
                message.author.guild_permissions.administrator
                or message.author.id == DONO_ID
            ):
                return

            try:
                await message.delete()

                await log(
                    message.guild,
                    f"⚠️ {message.author} enviou link: {message.content}"
                )

                await message.guild.ban(message.author, reason=MOTIVO)

                await log(
                    message.guild,
                    f"🚫 {message.author} foi banido por divulgação"
                )

                return
            except:
                pass

        # ==================== COMANDOS ====================
        await bot.process_commands(message)

    except Exception as e:
        print(f"Erro no on_message: {e}")

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
