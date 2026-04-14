import discord
from discord.ext import commands
import json
import os
import traceback
import re

# ================= CONFIG =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"

DONO_ID = 766709835701682208
MOTIVO = "Divulgação de servidor"

# ✅ CARGOS DOS CHEFES
CARGOS_AUTORIZADOS = [
    1464361173305655389,
    1409338610854920374,
    1409306638548209826,
    1466216337339846828
]

# ================= BANCO =================
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

# ================= EMBED =================
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

# ================= SELECT =================
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

# ================= VIEW PRINCIPAL =================
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
        await interaction.response.send_message("✏️ Envie o novo nome da família.", ephemeral=True)

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

# ================= COMANDO FAMÍLIA =================
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

# ================= SLASH COMMANDS =================
@bot.tree.command(name="autorizar", description="Autorizar usuário")
async def autorizar(interaction: discord.Interaction, user: discord.Member):

    if not (interaction.user.id == DONO_ID or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)

    autorizados = carregar_autorizados()

    if user.id in autorizados:
        return await interaction.response.send_message("⚠️ Já autorizado!", ephemeral=True)

    autorizados.append(user.id)
    salvar_autorizados(autorizados)

    await interaction.response.send_message(f"✅ {user.mention} autorizado!", ephemeral=True)

@bot.tree.command(name="remover_autorizacao", description="Remover autorização")
async def remover_autorizacao(interaction: discord.Interaction, user: discord.Member):

    if not (interaction.user.id == DONO_ID or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)

    autorizados = carregar_autorizados()

    if user.id not in autorizados:
        return await interaction.response.send_message("⚠️ Não está autorizado!", ephemeral=True)

    autorizados.remove(user.id)
    salvar_autorizados(autorizados)

    await interaction.response.send_message(f"❌ {user.mention} removido!", ephemeral=True)

# ================= MENSAGENS =================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    texto = message.content.lower()
    texto_limpo = texto.strip()

    # SAUDAÇÕES
    if texto_limpo.startswith("bom dia"):
        await message.reply("Bom dia! ☀️", mention_author=False)
        return

    if texto_limpo.startswith("boa tarde"):
        await message.reply("Boa tarde! 🌤️", mention_author=False)
        return

    if texto_limpo.startswith("boa noite"):
        await message.reply("Boa noite! 🌙", mention_author=False)
        return

    # BLOQUEIO DE CONVITES
    if re.search(r"(discord\.gg\/\w+|discord\.com\/invite\/\w+)", message.content):
        try:
            await message.delete()
            await message.guild.ban(message.author, reason=MOTIVO)
        except:
            pass
        return

    await bot.process_commands(message)

# ================= READY =================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online!")

    try:
        synced = await bot.tree.sync()
        print(f"🔄 Slash sincronizados ({len(synced)})")
    except Exception:
        traceback.print_exc()

# ================= TOKEN =================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
