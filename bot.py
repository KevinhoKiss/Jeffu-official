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
except Exception:
    MongoClient = None

# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
LOG_CHANNEL_ID = 1466542559730991164

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DONO_ID = 766709835701682208

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
_active_mutes = {}

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
        except Exception as e:
            print("[UNMUTE ERROR] Falha ao remover role Muted do membro:", e)
            traceback.print_exc()
            return False

        try:
            guild_key = str(guild.id)
            if guild_key in _active_mutes and str(member.id) in _active_mutes[guild_key]:
                del _active_mutes[guild_key][str(member.id)]
                if not _active_mutes[guild_key]:
                    del _active_mutes[guild_key]
                _save_mutes()
        except Exception as e:
            print("[UNMUTE WARN] Falha ao atualizar _active_mutes:", e)
            traceback.print_exc()

        try:
            await log(guild, f"🔊 {member.mention} ({member.id}) foi desmutado.")
        except Exception:
            pass

        return True

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
_reaction_rules = {}

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
        tmp = REACTIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_reaction_rules, f, indent=2, ensure_ascii=False)
        os.replace(tmp, REACTIONS_FILE)
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
                        "channel_id": 0,
                        "keyword": kw,
                        "is_regex": False,
                        "emojis": rule["emojis"]
                    })
        _reaction_rules[gk]["by_keyword"] = existing
    _save_reaction_rules()

# ==================== HELPERS ====================
def _mentions_jeffu(message: discord.Message) -> bool:
    """
    Retorna True se a mensagem mencionar 'jeffu' por substring
    ou se alguma das menções tiver name/display_name contendo 'jeffu'.
    """
    try:
        content = (message.content or "").lower()
        if "jeffu" in content:
            return True
        for m in getattr(message, "mentions", []):
            try:
                # display_name existe em Member; em User usamos name
                name = ""
                if hasattr(m, "display_name"):
                    name = (m.display_name or m.name or "").lower()
                else:
                    name = (getattr(m, "name", "") or "").lower()
                if "jeffu" in name:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

# ==================== EVENTOS E MODERAÇÃO (ÚNICO on_message) ====================
INVITE_REGEX = re.compile(r"(discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[A-Za-z0-9\-]+)", re.IGNORECASE)

BAD_WORDS_PATTERN = re.compile(
    r"\b(?:cala boca|calaboca|clbc|cbc|fica quieto|quieto)\b(?:.*(?:jeffu|<@!?\d+>))?",
    re.IGNORECASE
)

@bot.event
async def on_message(message: discord.Message):
    try:
        # ignora bots e webhooks
        if message.author.bot:
            return
        if getattr(message, "webhook_id", None) is not None:
            return
        if not message.content and message.embeds:
            return

        # aplicar regras por palavra (prioritário)
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

        texto = (message.content or "").strip()
        lower = texto.lower()

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

        # ===== GREETINGS: responder a qualquer mensagem que contenha saudação =====
        # agora responde a "bom dia", "boa tarde", "boa noite" mesmo sem menção ao bot
        saudacoes = {
            "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
            "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
            "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>"
        }
        # procura a primeira saudação presente no texto
        for chave, resposta in saudacoes.items():
            if chave in lower:
                try:
                    await message.reply(resposta, mention_author=False)
                except Exception as e:
                    print("[GREET WARN] Falha ao enviar saudação:", e)
                    traceback.print_exc()
                # responde apenas uma saudação por mensagem
                return

        # REGRAS AUTOMÁTICAS (respostas rápidas)
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

        # Interações dirigidas ao bot (DM ou menção)
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = bot.user in message.mentions if bot.user else False
        should_respond_personal = is_dm or mentions_bot

        if should_respond_personal:
            # agradecimentos e "te amo" agora exigem menção/substring 'jeffu'
            if re.search(r"(agradecido|obg|obrigado)", texto, re.IGNORECASE) and _mentions_jeffu(message):
                await message.reply("Não há de que <:amem:1466774899686117426>", mention_author=False)
                return

            if re.search(r"(te amo|amo vc|amo você|amo voce)", texto, re.IGNORECASE) and _mentions_jeffu(message):
                await message.reply("💙 Obrigado... <:shame:1466777359586693376>", mention_author=False)
                return

            if BAD_WORDS_PATTERN.search(texto):
                try:
                    await message.reply("<:looking:1466793665463844894> Me deixa trabalhar, poxa...", mention_author=False)
                except Exception as e:
                    print("[REPLY WARN] Falha ao responder a mensagem:", e)
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
