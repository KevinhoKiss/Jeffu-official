import discord
from discord.ext import commands
import os
import json
import re
import random
import traceback
import time

# ==================== CONFIG ====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

CARGO_FAMILIA = "Família"
CARGO_MUTADO = "Mutado"

LOG_CHANNEL_ID = 1466542559730991164  # COLOQUE O ID DO CANAL DE LOG

ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"
WARNS_FILE = "warns.json"

cooldown = {}
convites = {}

# ==================== JSON ====================
def load_json(file, default):
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default, f)

    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return default

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

# ==================== LOG ====================
async def log(guild, msg):
    canal = guild.get_channel(LOG_CHANNEL_ID)
    if canal:
        await canal.send(msg)

# ==================== FAMÍLIAS ====================
@bot.command()
async def criar_familia(ctx, *, nome):
    data = load_json(ARQUIVO, {})
    user_id = str(ctx.author.id)

    if user_id in data:
        return await ctx.reply("❌ Você já tem uma família!")

    data[user_id] = {
        "nome": nome,
        "membros": [user_id]
    }

    save_json(ARQUIVO, data)
    await ctx.reply(f"✅ Família **{nome}** criada!")

@bot.command()
async def familia(ctx):
    data = load_json(ARQUIVO, {})
    user_id = str(ctx.author.id)

    if user_id not in data:
        return await ctx.reply("❌ Você não tem família!")

    membros = "\n".join(f"<@{m}>" for m in data[user_id]["membros"])

    embed = discord.Embed(
        title=f"👥 {data[user_id]['nome']}",
        description=membros,
        color=0x5865F2
    )

    await ctx.reply(embed=embed)

@bot.command()
async def convidar(ctx, membro: discord.Member):
    convites[membro.id] = ctx.author.id
    await ctx.reply(f"📩 Convite enviado para {membro.mention}")

@bot.command()
async def aceitar(ctx):
    if ctx.author.id not in convites:
        return await ctx.reply("❌ Nenhum convite!")

    dono_id = str(convites[ctx.author.id])
    data = load_json(ARQUIVO, {})

    data[dono_id]["membros"].append(str(ctx.author.id))
    save_json(ARQUIVO, data)

    cargo = discord.utils.get(ctx.guild.roles, name=CARGO_FAMILIA)
    if cargo:
        await ctx.author.add_roles(cargo)

    await ctx.reply("✅ Você entrou na família!")

# ==================== WARNS ====================
def add_warn(user_id):
    data = load_json(WARNS_FILE, {})
    data[str(user_id)] = data.get(str(user_id), 0) + 1
    save_json(WARNS_FILE, data)
    return data[str(user_id)]

# ==================== IA ====================
respostas = {
    "oi": ["Oi!", "Eae!", "Fala!"],
    "tudo bem": ["Tô bem 😄", "Melhor agora!"],
    "bot": ["Sim? 👀", "Tô aqui!"]
}

def responder(msg):
    for chave in respostas:
        if chave in msg:
            return random.choice(respostas[chave])

# ==================== READY ====================
@bot.event
async def on_ready():
    print(f'✅ Bot {bot.user} conectado!')

    for file, default in [
        (ARQUIVO, {}),
        (AUTORIZADOS_FILE, []),
        (WARNS_FILE, {})
    ]:
        load_json(file, default)

    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos sincronizados")
    except:
        traceback.print_exc()

# ==================== MENSAGENS ====================
@bot.event
async def on_message(message):
    try:
        if message.author.bot or not message.guild:
            return

        print(f"📨 {message.author}: {message.content}")

        texto = message.content.lower()
        texto_limpo = texto.strip()

        # ===== COOLDOWN =====
        if message.author.id in cooldown:
            if time.time() - cooldown[message.author.id] < 3:
                return
        cooldown[message.author.id] = time.time()

        # ===== SAUDAÇÕES =====
        saudacoes = {
            "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
            "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
            "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
        }

        for chave in saudacoes:
            if texto_limpo.startswith(chave):
                await message.reply(saudacoes[chave], mention_author=False)

        # ===== INTERAÇÕES =====
        if re.search(r"(agradecido|obg|obrigado).*jeffu", texto):
            await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)

        elif re.search(r"(te amo|amo vc|amo você).*jeffu", texto):
            await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)

        elif re.search(r"(cala boca|calaboca|clbc|cbc|fica quieto|quieto).*jeffu", texto):
            await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)

        # ===== IA =====
        resp = responder(texto)
        if resp:
            await message.reply(resp)

        # ===== ANTI-DIVULGAÇÃO =====
        invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

        if re.search(invite_pattern, message.content):

            if (
                message.author.guild_permissions.administrator
                or message.author.id == DONO_ID
            ):
                return

            warns = add_warn(message.author.id)
            await message.delete()

            if warns == 1:
                await message.channel.send(f"⚠️ {message.author.mention} aviso 1/3")
                await log(message.guild, f"⚠️ {message.author} tomou warn 1")

            elif warns == 2:
                role = discord.utils.get(message.guild.roles, name=CARGO_MUTADO)
                if role:
                    await message.author.add_roles(role)

                await message.channel.send(f"🔇 {message.author.mention} mutado!")
                await log(message.guild, f"🔇 {message.author} mutado")

            elif warns >= 3:
                await message.guild.ban(message.author, reason=MOTIVO)
                await log(message.guild, f"🚫 {message.author} banido")

        # ===== COMANDOS =====
        await bot.process_commands(message)

    except Exception as e:
        print(f"Erro no on_message: {e}")

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
