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
        canal = guild.get_channel(LOG_CHANNEL_ID) if guild else None
        if canal:
            await canal.send(mensagem)
        else:
            # fallback: print
            print("[LOG]", mensagem)
    except Exception:
        print("[LOG ERROR] Falha ao enviar log:")
        traceback.print_exc()

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
        if familias_db:
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
        traceback.print_exc()

    # fallback para arquivo local
    if not os.path.exists(ARQUIVO):
        return {}
    try:
        with open(ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[FILE WARN] Falha ao carregar arquivo:", e)
        traceback.print_exc()
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
        if familias_db:
            familias_db.update_one(
                {"_id": "familias"},
                {"$set": {"data": data}},
                upsert=True
            )
            return
    except Exception as e:
        print("[DB WARN] Falha ao salvar no MongoDB:", e)
        traceback.print_exc()

    # fallback para arquivo local
    tmp = ARQUIVO + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, ARQUIVO)
    except Exception as e:
        print("[FILE ERROR] Falha ao salvar arquivo:", e)
        traceback.print_exc()
        try:
            with open(ARQUIVO, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e2:
            print("[FILE ERROR] Falha final ao salvar arquivo:", e2)
            traceback.print_exc()

def carregar_autorizados():
    if not os.path.exists(AUTORIZADOS_FILE):
        return []
    try:
        with open(AUTORIZADOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[FILE WARN] Falha ao carregar autorizados:", e)
        traceback.print_exc()
        return []

def salvar_autorizados(lista):
    try:
        with open(AUTORIZADOS_FILE, "w", encoding="utf-8") as f:
            json.dump(lista, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[FILE ERROR] Falha ao salvar autorizados:", e)
        traceback.print_exc()

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
            traceback.print_exc()

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
            traceback.print_exc()
        await setup_muted_role(guild, role)
        return role

    # se não existe, cria
    try:
        if not guild.me.guild_permissions.manage_roles:
            print("[MUTE ERROR] Bot não tem Manage Roles; não é possível criar Muted role.")
            return None
    except Exception:
        print("[MUTE ERROR] Não foi possível checar permissões do bot para criar Muted role.")
        return None

    try:
        role = await guild.create_role(name="Muted", permissions=perms,
                                       reason="Role de mute criado pelo bot")
        print(f"[MUTE OK] Role Muted criado: {role} (id={role.id})")
        await setup_muted_role(guild, role)
        return role
    except Exception as e:
        print(f"[MUTE ERROR] Falha ao criar role Muted: {e}")
        traceback.print_exc()
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
                traceback.print_exc()
            return True
        except Exception as e:
            print("[MUTE ERROR] Falha ao adicionar role Muted ao membro:", e)
            traceback.print_exc()
            return False
    except Exception as e:
        print("[MUTE ERROR] Erro inesperado em mute_member:", e)
        traceback.print_exc()
        return False

async def safe_get_or_create_role(guild: discord.Guild, role_name: str, color_int: int = None):
    """
    Cria ou reutiliza um cargo com logs e checagem de permissão Manage Roles.
    """
    try:
        if not guild.me.guild_permissions.manage_roles:
            print("[ROLE ERROR] Bot não tem Manage Roles no servidor.")
            return None

        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            if color_int is not None:
                try:
                    await role.edit(colour=discord.Colour(color_int))
                except Exception as e:
                    print("[ROLE WARN] Não foi possível editar cor do role existente:", e)
                    traceback.print_exc()
            print(f"[ROLE OK] Reutilizando role existente: {role.name} (id={role.id})")
            return role

        if color_int is not None:
            role = await guild.create_role(name=role_name,
                                           colour=discord.Colour(color_int),
                                           reason="Criado pelo sistema de famílias")
        else:
            role = await guild.create_role(name=role_name,
                                           reason="Criado pelo sistema de famílias")
        print(f"[ROLE OK] Role criado: {role} (id={role.id})")
        return role

    except discord.Forbidden:
        print("[ROLE ERROR] Forbidden: bot não pode criar/editar roles.")
    except discord.HTTPException as e:
        print("[ROLE ERROR] HTTPException ao criar role:", e)
        traceback.print_exc()
    except Exception as e:
        print("[ROLE ERROR] Erro inesperado ao criar role:", e)
        traceback.print_exc()
    return None

async def aplicar_cargo_a_todos(guild: discord.Guild, role: discord.Role, membros_list: list):
    for m_id in membros_list:
        try:
            membro = guild.get_member(int(m_id))
            if membro and role not in membro.roles:
                await membro.add_roles(role)
        except Exception as e:
            print(f"[ROLE APPLY WARN] Falha ao aplicar role a {m_id}: {e}")
            traceback.print_exc()
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
        traceback.print_exc()

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
            traceback.print_exc()
            color_int = None

    role = None
    role_id = familia.get("role_id")
    if role_id:
        try:
            role = guild.get_role(int(role_id))
        except Exception as e:
            print("[ROLE WARN] role_id salvo não encontrado no guild:", e)
            traceback.print_exc()
            role = None

    if role:
        try:
            if role.name != role_display_name:
                await role.edit(name=role_display_name)
        except Exception as e:
            print("[ROLE WARN] Não foi possível renomear role existente:", e)
            traceback.print_exc()
        if color_int is not None:
            try:
                await role.edit(colour=discord.Colour(color_int))
            except Exception as e:
                print("[ROLE WARN] Não foi possível editar cor do role existente:", e)
                traceback.print_exc()
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
                        traceback.print_exc()
        except Exception as e:
            print("[ROLE ERROR] Erro ao obter/criar role:", e)
            traceback.print_exc()
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
                traceback.print_exc()

        try:
            await aplicar_cargo_a_todos(guild, role, familia.get("membros", []))
        except Exception as e:
            print("[ROLE WARN] Erro ao aplicar role a todos:", e)
            traceback.print_exc()
        return role

    return None

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
                traceback.print_exc()
                cargo = None

            if membro and cargo:
                try:
                    await membro.add_roles(cargo)
                except Exception as e:
                    print(f"[ACEITAR WARN] Falha ao adicionar role ao membro: {e}")
                    traceback.print_exc()
                    pass

        await interaction.response.send_message("✅ Você entrou na família!", ephemeral=True)

class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Ver Família", style=discord.ButtonStyle.blurple, custom_id="painel:ver")
    async def ver(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = carregar()
        user_id = str(interaction.user.id)
        familia = next((info for info in data.values() if user_id in info.get("membros", [])), None)
        if not familia:
            return await interaction.response.send_message("❌ Você não está em nenhuma família", ephemeral=True)

        membros = "\n".join(f"<@{m}>" for m in familia.get("membros", []))
        embed = discord.Embed(title=f"🏠 {familia.get('nome','Família')}", description=membros, color=0x5865F2)
        embed.add_field(name="👑 Dono", value=f"<@{familia.get('dono')}>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.red, custom_id="painel:sair")
    async def sair_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = carregar()
        user_id = str(interaction.user.id)
        for dono, info in data.items():
            if user_id in info.get("membros", []):
                if user_id == str(info.get("dono")):
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
                    traceback.print_exc()
                    pass
                return await interaction.response.send_message("👋 Você saiu da família!", ephemeral=True)
        await interaction.response.send_message("❌ Você não está em nenhuma família", ephemeral=True)

# (restante das views e comandos seguem — para brevidade, manter a mesma lógica já presente no seu arquivo)
# ==================== COMANDOS ====================
@bot.command()
async def familia(ctx):
    autorizados = carregar_autorizados()
    if not (
        any(role.id in CARGOS_AUTORIZADOS for role in ctx.author.roles)
        or ctx.author.guild_permissions.administrator
        or ctx.author.id in autorizados
    ):
        return await ctx.reply("❌ Você não tem permissão para usar este comando.")

    view = PainelView()
    await ctx.reply("Painel de famílias:", view=view)

@bot.command(name="criar")
@commands.guild_only()
async def criar_familia(ctx, *, nome: str = "Minha Família"):
    user_id = str(ctx.author.id)
    data = carregar()
    if user_id in data:
        return await ctx.reply("❌ Você já tem uma família criada.")
    familia = {
        "dono": user_id,
        "nome": nome,
        "membros": [user_id],
        "cor": "#5865F2",
        "permissoes": [],
        "limite": 50,
        "vip": "Nenhum"
    }
    data[user_id] = familia
    salvar(data)
    try:
        await atualizar_ou_criar_role_da_familia(user_id)
    except Exception as e:
        print("[CRIAR WARN] Erro ao criar role:", e)
        traceback.print_exc()
    await ctx.reply(f"✅ Família **{nome}** criada com sucesso!")

@bot.command(name="convidar")
@commands.guild_only()
async def convidar(ctx, membro: discord.Member):
    data = carregar()
    dono_key = str(ctx.author.id)
    familia = data.get(dono_key)
    if not familia:
        familia = next((v for k, v in data.items() if str(v.get("dono")) == dono_key), None)
        if not familia:
            return await ctx.reply("❌ Você não é dono de nenhuma família.")
    if str(membro.id) in familia.get("membros", []):
        return await ctx.reply("❌ Esse usuário já está na família.")
    convites[membro.id] = {"dono": int(dono_key), "tempo": time.time()}
    try:
        view = AceitarView(int(dono_key))
        await membro.send(f"Você foi convidado para entrar na família **{familia.get('nome')}**. Clique para aceitar.", view=view)
        await ctx.reply(f"✅ Convite enviado para {membro.mention}.")
    except Exception as e:
        print("[CONVIDAR WARN] Falha ao enviar DM:", e)
        traceback.print_exc()
        await ctx.reply(f"⚠️ Não foi possível enviar DM para {membro.mention}. O convite foi registrado; peça para o usuário verificar as DMs ou use `!convidar` novamente.")

@bot.command(name="painel")
@commands.guild_only()
async def painel(ctx):
    view = PainelView()
    await ctx.reply("Painel de famílias:", view=view)

@bot.command(name="gerenciar")
@commands.guild_only()
async def gerenciar(ctx):
    data = carregar()
    user_id = str(ctx.author.id)
    familia = data.get(user_id)
    if not familia:
        familia = next((v for k, v in data.items() if str(v.get("dono")) == user_id), None)
        if not familia:
            return await ctx.reply("❌ Você não é dono de nenhuma família.")
        dono_key = familia.get("dono")
    else:
        dono_key = user_id
    await enviar_embed_gerenciar(ctx, int(dono_key))

# ==================== EVENTOS E MODERAÇÃO ====================
# Regex mais abrangente para invites
INVITE_REGEX = re.compile(r"(discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[A-Za-z0-9\-]+)", re.IGNORECASE)

@bot.event
async def on_message(message: discord.Message):
    try:
        # ignora mensagens do bot
        if message.author.bot:
            return

        texto = (message.content or "").lower()
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = bot.user and (bot.user.mentioned_in(message))

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

        # --- DETECÇÃO DE INVITES ---
        try:
            if INVITE_REGEX.search(message.content or ""):
                try:
                    await message.delete()
                except Exception as e:
                    print("[MOD WARN] Falha ao deletar mensagem com invite:", e)
                    traceback.print_exc()

                guild = message.guild
                # tenta mutar o autor (aplica role Muted)
                try:
                    if guild:
                        membro = guild.get_member(message.author.id)
                        if membro:
                            ok = await mute_member(guild, membro)
                            if not ok:
                                print("[MOD WARN] Não foi possível aplicar mute ao membro.")
                except Exception as e:
                    print("[MOD ERROR] Erro ao tentar mutar membro:", e)
                    traceback.print_exc()

                # notifica canal de logs
                try:
                    aviso = (
                        f"⚠️ Invite removido!\n"
                        f"Usuário: {message.author.mention} ({message.author.id})\n"
                        f"Canal: {message.channel.mention if message.channel else 'DM'}\n"
                        f"Conteúdo: {message.content}"
                    )
                    if guild:
                        await log(guild, aviso)
                except Exception as e:
                    print("[LOG WARN] Falha ao enviar aviso de invite:", e)
                    traceback.print_exc()

                return
        except Exception as e:
            print("[ON_MESSAGE WARN] Erro ao checar invites:", e)
            traceback.print_exc()

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
        traceback.print_exc()

# ==================== STARTUP / TOKEN ====================
@bot.event
async def on_ready():
    print(f"[BOT] Logado como {bot.user} (id: {bot.user.id})")
    # tenta atualizar roles para todas as familias no startup (não bloqueante)
    try:
        data = carregar()
        for dono in list(data.keys()):
            try:
                asyncio.create_task(atualizar_ou_criar_role_da_familia(dono))
            except Exception:
                pass
    except Exception:
        pass

# Carrega token do ambiente
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN.")
