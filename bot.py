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
async def log(guild: discord.Guild, mensagem: str):
    """
    Envia mensagem para o canal de logs configurado (LOG_CHANNEL_ID).
    Se não encontrar, tenta fallback por nome 'mod-logs' e, se nada, imprime no console.
    """
    try:
        canal = None
        if guild and LOG_CHANNEL_ID:
            canal = guild.get_channel(LOG_CHANNEL_ID)
        if not canal and guild:
            canal = discord.utils.get(guild.text_channels, name="mod-logs")
        if canal:
            await canal.send(mensagem)
        else:
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
    try:
        if familias_db is not None:
            doc = familias_db.find_one({"_id": "familias"})
            if doc and "data" in doc and isinstance(doc["data"], dict):
                return doc["data"]
            return {}
    except Exception as e:
        print("[DB WARN] Falha ao carregar do MongoDB:", e)
        traceback.print_exc()

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

    try:
        if familias_db is not None:
            familias_db.update_one({"_id": "familias"}, {"$set": {"data": data}}, upsert=True)
            return
    except Exception as e:
        print("[DB WARN] Falha ao salvar no MongoDB:", e)
        traceback.print_exc()

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
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    add_reactions=False,
                    send_tts_messages=False,
                    attach_files=False,
                    embed_links=False
                )
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
    perms.update(
        send_messages=False,
        add_reactions=False,
        send_tts_messages=False,
        attach_files=False,
        embed_links=False,
        speak=False,
        connect=False,
        mention_everyone=False
    )

    if role:
        try:
            await role.edit(permissions=perms)
        except Exception as e:
            print(f"[MUTE WARN] Não foi possível editar permissões do role Muted: {e}")
            traceback.print_exc()
        await setup_muted_role(guild, role)
        return role

    try:
        if not guild.me.guild_permissions.manage_roles:
            print("[MUTE ERROR] Bot não tem Manage Roles; não é possível criar Muted role.")
            return None
    except Exception:
        print("[MUTE ERROR] Não foi possível checar permissões do bot para criar Muted role.")
        return None

    try:
        role = await guild.create_role(name="Muted", permissions=perms, reason="Role de mute criado pelo bot")
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
    (mantido para compatibilidade; preferir mute_member_with_duration)
    """
    try:
        if not guild or not member:
            return False
        role = await get_or_create_muted_role(guild)
        if not role:
            return False
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

        try:
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
        except Exception as e:
            print("[ROLE ERROR] Erro ao procurar role existente:", e)
            traceback.print_exc()

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
            traceback.print_exc()
        except Exception as e:
            print("[ROLE ERROR] Erro inesperado ao criar role:", e)
            traceback.print_exc()
        return None

    except Exception as e:
        print("[ROLE ERROR] Erro inesperado em safe_get_or_create_role:", e)
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

# ==================== MUTES AUTOMÁTICOS (10 minutos) ==================
MUTES_FILE = "active_mutes.json"
_active_mutes = {}  # formato: {guild_id_str: {member_id_str: unmute_timestamp}}

def _load_mutes():
    global _active_mutes
    try:
        if os.path.exists(MUTES_FILE):
            with open(MUTES_FILE, "r", encoding="utf-8") as f:
                _active_mutes = json.load(f)
        else:
            _active_mutes = {}
    except Exception as e:
        print("[MUTE PERSIST WARN] Falha ao carregar mutes:", e)
        traceback.print_exc()
        _active_mutes = {}

def _save_mutes():
    try:
        with open(MUTES_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_active_mutes, f, indent=2)
        os.replace(MUTES_FILE + ".tmp", MUTES_FILE)
    except Exception as e:
        print("[MUTE PERSIST WARN] Falha ao salvar mutes:", e)
        traceback.print_exc()

_load_mutes()

async def _schedule_unmute(guild_id: int, member_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    try:
        guild = bot.get_guild(int(guild_id))
        guild_key = str(guild_id)
        if guild_key in _active_mutes and str(member_id) in _active_mutes[guild_key]:
            del _active_mutes[guild_key][str(member_id)]
            if not _active_mutes[guild_key]:
                del _active_mutes[guild_key]
            _save_mutes()

        if not guild:
            return

        member = guild.get_member(int(member_id))
        role = discord.utils.get(guild.roles, name="Muted")
        if role and member and role in member.roles:
            try:
                await member.remove_roles(role, reason="Unmute automático (10 minutos expirados)")
                try:
                    await log(guild, f"🔊 {member.mention} ({member.id}) foi desmutado automaticamente (10m).")
                except Exception:
                    pass
            except Exception as e:
                print("[SCHEDULE UNMUTE ERROR]", e)
                traceback.print_exc()
    except Exception as e:
        print("[SCHEDULE UNMUTE ERROR]", e)
        traceback.print_exc()

async def mute_member_with_duration(guild: discord.Guild, member: discord.Member, seconds: int = 600) -> bool:
    """
    Aplica o role 'Muted' ao membro por 'seconds' segundos (padrão 600 = 10m).
    """
    try:
        if not guild or not member:
            return False

        role = await get_or_create_muted_role(guild)
        if not role:
            return False

        if role in member.roles:
            if seconds and seconds > 0:
                guild_key = str(guild.id)
                if guild_key not in _active_mutes:
                    _active_mutes[guild_key] = {}
                _active_mutes[guild_key][str(member.id)] = int(time.time()) + int(seconds)
                _save_mutes()
                asyncio.create_task(_schedule_unmute(guild.id, member.id, int(seconds)))
            return True

        try:
            await member.add_roles(role, reason="Muted automático (invite) — 10 minutos")
        except Exception as e:
            print("[MUTE ERROR] Falha ao adicionar role Muted ao membro:", e)
            traceback.print_exc()
            return False

        if seconds and seconds > 0:
            guild_key = str(guild.id)
            if guild_key not in _active_mutes:
                _active_mutes[guild_key] = {}
            _active_mutes[guild_key][str(member.id)] = int(time.time()) + int(seconds)
            _save_mutes()
            asyncio.create_task(_schedule_unmute(guild.id, member.id, int(seconds)))

        try:
            await log(guild, f"🔇 {member.mention} ({member.id}) mutado automaticamente por 10 minutos (envio de invite).")
        except Exception:
            pass

        return True
    except Exception as e:
        print("[MUTE ERROR] Erro inesperado em mute_member_with_duration:", e)
        traceback.print_exc()
        return False

# ==================== UNMUTE MANUAL ====================
async def unmute_member(guild: discord.Guild, member: discord.Member) -> bool:
    try:
        if not guild or not member:
            return False
        role = discord.utils.get(guild.roles, name="Muted")
        if not role:
            return False
        if role not in member.roles:
            return True
        try:
            await member.remove_roles(role, reason="Unmuted pelo bot")
            guild_key = str(guild.id)
            if guild_key in _active_mutes and str(member.id) in _active_mutes[guild_key]:
                del _active_mutes[guild_key][str(member.id)]
                if not _active_mutes[guild_key]:
                    del _active_mutes[guild_key]
                _save_mutes()
            try:
                await log(guild, f"🔊 {member.mention} ({member.id}) foi desmutado.")
            except Exception:
                pass
            return True
        except Exception as e:
            print("[UNMUTE ERROR] Falha ao remover role Muted do membro:", e)
            traceback.print_exc()
            return False
    except Exception as e:
        print("[UNMUTE ERROR] Erro inesperado em unmute_member:", e)
        traceback.print_exc()
        return False

@bot.command(name="unmute")
@commands.guild_only()
@commands.has_permissions(manage_roles=True)
async def cmd_unmute(ctx, membro: discord.Member = None):
    if membro is None:
        return await ctx.reply("❌ Mencione o usuário que deseja desmutar. Ex: `!unmute @usuario`", mention_author=False)

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
        traceback.print_exc()
        await ctx.reply("❌ Ocorreu um erro ao tentar desmutar.", mention_author=False)

# ==================== REAÇÕES AUTOMÁTICAS (persistentes) ==================
REACTIONS_FILE = "reactions_rules.json"
_reaction_rules = {}  # formato: {guild_id_str: {"by_message": {}, "by_keyword": [] } }

def _load_reaction_rules():
    global _reaction_rules
    try:
        if os.path.exists(REACTIONS_FILE):
            with open(REACTIONS_FILE, "r", encoding="utf-8") as f:
                _reaction_rules = json.load(f)
        else:
            _reaction_rules = {}
    except Exception as e:
        print("[REACTIONS WARN] Falha ao carregar regras:", e)
        traceback.print_exc()
        _reaction_rules = {}

def _save_reaction_rules():
    try:
        with open(REACTIONS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_reaction_rules, f, indent=2, ensure_ascii=False)
        os.replace(REACTIONS_FILE + ".tmp", REACTIONS_FILE)
    except Exception as e:
        print("[REACTIONS WARN] Falha ao salvar regras:", e)
        traceback.print_exc()

_load_reaction_rules()

def _ensure_guild_rules(guild_id: int):
    gk = str(guild_id)
    if gk not in _reaction_rules:
        _reaction_rules[gk] = {"by_message": {}, "by_keyword": []}
    return _reaction_rules[gk]

async def _try_add_reaction(message: discord.Message, emoji: str):
    """
    Tenta adicionar a reação. emoji pode ser unicode (ex: 👍) ou custom (<:name:id> ou <a:name:id>).
    """
    try:
        await message.add_reaction(emoji)
        return True
    except Exception:
        try:
            m = re.match(r"<a?:\w+:(\d+)>", emoji)
            if m:
                emoji_id = int(m.group(1))
                partial = discord.PartialEmoji(name=None, id=emoji_id, animated=False)
                await message.add_reaction(partial)
                return True
        except Exception:
            pass
    return False

@bot.command(name="reactadd")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def cmd_reactadd(ctx, target: str = None, *emojis):
    if not target or not emojis:
        return await ctx.reply("❌ Uso: `!reactadd <channel_id/message_id | message_link | keyword:palavra> emoji1 emoji2 ...`", mention_author=False)

    guild_rules = _ensure_guild_rules(ctx.guild.id)

    if target.startswith("keyword:"):
        keyword = target.split("keyword:", 1)[1].strip()
        if not keyword:
            return await ctx.reply("❌ Keyword inválida.", mention_author=False)
        guild_rules["by_keyword"].append({
            "channel_id": ctx.channel.id,
            "keyword": keyword.lower(),
            "emojis": list(emojis)
        })
        _save_reaction_rules()
        return await ctx.reply(f"✅ Regra adicionada: quando mensagem em {ctx.channel.mention} contiver `{keyword}`, reagir com: {' '.join(emojis)}", mention_author=False)

    channel_id = None
    message_id = None
    m = re.match(r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)", target)
    if m:
        channel_id = int(m.group(1))
        message_id = int(m.group(2))
    else:
        if "/" in target:
            parts = target.split("/")
            try:
                channel_id = int(parts[0]); message_id = int(parts[1])
            except Exception:
                pass
        else:
            try:
                message_id = int(target)
                channel_id = ctx.channel.id
            except Exception:
                pass

    if not channel_id or not message_id:
        return await ctx.reply("❌ Não consegui interpretar target. Use link de mensagem, channel_id/message_id ou keyword:palavra.", mention_author=False)

    ch_map = guild_rules["by_message"].setdefault(str(channel_id), {})
    ch_map[str(message_id)] = list(emojis)
    _save_reaction_rules()
    return await ctx.reply(f"✅ Regra adicionada: reagir à mensagem `{message_id}` em <#{channel_id}> com: {' '.join(emojis)}", mention_author=False)

@bot.command(name="reactremove")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def cmd_reactremove(ctx, target: str = None):
    if not target:
        return await ctx.reply("❌ Uso: `!reactremove channel_id/message_id` ou `!reactremove message_id`", mention_author=False)

    guild_rules = _ensure_guild_rules(ctx.guild.id)

    channel_id = None
    message_id = None
    if "/" in target:
        parts = target.split("/")
        try:
            channel_id = int(parts[0]); message_id = int(parts[1])
        except Exception:
            pass
    else:
        try:
            message_id = int(target)
            channel_id = ctx.channel.id
        except Exception:
            pass

    if channel_id and message_id:
        ch_map = guild_rules["by_message"].get(str(channel_id), {})
        if str(message_id) in ch_map:
            del ch_map[str(message_id)]
            if not ch_map:
                guild_rules["by_message"].pop(str(channel_id), None)
            _save_reaction_rules()
            return await ctx.reply(f"✅ Regra removida para mensagem `{message_id}` em <#{channel_id}>", mention_author=False)
        else:
            return await ctx.reply("❌ Regra não encontrada para essa mensagem.", mention_author=False)

    return await ctx.reply("❌ Não consegui interpretar target. Use channel_id/message_id ou message_id no canal atual.", mention_author=False)

@bot.command(name="reactlist")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def cmd_reactlist(ctx):
    guild_rules = _ensure_guild_rules(ctx.guild.id)
    lines = []
    for ch_id, msgs in guild_rules.get("by_message", {}).items():
        for m_id, emojis in msgs.items():
            lines.append(f"Mensagem: <#{ch_id}>/{m_id}  →  {' '.join(emojis)}")
    for i, kw in enumerate(guild_rules.get("by_keyword", []), start=1):
        ch = kw.get("channel_id")
        lines.append(f"Keyword #{i}: canal <#{ch}> palavra `{kw.get('keyword')}` → {' '.join(kw.get('emojis',[]))}")
    if not lines:
        return await ctx.reply("Nenhuma regra configurada neste servidor.", mention_author=False)
    chunk = "\n".join(lines)
    if len(chunk) < 1900:
        await ctx.reply(f"**Regras:**\n{chunk}", mention_author=False)
    else:
        for i in range(0, len(lines), 30):
            await ctx.reply("```\n" + "\n".join(lines[i:i+30]) + "\n```", mention_author=False)

@bot.command(name="reactapply")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def cmd_reactapply(ctx, channel_id: int = None, message_id: int = None):
    if not channel_id or not message_id:
        return await ctx.reply("❌ Uso: `!reactapply <channel_id> <message_id>`", mention_author=False)
    try:
        ch = ctx.guild.get_channel(channel_id)
        if not ch:
            return await ctx.reply("❌ Canal não encontrado.", mention_author=False)
        try:
            msg = await ch.fetch_message(message_id)
        except Exception:
            return await ctx.reply("❌ Mensagem não encontrada (verifique IDs e permissões).", mention_author=False)
        gk = str(ctx.guild.id)
        rules = _reaction_rules.get(gk, {})
        emojis = rules.get("by_message", {}).get(str(channel_id), {}).get(str(message_id))
        if not emojis:
            return await ctx.reply("❌ Não há regras configuradas para essa mensagem.", mention_author=False)
        for em in emojis:
            await _try_add_reaction(msg, em)
        return await ctx.reply("✅ Reações aplicadas.", mention_author=False)
    except Exception as e:
        print("[REACT APPLY ERROR]", e)
        traceback.print_exc()
        await ctx.reply("❌ Erro ao aplicar reações.", mention_author=False)

# ==================== VIEWS E INTERAÇÕES (resumido) ====================
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

# ==================== COMANDOS BÁSICOS (exemplos) ====================
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
                        traceback.print_exc()
                        pass
        except Exception as e:
            print("[FAMILIA WARN] Erro ao atualizar/criar role da familia:", e)
            traceback.print_exc()
            pass

    membros = "\n".join(f"<@{m}>" for m in data[user_id]["membros"])
    embed = discord.Embed(title=f"👥 {data[user_id]['nome']}", color=0x5865F2)
    embed.add_field(name="👑 Dono", value=f"<@{data[user_id]['dono']}>", inline=False)
    embed.add_field(name=f"👥 Membros ({len(data[user_id]['membros'])})", value=membros, inline=False)
    await ctx.reply(embed=embed)

# ==================== REGEXS E UTILITÁRIOS ====================
BAD_WORDS_PATTERN = re.compile(
    r"\b(?:cala boca|calaboca|clbc|cbc|fica quieto|quieto)\b(?:.*(?:jeffu|<@!?\d+>))?",
    re.IGNORECASE
)

# ==================== DEFAULT KEYWORD RULES (auto) ==================
DEFAULT_KEYWORD_RULES = [
    {
        "keywords": ["bis", "bisdov", "bisdov3", "chefe"],
        "emojis": ["<:FBI:1466776866122629252>"]
    },
    {
        "keywords": ["theus", "matheus", "god", "matheuz", "matheuss", "matheuzinho"],
        "emojis": ["<:suspect:1466766825361641634>"]
    },
    {
        "keywords": ["lipe", "lipezinho", "lipezito"],
        "emojis": ["<:808757471270404098:1466605544143061193>"]
    }
]

def _ensure_default_rules_for_all_guilds():
    """
    Garante que cada guild tenha as regras DEFAULT_KEYWORD_RULES (channel_id = 0).
    Chame esta função em on_ready (após _load_reaction_rules()).
    """
    for guild in bot.guilds:
        gk = str(guild.id)
        if gk not in _reaction_rules:
            _reaction_rules[gk] = {"by_message": {}, "by_keyword": []}
        existing = _reaction_rules[gk].get("by_keyword", [])
        for rule in DEFAULT_KEYWORD_RULES:
            for kw in rule["keywords"]:
                found = False
                for ex in existing:
                    if ex.get("keyword","").lower() == kw.lower() and set(ex.get("emojis",[])) == set(rule["emojis"]):
                        found = True
                        break
                if not found:
                    existing.append({
                        "channel_id": 0,            # 0 = wildcard (qualquer canal)
                        "keyword": kw,
                        "is_regex": False,
                        "emojis": rule["emojis"]
                    })
        _reaction_rules[gk]["by_keyword"] = existing
    _save_reaction_rules()

# ==================== EVENTOS E MODERAÇÃO (ÚNICO on_message) ====================
INVITE_REGEX = re.compile(r"(discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[A-Za-z0-9\-]+)", re.IGNORECASE)

@bot.event
async def on_message(message: discord.Message):
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

        # --- APLICAR REAÇÕES POR PALAVRAS (prioritário) ---
        try:
            guild = message.guild
            if guild:
                gk = str(guild.id)
                rules = _reaction_rules.get(gk, {})
                for kw in rules.get("by_keyword", []):
                    try:
                        ch_id = int(kw.get("channel_id", 0))
                        if ch_id != 0 and ch_id != message.channel.id:
                            continue
                        content = (message.content or "")
                        if not content:
                            continue
                        if kw.get("is_regex"):
                            try:
                                if re.search(kw.get("keyword", ""), content, re.IGNORECASE):
                                    for em in kw.get("emojis", []):
                                        await _try_add_reaction(message, em)
                            except re.error:
                                print("[REACTIONS WARN] Regex inválida para regra:", kw.get("keyword"))
                        else:
                            if kw.get("keyword", "").lower() in content.lower():
                                for em in kw.get("emojis", []):
                                    await _try_add_reaction(message, em)
                    except Exception:
                        pass
        except Exception as e:
            print("[REACTIONS ERROR] ao aplicar regras:", e)
            traceback.print_exc()

        # normaliza texto
        texto = (message.content or "").strip()

        # BLOQUEIO DE INVITES (10m automático)
        if INVITE_REGEX.search(message.content or ""):
            if (message.author.guild_permissions.administrator or message.author.id == DONO_ID):
                await bot.process_commands(message)
                return
            try:
                await message.delete()
            except Exception as e:
                print("[BLOQUEIO WARN] Erro ao deletar mensagem com invite:", e)
                traceback.print_exc()
            try:
                if message.guild:
                    membro = message.guild.get_member(message.author.id)
                    if membro:
                        ok = await mute_member_with_duration(message.guild, membro, seconds=600)
                        if not ok:
                            print("[MOD WARN] Não foi possível aplicar mute automático.")
                    await log(message.guild, f"⚠️ {message.author} enviou invite e foi mutado por 10m: {message.content}")
            except Exception as e:
                print("[BLOQUEIO WARN] Erro ao processar invite:", e)
                traceback.print_exc()
            return

# REGRAS AUTOMÁTICAS (respostas rápidas) — versão limpa
texto = (message.content or "").strip()
lower = texto.lower()

palavras_chave = ["login", "senha", "esqueci", "não consigo", "nao consigo", "acesso", "ajuda", "ticket", "suporte"]
if any(p in lower for p in palavras_chave):
    await message.reply("🔐 Para suporte, vá em <#1479642544429076500>", mention_author=False)
    return

frases_site = ["o site caiu", "site caiu", "site tá fora", "site ta fora", "site offline", "site não funciona", "site nao funciona", "site saiu do ar"]
if any(frase in lower for frase in frases_site):
    await message.reply("🌐 Veja em <#1409296003034644542>", mention_author=False)
    return

frases_obras = ["sugestão de obra", "sugestões de obra", "sugestão de obras", "sugestões de obras", "indicação de obra", "indicações de obras", "obras sugeridas", "obras recomendadas"]
if any(frase in lower for frase in frases_obras):
    await message.reply("📚 Sugestões de obras é em <#1466087941506990171>", mention_author=False)
    return

frases_capitulos = [
    "faltando capítulos", "faltam capítulos", "capítulos faltando", "capitulo faltando", "capítulos sumiram",
    "faltando capitulo", "não tem capítulos", "nao tem capitulos", "cadê os capítulos", "cade os capitulos",
    "onde estão os capítulos", "onde estao os capitulos"
]
if any(frase in lower for frase in frases_capitulos):
    await message.reply("<#1452799882149761144>", mention_author=False)
    return

is_dm = isinstance(message.channel, discord.DMChannel)
mentions_bot = bot.user in message.mentions if bot.user else False
if is_dm or mentions_bot:
    saudacoes = {
        "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
        "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
        "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
    }
    for chave, resposta in saudacoes.items():
        if lower.startswith(chave) or (mentions_bot and chave in lower):
            await message.reply(resposta, mention_author=False)
            return

    if re.search(r"(agradecido jeffu|obg jeffu|obrigado jeffu|vlw jeffu)", texto, re.IGNORECASE):
        await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)
        return

    if re.search(r"(te amo jeffu|amo vc jeffu|amo você jeffu|amo voce jeffu|jeffu te amo |jeffu amo vc |jeffu amo você|jeffu amo voce )", texto, re.IGNORECASE):
        await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)
        return

    if BAD_WORDS_PATTERN.search(texto):
        await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)
        return


# ==================== STARTUP / TOKEN ====================
@bot.event
async def on_ready():
    print(f"[BOT] Logado como {bot.user} (id: {bot.user.id})")
    try:
        _ensure_default_rules_for_all_guilds()
    except Exception as e:
        print("[DEFAULT RULES WARN] Falha ao garantir regras padrão:", e)
        traceback.print_exc()

    # re-agenda mutes carregados do arquivo
    try:
        now = int(time.time())
        for guild_key, members in list(_active_mutes.items()):
            for member_id, unmute_ts in list(members.items()):
                delay = int(unmute_ts) - now
                if delay <= 0:
                    guild = bot.get_guild(int(guild_key))
                    if guild:
                        member = guild.get_member(int(member_id))
                        if member:
                            role = discord.utils.get(guild.roles, name="Muted")
                            if role and role in member.roles:
                                try:
                                    await member.remove_roles(role, reason="Unmute pós-restart (tempo expirado)")
                                    await log(guild, f"🔊 {member.mention} ({member.id}) foi desmutado (tempo expirado durante reinício).")
                                except Exception:
                                    pass
                    try:
                        del _active_mutes[guild_key][member_id]
                    except Exception:
                        pass
                else:
                    asyncio.create_task(_schedule_unmute(int(guild_key), int(member_id), delay))
        _save_mutes()
    except Exception as e:
        print("[MUTE RELOAD WARN]", e)
        traceback.print_exc()

# Carrega token do ambiente e inicia
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN.")
