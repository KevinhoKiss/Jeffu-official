import discord
from discord.ext import commands
import os
import traceback
import re
import json
import time
import asyncio

try:
    from pymongo import MongoClient
except:
    MongoClient = None

# Trechos originais do arquivo (incluídos aqui como referência):
# embed = discord.Embed( title=f"👥 {data[user_id]['nome']}", color=0x5865F2 )
# embed.add_field(name="👑 Dono", value=f"<@{data[user_id]['dono']}>", inline=False)

# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
LOG_CHANNEL_ID = 1466542559730991164

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

convites = {}  # ✅ só uma vez

# ==================== LOG ====================
async def log(guild, mensagem):
    canal = guild.get_channel(LOG_CHANNEL_ID)
    if canal:
        await canal.send(mensagem)

# ==================== MONGO ====================
mongo = None
try:
    mongo = MongoClient(os.getenv("MONGO_URI"))
    db = mongo["bot"]
    familias_db = db["familias"]
except:
    mongo = None

# ==================== JSON ====================
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

# ==================== BANCO ====================
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

        convites.pop(interaction.user.id, None)

        # adicionar cargo
        guild = bot.get_guild(SEU_ID_DO_SERVIDOR)

        if guild:
            membro = guild.get_member(interaction.user.id)
            cargo = discord.utils.get(guild.roles, name="Família")

            if membro and cargo:
                await membro.add_roles(cargo)

        await interaction.response.send_message("✅ Você entrou na família!", ephemeral=True)
        

class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Ver Família",
        style=discord.ButtonStyle.blurple,
        custom_id="painel:ver"
    )
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):

        data = carregar()
        user_id = str(interaction.user.id)

        familia = next(
            (info for info in data.values() if user_id in info["membros"]),
            None
        )

        if not familia:
            return await interaction.response.send_message(
                "❌ Você não está em nenhuma família",
                ephemeral=True
            )

        membros = "\n".join(f"<@{m}>" for m in familia["membros"])

        embed = discord.Embed(
            title=f"🏠 {familia['nome']}",
            description=membros,
            color=0x5865F2
        )

        embed.add_field(name="👑 Dono", value=f"<@{familia['dono']}>")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="🚪 Sair",
        style=discord.ButtonStyle.red,
        custom_id="painel:sair"
    )
    async def sair_btn(self, interaction: discord.Interaction, button: discord.ui.Button):

        data = carregar()
        user_id = str(interaction.user.id)

        for dono, info in data.items():
            if user_id in info["membros"]:

                if user_id == info["dono"]:
                    return await interaction.response.send_message(
                        "❌ Você é o dono!",
                        ephemeral=True
                    )

                info["membros"].remove(user_id)
                salvar(data)

                cargo = discord.utils.get(interaction.guild.roles, name="Família")
                if cargo:
                    await interaction.user.remove_roles(cargo)

                return await interaction.response.send_message(
                    "👋 Você saiu da família!",
                    ephemeral=True
                )

        await interaction.response.send_message(
            "❌ Você não está em nenhuma família",
            ephemeral=True
        )
        
# ==================== SISTEMA FAMÍLIA COMPLETO ====================

# ==================== NOVAS VIEWS E FUNÇÕES (para reproduzir o layout da imagem) ====================

