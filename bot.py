# bot.py
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

convites = {}  # convites temporários: {user_id: {"dono": dono_id, "tempo": timestamp}}

# ==================== LOG ====================
async def log(guild, mensagem):
    try:
        canal = guild.get_channel(LOG_CHANNEL_ID)
        if canal:
            await canal.send(mensagem)
    except Exception:
        pass

# ==================== MONGO ====================
mongo = None
familias_db = None
try:
    MONGO_URI = os.getenv("MONGO_URI")
    if MONGO_URI and MongoClient:
        mongo = MongoClient(MONGO_URI)
        db = mongo["bot"]
        familias_db = db["familias"]
        print("[MONGO] Conectado ao MongoDB")
    else:
        mongo = None
        familias_db = None
        if not MongoClient:
            print("[MONGO] pymongo não instalado; usando fallback de arquivo")
        else:
            print("[MONGO] MONGO_URI não definido; usando fallback de arquivo")
except Exception as e:
    print("[MONGO WARN] Não foi possível conectar ao MongoDB:", e)
    mongo = None
    familias_db = None

# ==================== PERSISTÊNCIA (carregar/salvar) ====================
def carregar():
    """
    Retorna o dicionário de familias.
    Usa MongoDB se disponível, caso contrário lê o arquivo JSON local.
    """
    # tenta usar mongo
    try:
        if familias_db is not None:
            doc = familias_db.find_one({"_id": "familias"})
            if doc and "data" in doc:
                if isinstance(doc["data"], dict):
                    return doc["data"]
                else:
                    print("[DB WARN] Documento 'familias' no MongoDB não é um dict. Ignorando.")
                    return {}
            return {}
    except Exception as e:
        print("[DB WARN] Falha ao carregar do MongoDB:", e)

    # fallback para arquivo local
    if not os.path.exists(ARQUIVO):
        return {}
    try:
        with open(ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[FILE WARN] Falha ao carregar arquivo:", e)
        return {}

def salvar(data):
    """
    Salva o dicionário de familias.
    Tenta salvar no MongoDB se disponível; caso contrário salva no arquivo local.
    """
    if not isinstance(data, dict):
        print("[SAVE ERROR] Dados a salvar não são um dict. Abortando.")
        return

    # tenta salvar no mongo
    try:
        if familias_db is not None:
            familias_db.update_one(
                {"_id": "familias"},
                {"$set": {"data": data}},
                upsert=True
            )
            return
    except Exception as e:
        print("[DB WARN] Falha ao salvar no MongoDB:", e)

    # fallback para arquivo local
    tmp = ARQUIVO + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, ARQUIVO)
    except Exception as e:
        print("[FILE ERROR] Falha ao salvar arquivo:", e)
        try:
            with open(ARQUIVO, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e2:
            print("[FILE ERROR] Falha final ao salvar arquivo:", e2)

def carregar_autorizados():
    if not os.path.exists(AUTORIZADOS_FILE):
        return []
    try:
        with open(AUTORIZADOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[FILE WARN] Falha ao carregar autorizados:", e)
        return []

def salvar_autorizados(lista):
    try:
        with open(AUTORIZADOS_FILE, "w", encoding="utf-8") as f:
            json.dump(lista, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[FILE ERROR] Falha ao salvar autorizados:", e)

# ==================== UTILITÁRIOS DE CARGO ====================
PERMS_FRIENDLY = {
    "Enviar links": "embed_links",
    "Enviar imagens/arquivos": "attach_files",
    "Enviar áudio/voz (conectar)": "connect",
    "Falar no canal de voz": "speak",
    "Enviar mensagens": "send_messages",
    "Adicionar reações": "add_reactions",
    "Ler histórico de mensagens": "read_message_history",
    "Gerenciar mensagens": "manage_messages",
    "Mencionar everyone": "mention_everyone",
    "Gerenciar cargos": "manage_roles"
}
ALLOWED_PERMS = set(PERMS_FRIENDLY.values())

async def setup_muted_role(guild: discord.Guild, role: discord.Role):
    """
    Configura o cargo Muted em todos os canais do servidor,
    negando envio de mensagens em texto e fala em voz.
    """
    for channel in guild.channels:
        try:
            if isinstance(channel, discord.TextChannel):
                await channel.set_permissions(role, send_messages=False, add_reactions=False)
            elif isinstance(channel, discord.VoiceChannel):
                await channel.set_permissions(role, speak=False, connect=False)
        except Exception as e:
            print(f"[MUTE SETUP WARN] Falha ao configurar canal {getattr(channel,'name',str(channel))}: {e}")

async def get_or_create_muted_role(guild: discord.Guild):
    """
    Garante que exista um cargo 'Muted' com permissões negadas
    e aplica essas permissões em todos os canais.
    """
    role = discord.utils.get(guild.roles, name="Muted")
    perms = discord.Permissions.none()
    perms.update(send_messages=False, speak=False, add_reactions=False,
                 attach_files=False, embed_links=False)

    if role:
        try:
            await role.edit(permissions=perms)
        except Exception as e:
            print(f"[MUTE WARN] Não foi possível editar permissões do role Muted: {e}")
        await setup_muted_role(guild, role)
        return role

    # se não existe, cria
    if not guild.me.guild_permissions.manage_roles:
        print("[MUTE ERROR] Bot não tem Manage Roles; não é possível criar Muted role.")
        return None

    try:
        role = await guild.create_role(name="Muted", permissions=perms,
                                       reason="Role de mute criado pelo bot")
        print(f"[MUTE OK] Role Muted criado: {role} (id={role.id})")
        await setup_muted_role(guild, role)
        return role
    except Exception as e:
        print(f"[MUTE ERROR] Falha ao criar role Muted: {e}")
        return None

async def mute_member(guild: discord.Guild, member: discord.Member):
    """
    Aplica o role Muted ao membro. Retorna True se aplicado com sucesso.
    """
    try:
        if not guild or not member:
            return False
        role = await get_or_create_muted_role(guild)
        if not role:
            return False
        # checa se já está mutado
        if role in member.roles:
            return True
        try:
            await member.add_roles(role, reason="Muted por envio de invite/propaganda")
            info = f"🔇 {member.mention} ({member.id}) foi mutado por envio de invite/propaganda."
            try:
                await log(guild, info)
            except Exception:
                print("[MUTE LOG WARN] Falha ao logar mute no canal de logs.")
            return True
        except Exception as e:
            print("[MUTE ERROR] Falha ao adicionar role Muted ao membro:", e)
            return False
    except Exception as e:
        print("[MUTE ERROR] Erro inesperado em mute_member:", e)
        return False

async def safe_get_or_create_role(guild: discord.Guild, role_name: str, color_int: int = None):
    """
    Cria ou reutiliza um cargo com logs e checagem de permissão Manage Roles.
    """
    try:
        if not guild.me.guild_permissions.manage_roles:
            print("[ROLE ERROR] Bot não tem Manage Roles no servidor.")
            return None

        # tenta reutilizar role existente pelo nome
        try:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                # tenta atualizar cor se fornecida
                if color_int is not None:
                    try:
                        await role.edit(colour=discord.Colour(color_int))
                    except Exception as e:
                        print("[ROLE WARN] Não foi possível editar cor do role existente:", e)
                print(f"[ROLE OK] Reutilizando role existente: {role.name} (id={role.id})")
                return role
        except Exception as e:
            print("[ROLE ERROR] Erro ao procurar role existente:", e)

        # cria novo role
        try:
            if color_int is not None:
                role = await guild.create_role(name=role_name, colour=discord.Colour(color_int), reason="Criado pelo sistema de famílias")
            else:
                role = await guild.create_role(name=role_name, reason="Criado pelo sistema de famílias")
            print(f"[ROLE OK] Role criado: {role} (id={role.id})")
            return role
        except discord.Forbidden:
            print("[ROLE ERROR] Forbidden: bot não pode criar/editar roles (hierarquia ou permissão).")
        except discord.HTTPException as e:
            print("[ROLE ERROR] HTTPException ao criar role:", e)
        except Exception as e:
            print("[ROLE ERROR] Erro inesperado ao criar role:", e)
        return None

    except Exception as e:
        print("[ROLE ERROR] Erro inesperado em safe_get_or_create_role:", e)
        return None

async def aplicar_cargo_a_todos(guild: discord.Guild, role: discord.Role, membros_list: list):
    for m_id in membros_list:
        try:
            membro = guild.get_member(int(m_id))
            if membro and role not in membro.roles:
                await membro.add_roles(role)
        except Exception as e:
            print(f"[ROLE APPLY WARN] Falha ao aplicar role a {m_id}: {e}")
            pass

def build_permissions_from_list(perms_list):
    perms = discord.Permissions.none()
    for name in perms_list:
        n = name.strip().lower()
        if n in ALLOWED_PERMS:
            try:
                setattr(perms, n, True)
            except Exception:
                pass
    return perms

async def aplicar_permissoes_ao_role(role: discord.Role, perms_list):
    try:
        perms = build_permissions_from_list(perms_list)
        await role.edit(permissions=perms)
        print(f"[ROLE OK] Permissões aplicadas ao role {role.name}: {perms_list}")
    except discord.Forbidden:
        print("[ROLE ERROR] Forbidden: bot não pode editar permissões do role (hierarquia ou permissão).")
    except Exception as e:
        print("[ROLE ERROR] Erro ao aplicar permissões ao role:", e)

async def atualizar_ou_criar_role_da_familia(dono_key: str):
    """
    Garante que exista um cargo para a família dono_key e aplica cor/perms/membros.
    """
    data = carregar()
    familia = data.get(str(dono_key))
    if not familia:
        return None

    guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
    if not guild:
        print("[ROLE ERROR] Guild não encontrado com SEU_ID_DO_SERVIDOR.")
        return None

    nome_familia = familia.get("nome", "Minha Família")
    role_display_name = f"Família • {nome_familia}"

    cor_value = familia.get("cor", None)
    color_int = None
    if isinstance(cor_value, str) and cor_value.startswith("#"):
        try:
            color_int = int(cor_value.replace("#", ""), 16)
        except Exception as e:
            print("[ROLE WARN] HEX inválido em familia['cor']:", e)
            color_int = None

    role = None
    role_id = familia.get("role_id")
    if role_id:
        try:
            role = guild.get_role(int(role_id))
        except Exception as e:
            print("[ROLE WARN] role_id salvo não encontrado no guild:", e)
            role = None

    if role:
        try:
            if role.name != role_display_name:
                await role.edit(name=role_display_name)
        except Exception as e:
            print("[ROLE WARN] Não foi possível renomear role existente:", e)
        if color_int is not None:
            try:
                await role.edit(colour=discord.Colour(color_int))
            except Exception as e:
                print("[ROLE WARN] Não foi possível editar cor do role existente:", e)
    else:
        try:
            role = discord.utils.get(guild.roles, name=role_display_name)
            if not role:
                role = await safe_get_or_create_role(guild, role_display_name, color_int)
            else:
                if color_int is not None:
                    try:
                        await role.edit(colour=discord.Colour(color_int))
                    except Exception as e:
                        print("[ROLE WARN] Não foi possível editar cor do role encontrado por nome:", e)
        except Exception as e:
            print("[ROLE ERROR] Erro ao obter/criar role:", e)
            role = None

    if role:
        familia["role_id"] = role.id
        salvar(data)

        permissoes = familia.get("permissoes", [])
        if isinstance(permissoes, list) and permissoes:
            try:
                await aplicar_permissoes_ao_role(role, permissoes)
            except Exception as e:
                print("[ROLE WARN] Erro ao aplicar permissoes salvas:", e)

        try:
            await aplicar_cargo_a_todos(guild, role, familia.get("membros", []))
        except Exception as e:
            print("[ROLE WARN] Erro ao aplicar role a todos:", e)
        return role

    return None

async def unmute_member(guild: discord.Guild, member: discord.Member) -> bool:
    """
    Remove o role 'Muted' do membro. Retorna True se removido com sucesso.
    """
    try:
        if not guild or not member:
            return False
        role = discord.utils.get(guild.roles, name="Muted")
        if not role:
            return False
        if role not in member.roles:
            return True  # já não está mutado
        try:
            await member.remove_roles(role, reason="Unmuted pelo bot")
            try:
                await log(guild, f"🔊 {member.mention} ({member.id}) foi desmutado.")
            except Exception:
                pass
            return True
        except Exception as e:
            print("[UNMUTE ERROR] Falha ao remover role Muted do membro:", e)
            return False
    except Exception as e:
        print("[UNMUTE ERROR] Erro inesperado em unmute_member:", e)
        return False

@bot.command(name="unmute")
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
async def cmd_unmute(ctx, membro: discord.Member = None):
    """
    Uso: !unmute @usuario
    Remove o role 'Muted' do usuário mencionado.
    """
    if membro is None:
        return await ctx.reply("❌ Mencione o usuário que deseja desmutar. Ex: `!unmute @usuario`", mention_author=False)

    # checagem de hierarquia
    bot_member = ctx.guild.me
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and bot_member.top_role <= muted_role:
        return await ctx.reply("❌ Não posso remover o mute: meu cargo está abaixo do cargo Muted.", mention_author=False)

    try:
        ok = await unmute_member(ctx.guild, membro)
        if ok:
            await ctx.reply(f"✅ {membro.mention} foi desmutado.", mention_author=False)
        else:
            await ctx.reply("⚠️ Não foi possível desmutar esse usuário (role Muted não encontrado ou erro).", mention_author=False)
    except commands.MissingPermissions:
        await ctx.reply("❌ Você não tem permissão para usar esse comando.", mention_author=False)
    except Exception as e:
        print("[CMD UNMUTE ERROR]", e)
        await ctx.reply("❌ Ocorreu um erro ao tentar desmutar.", mention_author=False)

# ==================== VIEWS E INTERAÇÕES ====================
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

        guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
        if guild:
            membro = guild.get_member(interaction.user.id)
            try:
                await atualizar_ou_criar_role_da_familia(convite["dono"])
                data = carregar()
                role_id = data.get(str(convite["dono"]), {}).get("role_id")
                cargo = guild.get_role(int(role_id)) if role_id else None
            except Exception as e:
                print("[ACEITAR WARN] Erro ao obter role após criar:", e)
                cargo = None

            if membro and cargo:
                try:
                    await membro.add_roles(cargo)
                except Exception as e:
                    print(f"[ACEITAR WARN] Falha ao adicionar role ao membro: {e}")
                    pass

        await interaction.response.send_message("✅ Você entrou na família!", ephemeral=True)

class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Ver Família", style=discord.ButtonStyle.blurple, custom_id="painel:ver")
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = carregar()
        user_id = str(interaction.user.id)
        familia = next((info for info in data.values() if user_id in info["membros"]), None)
        if not familia:
            return await interaction.response.send_message("❌ Você não está em nenhuma família", ephemeral=True)

        membros = "\n".join(f"<@{m}>" for m in familia["membros"])
        embed = discord.Embed(title=f"🏠 {familia['nome']}", description=membros, color=0x5865F2)
        embed.add_field(name="👑 Dono", value=f"<@{familia['dono']}>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.red, custom_id="painel:sair")
    async def sair_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = carregar()
        user_id = str(interaction.user.id)
        for dono, info in data.items():
            if user_id in info["membros"]:
                if user_id == info["dono"]:
                    return await interaction.response.send_message("❌ Você é o dono!", ephemeral=True)
                info["membros"].remove(user_id)
                salvar(data)
                try:
                    guild = interaction.guild
                    role_id = info.get("role_id")
                    if guild and role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await interaction.user.remove_roles(role)
                except Exception as e:
                    print("[SAIR WARN] Falha ao remover role do usuário:", e)
                    pass
                return await interaction.response.send_message("👋 Você saiu da família!", ephemeral=True)
        await interaction.response.send_message("❌ Você não está em nenhuma família", ephemeral=True)

# Perms select UI
class PermsSelect(discord.ui.Select):
    def __init__(self, dono_id: str):
        options = [discord.SelectOption(label=label, value=value) for label, value in PERMS_FRIENDLY.items()]
        super().__init__(placeholder="Selecione as permissões para o cargo da família",
                         min_values=0, max_values=len(options), options=options, custom_id=f"perms_select:{dono_id}")

    async def callback(self, interaction: discord.Interaction):
        try:
            dono_key = str(interaction.user.id)
            data = carregar()
            familia = data.get(dono_key)
            if not familia:
                familia = next((v for k, v in data.items() if str(v.get("dono")) == dono_key), None)
                if not familia:
                    return await interaction.response.send_message("❌ Família não encontrada para salvar permissões.", ephemeral=True)

            selecionadas = list(self.values)
            familia["permissoes"] = selecionadas
            salvar(data)

            try:
                await atualizar_ou_criar_role_da_familia(familia.get("dono"))
            except Exception as e:
                print("[PERMS WARN] Erro ao aplicar permissoes:", e)

            friendly = [k for k, v in PERMS_FRIENDLY.items() if v in selecionadas]
            texto = ", ".join(friendly) if friendly else "Nenhuma"
            await interaction.response.send_message(f"✅ Permissões salvas: {texto}", ephemeral=True)
        except Exception as e:
            print("[PERMS ERROR] Erro no callback do select:", e)
            await interaction.response.send_message("❌ Ocorreu um erro ao salvar as permissões.", ephemeral=True)

class PermsSelectView(discord.ui.View):
    def __init__(self, dono_id: str):
        super().__init__(timeout=60)
        self.add_item(PermsSelect(dono_id))

class EditarFamiliaView(discord.ui.View):
    def __init__(self, dono_id, familia_id):
        super().__init__(timeout=None)
        self.dono_id = dono_id
        self.familia_id = familia_id

    async def _await_response_and_save(self, interaction: discord.Interaction, prompt: str, field: str, transform=None, allow_attachments=False):
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

        value = None
        if allow_attachments and msg.attachments:
            value = msg.attachments[0].url
        else:
            value = msg.content.strip()

        if transform:
            try:
                value = transform(value)
            except Exception as e:
                return await interaction.followup.send("❌ Valor inválido: " + str(e), ephemeral=True)

        if not value:
            value = data[dono_key].get(field, "")

        data[dono_key][field] = value
        salvar(data)

        if field in ("nome", "cor", "cargo", "permissoes"):
            try:
                await atualizar_ou_criar_role_da_familia(dono_key)
            except Exception as e:
                print("[EDITAR WARN] Erro ao atualizar/criar role da familia:", e)
                pass

        display = value
        if field == "cor":
            display = value
        if field == "icone":
            display = value if value else "Nenhum"
        if field == "permissoes":
            display = ", ".join(value) if isinstance(value, list) else str(value)
        await interaction.followup.send(f"✅ {field.capitalize()} alterado para **{display}**", ephemeral=True)

    @discord.ui.button(label="Nome", style=discord.ButtonStyle.secondary, custom_id="editar:nome")
    async def nome(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(interaction, "✏️ Envie o novo nome no chat. Você tem 60 segundos.", field="nome")

    @discord.ui.button(label="Descrição", style=discord.ButtonStyle.secondary, custom_id="editar:descricao")
    async def descricao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(interaction, "✏️ Envie a nova descrição no chat. Você tem 60 segundos.", field="descricao")

    @discord.ui.button(label="Ícone", style=discord.ButtonStyle.secondary, custom_id="editar:icone")
    async def icone(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._await_response_and_save(interaction, "✏️ Envie o link do ícone ou anexe a imagem. Você tem 60 segundos.", field="icone", allow_attachments=True)

    @discord.ui.button(label="Cor", style=discord.ButtonStyle.secondary, custom_id="editar:cor")
    async def cor(self, interaction: discord.Interaction, button: discord.ui.Button):
        def transform_cor(v):
            v = v.strip()
            if not v:
                return v
            if v.startswith("#"):
                hexpart = v.replace("#", "")
                if len(hexpart) == 6:
                    return f"#{hexpart.upper()}"
                else:
                    raise ValueError("HEX inválido")
            return v

        await self._await_response_and_save(interaction, "✏️ Envie a cor em HEX (ex: #5865F2) ou um nome. Você tem 60 segundos.", field="cor", transform=transform_cor)

    @discord.ui.button(label="Permissões", style=discord.ButtonStyle.secondary, custom_id="editar:permissoes")
    async def permissoes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode editar permissões.", ephemeral=True)

        data = carregar()
        familia = data.get(str(self.dono_id), {})
        atuais = familia.get("permissoes", [])

        view = PermsSelectView(self.dono_id)
        try:
            select: PermsSelect = view.children[0]
            select.values = [v for v in atuais if v in PERMS_FRIENDLY.values()]
        except Exception:
            pass

        await interaction.response.send_message("🛠️ Selecione as permissões para o cargo da família.", view=view, ephemeral=True)

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.gray, custom_id="editar:voltar")
    async def voltar(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        if str(interaction.user.id) != str(self.dono_id):
            return await interaction.response.send_message("❌ Apenas o dono pode editar.", ephemeral=True)
        view = EditarFamiliaView(self.dono_id, self.dono_id)
        await interaction.response.send_message("✏️ Editando família...", view=view, ephemeral=True)

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
            try:
                guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
                if guild:
                    role_id = data[str(self.dono_id)].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await role.delete(reason="Família excluída")
            except Exception as e:
                print("[EXCLUIR WARN] Falha ao deletar role associado:", e)
                pass
            del data[str(self.dono_id)]
            salvar(data)
            return await interaction.response.send_message("🗑️ Família excluída.", ephemeral=True)
        await interaction.response.send_message("❌ Família não encontrada.", ephemeral=True)

    @discord.ui.button(label="🏠 Início", style=discord.ButtonStyle.gray, custom_id="gerenciar:inicio")
    async def inicio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🏠 Menu inicial.", ephemeral=True)

async def enviar_embed_gerenciar(ctx_or_interaction, dono_id):
    data = carregar()
    familia = data.get(str(dono_id))
    if not familia:
        if isinstance(ctx_or_interaction, discord.Interaction):
            return await ctx_or_interaction.response.send_message("❌ Família não encontrada.", ephemeral=True)
        return await ctx_or_interaction.reply("❌ Família não encontrada.")

    nome = familia.get("nome", "Minha Família")
    status = "✅ Ativa"
    dono = familia.get("dono")
    membros_count = len(familia.get("membros", []))
    limite = familia.get("limite", 50)
    cargo_name = familia.get("cargo", nome)
    cor_value = familia.get("cor", "#5865F2")
    vip = familia.get("vip", "Nenhum")

    color_int = 0x5865F2
    cargo_display = f"🏠 @{cargo_name}"
    cor_display = cor_value

    guild = bot.get_guild(SEU_ID_DO_SERVIDOR)
    if guild:
        role = None
        role_id = familia.get("role_id")
        if role_id:
            try:
                role = guild.get_role(int(role_id))
            except:
                role = None

        if role:
            try:
                color_int = role.color.value
            except:
                color_int = color_int
            cargo_display = f"🏠 {role.name}"
        else:
            role_by_name = discord.utils.get(guild.roles, name=f"Família • {nome}")
            if role_by_name:
                try:
                    color_int = role_by_name.color.value
                except:
                    color_int = color_int
                cargo_display = f"🏠 {role_by_name.name}"
            else:
                role_by_name2 = discord.utils.get(guild.roles, name=cargo_name)
                if role_by_name2:
                    try:
                        color_int = role_by_name2.color.value
                    except:
                        color_int = color_int
                    cargo_display = f"🏠 {role_by_name2.name}"
                else:
                    role_by_cor = discord.utils.get(guild.roles, name=cor_value)
                    if role_by_cor:
                        try:
                            color_int = role_by_cor.color.value
                        except:
                            color_int = color_int
                        cor_display = role_by_cor.name

    if isinstance(cor_value, str) and cor_value.startswith("#"):
        try:
            color_int = int(cor_value.replace("#", ""), 16)
            cor_display = cor_value.upper()
        except:
            cor_display = cor_value

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
    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed, view=view)
    else:
        await ctx_or_interaction.reply(embed=embed, view=view)

# ==================== COMANDOS ====================
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
            "permissoes": []
        }
        salvar(data)
        try:
            role = await atualizar_ou_criar_role_da_familia(user_id)
            if role:
                membro = ctx.guild.get_member(ctx.author.id)
                if membro:
                    try:
                        await membro.add_roles(role)
                    except Exception as e:
                        print("[FAMILIA WARN] Falha ao adicionar role ao dono:", e)
                        pass
        except Exception as e:
            print("[FAMILIA WARN] Erro ao atualizar/criar role da familia:", e)
            pass

    membros = "\n".join(f"<@{m}>" for m in data[user_id]["membros"])
    embed = discord.Embed(title=f"👥 {data[user_id]['nome']}", color=0x5865F2)
    embed.add_field(name="👑 Dono", value=f"<@{data[user_id]['dono']}>", inline=False)
    embed.add_field(name=f"👥 Membros ({len(data[user_id]['membros'])})", value=membros, inline=False)
    await ctx.reply(embed=embed)

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

    convites[membro.id] = {"dono": ctx.author.id, "tempo": time.time()}
    view = AceitarView(ctx.author.id)
    try:
        await membro.send(f"📩 Convite para a família de {ctx.author.mention} (expira em 60s)", view=view)
        await ctx.reply(f"✅ Convite enviado para {membro.mention}")
    except Exception as e:
        print("[CONVIDAR WARN] Falha ao enviar DM:", e)
        await ctx.reply("❌ Não consegui enviar DM para esse usuário")

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
            try:
                guild = ctx.guild
                role_id = info.get("role_id")
                if guild and role_id:
                    role = guild.get_role(int(role_id))
                    if role:
                        await ctx.author.remove_roles(role)
            except Exception as e:
                print("[SAIR WARN] Falha ao remover role do usuário:", e)
                pass
            return await ctx.reply("👋 Você saiu da família!")
    await ctx.reply("❌ Você não está em nenhuma família")

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
    try:
        guild = ctx.guild
        role_id = familia.get("role_id")
        if guild and role_id:
            role = guild.get_role(int(role_id))
            if role:
                await membro.remove_roles(role)
    except Exception as e:
        print("[EXPULSAR WARN] Falha ao remover role do membro:", e)
        pass
    await ctx.reply(f"🚫 {membro.mention} foi expulso")

@bot.command()
async def painel(ctx):
    data = carregar()
    user_id = str(ctx.author.id)
    for dono, info in data.items():
        if user_id in info["membros"]:
            membros = "\n".join(f"<@{m}>" for m in info["membros"])
            embed = discord.Embed(title=f"🏠 {info['nome']}", description=membros, color=0x5865F2)
            embed.add_field(name="👑 Dono", value=f"<@{info['dono']}>")
            return await ctx.reply(embed=embed)
    await ctx.reply("❌ Você não está em nenhuma família")

@bot.command()
async def up(ctx, tipo=None):
    if tipo != "painel":
        return await ctx.reply("❌ Use: !up painel")
    embed = discord.Embed(title="🏠 Sistema de Família", description="Use os botões abaixo 👇", color=0x5865F2)
    await ctx.send(embed=embed, view=PainelView())

@bot.command()
async def gerenciar(ctx):
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
        await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="Suporte - Tickets"))
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} comandos sincronizados")
    except Exception:
        traceback.print_exc()

# ==================== MENSAGENS (on_message) sem IA ====================
INVITE_REGEX = re.compile(r"(discord(?:\.gg|app\.com\/invite)\/[A-Za-z0-9\-]+)", re.IGNORECASE)

@bot.event
async def on_message(message):
    try:
        # evita responder a si mesmo ou a outros bots
        if message.author.bot:
            return

        # ignore webhooks
        if getattr(message, "webhook_id", None) is not None:
            return

        # ignore messages that are only embeds (common for error/report bots)
        if not message.content and message.embeds:
            return

        # sempre processa comandos (prefixo "!")
        # mas só responde automaticamente a mensagens "direcionadas" ao bot para interações casuais
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = bot.user in message.mentions if bot.user else False

        # se o canal for chamado "erro", não envie respostas automáticas (apenas comandos)
        channel_name = getattr(message.channel, "name", "")
        if channel_name and channel_name.lower() == "erro":
            await bot.process_commands(message)
            return

        # registra para debug
        print(f"📨 {message.author} ({'DM' if is_dm else channel_name}): {message.content}")

        texto = (message.content or "").lower().strip()

        # --- BLOQUEIO DE INVITES (mantém comportamento atual) ---
        if INVITE_REGEX.search(message.content or ""):
            # permite admins/dono
            if (message.author.guild_permissions.administrator or message.author.id == DONO_ID):
                await bot.process_commands(message)
                return
            try:
                await message.delete()
            except Exception as e:
                print("[BLOQUEIO WARN] Erro ao deletar mensagem com invite:", e)
            try:
                if message.guild:
                    membro = message.guild.get_member(message.author.id)
                    if membro:
                        await mute_member(message.guild, membro)
                    await log(message.guild, f"⚠️ {message.author} enviou link: {message.content}")
            except Exception as e:
                print("[BLOQUEIO WARN] Erro ao processar invite:", e)
            return

        # --- REGRAS QUE DEVEM RODAR SEMPRE (mesmo sem menção) ---
        palavras_chave = ["login", "senha", "esqueci", "não consigo", "nao consigo", "acesso", "ajuda", "ticket", "suporte"]
        if any(p in texto for p in palavras_chave):
            await message.reply("🔐 Para suporte, vá em <#1479642544429076500>", mention_author=False)
            return

        frases_site = ["o site caiu", "site caiu", "site tá fora", "site ta fora", "site offline", "site não funciona", "site nao funciona", "site saiu do ar"]
        if any(frase in texto for frase in frases_site):
            await message.reply("🌐 Veja em <#1409296003034644542>", mention_author=False)
            return

        frases_obras = ["sugestão de obra", "sugestões de obra", "sugestão de obras", "sugestões de obras", "indicação de obra", "indicações de obras", "obras sugeridas", "obras recomendadas"]
        if any(frase in texto for frase in frases_obras):
            await message.reply("📚 Sugestões de obras é em <#1466087941506990171>", mention_author=False)
            return

        frases_capitulos = [
            "faltando capítulos", "faltam capítulos", "capítulos faltando", "capitulo faltando", "capítulos sumiram",
            "faltando capitulo", "não tem capítulos", "nao tem capitulos", "cadê os capítulos", "cade os capitulos",
            "onde estão os capítulos", "onde estao os capitulos"
        ]
        if any(frase in texto for frase in frases_capitulos):
            await message.reply("<#1452799882149761144>", mention_author=False)
            return

        # --- INTERAÇÕES que devem ocorrer apenas quando a mensagem for dirigida ao bot ---
        should_respond_personal = is_dm or mentions_bot

        if should_respond_personal:
            # SAUDAÇÕES
            saudacoes = {
                "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
                "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
                "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
            }
            for chave in saudacoes:
                if texto.startswith(chave) or (mentions_bot and chave in texto):
                    await message.reply(saudacoes[chave], mention_author=False)
                    return

            # INTERAÇÕES
            if re.search(r"(agradecido|obg|obrigado).*(jeffu)?", texto):
                await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)
                return

            if re.search(r"(te amo|amo vc|amo você).*(jeffu)?", texto):
                await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)
                return

            if re.search(r"(cala boca|calaboca|clbc|cbc|fica quieto|quieto).*(jeffu)?", texto):
                await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)
                return

        # processa comandos normalmente (sempre)
        await bot.process_commands(message)

    except Exception as e:
        print(f"Erro no on_message: {e}")

# ==================== TOKEN ====================
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado!")
