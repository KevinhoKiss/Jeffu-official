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

# ==================== UTILITÁRIOS DE CARGO ====================
async def get_or_create_role(guild: discord.Guild, role_name: str, color_int: int = None):
    """
    Procura um cargo pelo nome; se não existir, cria.
    Retorna o objeto Role ou None em caso de falha.
    """
    try:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            return role
        # cria o cargo (bot precisa de Manage Roles)
        if color_int is not None:
            role = await guild.create_role(name=role_name, colour=discord.Colour(color_int), reason="Criado pelo sistema de famílias")
        else:
            role = await guild.create_role(name=role_name, reason="Criado pelo sistema de famílias")
        return role
    except Exception:
        return None

async def aplicar_cargo_a_todos(guild: discord.Guild, role: discord.Role, membros_list: list):
    """
    Aplica o cargo a todos os membros listados (membros_list contém IDs como strings).
    """
    for m_id in membros_list:
        try:
            membro = guild.get_member(int(m_id))
            if membro and role not in membro.roles:
                await membro.add_roles(role)
        except Exception:
            pass

async def atualizar_ou_criar_role_da_familia(dono_key: str):
    """
    Garante que exista um cargo para a família dono_key.
    - Usa familia['nome'] como nome do cargo.
    - Usa familia['cor'] (HEX) para definir cor do cargo se for HEX.
    - Salva role_id em familias.json.
    - Aplica o cargo a todos os membros.
    """
    data = carregar()
    familia = data.get(str(dono_key))
    if not familia:
        return None

    guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
    if not guild:
        return None

    nome_familia = familia.get("nome", "Minha Família")
    cor_value = familia.get("cor", None)  # pode ser "#5865F2" ou nome
    color_int = None
    if isinstance(cor_value, str) and cor_value.startswith("#"):
        try:
            color_int = int(cor_value.replace("#", ""), 16)
        except:
            color_int = None

    # tenta usar role_id salvo
    role = None
    role_id = familia.get("role_id")
    if role_id:
        try:
            role = guild.get_role(int(role_id))
        except:
            role = None

    # se role existe mas nome mudou, tenta renomear
    if role:
        try:
            if role.name != nome_familia:
                await role.edit(name=nome_familia)
        except Exception:
            pass
        # atualiza cor se possível
        if color_int is not None:
            try:
                await role.edit(colour=discord.Colour(color_int))
            except Exception:
                pass
    else:
        # procura por cargo com mesmo nome (reutiliza se achar)
        role = discord.utils.get(guild.roles, name=nome_familia)
        if not role:
            role = await get_or_create_role(guild, nome_familia, color_int)

    # se conseguiu criar/obter role, salva role_id e aplica a todos
    if role:
        familia["role_id"] = role.id
        salvar(data)
        await aplicar_cargo_a_todos(guild, role, familia.get("membros", []))
        return role

    return None

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

        # adicionar cargo (garante que o cargo exista e aplique ao membro)
        guild = bot.get_guild(SEU_ID_DO_SERVIDOR)

        if guild:
            membro = guild.get_member(interaction.user.id)

            # garante que o cargo da família exista e esteja aplicado a todos
            try:
                await atualizar_ou_criar_role_da_familia(convite["dono"])
                data = carregar()
                role_id = data.get(str(convite["dono"]), {}).get("role_id")
                cargo = guild.get_role(int(role_id)) if role_id else None
            except Exception:
                cargo = None

            if membro and cargo:
                try:
                    await membro.add_roles(cargo)
                except Exception:
                    pass

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

                # tenta remover role do usuário se existir
                try:
                    guild = interaction.guild
                    role_id = info.get("role_id")
                    if guild and role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await interaction.user.remove_roles(role)
                except Exception:
                    pass

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

    async def _await_response_and_save(self, interaction: discord.Interaction, prompt: str, field: str, transform=None, allow_attachments=False):
        """
        Prompt the user (ephemeral), wait for their next message in the same channel,
        then save the content (or attachment URL) into familias.json under the given field.
        transform: optional function to transform the raw message content before saving.
        allow_attachments: if True and the user sends an attachment, save the attachment URL.
        """
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode editar.", ephemeral=True)

        await interaction.response.send_message(prompt, ephemeral=True)

        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await interaction.followup.send("⏰ Tempo esgotado. Tente novamente.", ephemeral=True)

        data = carregar()
        dono_key = str(self.dono_id)
        if dono_key not in data:
            return await interaction.followup.send("❌ Família não encontrada.", ephemeral=True)

        # Determine value to save
        value = None
        if allow_attachments and msg.attachments:
            # save first attachment URL
            value = msg.attachments[0].url
        else:
            value = msg.content.strip()

        if transform:
            try:
                value = transform(value)
            except Exception:
                return await interaction.followup.send("❌ Valor inválido.", ephemeral=True)

        # If empty, keep previous
        if not value:
            value = data[dono_key].get(field, "")

        data[dono_key][field] = value
        salvar(data)

        # se alterou nome ou cor (ou cargo), atualiza/cria o role e aplica a todos
        if field in ("nome", "cor", "cargo"):
            try:
                await atualizar_ou_criar_role_da_familia(dono_key)
            except Exception:
                pass

        # respond confirming change
        display = value
        if field == "cor":
            # show as-is (name or HEX)
            display = value
        if field == "icone":
            display = value if value else "Nenhum"
        await interaction.followup.send(f"✅ {field.capitalize()} alterado para **{display}**", ephemeral=True)

    @discord.ui.button(label="Nome", style=discord.ButtonStyle.secondary, custom_id="editar:nome")
    async def nome(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(
            interaction,
            "✏️ Envie o novo nome no chat. Você tem 60 segundos.",
            field="nome"
        )

    @discord.ui.button(label="Descrição", style=discord.ButtonStyle.secondary, custom_id="editar:descricao")
    async def descricao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(
            interaction,
            "✏️ Envie a nova descrição no chat. Você tem 60 segundos.",
            field="descricao"
        )

    @discord.ui.button(label="Ícone", style=discord.ButtonStyle.secondary, custom_id="editar:icone")
    async def icone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(
            interaction,
            "✏️ Envie o link do ícone ou anexe a imagem. Você tem 60 segundos.",
            field="icone",
            allow_attachments=True
        )

    @discord.ui.button(label="Cor", style=discord.ButtonStyle.secondary, custom_id="editar:cor")
    async def cor(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Accept either a HEX like #5865F2 or a name (e.g., "Azul Padrão" or a role name)
        def transform_cor(v):
            v = v.strip()
            if not v:
                return v
            # normalize hex
            if v.startswith("#"):
                # validate hex length 6
                hexpart = v.replace("#", "")
                if len(hexpart) == 6:
                    # uppercase
                    return f"#{hexpart.upper()}"
                else:
                    raise ValueError("HEX inválido")
            # otherwise keep as name
            return v

        await self._await_response_and_save(
            interaction,
            "✏️ Envie a cor em HEX (ex: #5865F2) ou um nome (ex: Azul Padrão). Você tem 60 segundos.",
            field="cor",
            transform=transform_cor
        )

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
            # tenta remover o cargo associado (opcional)
            try:
                guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
                if guild:
                    role_id = data[str(self.dono_id)].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await role.delete(reason="Família excluída")
            except Exception:
                pass

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
    cor_value = familia.get("cor", "#5865F2")  # pode ser "#5865F2" ou "Nome da Cor"
    vip = familia.get("vip", "Nenhum")

    # tenta obter cargo real no servidor para usar a cor do cargo (se possível)
    color_int = 0x5865F2
    cargo_display = f"🏠 @{cargo_name}"
    cor_display = cor_value

    guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
    if guild:
        # tenta achar cargo pelo role_id salvo
        role = None
        role_id = familia.get("role_id")
        if role_id:
            try:
                role = guild.get_role(int(role_id))
            except:
                role = None

        # se role existe, usa suas propriedades
        if role:
            try:
                color_int = role.color.value
            except:
                color_int = color_int
            cargo_display = f"🏠 {role.name}"
        else:
            # tenta achar cargo pelo nome salvo em 'cargo_name'
            role_by_name = discord.utils.get(guild.roles, name=cargo_name)
            if role_by_name:
                try:
                    color_int = role_by_name.color.value
                except:
                    color_int = color_int
                cargo_display = f"🏠 {role_by_name.name}"
            else:
                # se 'cor_value' for um nome de cargo existente, tenta usar a cor desse cargo
                role_by_cor = discord.utils.get(guild.roles, name=cor_value)
                if role_by_cor:
                    try:
                        color_int = role_by_cor.color.value
                    except:
                        color_int = color_int
                    cor_display = role_by_cor.name

    # se cor_value for HEX, converte para int e mostra o HEX como display
    if isinstance(cor_value, str) and cor_value.startswith("#"):
        try:
            color_int = int(cor_value.replace("#", ""), 16)
            cor_display = cor_value.upper()
        except:
            # mantém color_int padrão se falhar
            cor_display = cor_value

    # se cor_value for um nome (não começa com #) e não foi resolvido para um role, mostramos o nome
    if isinstance(cor_value, str) and not cor_value.startswith("#"):
        cor_display = cor_value

    embed = discord.Embed(title=f"👥 **{nome}**", color=color_int)
    embed.add_field(name="Status", value=f"{status} · Dono: <@{dono}>", inline=False)
    embed.add_field(name="Membros", value=f"{membros_count}/{limite}", inline=True)
    embed.add_field(name="Cargo", value=cargo_display, inline=True)
    embed.add_field(name="Tier VIP", value=vip, inline=True)
    embed.add_field(name="Cor", value=cor_display, inline=True)

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

        # cria/atualiza cargo da família e aplica ao dono
        try:
            role = await atualizar_ou_criar_role_da_familia(user_id)
            if role:
                membro = ctx.guild.get_member(ctx.author.id)
                if membro:
                    try:
                        await membro.add_roles(role)
                    except Exception:
                        pass
        except Exception:
            pass

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

            # tenta remover role do usuário se existir
            try:
                guild = ctx.guild
                role_id = info.get("role_id")
                if guild and role_id:
                    role = guild.get_role(int(role_id))
                    if role:
                        await ctx.author.remove_roles(role)
            except Exception:
                pass

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

    # tenta remover role do membro se existir
    try:
        guild = ctx.guild
        role_id = familia.get("role_id")
        if guild and role_id:
            role = guild.get_role(int(role_id))
            if role:
                await membro.remove_roles(role)
    except Exception:
        pass

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
            "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>",
            "site caiu": "Da uma olhada em <#1409296003034644542> <#1409296003034644542>"
        }

        for chave in saudacoes:
            if texto_limpo.startswith(chave):
                await message.reply(saudacoes[chave], mention_author=False)
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

    await bot.process_commands(message)
    
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
