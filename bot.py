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

# ✅ CARGOS DOS CHEFES
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

# ==================== EMBED ====================
def criar_embed(user, fam):
    embed = discord.Embed(
        title=f"👥 {fam['nome']}",
        color=fam.get("cor", 0x5865F2)
    )

    embed.add_field(name="Status", value="✅ Ativa", inline=True)
    embed.add_field(name="Dono", value=user.mention, inline=True)
    embed.add_field(name="Membros", value=f"{len(fam['membros'])}/50", inline=False)

    membros = "\n".join(f"<@{m}>" for m in fam["membros"])
    embed.add_field(name="Lista", value=membros or "Nenhum", inline=False)

    return embed

# ==================== SELECT ====================
class ConvidarSelect(discord.ui.UserSelect):
    def __init__(self, dono_id):
        super().__init__(placeholder="Escolha um usuário...", min_values=1, max_values=1)
        self.dono_id = dono_id

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]

        data = carregar()
        fam = data[self.dono_id]

        if str(user.id) in fam["membros"]:
            return await interaction.response.send_message("⚠️ Já está na família!", ephemeral=True)

        fam["membros"].append(str(user.id))
        salvar(data)

        await interaction.response.send_message(f"✅ {user.mention} entrou na família!", ephemeral=True)

class ConvidarView(discord.ui.View):
    def __init__(self, dono_id):
        super().__init__(timeout=60)
        self.add_item(ConvidarSelect(dono_id))

# ==================== VIEW ====================
class FamiliaView(discord.ui.View):
    def __init__(self, dono_id):
        super().__init__(timeout=None)
        self.dono_id = str(dono_id)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.dono_id:
            await interaction.response.send_message("❌ Só o dono pode usar!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Editar", emoji="✏️", style=discord.ButtonStyle.primary)
    async def editar(self, interaction, button):
        await interaction.response.send_message("Digite o novo nome:", ephemeral=True)

        def check(m):
            return m.author == interaction.user

        msg = await bot.wait_for("message", check=check)

        data = carregar()
        data[self.dono_id]["nome"] = msg.content
        salvar(data)

        await interaction.followup.send("✅ Nome atualizado!", ephemeral=True)

    @discord.ui.button(label="Convidar", emoji="👤", style=discord.ButtonStyle.primary)
    async def convidar(self, interaction, button):
        await interaction.response.send_message(
            "Selecione um usuário:",
            view=ConvidarView(self.dono_id),
            ephemeral=True
        )

    @discord.ui.button(label="Membros", emoji="👥", style=discord.ButtonStyle.secondary)
    async def membros(self, interaction, button):
        data = carregar()
        fam = data[self.dono_id]

        membros = "\n".join(f"<@{m}>" for m in fam["membros"])
        await interaction.response.send_message(f"👥 Membros:\n{membros}", ephemeral=True)

    @discord.ui.button(label="Excluir", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def excluir(self, interaction, button):
        data = carregar()
        data.pop(self.dono_id, None)
        salvar(data)

        await interaction.response.send_message("🗑️ Família excluída!", ephemeral=True)

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
            "membros": [user_id],
            "cor": 0x5865F2
        }
        salvar(data)

    embed = criar_embed(ctx.author, data[user_id])
    view = FamiliaView(user_id)

    await ctx.reply(embed=embed, view=view, mention_author=False)

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
        await bot.tree.sync()
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

    respondeu = False  # 🔥 controle

    # ==================== SAUDAÇÕES ====================
    saudacoes = {
        "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
        "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
        "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
    }

    for chave in saudacoes:
        if texto_limpo.startswith(chave):
            await message.reply(saudacoes[chave], mention_author=False)
            respondeu = True

    # ==================== INTERAÇÕES ====================
    if re.search(r"(agradecido|obg|obrigado).*(jeffu)?", texto):
        await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)
        respondeu = True

    if re.search(r"(te amo|amo vc|amo você).*(jeffu)?", texto):
        await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)
        respondeu = True

    if re.search(r"(cala boca|calaboca|clbc|cbc|fica quieto|quieto).*(jeffu)?", texto):
        await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)
        respondeu = True

    # ==================== BLOQUEIO ====================
    invite_pattern = r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)"

    if re.search(invite_pattern, message.content):
        try:
            invite_link = re.search(invite_pattern, message.content).group(0)
            invite = await bot.fetch_invite(invite_link)

            if invite.guild.id != message.guild.id:
                await message.delete()
                await message.guild.ban(message.author, reason=MOTIVO)
                return
        except:
            await message.delete()
            await message.guild.ban(message.author, reason="Convite suspeito")
            return

    # ==================== SUPORTE ====================
    if any(p in texto for p in [
        "login", "senha", "esqueci", "não consigo",
        "nao consigo", "ajuda", "ticket", "suporte"
    ]):
        await message.reply(
            "🔐 Para suporte relacionado ao site, vá em <#1479642544429076500>",
            mention_author=False
        )
        respondeu = True

    # ==================== SITE ====================
    if any(frase in texto for frase in [
        "o site caiu", "site caiu", "site tá fora",
        "site ta fora", "site offline",
        "site não funciona", "site nao funciona",
        "site saiu do ar"
    ]):
        await message.reply(
            "🌐 Veja em <#1409296003034644542>",
            mention_author=False
        )
        respondeu = True

    # 🔥 SEMPRE PROCESSA COMANDOS
    await bot.process_commands(message)

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