class EditarFamiliaView(discord.ui.View):
    def __init__(self, dono_id, familia_id):
        super().__init__(timeout=None)
        self.dono_id = dono_id
        self.familia_id = familia_id

    @discord.ui.button(label="Nome", style=discord.ButtonStyle.secondary, custom_id="editar:nome")
    async def nome(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode editar.", ephemeral=True)

        await interaction.response.send_message("✏️ Envie o novo nome no chat. Você tem 30 segundos.", ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            return await interaction.followup.send("⏰ Tempo esgotado. Tente novamente.", ephemeral=True)

        data = carregar()
        dono_key = str(self.dono_id)
        if dono_key not in data:
            return await interaction.followup.send("❌ Família não encontrada.", ephemeral=True)

        data[dono_key]["nome"] = msg.content.strip() or data[dono_key].get("nome", "Minha Família")
        salvar(data)

        await interaction.followup.send(f"✅ Nome alterado para **{data[dono_key]['nome']}**", ephemeral=True)

    @discord.ui.button(label="Descrição", style=discord.ButtonStyle.secondary, custom_id="editar:descricao")
    async def descricao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✏️ Envie a nova descrição no chat (resposta efêmera).", ephemeral=True)

    @discord.ui.button(label="Ícone", style=discord.ButtonStyle.secondary, custom_id="editar:icone")
    async def icone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✏️ Envie o link do ícone ou anexe a imagem.", ephemeral=True)

    @discord.ui.button(label="Cor", style=discord.ButtonStyle.secondary, custom_id="editar:cor")
    async def cor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✏️ Envie a cor em HEX (ex: #5865F2).", ephemeral=True)

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.gray, custom_id="editar:voltar")
    async def voltar(self, interaction: discord.Interaction, button: discord.ui.Button):
        # reabre a view de gerenciamento
        await enviar_embed_gerenciar(interaction, int(self.dono_id))

    @discord.ui.button(label="Início", style=discord.ButtonStyle.gray, custom_id="editar:inicio")
    async def inicio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🏠 Voltando ao menu inicial...", ephemeral=True)


class GerenciarFamiliaView(discord.ui.View):
    def __init__(self, dono_id):
        super().__init__(timeout=None)
        self.dono_id = dono_id

    @discord.ui.button(label="✏️ Editar", style=discord.ButtonStyle.primary, custom_id="gerenciar:editar")
    async def editar(self, interaction: discord.Interaction, button: discord.ui.Button):
        # só o dono pode editar
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode editar.", ephemeral=True)
        view = EditarFamiliaView(self.dono_id, self.dono_id)
        await interaction.response.send_message(f"✏️ Editando família...", view=view, ephemeral=True)

    @discord.ui.button(label="👤 Convidar", style=discord.ButtonStyle.success, custom_id="gerenciar:convidar")
    async def convidar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use o comando `!convidar @membro` para convidar.", ephemeral=True)

    @discord.ui.button(label="👥 Membros", style=discord.ButtonStyle.blurple, custom_id="gerenciar:membros")
    async def membros(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = carregar()
        familia = data.get(str(self.dono_id))
        if not familia:
            return await interaction.response.send_message("❌ Família não encontrada.", ephemeral=True)
        membros = "\n".join(f"<@{m}>" for m in familia["membros"])
        await interaction.response.send_message(f"**Membros:**\n{membros}", ephemeral=True)

    @discord.ui.button(label="🗑️ Excluir família", style=discord.ButtonStyle.danger, custom_id="gerenciar:excluir")
    async def excluir(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode excluir.", ephemeral=True)
        data = carregar()
        if str(self.dono_id) in data:
            del data[str(self.dono_id)]
            salvar(data)
            return await interaction.response.send_message("🗑️ Família excluída.", ephemeral=True)
        await interaction.response.send_message("❌ Família não encontrada.", ephemeral=True)

    @discord.ui.button(label="🏠 Início", style=discord.ButtonStyle.gray, custom_id="gerenciar:inicio")
    async def inicio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🏠 Menu inicial.", ephemeral=True)


async def enviar_embed_gerenciar(ctx_or_interaction, dono_id):
    """
    Envia o embed no formato semelhante ao da imagem:
    - Título com nome da família
    - Status, Dono, Membros, Cargo, Tier VIP
    - Lista de membros
    - View com botões: Editar, Convidar, Membros, Excluir família, Início
    """
    data = carregar()
    familia = data.get(str(dono_id))
    if not familia:
        # se for interaction
        if isinstance(ctx_or_interaction, discord.Interaction):
            return await ctx_or_interaction.response.send_message("❌ Família não encontrada.", ephemeral=True)
        return await ctx_or_interaction.reply("❌ Família não encontrada.")

    # campos que aparecem na imagem
    nome = familia.get("nome", "Minha Família")
    status = "✅ Ativa"
    dono = familia.get("dono")
    membros_count = len(familia.get("membros", []))
    limite = familia.get("limite", 50)
    cargo_name = familia.get("cargo", nome)  # se tiver cargo salvo
    cor_hex = familia.get("cor", "#5865F2")
    vip = familia.get("vip", "Nenhum")

    # tenta converter cor hex para int; se falhar, usa cor padrão
    try:
        color_int = int(cor_hex.replace("#",""), 16)
    except:
        color_int = 0x5865F2

    embed = discord.Embed(title=f"👥 **{nome}**", color=color_int)
    embed.add_field(name="Status", value=f"{status} · Dono: <@{dono}>", inline=False)
    embed.add_field(name="Membros", value=f"{membros_count}/{limite}", inline=True)
    embed.add_field(name="Cargo", value=f"🏠 @{cargo_name}", inline=True)
    embed.add_field(name="Tier VIP", value=vip, inline=True)

    membros = "\n".join(f"👑 <@{m}>" if m == str(dono) else f"<@{m}>" for m in familia.get("membros", []))
    embed.add_field(name="Membros:", value=membros or "Nenhum", inline=False)
    embed.set_footer(text="Sistema de Famílias")

    view = GerenciarFamiliaView(dono_id)
    # enviar dependendo do tipo
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx_or_interaction.reply(embed=embed, view=view)

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
        color=0x5865F2
    )

    embed.add_field(name="👑 Dono", value=f"<@{data[user_id]['dono']}>", inline=False)
    embed.add_field(name=f"👥 Membros ({len(data[user_id]['membros'])})", value=membros, inline=False)

    await ctx.reply(embed=embed)

# ==================== CONVIDAR ====================
@bot.command()
async def convidar(ctx, membro: discord.Member = None):

    if membro is None:
        return await ctx.reply("❌ Você precisa mencionar alguém!")

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
                return await ctx.reply("❌ Você é o dono!")

            info["membros"].remove(user_id)
            salvar(data)

            return await ctx.reply("👋 Você saiu da família!")

    await ctx.reply("❌ Você não está em nenhuma família")


# ==================== EXPULSAR ====================

@bot.command()
async def expulsar(ctx, membro: discord.Member):
    data = carregar()
    user_id = str(ctx.author.id)
    alvo_id = str(membro.id)

    familia = data.get(user_id)

    if not familia:
        return await ctx.reply("❌ Você não tem família")

    if alvo_id not in familia["membros"]:
        return await ctx.reply("❌ Esse usuário não está na sua família")

    if alvo_id == user_id:
        return await ctx.reply("❌ Você não pode expulsar a si mesmo")

    familia["membros"].remove(alvo_id)
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

@bot.command()
async def up(ctx, tipo=None):
    if tipo != "painel":
        return await ctx.reply("❌ Use: !up painel")

    embed = discord.Embed(
        title="🏠 Sistema de Família",
        description="Use os botões abaixo 👇",
        color=0x5865F2
    )

    await ctx.send(embed=embed, view=PainelView())

# Comando para abrir gerenciamento (novo)
@bot.command()
async def gerenciar(ctx):
    # verifica se o autor tem família e pega o dono correspondente
    data = carregar()
    user_id = str(ctx.author.id)
    familia = next((dono for dono, info in data.items() if user_id in info["membros"]), None)
    if not familia:
        return await ctx.reply("❌ Você não está em nenhuma família")
    await enviar_embed_gerenciar(ctx, int(familia))

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
    bot.add_view(PainelView())

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
                return
        # ==================== INTERAÇÕES ====================
        if re.search(r"(agradecido|obg|obrigado).*(jeffu)?", texto):
            await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)
            return

        if re.search(r"(te amo|amo vc|amo você).*(jeffu)?", texto):
            await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)
            return

        if re.search(r"(cala boca|calaboca|clbc|cbc|fica quieto|quieto).*(jeffu)?", texto):
            await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)
            return

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

        # ✅ MUITO IMPORTANTE (não remover)
        await bot.process_commands(message)

    except Exception as e:
        print(f"Erro no on_message: {e}")

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
