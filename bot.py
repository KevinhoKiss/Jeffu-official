
import discord
from discord import app_commands
from discord.ext import commands
import os, re, json, time, traceback, unicodedata, asyncio
from pathlib import Path
from collections import defaultdict, deque
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
except Exception:
    psycopg2 = None
    ThreadedConnectionPool = None


# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
LOG_CHANNEL_ID = 1495200091974271209
BAN_LOG_CHANNEL_ID = 1466542559730991164
DONO_ID = 766709835701682208
ARQUIVO = 'familias.json'
AUTORIZADOS_FILE = 'autorizados.json'
REACTIONS_FILE = 'reactions_rules.json'
BAN_AO_DETECTAR_CONVITE = True
AVISAR_POR_DM_ANTES_DO_BAN = True
MENSAGEM_DM_BAN = (
    '⚠️ Você foi banido automaticamente por enviar convite/propaganda no servidor.\n'
    'Se acreditar que foi um engano, entre em contato com a staff.'
)
COOLDOWN_INTENT_SECONDS = 0
COOLDOWN_USER_INTENT_SECONDS = 0
CONTEXT_MAX_AGE_SECONDS = 180
INVITE_REGEX = re.compile(r'(discord(?:\.gg|\.com/invite|app\.com/invite)/[A-Za-z0-9\-]+)', re.IGNORECASE)
BAD_WORDS_PATTERN = re.compile(r'\b(?:cala boca|calaboca|clbc|cbc|fica quieto|quieto|se aquieta)\b(?:.*(?:jeffu|<@!?\d+>))?', re.IGNORECASE)

# Persistência em PostgreSQL (Railway) para usuários
POSTGRES_DSN = (
    os.getenv('DATABASE_URL')
    or os.getenv('POSTGRES_URL')
    or os.getenv('POSTGRESQL_URL')
)
POSTGRES_HOST = os.getenv('PGHOST') or os.getenv('POSTGRES_HOST')
POSTGRES_PORT = int(os.getenv('PGPORT') or os.getenv('POSTGRES_PORT') or 5432)
POSTGRES_DB = os.getenv('PGDATABASE') or os.getenv('POSTGRES_DB')
POSTGRES_USER = os.getenv('PGUSER') or os.getenv('POSTGRES_USER')
POSTGRES_PASSWORD = os.getenv('PGPASSWORD') or os.getenv('POSTGRES_PASSWORD')
POSTGRES_SSLMODE = os.getenv('PGSSLMODE') or ('require' if POSTGRES_DSN or POSTGRES_HOST else 'prefer')
POSTGRES_CONNECT_TIMEOUT = int(os.getenv('PGCONNECT_TIMEOUT') or 10)
POSTGRES_POOL_MIN = int(os.getenv('PG_POOL_MIN') or 1)
POSTGRES_POOL_MAX = int(os.getenv('PG_POOL_MAX') or 5)
POSTGRES_USERS_TABLE = os.getenv('PG_USERS_TABLE', 'discord_users').strip()
POSTGRES_SAVE_BOTS = str(os.getenv('PG_SAVE_BOTS', 'false')).lower() in ('1', 'true', 'yes', 'sim', 'on')

_postgres_pool = None


def _postgres_has_config() -> bool:
    return bool(POSTGRES_DSN or (POSTGRES_HOST and POSTGRES_DB and POSTGRES_USER and POSTGRES_PASSWORD))


def _postgres_safe_table_name(name: str) -> str:
    name = (name or 'discord_users').strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        return 'discord_users'
    return name


def _postgres_connect_kwargs() -> dict:
    if POSTGRES_DSN:
        dsn = POSTGRES_DSN
        if 'sslmode=' not in dsn:
            separator = '&' if '?' in dsn else '?'
            dsn = f'{dsn}{separator}sslmode={POSTGRES_SSLMODE}'
        return {
            'dsn': dsn,
            'connect_timeout': POSTGRES_CONNECT_TIMEOUT,
            'application_name': 'scanbot',
        }
    return {
        'host': POSTGRES_HOST,
        'port': POSTGRES_PORT,
        'dbname': POSTGRES_DB,
        'user': POSTGRES_USER,
        'password': POSTGRES_PASSWORD,
        'sslmode': POSTGRES_SSLMODE,
        'connect_timeout': POSTGRES_CONNECT_TIMEOUT,
        'application_name': 'scanbot',
    }


def postgres_enabled() -> bool:
    return psycopg2 is not None and _postgres_has_config()


def _postgres_get_pool():
    global _postgres_pool
    if _postgres_pool is not None:
        return _postgres_pool
    if not postgres_enabled():
        return None
    try:
        kwargs = _postgres_connect_kwargs()
        _postgres_pool = ThreadedConnectionPool(POSTGRES_POOL_MIN, POSTGRES_POOL_MAX, **kwargs)
        return _postgres_pool
    except Exception as e:
        print(f'[POSTGRES] Falha ao criar pool: {e}')
        traceback.print_exc()
        return None


def _postgres_users_table() -> str:
    return _postgres_safe_table_name(POSTGRES_USERS_TABLE)


def _postgres_init_sync() -> bool:
    if not postgres_enabled():
        if psycopg2 is None:
            print('[POSTGRES] psycopg2 não está instalado. Adicione psycopg2-binary ao projeto.')
        else:
            print('[POSTGRES] Banco não configurado. Defina DATABASE_URL ou variáveis PG* do Railway.')
        return False
    pool = _postgres_get_pool()
    if pool is None:
        return False
    conn = None
    table = _postgres_users_table()
    try:
        conn = pool.getconn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'''                CREATE TABLE IF NOT EXISTS {table} (
                    guild_id BIGINT NOT NULL,
                    guild_name TEXT,
                    user_id BIGINT NOT NULL,
                    username TEXT NOT NULL,
                    global_name TEXT,
                    display_name TEXT,
                    discriminator TEXT,
                    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
                    avatar_url TEXT,
                    joined_at TIMESTAMPTZ NULL,
                    created_discord_at TIMESTAMPTZ NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                )
            ''')
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_user_id ON {table} (user_id)')
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_last_seen_at ON {table} (last_seen_at DESC)')
        print(f'[POSTGRES] Tabela `{table}` pronta para uso.')
        return True
    except Exception as e:
        print(f'[POSTGRES] Falha ao criar tabela de usuários: {e}')
        traceback.print_exc()
        return False
    finally:
        if conn is not None and pool is not None:
            pool.putconn(conn)


async def postgres_init() -> bool:
    return await asyncio.to_thread(_postgres_init_sync)


def _member_payload(member: discord.abc.User, guild: discord.Guild | None = None) -> dict | None:
    if member is None:
        return None
    guild = guild or getattr(member, 'guild', None)
    if guild is None:
        return None
    is_bot = bool(getattr(member, 'bot', False))
    if is_bot and not POSTGRES_SAVE_BOTS:
        return None
    avatar_url = None
    try:
        avatar_url = str(member.display_avatar.url)
    except Exception:
        avatar_url = None
    joined_at = getattr(member, 'joined_at', None)
    created_at = getattr(member, 'created_at', None)
    return {
        'guild_id': int(guild.id),
        'guild_name': getattr(guild, 'name', None),
        'user_id': int(member.id),
        'username': getattr(member, 'name', None) or str(member),
        'global_name': getattr(member, 'global_name', None),
        'display_name': getattr(member, 'display_name', None) or getattr(member, 'name', None) or str(member),
        'discriminator': getattr(member, 'discriminator', None),
        'is_bot': is_bot,
        'avatar_url': avatar_url,
        'joined_at': joined_at,
        'created_discord_at': created_at,
    }


def _postgres_upsert_users_sync(payloads: list[dict]) -> int:
    if not payloads or not postgres_enabled():
        return 0
    pool = _postgres_get_pool()
    if pool is None:
        return 0
    conn = None
    table = _postgres_users_table()
    sql = f'''        INSERT INTO {table} (
            guild_id, guild_name, user_id, username, global_name, display_name,
            discriminator, is_bot, avatar_url, joined_at, created_discord_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            guild_name = EXCLUDED.guild_name,
            username = EXCLUDED.username,
            global_name = EXCLUDED.global_name,
            display_name = EXCLUDED.display_name,
            discriminator = EXCLUDED.discriminator,
            is_bot = EXCLUDED.is_bot,
            avatar_url = EXCLUDED.avatar_url,
            joined_at = COALESCE(EXCLUDED.joined_at, {table}.joined_at),
            created_discord_at = COALESCE(EXCLUDED.created_discord_at, {table}.created_discord_at),
            last_seen_at = NOW()
    '''
    rows = [
        (
            item['guild_id'],
            item['guild_name'],
            item['user_id'],
            item['username'],
            item['global_name'],
            item['display_name'],
            item['discriminator'],
            item['is_bot'],
            item['avatar_url'],
            item['joined_at'],
            item['created_discord_at'],
        )
        for item in payloads if item
    ]
    if not rows:
        return 0
    try:
        conn = pool.getconn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        return len(rows)
    except Exception as e:
        print(f'[POSTGRES] Falha ao salvar usuários: {e}')
        traceback.print_exc()
        return 0
    finally:
        if conn is not None and pool is not None:
            pool.putconn(conn)


async def postgres_save_member(member: discord.abc.User, guild: discord.Guild | None = None) -> bool:
    payload = _member_payload(member, guild)
    if payload is None:
        return False
    saved = await asyncio.to_thread(_postgres_upsert_users_sync, [payload])
    return saved > 0


async def postgres_sync_guild_members(guild: discord.Guild) -> int:
    payloads = [_member_payload(member, guild) for member in getattr(guild, 'members', [])]
    payloads = [item for item in payloads if item]
    if not payloads:
        return 0
    return await asyncio.to_thread(_postgres_upsert_users_sync, payloads)


async def postgres_sync_all_guilds() -> int:
    total = 0
    for guild in bot.guilds:
        try:
            saved = await postgres_sync_guild_members(guild)
            total += saved
            print(f'[POSTGRES] {saved} usuário(s) sincronizado(s) em {guild.name}.')
        except Exception as e:
            print(f'[POSTGRES] Falha ao sincronizar membros de {guild.name}: {e}')
            traceback.print_exc()
    return total


# intents/bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ==================== LOG VISUAL (GERAL) ====================
LOG_IMAGE_BG = (6, 6, 8)
LOG_IMAGE_BG_TOP = (14, 14, 18)
LOG_IMAGE_CARD = (22, 22, 28)
LOG_IMAGE_CARD_2 = (30, 30, 38)
LOG_IMAGE_CARD_BORDER = (58, 58, 70)
LOG_IMAGE_TEXT = (242, 244, 248)
LOG_IMAGE_MUTED = (168, 172, 182)
LOG_IMAGE_ACCENT = (124, 92, 255)
LOG_IMAGE_PILL = (37, 37, 48)
LOG_IMAGE_SHADOW = (0, 0, 0, 135)
LOG_IMAGE_LINE = (74, 74, 92)
LOG_IMAGE_BLUE = (51, 118, 255)
CHARACTER_ASSET_FILES = ('1ONXu.jpg', 'decor_character.png')
_character_asset_cache = None

LOG_BADGE_FONT_SIZE = 16
LOG_TITLE_FONT_SIZE = 30
LOG_SUBTITLE_FONT_SIZE = 26
LOG_BODY_FONT_SIZE = 24
LOG_LABEL_FONT_SIZE = 20
LOG_SMALL_FONT_SIZE = 13
LOG_LINE_HEIGHT = 34
LOG_LABEL_WIDTH = 220

# ==================== HELPERS ====================
def _agora_brasil_str(fmt='%d/%m/%Y %H:%M'):
    return datetime.now(ZoneInfo('America/Sao_Paulo')).strftime(fmt)


def _font_paths(bold=False):
    base = Path(__file__).resolve().parent
    return [
        base / 'fonts' / ('NotoSans-Bold.ttf' if bold else 'NotoSans-Regular.ttf'),
        base / ('NotoSans-Bold.ttf' if bold else 'NotoSans-Regular.ttf'),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
    ]


def _get_font(size, bold=False):
    for path in _font_paths(bold):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass
    return ImageFont.load_default()


def _text_width(draw, text, font):
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]


def _text_size(draw, text, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        return _text_width(draw, text, font), getattr(font, 'size', 20)


def _fit_font_for_width(draw, text, max_width, start_size, min_size=18, bold=False):
    for size in range(start_size, min_size - 1, -2):
        font = _get_font(size, bold)
        if _text_width(draw, text, font) <= max_width:
            return font
    return _get_font(min_size, bold)


def _wrap_text(draw, text, font, max_width):
    text = (text or '').strip()
    if not text:
        return ['']
    lines = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            lines.append('')
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f'{current} {word}'
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _crop_circle(img, size=112):
    img = img.convert('RGB').resize((size, size), Image.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    out.putalpha(mask)
    return out


def _remove_light_background(img):
    img = img.convert('RGBA')
    w, h = img.size
    px = img.load()

    def is_light_bg(x, y):
        r, g, b, a = px[x, y]
        bright = (int(r) + int(g) + int(b)) / 3
        spread = max(r, g, b) - min(r, g, b)
        return bright >= 180 and spread <= 35

    bg = [[False for _ in range(w)] for _ in range(h)]
    q = deque()
    for x in range(w):
        if is_light_bg(x, 0):
            q.append((x, 0))
            bg[0][x] = True
        if is_light_bg(x, h - 1) and not bg[h - 1][x]:
            q.append((x, h - 1))
            bg[h - 1][x] = True
    for y in range(h):
        if is_light_bg(0, y) and not bg[y][0]:
            q.append((0, y))
            bg[y][0] = True
        if is_light_bg(w - 1, y) and not bg[y][w - 1]:
            q.append((w - 1, y))
            bg[y][w - 1] = True
    while q:
        x, y = q.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and not bg[ny][nx] and is_light_bg(nx, ny):
                bg[ny][nx] = True
                q.append((nx, ny))
    alpha = Image.new('L', (w, h), 255)
    alpha_px = alpha.load()
    for y in range(h):
        for x in range(w):
            if bg[y][x]:
                alpha_px[x, y] = 0
    alpha = alpha.filter(ImageFilter.GaussianBlur(1.2))
    img.putalpha(alpha)
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _load_bottom_character():
    global _character_asset_cache
    if _character_asset_cache is not None:
        return _character_asset_cache.copy()
    chosen = None
    for filename in CHARACTER_ASSET_FILES:
        if os.path.exists(filename):
            chosen = filename
            break
    if not chosen:
        return None
    try:
        img = Image.open(chosen)
        if chosen.lower().endswith('.png'):
            img = img.convert('RGBA')
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
        else:
            img = _remove_light_background(img)
        _character_asset_cache = img.convert('RGBA')
        return _character_asset_cache.copy()
    except Exception:
        traceback.print_exc()
        return None


async def _avatar_bytes(member):
    if not member:
        return None
    try:
        return await member.display_avatar.with_size(256).read()
    except Exception:
        return None


async def _guild_icon_bytes(guild):
    if not guild or not getattr(guild, 'icon', None):
        return None
    try:
        return await guild.icon.with_size(128).read()
    except Exception:
        return None


def _initials_from_member(member):
    if not member:
        return '?'
    name = getattr(member, 'display_name', None) or getattr(member, 'name', None) or str(member)
    parts = [p for p in str(name).split() if p]
    return (parts[0][0] + parts[1][0]).upper() if len(parts) >= 2 else (str(name)[:2].upper() if name else '?')


def _draw_centered_pill(draw, cx, y, text, font, fill, text_fill, h_padding=24, v_padding=9, radius=22, max_width=None):
    tw, th = _text_size(draw, text, font)
    pill_w = tw + h_padding * 2
    pill_h = th + v_padding * 2
    if max_width and pill_w > max_width:
        pill_w = max_width
    x1 = int(cx - pill_w / 2)
    x2 = int(cx + pill_w / 2)
    draw.rounded_rectangle((x1, y, x2, y + pill_h), radius=radius, fill=fill)
    tx = int(cx - tw / 2)
    ty = y + int((pill_h - th) / 2) - 1
    draw.text((tx, ty), text, font=font, fill=text_fill)


def _accent_for_title(title, accent=None):
    title = (title or '').lower()
    if accent:
        return accent
    if 'ban' in title or 'convite' in title or 'bloqueado' in title:
        return (190, 72, 72)
    if 'resposta automática' in title:
        return (88, 154, 255)
    return LOG_IMAGE_ACCENT


def _draw_vertical_gradient(canvas, top_color, bottom_color):
    w, h = canvas.size
    base = Image.new('RGB', (w, h), top_color)
    px = base.load()
    tr, tg, tb = top_color
    br, bg, bb = bottom_color
    for y in range(h):
        t = y / max(1, h - 1)
        c = (
            int(tr + (br - tr) * t),
            int(tg + (bg - tg) * t),
            int(tb + (bb - tb) * t),
        )
        for x in range(w):
            px[x, y] = c
    return base


def _draw_background(canvas):
    w, h = canvas.size
    bg = _draw_vertical_gradient(canvas, LOG_IMAGE_BG_TOP, LOG_IMAGE_BG)
    canvas.paste(bg, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((-180, -120, 360, 280), fill=(12, 12, 18))
    draw.ellipse((w - 380, -150, w + 60, 250), fill=(10, 10, 16))
    draw.ellipse((w - 260, h - 210, w + 80, h + 30), fill=(12, 12, 18))
    draw.rectangle((0, h - 78, w, h), fill=(12, 12, 16))
    draw.rectangle((0, h - 18, w, h), fill=(44, 28, 139))
    draw.line((0, h - 78, w, h - 78), fill=LOG_IMAGE_BLUE, width=2)


def _paste_glow(canvas, box, color, blur=24, alpha=115, radius=28):
    glow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(box, radius=radius, fill=(*color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(canvas, glow)


def _paste_bottom_character_overlay(canvas):
    character = _load_bottom_character()
    if character is None:
        return canvas
    if character.mode != 'RGBA':
        character = character.convert('RGBA')
    w, h = canvas.size
    target_h = 95
    scale = target_h / max(1, character.height)
    target_w = max(1, int(character.width * scale))
    character = character.resize((target_w, target_h), Image.LANCZOS)
    x = -8
    y = h - 78 - target_h + 18
    shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((x + 6, h - 58, x + min(target_w, 90), h - 36), fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    canvas_rgba = canvas.convert('RGBA')
    canvas_rgba = Image.alpha_composite(canvas_rgba, shadow)
    canvas_rgba.paste(character, (x, y), character)
    return canvas_rgba

# ==================== CONTEXTO / AUTO-REPLY ====================
def _mentions_jeffu(message):
    try:
        content = (message.content or '').lower()
        if 'jeffu' in content:
            return True
        for m in getattr(message, 'mentions', []):
            name = (getattr(m, 'display_name', None) or getattr(m, 'name', '') or '').lower()
            if 'jeffu' in name:
                return True
    except Exception:
        pass
    return False


def normalize_text(text):
    text = (text or '').lower().strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'<@!?\d+>|<#\d+>|<a?:\w+:\d+>', ' ', text)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def contains_term(text, term):
    text = normalize_text(text)
    term = normalize_text(term)
    if not term:
        return False
    return re.search(rf'(?<!\w){re.escape(term)}(?!\w)', text) is not None


def find_matches(text, terms):
    return [term for term in terms if contains_term(text, term)]


def short_greeting_type(text):
    text = normalize_text(text)
    greetings = {
        'bom dia': ['bom dia', 'bom dia gente', 'bom dia pessoal'],
        'boa tarde': ['boa tarde', 'boa tarde gente', 'boa tarde pessoal'],
        'boa noite': ['boa noite', 'boa noite gente', 'boa noite pessoal'],
    }
    for label, variants in greetings.items():
        if text in variants:
            return label
    return None


CHANNEL_CONTEXT = defaultdict(lambda: deque(maxlen=12))
USER_CHANNEL_CONTEXT = defaultdict(lambda: deque(maxlen=8))
LAST_INTENT_REPLY_TS = {}
LAST_USER_INTENT_REPLY_TS = {}


def _channel_key(message):
    guild_id = message.guild.id if message.guild else 0
    return (guild_id, message.channel.id)


def _user_channel_key(message):
    guild_id = message.guild.id if message.guild else 0
    return (guild_id, message.channel.id, message.author.id)


def remember_context(message, intent, score, matched_groups, reply):
    record = {
        'intent': intent,
        'score': score,
        'matched_groups': matched_groups,
        'reply': reply,
        'ts': time.time(),
        'user_id': message.author.id,
        'channel_id': message.channel.id,
    }
    CHANNEL_CONTEXT[_channel_key(message)].append(record)
    USER_CHANNEL_CONTEXT[_user_channel_key(message)].append(record)


def _recent_context(records, max_age=CONTEXT_MAX_AGE_SECONDS):
    now = time.time()
    return [r for r in records if now - r.get('ts', 0) <= max_age]


def get_recent_intents(message):
    return {
        'channel': _recent_context(CHANNEL_CONTEXT[_channel_key(message)]),
        'user_channel': _recent_context(USER_CHANNEL_CONTEXT[_user_channel_key(message)]),
    }


def cooldown_status(message, intent):
    now = time.time()
    channel_key = (_channel_key(message), intent)
    user_key = (_user_channel_key(message), intent)
    channel_wait = max(0, COOLDOWN_INTENT_SECONDS - int(now - LAST_INTENT_REPLY_TS.get(channel_key, 0)))
    user_wait = max(0, COOLDOWN_USER_INTENT_SECONDS - int(now - LAST_USER_INTENT_REPLY_TS.get(user_key, 0)))
    return {
        'blocked': channel_wait > 0 or user_wait > 0,
        'channel_wait': channel_wait,
        'user_wait': user_wait,
    }


def mark_cooldown(message, intent):
    now = time.time()
    LAST_INTENT_REPLY_TS[(_channel_key(message), intent)] = now
    LAST_USER_INTENT_REPLY_TS[(_user_channel_key(message), intent)] = now


INTENT_RULES = {
    'site_status': {
        'reply': '🌐 Veja em <#1409296003034644542>',
        'threshold': 7,
        'groups': [
            {'name': 'entidade', 'terms': ['site', 'sistema', 'app', 'aplicativo', 'plataforma'], 'weight': 3, 'required': True, 'cap': 1},
            {'name': 'problema', 'terms': ['caiu', 'fora do ar', 'offline', 'nao funciona', 'nao abre', 'saiu do ar', 'instavel', 'lento', 'travando', 'bugado', 'carregando', 'erro'], 'weight': 4, 'required': True, 'cap': 2},
        ],
        'followup_terms': ['continua', 'ainda', 'voltou', 'normalizou', 'agora', 'ruim', 'instavel', 'lento', 'fora', 'piorou', 'melhorou'],
        'negatives': ['site bonito', 'site lindo', 'gostei do site', 'nome do site'],
        'context_boost_user': 5,
        'context_boost_channel': 3,
    },
    'support': {
        'reply': '🔐 Para suporte, vá em <#1479642544429076500>',
        'threshold': 6,
        'groups': [
            {'name': 'assunto', 'terms': ['login', 'senha', 'acesso', 'conta', 'ticket', 'suporte', 'entrar', 'logar', 'acessar'], 'weight': 3, 'required': True, 'cap': 2},
            {'name': 'problema', 'terms': ['nao consigo', 'não consigo', 'esqueci', 'erro', 'ajuda', 'recuperar', 'sem acesso', 'problema', 'abrir', 'como', 'falhou', 'travou', 'nao entra', 'não entra'], 'weight': 3, 'required': True, 'cap': 2},
        ],
        'followup_terms': ['continua', 'ainda', 'deu ruim', 'nao foi', 'não foi', 'nao resolveu', 'não resolveu', 'nao deu', 'não deu', 'continua igual'],
        'negatives': ['minha senha e forte', 'gostei da senha', 'troquei minha senha e pronto'],
        'context_boost_user': 5,
        'context_boost_channel': 2,
    },
    'obra_suggestion': {
        'reply': '📚 Sugestões de obras é em <#1466087941506990171>',
        'threshold': 6,
        'groups': [
            {'name': 'midia', 'terms': ['obra', 'obras', 'manga', 'manhwa', 'novel', 'titulo', 'titulos'], 'weight': 2, 'required': True, 'cap': 2},
            {'name': 'intencao', 'terms': ['sugestao', 'sugestoes', 'indicar', 'indicacao', 'recomendar', 'recomendacao'], 'weight': 4, 'required': True, 'cap': 2},
        ],
        'followup_terms': ['onde sugiro', 'onde mando', 'tem canal', 'posso indicar'],
        'negatives': ['obra boa', 'essa obra e ruim', 'terminei a obra'],
        'context_boost_user': 4,
        'context_boost_channel': 2,
    },
    'missing_chapters': {
        'reply': '<#1452799882149761144>',
        'threshold': 7,
        'groups': [
            {'name': 'assunto', 'terms': ['capitulo', 'capitulos'], 'weight': 3, 'required': True, 'cap': 2},
            {'name': 'problema', 'terms': ['faltando', 'faltam', 'sumiu', 'sumiram', 'nao tem', 'incompleto', 'cade', 'onde estao', 'faltou', 'nao veio'], 'weight': 4, 'required': True, 'cap': 2},
        ],
        'followup_terms': ['continua', 'ainda', 'sumiu', 'faltando', 'sem', 'nao veio', 'segue faltando'],
        'negatives': ['esse capitulo foi bom', 'li o capitulo', 'gostei do capitulo'],
        'context_boost_user': 5,
        'context_boost_channel': 3,
    },
}

GREETING_REPLIES = {
    'bom dia': 'Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?',
    'boa tarde': 'Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>',
    'boa noite': 'Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>',
}


def score_intent(normalized_text, intent_name, rule, context):
    score, matched_groups, missing_required = 0, {}, []
    for negative in rule.get('negatives', []):
        if contains_term(normalized_text, negative):
            return {
                'intent': intent_name,
                'score': -999,
                'matched_groups': {},
                'missing_required': [],
                'context_used': None,
                'negative_hit': negative,
            }
    for group in rule.get('groups', []):
        matches = find_matches(normalized_text, group.get('terms', []))
        if matches:
            capped_len = min(len(matches), int(group.get('cap', len(matches))))
            group_score = capped_len * int(group.get('weight', 1))
            score += group_score
            matched_groups[group['name']] = {'matches': matches, 'score': group_score}
        elif group.get('required'):
            missing_required.append(group['name'])

    context_used = None
    user_recent = context.get('user_channel', [])
    chan_recent = context.get('channel', [])
    user_same_intent = any(item.get('intent') == intent_name for item in user_recent)
    channel_same_intent = any(item.get('intent') == intent_name for item in chan_recent)
    followup_matches = find_matches(normalized_text, rule.get('followup_terms', []))
    if user_same_intent and followup_matches:
        boost = int(rule.get('context_boost_user', 0))
        score += boost
        matched_groups['contexto_usuario_canal'] = {'matches': followup_matches, 'score': boost}
        context_used = 'user_channel'
    elif channel_same_intent and followup_matches:
        boost = int(rule.get('context_boost_channel', 0))
        score += boost
        matched_groups['contexto_canal'] = {'matches': followup_matches, 'score': boost}
        context_used = 'channel'

    if missing_required and context_used is None:
        score -= 2 * len(missing_required)

    return {
        'intent': intent_name,
        'score': score,
        'matched_groups': matched_groups,
        'missing_required': missing_required,
        'context_used': context_used,
        'negative_hit': None,
    }


def detect_auto_reply(message):
    raw_text = message.content or ''
    text = normalize_text(raw_text)
    if not text:
        return None
    greeting = short_greeting_type(text)
    if greeting:
        return {
            'intent': 'greeting',
            'reply': GREETING_REPLIES[greeting],
            'score': 999,
            'matched_groups': {'greeting': {'matches': [greeting], 'score': 999}},
            'context_used': None,
            'threshold': 1,
            'negative_hit': None,
            'missing_required': [],
        }
    context = get_recent_intents(message)
    candidates = []
    for intent_name, rule in INTENT_RULES.items():
        scored = score_intent(text, intent_name, rule, context)
        scored['reply'] = rule['reply']
        scored['threshold'] = rule['threshold']
        candidates.append(scored)
    candidates = [c for c in candidates if c['score'] > -999]
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x['score'])
    return best if best['score'] >= best['threshold'] else None

# ==================== MODERAÇÃO ====================
def get_bot_member(guild):
    try:
        return guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    except Exception:
        return None


def channel_perm_snapshot(message):
    bot_member = get_bot_member(message.guild) if message.guild else None
    if not message.guild or not bot_member:
        return {
            'manage_messages': False,
            'manage_roles': False,
            'ban_members': False,
            'send_messages': False,
            'attach_files': False,
            'read_message_history': False,
        }
    perms = message.channel.permissions_for(bot_member)
    return {
        'manage_messages': perms.manage_messages,
        'manage_roles': bot_member.guild_permissions.manage_roles,
        'ban_members': bot_member.guild_permissions.ban_members,
        'send_messages': perms.send_messages,
        'attach_files': perms.attach_files,
        'read_message_history': perms.read_message_history,
    }


def build_missing_perms_reason(snapshot):
    missing = []
    if not snapshot.get('manage_messages'):
        missing.append('Gerenciar Mensagens')
    if not snapshot.get('manage_roles'):
        missing.append('Gerenciar Cargos')
    if not snapshot.get('ban_members'):
        missing.append('Banir Membros')
    if not snapshot.get('send_messages'):
        missing.append('Enviar Mensagens')
    if not snapshot.get('attach_files'):
        missing.append('Anexar Arquivos')
    return 'Permissões ausentes: ' + ', '.join(missing) if missing else 'sem permissões ausentes'


async def try_delete_message(message):
    if not message.guild:
        return False, 'mensagem fora de servidor'
    snapshot = channel_perm_snapshot(message)
    if not snapshot.get('manage_messages'):
        return False, 'sem permissão Gerenciar Mensagens'
    try:
        await message.delete()
        return True, 'mensagem removida'
    except discord.Forbidden:
        return False, 'discord retornou Missing Permissions ao apagar'
    except Exception as e:
        return False, f'falha ao apagar: {e}'


async def try_send_dm_warning(member, message_text, channel_name, reason):
    if not member:
        return False, 'membro inválido'
    if not AVISAR_POR_DM_ANTES_DO_BAN:
        return False, 'aviso por DM desativado'
    dm_content = f"{MENSAGEM_DM_BAN}\n\nCanal: {channel_name or 'desconhecido'}\nMotivo: {reason}\nMensagem: {message_text or 'sem mensagem'}"
    try:
        await member.send(dm_content)
        return True, 'aviso por DM enviado'
    except discord.Forbidden:
        return False, 'DM fechada ou bloqueada'
    except Exception as e:
        return False, f'falha ao enviar DM: {e}'


async def try_ban_member(guild, member, reason='Ban automático'):
    if not guild or not member:
        return False, 'guild ou membro inválido'
    bot_member = get_bot_member(guild)
    if not bot_member:
        return False, 'bot não encontrado no servidor'
    if not bot_member.guild_permissions.ban_members:
        return False, 'sem permissão Banir Membros'
    try:
        if member == guild.owner:
            return False, 'não posso banir o dono do servidor'
    except Exception:
        pass
    try:
        if bot_member.top_role <= member.top_role:
            return False, 'hierarquia insuficiente para banir'
    except Exception:
        pass
    try:
        await member.ban(reason=reason)
        return True, 'usuário banido'
    except discord.Forbidden:
        return False, 'discord retornou Missing Permissions ao banir'
    except Exception as e:
        return False, f'falha ao banir: {e}'


async def audit_permission_status(guild):
    bot_member = get_bot_member(guild)
    if not guild or not bot_member:
        return
    missing = []
    if not bot_member.guild_permissions.manage_messages:
        missing.append('Gerenciar Mensagens')
    if not bot_member.guild_permissions.ban_members:
        missing.append('Banir Membros')
    if missing:
        print('[PERMS WARN] Bot sem permissões globais importantes:', ', '.join(missing))

# ==================== REAÇÕES ====================
_reaction_rules = {}


def _load_reaction_rules():
    global _reaction_rules
    try:
        if os.path.exists(REACTIONS_FILE):
            with open(REACTIONS_FILE, 'r', encoding='utf-8') as f:
                _reaction_rules = json.load(f)
        else:
            _reaction_rules = {}
    except Exception:
        _reaction_rules = {}


def _save_reaction_rules():
    try:
        with open(REACTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_reaction_rules, f, indent=2, ensure_ascii=False)
    except Exception:
        traceback.print_exc()


def _ensure_default_rules_for_all_guilds():
    default_rules = [
        {'keywords': ['bis', 'bisdov', 'bisdov3', 'chefe'], 'emojis': ['<:FBI:1466776866122629252>']},
        {'keywords': ['theus', 'matheus', 'god', 'matheuz', 'matheuss', 'matheuzinho'], 'emojis': ['<:suspect:1466766825361641634>']},
        {'keywords': ['lipe', 'lipezinho', 'lipezito'], 'emojis': ['<:808757471270404098:1466605544143061193>']},
    ]
    for guild in bot.guilds:
        gk = str(guild.id)
        if gk not in _reaction_rules:
            _reaction_rules[gk] = {'by_keyword': []}
        existing = _reaction_rules[gk].get('by_keyword', [])
        for rule in default_rules:
            for kw in rule['keywords']:
                if not any(ex.get('keyword', '').lower() == kw.lower() for ex in existing):
                    existing.append({'channel_id': 0, 'keyword': kw, 'is_regex': False, 'emojis': rule['emojis']})
        _reaction_rules[gk]['by_keyword'] = existing
    _save_reaction_rules()


async def _try_add_reaction(message, emoji):
    try:
        await message.add_reaction(emoji)
        return True
    except Exception:
        try:
            m = re.match(r'<a?:\w+:(\d+)>', emoji)
            if m:
                await message.add_reaction(discord.PartialEmoji(name=None, id=int(m.group(1)), animated=False))
                return True
        except Exception:
            pass
    return False


async def _apply_keyword_reactions(message):
    if not message.guild or message.author.bot:
        return
    rules = _reaction_rules.get(str(message.guild.id), {}).get('by_keyword', [])
    text = (message.content or '').lower()
    for rule in rules:
        channel_id = int(rule.get('channel_id') or 0)
        if channel_id and message.channel.id != channel_id:
            continue
        keyword = (rule.get('keyword') or '').lower().strip()
        if not keyword:
            continue
        matched = False
        if rule.get('is_regex'):
            try:
                matched = re.search(keyword, message.content or '', re.IGNORECASE) is not None
            except Exception:
                matched = False
        else:
            matched = keyword in text
        if matched:
            for emoji in rule.get('emojis', []):
                await _try_add_reaction(message, emoji)
            break

# ==================== LOG GERAL ====================
async def _build_log_image(guild, member=None, title='Log', channel_name='', reason='', action='', message_text='', accent=None):
    width, height = 1000, 800
    accent = _accent_for_title(title, accent)
    dummy = Image.new('RGB', (width, height), LOG_IMAGE_BG)
    dummy_draw = ImageDraw.Draw(dummy)
    display_name = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
    hero_font = _fit_font_for_width(dummy_draw, title or 'Evento registrado', 700, LOG_TITLE_FONT_SIZE, min_size=20, bold=True)
    sub_font = _fit_font_for_width(dummy_draw, display_name[:44], 620, LOG_SUBTITLE_FONT_SIZE, min_size=16, bold=False)
    badge_font = _get_font(LOG_BADGE_FONT_SIZE, True)
    body_font = _get_font(LOG_BODY_FONT_SIZE, False)
    label_font = _get_font(LOG_LABEL_FONT_SIZE, True)
    small_font = _get_font(LOG_SMALL_FONT_SIZE, False)
    lines = [
        ('Nome', display_name),
        ('Chat', channel_name or 'sistema'),
        ('Motivo', reason or 'não informado'),
        ('Ação', action or 'não informada'),
        ('Mensagem', message_text or 'sem mensagem'),
    ]
    card_w = 860
    card_x = (width - card_w) // 2
    card_y = 70
    avatar_size = 150
    avatar_y = card_y + 18
    pill_top = avatar_y + avatar_size + 14
    sub_top = pill_top + 58
    details_x1, details_x2 = card_x + 26, card_x + card_w - 26
    body_max_w = details_x2 - details_x1 - 300
    rendered = []
    for label, value in lines:
        wrapped = _wrap_text(dummy_draw, value, body_font, body_max_w) or ['']
        rendered.append((label, wrapped[:3 if label == 'Mensagem' else 2]))
    line_h = LOG_LINE_HEIGHT
    detail_rows = sum(len(v) for _, v in rendered)
    details_y1 = sub_top + 78
    content_h = 88 + detail_rows * line_h + 24
    details_y2 = details_y1 + content_h
    card_h = max(600, (details_y2 - card_y) + 48)
    canvas = Image.new('RGB', (width, height), LOG_IMAGE_BG)
    _draw_background(canvas)
    canvas = canvas.convert('RGBA')
    canvas = _paste_glow(canvas, (card_x - 8, card_y - 8, card_x + card_w + 8, card_y + card_h + 8), accent, blur=26, alpha=82, radius=40)
    shadow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(shadow)
    sh_draw.rounded_rectangle((card_x + 8, card_y + 14, card_x + card_w + 8, card_y + card_h + 14), radius=36, fill=LOG_IMAGE_SHADOW)
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    canvas = Image.alpha_composite(canvas, shadow)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), radius=36, fill=LOG_IMAGE_CARD, outline=LOG_IMAGE_CARD_BORDER, width=3)
    draw.rounded_rectangle((card_x + 8, card_y + 8, card_x + card_w - 8, card_y + card_h - 8), radius=30, outline=LOG_IMAGE_LINE, width=1)
    draw.ellipse((card_x + card_w - 106, card_y - 2, card_x + card_w - 26, card_y + 38), fill=accent)
    badge_text = (guild.name if guild else 'Discord')[:18]
    badge_w = max(180, min(300, int(len(badge_text) * 14) + 100))
    badge_x, badge_y = card_x + 18, card_y + 16
    draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + 60), radius=16, fill=LOG_IMAGE_PILL)
    icon_raw = await _guild_icon_bytes(guild)
    if icon_raw:
        icon_img = _crop_circle(Image.open(BytesIO(icon_raw)), 34)
        canvas.paste(icon_img, (badge_x + 10, badge_y + 13), icon_img)
    else:
        draw.ellipse((badge_x + 10, badge_y + 13, badge_x + 44, badge_y + 47), fill=(88, 81, 148))
    draw.text((badge_x + 54, badge_y + 6), 'Discord', font=small_font, fill=LOG_IMAGE_MUTED)
    draw.text((badge_x + 54, badge_y + 26), badge_text, font=badge_font, fill=LOG_IMAGE_TEXT)
    avatar_cx = card_x + card_w // 2
    avatar_ring_box = (avatar_cx - avatar_size // 2 - 10, avatar_y - 10, avatar_cx + avatar_size // 2 + 10, avatar_y + avatar_size + 10)
    canvas = _paste_glow(canvas, avatar_ring_box, accent, blur=16, alpha=70, radius=999)
    draw = ImageDraw.Draw(canvas)
    avatar_raw = await _avatar_bytes(member)
    if avatar_raw:
        avatar_img = _crop_circle(Image.open(BytesIO(avatar_raw)), avatar_size)
    else:
        avatar_img = Image.new('RGBA', (avatar_size, avatar_size), (0, 0, 0, 0))
        av_draw = ImageDraw.Draw(avatar_img)
        av_draw.ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=(40, 36, 83), outline=(16, 14, 35), width=4)
        initials = _initials_from_member(member)
        f = _get_font(40, True)
        tw, th = _text_size(av_draw, initials, f)
        av_draw.text(((avatar_size - tw) / 2, (avatar_size - th) / 2 - 2), initials, font=f, fill=(255, 255, 255))
    canvas.paste(avatar_img, (avatar_cx - avatar_size // 2, avatar_y), avatar_img)
    _draw_centered_pill(draw, avatar_cx, pill_top, title or 'Evento registrado', hero_font, LOG_IMAGE_PILL, LOG_IMAGE_TEXT, h_padding=30, v_padding=10, radius=22, max_width=card_w - 120)
    _draw_centered_pill(draw, avatar_cx, sub_top, display_name[:44], sub_font, (48, 48, 60), LOG_IMAGE_MUTED, h_padding=22, v_padding=8, radius=16, max_width=card_w - 150)
    draw.rounded_rectangle((details_x1, details_y1, details_x2, details_y2), radius=22, fill=LOG_IMAGE_CARD_2)
    draw.text((details_x1 + 20, details_y1 + 16), 'Resumo do evento', font=label_font, fill=LOG_IMAGE_MUTED)
    draw.line((details_x1 + 20, details_y1 + 58, details_x2 - 20, details_y1 + 58), fill=LOG_IMAGE_LINE, width=1)
    label_w = LOG_LABEL_WIDTH
    y = details_y1 + 78
    for label, parts in rendered:
        draw.text((details_x1 + 20, y), f'{label}:', font=label_font, fill=LOG_IMAGE_MUTED)
        inner_y = y
        for seg in parts:
            draw.text((details_x1 + 20 + label_w, inner_y), seg, font=body_font, fill=LOG_IMAGE_TEXT)
            inner_y += line_h
        y = inner_y + 6
    stamp = _agora_brasil_str('%d/%m/%Y %H:%M')
    draw.text((card_x + card_w - 165, card_y + card_h - 24), stamp, font=small_font, fill=LOG_IMAGE_MUTED)
    canvas = _paste_bottom_character_overlay(canvas)
    bio = BytesIO()
    canvas.convert('RGB').save(bio, format='PNG')
    bio.seek(0)
    return bio


async def log(guild, member=None, title='Log', channel_name='', reason='', action='', message_text='', accent=LOG_IMAGE_ACCENT, target_channel_id=None, fallback_channel_name='mod-logs'):
    try:
        canal = None
        final_channel_id = target_channel_id or LOG_CHANNEL_ID
        if guild and final_channel_id:
            canal = guild.get_channel(final_channel_id)
        if not canal and guild and fallback_channel_name:
            canal = discord.utils.get(guild.text_channels, name=fallback_channel_name)
        if canal:
            perms = canal.permissions_for(guild.me or guild.get_member(bot.user.id)) if guild and bot.user else None
            try:
                if perms and perms.send_messages and perms.attach_files:
                    image_bytes = await _build_log_image(guild, member=member, title=title, channel_name=channel_name, reason=reason, action=action, message_text=message_text, accent=accent)
                    await canal.send(file=discord.File(fp=image_bytes, filename='log.png'))
                else:
                    who = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
                    await canal.send(f"**{title}**\nNome: {who}\nChat: {channel_name}\nMotivo: {reason}\nAção: {action}\nMensagem: {message_text}")
            except Exception:
                traceback.print_exc()
        else:
            print('[LOG]', title, channel_name, reason, action, message_text)
    except Exception:
        traceback.print_exc()

# ==================== SISTEMA DE FAMÍLIAS (V4) ====================
FAMILIAS_DB_FILE = ARQUIVO
FAMILY_HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')


def _family_db_load() -> dict:
    if not os.path.exists(FAMILIAS_DB_FILE):
        return {}
    try:
        with open(FAMILIAS_DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return {}


def _family_db_save(data: dict):
    with open(FAMILIAS_DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _family_slug(text: str) -> str:
    text = unicodedata.normalize('NFKD', str(text).strip().lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9\s_-]', '', text)
    text = re.sub(r'[\s_-]+', '_', text).strip('_')
    return text or 'familia'


def _family_bucket(data: dict, guild_id: int) -> dict:
    key = str(guild_id)
    if key not in data:
        data[key] = {
            'families': {},
            'authorized_roles': [],
            'authorized_users': [DONO_ID],
            'pending_invites': {},
            'family_log_channel_id': None,
        }
    bucket = data[key]
    bucket.setdefault('families', {})
    bucket.setdefault('authorized_roles', [])
    bucket.setdefault('authorized_users', [DONO_ID])
    bucket.setdefault('pending_invites', {})
    bucket.setdefault('family_log_channel_id', None)
    if DONO_ID not in bucket['authorized_users']:
        bucket['authorized_users'].append(DONO_ID)
    return bucket


def _family_find(bucket: dict, family_name: str):
    slug = _family_slug(family_name)
    families = bucket.get('families', {})
    if slug in families:
        return slug, families[slug]
    family_name_l = family_name.strip().lower()
    for key, fam in families.items():
        if fam.get('name', '').strip().lower() == family_name_l:
            return key, fam
    return None, None


def _family_find_by_slug(bucket: dict, slug: str):
    fam = bucket.get('families', {}).get(slug)
    return (slug, fam) if fam else (None, None)


def _family_parse_color(value: str | None):
    if not value:
        return discord.Colour(0x7c5cff), '#7c5cff'
    value = value.strip()
    if not FAMILY_HEX_COLOR_RE.match(value):
        raise ValueError('Use uma cor no formato #RRGGBB, ex: #7c5cff')
    if not value.startswith('#'):
        value = '#' + value
    return discord.Colour(int(value[1:], 16)), value.lower()


def _family_is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == DONO_ID


def _family_is_authorized_role(interaction: discord.Interaction, bucket: dict) -> bool:
    authorized_roles = set(int(x) for x in bucket.get('authorized_roles', []))
    return any(getattr(role, 'id', 0) in authorized_roles for role in getattr(interaction.user, 'roles', []))


def _family_is_authorized_user(interaction: discord.Interaction, bucket: dict) -> bool:
    authorized_users = set(int(x) for x in bucket.get('authorized_users', []))
    return interaction.user.id in authorized_users or _family_is_owner(interaction)


def _family_has_admin(interaction: discord.Interaction, bucket: dict) -> bool:
    return _family_is_owner(interaction) or _family_is_authorized_user(interaction, bucket) or _family_is_authorized_role(interaction, bucket)


def _family_can_manage(interaction: discord.Interaction, bucket: dict, family: dict) -> bool:
    return _family_has_admin(interaction, bucket) or int(family.get('leader_id', 0)) == interaction.user.id


async def _family_remove_member_from_other_families(guild: discord.Guild, bucket: dict, member: discord.Member, keep_slug: str | None = None):
    changed = False
    for slug, fam in list(bucket.get('families', {}).items()):
        if keep_slug and slug == keep_slug:
            continue
        members = fam.get('members', [])
        if member.id in members:
            members.remove(member.id)
            fam['members'] = members
            role_id = fam.get('role_id')
            if role_id:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason='Mudança de família')
                    except Exception:
                        traceback.print_exc()
            changed = True
    return changed


async def _family_sync_role_to_members(guild: discord.Guild, family: dict):
    role_id = family.get('role_id')
    if not role_id:
        return
    role = guild.get_role(int(role_id))
    if role is None:
        return
    family_member_ids = set(int(x) for x in family.get('members', []))
    for member in guild.members:
        try:
            if member.id in family_member_ids and role not in member.roles:
                await member.add_roles(role, reason='Sincronização de família')
            elif member.id not in family_member_ids and role in member.roles:
                await member.remove_roles(role, reason='Sincronização de família')
        except Exception:
            traceback.print_exc()


def _family_build_embed(title: str, description: str, color: int = 0x7c5cff) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def _family_build_details_embed(guild: discord.Guild, family: dict, selected_member: discord.Member | None = None) -> discord.Embed:
    role = guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
    leader = guild.get_member(int(family.get('leader_id', 0))) if family.get('leader_id') else None
    members = []
    for member_id in family.get('members', []):
        member = guild.get_member(int(member_id))
        members.append(member.mention if member else f'`{member_id}`')
    desc = (
        f"**Nome:** {family.get('name')}\n"
        f"**Cargo:** {role.mention if role else '`não encontrado`'}\n"
        f"**Cor:** `{family.get('color', '#7c5cff')}`\n"
        f"**Líder:** {leader.mention if leader else '`não encontrado`'}\n"
        f"**Membros ({len(members)}):** {' '.join(members) if members else 'nenhum'}"
    )
    embed = _family_build_embed(f"Família • {family.get('name')}", desc, int(family.get('color', '#7c5cff')[1:], 16))
    if family.get('image_url'):
        embed.set_thumbnail(url=family['image_url'])
    if selected_member is not None:
        embed.set_footer(text=f'Membro selecionado no painel: {selected_member.display_name}')
    else:
        embed.set_footer(text='Selecione uma família e um membro no painel')
    return embed


# Log verde de família
FAMILY_LOG_BG_TOP = (16, 73, 40)
FAMILY_LOG_BG = (8, 44, 24)
FAMILY_LOG_CARD = (18, 60, 36)
FAMILY_LOG_BORDER = (94, 210, 140)
FAMILY_LOG_TEXT = (239, 252, 244)
FAMILY_LOG_MUTED = (178, 226, 194)
FAMILY_LOG_PILL = (28, 88, 52)
FAMILY_LOG_LINE = (60, 140, 90)
FAMILY_LOG_SHADOW = (0, 0, 0, 120)


def _family_draw_gradient(canvas):
    w, h = canvas.size
    px = canvas.load()
    tr, tg, tb = FAMILY_LOG_BG_TOP
    br, bg, bb = FAMILY_LOG_BG
    for y in range(h):
        t = y / max(1, h - 1)
        c = (int(tr + (br - tr) * t), int(tg + (bg - tg) * t), int(tb + (bb - tb) * t))
        for x in range(w):
            px[x, y] = c


def _family_wrap(draw, text, font, max_width):
    text = (text or '').strip()
    if not text:
        return ['']
    words = text.split()
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f'{current} {word}'
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


async def _build_family_log_image(guild, member=None, title='Log de Família', reason='', action='', message_text=''):
    width, height = 1000, 620
    canvas = Image.new('RGB', (width, height), FAMILY_LOG_BG)
    _family_draw_gradient(canvas)
    rgba = canvas.convert('RGBA')
    shadow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    sh = ImageDraw.Draw(shadow)
    sh.rounded_rectangle((78, 78, 922, 538), radius=34, fill=FAMILY_LOG_SHADOW)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    rgba = Image.alpha_composite(rgba, shadow)
    draw = ImageDraw.Draw(rgba)
    draw.rounded_rectangle((70, 70, 930, 530), radius=34, fill=FAMILY_LOG_CARD, outline=FAMILY_LOG_BORDER, width=3)
    draw.rounded_rectangle((86, 86, 914, 514), radius=26, outline=FAMILY_LOG_LINE, width=1)
    draw.rectangle((0, 560, width, height), fill=(10, 32, 18))
    draw.rectangle((0, 602, width, height), fill=(58, 166, 92))
    title_font = _get_font(34, True)
    label_font = _get_font(22, True)
    body_font = _get_font(26, False)
    small_font = _get_font(16, False)
    badge_font = _get_font(18, True)
    display_name = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
    guild_name = (guild.name if guild else 'Discord')[:26]
    draw.rounded_rectangle((96, 92, 350, 146), radius=16, fill=FAMILY_LOG_PILL)
    draw.text((112, 104), guild_name, font=badge_font, fill=FAMILY_LOG_TEXT)
    draw.text((112, 124), 'Family Logs', font=small_font, fill=FAMILY_LOG_MUTED)
    draw.text((110, 184), title, font=title_font, fill=FAMILY_LOG_TEXT)
    draw.text((110, 236), 'Nome:', font=label_font, fill=FAMILY_LOG_MUTED)
    draw.text((300, 236), display_name, font=body_font, fill=FAMILY_LOG_TEXT)
    reason_lines = _family_wrap(draw, reason or 'não informado', body_font, 560)
    action_lines = _family_wrap(draw, action or 'não informada', body_font, 560)
    msg_lines = _family_wrap(draw, message_text or 'sem mensagem', body_font, 560)
    y = 286
    draw.text((110, y), 'Motivo:', font=label_font, fill=FAMILY_LOG_MUTED)
    iy = y
    for line in reason_lines:
        draw.text((300, iy), line, font=body_font, fill=FAMILY_LOG_TEXT)
        iy += 34
    y = iy + 14
    draw.text((110, y), 'Ação:', font=label_font, fill=FAMILY_LOG_MUTED)
    iy = y
    for line in action_lines:
        draw.text((300, iy), line, font=body_font, fill=FAMILY_LOG_TEXT)
        iy += 34
    y = iy + 14
    draw.text((110, y), 'Mensagem:', font=label_font, fill=FAMILY_LOG_MUTED)
    iy = y
    for line in msg_lines:
        draw.text((300, iy), line, font=body_font, fill=FAMILY_LOG_TEXT)
        iy += 34
    stamp = _agora_brasil_str('%d/%m/%Y %H:%M')
    draw.text((760, 500), stamp, font=small_font, fill=FAMILY_LOG_MUTED)
    bio = BytesIO()
    rgba.convert('RGB').save(bio, format='PNG')
    bio.seek(0)
    return bio


async def _send_family_log(guild: discord.Guild, member=None, title='Log de Família', reason='', action='', message_text=''):
    try:
        data = _family_db_load()
        bucket = _family_bucket(data, guild.id)
        channel_id = bucket.get('family_log_channel_id')
        if not channel_id:
            return
        canal = guild.get_channel(int(channel_id))
        if canal is None:
            return
        perms = canal.permissions_for(guild.me or guild.get_member(bot.user.id)) if guild and bot.user else None
        if perms and perms.send_messages and perms.attach_files:
            image_bytes = await _build_family_log_image(guild, member=member, title=title, reason=reason, action=action, message_text=message_text)
            await canal.send(file=discord.File(fp=image_bytes, filename='family_log.png'))
    except Exception:
        traceback.print_exc()

# ==================== OPERAÇÕES DE FAMÍLIA ====================
async def _family_create(interaction: discord.Interaction, name: str, color_str: str):
    data = _family_db_load()
    bucket = _family_bucket(data, interaction.guild_id)
    if not _family_has_admin(interaction, bucket):
        raise PermissionError('Você não tem permissão para criar famílias.')
    slug = _family_slug(name)
    if slug in bucket['families']:
        raise ValueError('Já existe uma família com esse nome.')
    discord_color, hex_color = _family_parse_color(color_str)
    role = await interaction.guild.create_role(name=name, colour=discord_color, reason=f'Família criada por {interaction.user}')
    family = {
        'name': name,
        'role_id': role.id,
        'color': hex_color,
        'image_url': '',
        'members': [interaction.user.id],
        'leader_id': interaction.user.id,
        'created_by': interaction.user.id,
        'created_at': datetime.utcnow().isoformat(),
    }
    bucket['families'][slug] = family
    if isinstance(interaction.user, discord.Member):
        await _family_remove_member_from_other_families(interaction.guild, bucket, interaction.user, keep_slug=slug)
        try:
            await interaction.user.add_roles(role, reason='Criador da família')
        except Exception:
            traceback.print_exc()
    _family_db_save(data)
    await _send_family_log(interaction.guild, member=interaction.user, title='Família criada', reason=f'Família {name} criada', action=f'Cargo criado: {role.name}', message_text=f'Cor: {hex_color}')
    return family, role


class _FamilyInviteView(discord.ui.View):
    def __init__(self, invited_user_id: int, guild_id_i: int, family_slug: str, invited_by_id: int):
        super().__init__(timeout=600)
        self.invited_user_id = invited_user_id
        self.guild_id = guild_id_i
        self.family_slug = family_slug
        self.invited_by_id = invited_by_id

    @discord.ui.button(label='✅ Aceitar', style=discord.ButtonStyle.success)
    async def accept_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invited_user_id:
            return await interaction.response.send_message('Esse convite não é para você.', ephemeral=True)
        data = _family_db_load()
        bucket = _family_bucket(data, self.guild_id)
        invite = bucket.get('pending_invites', {}).get(str(self.invited_user_id))
        if not invite:
            return await interaction.response.send_message('Esse convite expirou ou já foi usado.', ephemeral=True)
        if invite.get('family_slug') != self.family_slug:
            return await interaction.response.send_message('Convite inválido.', ephemeral=True)
        slug, family = _family_find_by_slug(bucket, self.family_slug)
        if not family:
            bucket['pending_invites'].pop(str(self.invited_user_id), None)
            _family_db_save(data)
            return await interaction.response.send_message('A família não existe mais.', ephemeral=True)
        guild = bot.get_guild(self.guild_id)
        if guild is None:
            return await interaction.response.send_message('Servidor não encontrado.', ephemeral=True)
        member = guild.get_member(self.invited_user_id)
        if member is None:
            return await interaction.response.send_message('Membro não encontrado no servidor.', ephemeral=True)
        await _family_remove_member_from_other_families(guild, bucket, member, keep_slug=slug)
        if member.id not in family['members']:
            family['members'].append(member.id)
        role = guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role and role not in member.roles:
            await member.add_roles(role, reason=f'Convite aceito para a família {family.get("name")}')
        bucket['pending_invites'].pop(str(self.invited_user_id), None)
        _family_db_save(data)
        await _send_family_log(guild, member=member, title='Convite de família aceito', reason=f'Família: {family.get("name")}', action='Usuário aceitou o convite', message_text='Convite aceito via DM')
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f'✅ Você entrou na família **{family.get("name")}**.', view=self)

    @discord.ui.button(label='❌ Recusar', style=discord.ButtonStyle.danger)
    async def decline_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invited_user_id:
            return await interaction.response.send_message('Esse convite não é para você.', ephemeral=True)
        data = _family_db_load()
        bucket = _family_bucket(data, self.guild_id)
        bucket.get('pending_invites', {}).pop(str(self.invited_user_id), None)
        _family_db_save(data)
        guild = bot.get_guild(self.guild_id)
        if guild:
            await _send_family_log(guild, member=interaction.user, title='Convite de família recusado', reason='Convite recusado', action=f'Família slug: {self.family_slug}', message_text='Convite recusado via DM')
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content='❌ Você recusou o convite.', view=self)


async def _family_send_invite(interaction: discord.Interaction, selected_slug: str, member: discord.Member):
    data = _family_db_load()
    bucket = _family_bucket(data, interaction.guild_id)
    slug, family = _family_find_by_slug(bucket, selected_slug)
    if not family:
        raise ValueError('Família não encontrada.')
    if not _family_can_manage(interaction, bucket, family):
        raise PermissionError('Você não pode gerenciar essa família.')
    if member.bot:
        raise ValueError('Não é permitido convidar bots.')
    pending = bucket.setdefault('pending_invites', {})
    existing = pending.get(str(member.id))
    if existing and existing.get('family_slug') == selected_slug:
        raise ValueError('Já existe um convite pendente para esse membro.')
    pending[str(member.id)] = {
        'family_slug': selected_slug,
        'invited_by': interaction.user.id,
        'created_at': datetime.utcnow().isoformat(),
    }
    _family_db_save(data)
    view = _FamilyInviteView(member.id, interaction.guild_id, selected_slug, interaction.user.id)
    embed = _family_build_embed(
        '📨 Convite para família',
        (
            f'Você foi convidado para entrar na família **{family.get("name")}**.\n\n'
            f'**Convidado por:** {interaction.user.mention}\n'
            f'**Família:** {family.get("name")}\n'
            f'**Cor:** `{family.get("color")}`\n\n'
            f'Deseja aceitar?'
        ),
        int(family.get('color', '#7c5cff')[1:], 16),
    )
    if family.get('image_url'):
        embed.set_thumbnail(url=family['image_url'])
    try:
        await member.send(embed=embed, view=view)
    except discord.Forbidden:
        pending.pop(str(member.id), None)
        _family_db_save(data)
        raise ValueError('Não foi possível enviar a DM. O usuário está com DMs fechadas.')
    await _send_family_log(interaction.guild, member=interaction.user, title='Convite de família enviado', reason=f'Família: {family.get("name")}', action=f'Convite enviado para {member}', message_text='Aguardando resposta na DM')
    return family

# Painel
class _FamilyCreateModal(discord.ui.Modal, title='Criar Família'):
    family_name = discord.ui.TextInput(label='Nome da família', max_length=60, required=True)
    family_color = discord.ui.TextInput(label='Cor (#RRGGBB)', default='#7c5cff', max_length=7, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            family, role = await _family_create(interaction, str(self.family_name.value), str(self.family_color.value or '#7c5cff'))
            await interaction.response.send_message(embed=_family_build_embed('✅ Família criada', f'**Nome:** {family["name"]}\n**Cargo:** {role.mention}\n**Cor:** `{family["color"]}`\n**Líder:** {interaction.user.mention}', int(family['color'][1:], 16)), ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao criar a família.', ephemeral=True)


class _FamilyRenameModal(discord.ui.Modal, title='Renomear Família'):
    new_name = discord.ui.TextInput(label='Novo nome', max_length=60, required=True)

    def __init__(self, selected_slug: str):
        super().__init__()
        self.selected_slug = selected_slug

    async def on_submit(self, interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find_by_slug(bucket, self.selected_slug)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode renomear essa família.', ephemeral=True)
        new_slug = _family_slug(str(self.new_name.value))
        if new_slug != slug and new_slug in bucket['families']:
            return await interaction.response.send_message('Já existe outra família com esse nome.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(name=str(self.new_name.value), reason=f'Renomeada por {interaction.user}')
        old_name = family['name']
        family['name'] = str(self.new_name.value)
        if new_slug != slug:
            bucket['families'].pop(slug)
            bucket['families'][new_slug] = family
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família renomeada', reason='Nome alterado', action=f'{old_name} → {self.new_name.value}', message_text='')
        await interaction.response.send_message(f'✅ Família renomeada para **{self.new_name.value}**.', ephemeral=True)


class _FamilyColorModal(discord.ui.Modal, title='Alterar Cor da Família'):
    new_color = discord.ui.TextInput(label='Nova cor (#RRGGBB)', default='#7c5cff', max_length=7, required=True)

    def __init__(self, selected_slug: str):
        super().__init__()
        self.selected_slug = selected_slug

    async def on_submit(self, interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find_by_slug(bucket, self.selected_slug)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode alterar a cor dessa família.', ephemeral=True)
        try:
            discord_color, hex_color = _family_parse_color(str(self.new_color.value))
        except ValueError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(colour=discord_color, reason=f'Cor da família alterada por {interaction.user}')
        family['color'] = hex_color
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Cor de família alterada', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
        await interaction.response.send_message(f'✅ Cor da família **{family.get("name")}** alterada para `{hex_color}`.', ephemeral=True)


class _FamilyPhotoModal(discord.ui.Modal, title='Alterar Foto da Família'):
    photo_url = discord.ui.TextInput(label='URL da foto', style=discord.TextStyle.paragraph, required=True)

    def __init__(self, selected_slug: str):
        super().__init__()
        self.selected_slug = selected_slug

    async def on_submit(self, interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find_by_slug(bucket, self.selected_slug)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode alterar a foto dessa família.', ephemeral=True)
        url = str(self.photo_url.value).strip()
        family['image_url'] = url
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Foto de família alterada', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
        await interaction.response.send_message(f'✅ Foto da família **{family.get("name")}** atualizada.', ephemeral=True)


class _FamilySelect(discord.ui.Select):
    def __init__(self, parent_view: '_FamilyPanelView', interaction: discord.Interaction):
        self.parent_view = parent_view
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        families = bucket.get('families', {})
        options = []
        for slug, fam in list(families.items())[:25]:
            options.append(discord.SelectOption(label=fam.get('name', slug)[:100], value=slug, description=f"{len(fam.get('members', []))} membro(s)"[:100]))
        if not options:
            options = [discord.SelectOption(label='Nenhuma família', value='__none__', description='Crie uma família primeiro')]
        super().__init__(placeholder='Selecione uma família do painel', min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == '__none__':
            self.parent_view.selected_slug = None
            return await interaction.response.send_message('Nenhuma família disponível ainda.', ephemeral=True)
        self.parent_view.selected_slug = value
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find_by_slug(bucket, value)
        if family:
            selected_member = interaction.guild.get_member(self.parent_view.selected_member_id) if self.parent_view.selected_member_id else None
            embed = _family_build_details_embed(interaction.guild, family, selected_member)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        else:
            await interaction.response.send_message('Família não encontrada.', ephemeral=True)


class _FamilyMemberUserSelect(discord.ui.UserSelect):
    def __init__(self, parent_view: '_FamilyPanelView'):
        self.parent_view = parent_view
        super().__init__(placeholder='Selecione um membro para convidar/remover no painel', min_values=1, max_values=1, row=1)

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]
        member = interaction.guild.get_member(user.id)
        if member is None:
            self.parent_view.selected_member_id = None
            return await interaction.response.send_message('O usuário selecionado não está no servidor.', ephemeral=True)
        self.parent_view.selected_member_id = member.id
        if self.parent_view.selected_slug:
            data = _family_db_load()
            bucket = _family_bucket(data, interaction.guild_id)
            _, family = _family_find_by_slug(bucket, self.parent_view.selected_slug)
            if family:
                embed = _family_build_details_embed(interaction.guild, family, member)
                return await interaction.response.edit_message(embed=embed, view=self.parent_view)
        await interaction.response.send_message(f'Membro selecionado no painel: {member.mention}', ephemeral=True)


class _FamilyPanelView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.selected_slug = None
        self.selected_member_id = None
        self.add_item(_FamilySelect(self, interaction))
        self.add_item(_FamilyMemberUserSelect(self))

    def _get_selected(self, interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        family = None
        slug = None
        if self.selected_slug:
            slug, family = _family_find_by_slug(bucket, self.selected_slug)
        member = interaction.guild.get_member(self.selected_member_id) if self.selected_member_id else None
        return data, bucket, slug, family, member

    @discord.ui.button(label='➕ Criar', style=discord.ButtonStyle.success, row=2)
    async def create_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_FamilyCreateModal())

    @discord.ui.button(label='👁️ Ver', style=discord.ButtonStyle.secondary, row=2)
    async def view_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        embed = _family_build_details_embed(interaction.guild, family, member)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label='✏️ Renomear', style=discord.ButtonStyle.primary, row=2)
    async def rename_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        await interaction.response.send_modal(_FamilyRenameModal(slug))

    @discord.ui.button(label='🎨 Cor', style=discord.ButtonStyle.primary, row=2)
    async def recolor_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        await interaction.response.send_modal(_FamilyColorModal(slug))

    @discord.ui.button(label='🖼️ Foto', style=discord.ButtonStyle.primary, row=3)
    async def photo_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        await interaction.response.send_modal(_FamilyPhotoModal(slug))

    @discord.ui.button(label='📨 Convidar', style=discord.ButtonStyle.success, row=3)
    async def invite_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        if not member:
            return await interaction.response.send_message('Selecione um membro no seletor de usuários primeiro.', ephemeral=True)
        try:
            family = await _family_send_invite(interaction, slug, member)
            embed = _family_build_details_embed(interaction.guild, family, member)
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(f'📨 Convite enviado para {member.mention}. Agora a pessoa precisa aceitar na DM.', ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao enviar o convite.', ephemeral=True)

    @discord.ui.button(label='➖ Remover', style=discord.ButtonStyle.danger, row=3)
    async def remove_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        if not member:
            return await interaction.response.send_message('Selecione um membro no seletor de usuários primeiro.', ephemeral=True)
        try:
            if not _family_can_manage(interaction, bucket, family):
                raise PermissionError('Você não pode gerenciar essa família.')
            if member.id not in family.get('members', []):
                raise ValueError('Esse membro não está nessa família.')
            family['members'].remove(member.id)
            role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
            if role and role in member.roles:
                await member.remove_roles(role, reason=f'Removido da família {family.get("name")}')
            _family_db_save(data)
            await _send_family_log(interaction.guild, member=interaction.user, title='Membro removido da família', reason=f'Família: {family.get("name")}', action=f'Membro removido: {member}', message_text='')
            embed = _family_build_details_embed(interaction.guild, family, member)
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(f'✅ {member.mention} foi removido da família **{family.get("name")}**.', ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao remover membro.', ephemeral=True)

    @discord.ui.button(label='🏆 Ranking', style=discord.ButtonStyle.secondary, row=4)
    async def rank_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        families = list(bucket.get('families', {}).values())
        if not families:
            return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
        families.sort(key=lambda f: len(f.get('members', [])), reverse=True)
        lines = []
        for idx, fam in enumerate(families[:10], start=1):
            lines.append(f"**{idx}.** {fam.get('name')} — {len(fam.get('members', []))} membro(s)")
        await interaction.response.send_message(embed=_family_build_embed('Ranking de Famílias', '\n'.join(lines)), ephemeral=True)

    @discord.ui.button(label='🗑️ Deletar', style=discord.ButtonStyle.danger, row=4)
    async def delete_family(self, interaction: discord.Interaction, button: discord.ui.Button):
        data, bucket, slug, family, member = self._get_selected(interaction)
        if not family:
            return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode deletar essa família.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            try:
                await role.delete(reason=f'Família deletada por {interaction.user}')
            except Exception:
                traceback.print_exc()
        bucket['families'].pop(slug, None)
        _family_db_save(data)
        self.selected_slug = None
        await _send_family_log(interaction.guild, member=interaction.user, title='Família deletada', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
        await interaction.response.send_message(f'🗑️ Família **{family.get("name")}** deletada com sucesso.', ephemeral=True)

    @discord.ui.button(label='❓ Ajuda', style=discord.ButtonStyle.secondary, row=4)
    async def help_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message('Painel V4: selecione a família no primeiro menu e o membro no seletor de usuários. Depois use o botão 📨 Convidar para enviar um convite na DM da pessoa. A pessoa só entra na família se aceitar.', ephemeral=True)


async def _family_name_autocomplete(interaction: discord.Interaction, current: str):
    data = _family_db_load()
    bucket = _family_bucket(data, interaction.guild_id)
    families = bucket.get('families', {})
    current_l = current.lower().strip()
    out = []
    for slug, fam in families.items():
        name = fam.get('name', slug)
        if not current_l or current_l in name.lower():
            out.append(app_commands.Choice(name=name[:100], value=name[:100]))
        if len(out) >= 25:
            break
    return out


def setup_family_system():
    if getattr(bot, '_family_system_v4_registered', False):
        return
    bot._family_system_v4_registered = True
    guild_obj = discord.Object(id=int(SEU_ID_DO_SERVIDOR))
    family_group = app_commands.Group(name='familia', description='Sistema de famílias do servidor')

    @family_group.command(name='painel', description='Abre o painel interativo de famílias (V4)')
    async def familia_painel(interaction: discord.Interaction):
        embed = _family_build_embed(
            'Painel de Famílias • V4',
            'Selecione uma família no menu e um membro no seletor de usuários.\n\nUse o botão 📨 Convidar para enviar um convite na DM da pessoa, e o botão ➖ Remover para retirar membros já participantes.',
        )
        await interaction.response.send_message(embed=embed, view=_FamilyPanelView(interaction), ephemeral=True)

    @family_group.command(name='setlog', description='Define o canal de logs das famílias')
    @app_commands.describe(canal='Canal onde os logs de famílias serão enviados')
    async def familia_setlog(interaction: discord.Interaction, canal: discord.TextChannel):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode alterar o canal de log das famílias.', ephemeral=True)
        bucket['family_log_channel_id'] = canal.id
        _family_db_save(data)
        await interaction.response.send_message(f'✅ Canal de log das famílias definido para {canal.mention}.', ephemeral=True)

    @family_group.command(name='autorizarcargo', description='Autoriza um cargo para gerenciar famílias')
    @app_commands.describe(cargo='Cargo autorizado a criar/gerenciar famílias')
    async def familia_autorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode autorizar cargos.', ephemeral=True)
        if cargo.id in bucket['authorized_roles']:
            return await interaction.response.send_message('Esse cargo já está autorizado.', ephemeral=True)
        bucket['authorized_roles'].append(cargo.id)
        _family_db_save(data)
        await interaction.response.send_message(f'✅ Cargo autorizado para gerenciar famílias: {cargo.mention}', ephemeral=True)

    @family_group.command(name='desautorizarcargo', description='Remove um cargo autorizado')
    @app_commands.describe(cargo='Cargo a remover da lista de autorizados')
    async def familia_desautorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode desautorizar cargos.', ephemeral=True)
        if cargo.id not in bucket['authorized_roles']:
            return await interaction.response.send_message('Esse cargo não está autorizado.', ephemeral=True)
        bucket['authorized_roles'].remove(cargo.id)
        _family_db_save(data)
        await interaction.response.send_message(f'✅ Cargo removido da lista de autorizados: {cargo.mention}', ephemeral=True)

    @family_group.command(name='autorizados', description='Lista cargos autorizados a gerenciar famílias')
    async def familia_autorizados(interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        roles = []
        for role_id in bucket.get('authorized_roles', []):
            role = interaction.guild.get_role(int(role_id))
            roles.append(role.mention if role else f'`{role_id}` (inexistente)')
        desc = '\n'.join(roles) if roles else 'Nenhum cargo autorizado ainda.'
        log_ch = bucket.get('family_log_channel_id')
        if log_ch:
            canal = interaction.guild.get_channel(int(log_ch))
            desc += f"\n\n**Canal de log:** {canal.mention if canal else f'`{log_ch}`'}"
        await interaction.response.send_message(embed=_family_build_embed('Configurações de Famílias', desc), ephemeral=True)

    @family_group.command(name='criar', description='Cria uma nova família')
    @app_commands.describe(nome='Nome da família', cor='Cor da família em #RRGGBB')
    async def familia_criar(interaction: discord.Interaction, nome: str, cor: str | None = '#7c5cff'):
        try:
            family, role = await _family_create(interaction, nome, cor or '#7c5cff')
            await interaction.response.send_message(embed=_family_build_embed('✅ Família criada', f'**Nome:** {nome}\n**Cargo:** {role.mention}\n**Cor:** `{family["color"]}`\n**Líder:** {interaction.user.mention}', int(family['color'][1:], 16)), ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao criar a família.', ephemeral=True)

    @family_group.command(name='listar', description='Lista todas as famílias cadastradas')
    async def familia_listar(interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        families = bucket.get('families', {})
        if not families:
            return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
        lines = []
        for slug, fam in families.items():
            role = interaction.guild.get_role(int(fam.get('role_id', 0))) if fam.get('role_id') else None
            lines.append(f"• **{fam.get('name')}** — {len(fam.get('members', []))} membro(s) — cargo: {role.mention if role else '`não encontrado`'}")
        await interaction.response.send_message(embed=_family_build_embed('Famílias cadastradas', '\n'.join(lines)), ephemeral=True)

    @family_group.command(name='ver', description='Mostra os detalhes de uma família')
    @app_commands.describe(nome='Nome da família')
    @app_commands.autocomplete(nome=_family_name_autocomplete)
    async def familia_ver(interaction: discord.Interaction, nome: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find(bucket, nome)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        await interaction.response.send_message(embed=_family_build_details_embed(interaction.guild, family), ephemeral=True)

    @family_group.command(name='renomear', description='Renomeia uma família')
    @app_commands.describe(familia='Família atual', novo_nome='Novo nome da família')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_renomear(interaction: discord.Interaction, familia: str, novo_nome: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode renomear essa família.', ephemeral=True)
        new_slug = _family_slug(novo_nome)
        if new_slug != slug and new_slug in bucket['families']:
            return await interaction.response.send_message('Já existe outra família com esse nome.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(name=novo_nome, reason=f'Renomeada por {interaction.user}')
        old_name = family['name']
        family['name'] = novo_nome
        if new_slug != slug:
            bucket['families'].pop(slug)
            bucket['families'][new_slug] = family
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família renomeada', reason='Nome alterado', action=f'{old_name} → {novo_nome}', message_text='')
        await interaction.response.send_message(f'✅ Família renomeada para **{novo_nome}**.', ephemeral=True)

    @family_group.command(name='cor', description='Altera a cor da família e do cargo')
    @app_commands.describe(familia='Família alvo', cor='Nova cor em #RRGGBB')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_cor(interaction: discord.Interaction, familia: str, cor: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode alterar a cor dessa família.', ephemeral=True)
        try:
            discord_color, hex_color = _family_parse_color(cor)
        except ValueError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(colour=discord_color, reason=f'Cor da família alterada por {interaction.user}')
        family['color'] = hex_color
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Cor de família alterada', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
        await interaction.response.send_message(f'✅ Cor da família **{family.get("name")}** alterada para `{hex_color}`.', ephemeral=True)

    @family_group.command(name='foto', description='Altera a foto da família por URL')
    @app_commands.describe(familia='Família alvo', url='URL da nova imagem')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_foto(interaction: discord.Interaction, familia: str, url: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode alterar a foto dessa família.', ephemeral=True)
        family['image_url'] = url
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Foto de família alterada', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
        await interaction.response.send_message(f'✅ Foto da família **{family.get("name")}** atualizada.', ephemeral=True)

    @family_group.command(name='add', description='Convida um membro para a família')
    @app_commands.describe(familia='Família alvo', membro='Membro a convidar')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_add(interaction: discord.Interaction, familia: str, membro: discord.Member):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode gerenciar essa família.', ephemeral=True)
        try:
            await _family_send_invite(interaction, slug, membro)
            await interaction.response.send_message(f'📨 Convite enviado para {membro.mention}. Agora a pessoa precisa aceitar na DM.', ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao enviar o convite.', ephemeral=True)

    @family_group.command(name='remove', description='Remove um membro da família')
    @app_commands.describe(familia='Família alvo', membro='Membro a remover')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_remove(interaction: discord.Interaction, familia: str, membro: discord.Member):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode gerenciar essa família.', ephemeral=True)
        if membro.id not in family.get('members', []):
            return await interaction.response.send_message('Esse membro não está nessa família.', ephemeral=True)
        family['members'].remove(membro.id)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role and role in membro.roles:
            await membro.remove_roles(role, reason=f'Removido da família {family.get("name")}')
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Membro removido da família', reason=f'Família: {family.get("name")}', action=f'Membro removido: {membro}', message_text='')
        await interaction.response.send_message(f'✅ {membro.mention} foi removido da família **{family.get("name")}**.', ephemeral=True)

    @family_group.command(name='deletar', description='Deleta uma família')
    @app_commands.describe(familia='Família alvo')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_deletar(interaction: discord.Interaction, familia: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        slug, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode deletar essa família.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            try:
                await role.delete(reason=f'Família deletada por {interaction.user}')
            except Exception:
                traceback.print_exc()
        bucket['families'].pop(slug, None)
        _family_db_save(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família deletada', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
        await interaction.response.send_message(f'🗑️ Família **{family.get("name")}** deletada com sucesso.', ephemeral=True)

    @family_group.command(name='sync', description='Sincroniza membros e cargo da família')
    @app_commands.describe(familia='Família alvo')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_sync(interaction: discord.Interaction, familia: str):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        _, family = _family_find(bucket, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, bucket, family):
            return await interaction.response.send_message('Você não pode sincronizar essa família.', ephemeral=True)
        await _family_sync_role_to_members(interaction.guild, family)
        _family_db_save(data)
        await interaction.response.send_message(f'🔄 Família **{family.get("name")}** sincronizada com o cargo.', ephemeral=True)

    @family_group.command(name='ranking', description='Mostra o ranking de famílias por quantidade de membros')
    async def familia_ranking(interaction: discord.Interaction):
        data = _family_db_load()
        bucket = _family_bucket(data, interaction.guild_id)
        families = list(bucket.get('families', {}).values())
        if not families:
            return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
        families.sort(key=lambda f: len(f.get('members', [])), reverse=True)
        lines = []
        for idx, fam in enumerate(families[:10], start=1):
            lines.append(f"**{idx}.** {fam.get('name')} — {len(fam.get('members', []))} membro(s)")
        await interaction.response.send_message(embed=_family_build_embed('Ranking de Famílias', '\n'.join(lines)), ephemeral=True)

    bot.tree.add_command(family_group, guild=guild_obj)

    async def _family_on_ready_sync():
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            print(f'[FAMILIAS V4] Slash commands sincronizados no servidor {SEU_ID_DO_SERVIDOR}: {len(synced)} comando(s)')
        except Exception as e:
            print('[FAMILIAS V4] Falha ao sincronizar slash commands:', e)
            traceback.print_exc()

    bot.add_listener(_family_on_ready_sync, 'on_ready')

setup_family_system()

# ==================== EVENTOS ====================
@bot.event
async def on_message(message):
    try:
        if message.author.bot:
            return
        if getattr(message, 'webhook_id', None) is not None:
            return
        if not message.content and message.embeds:
            return

        if message.guild:
            try:
                await postgres_save_member(message.author, message.guild)
            except Exception:
                traceback.print_exc()

        texto = message.content or ''

        # auto-ban convite
        invite_match = INVITE_REGEX.search(texto)
        if invite_match and message.guild and BAN_AO_DETECTAR_CONVITE:
            deleted_ok, deleted_reason = await try_delete_message(message)
            dm_ok, dm_reason = await try_send_dm_warning(message.author, texto, getattr(message.channel, 'name', 'DM'), 'Convite/propaganda detectado')
            ban_ok, ban_reason = await try_ban_member(message.guild, message.author, reason='Convite/propaganda detectado automaticamente')
            await log(
                message.guild,
                member=message.author,
                title='Ban automático por convite',
                channel_name=getattr(message.channel, 'name', 'desconhecido'),
                reason='Convite/propaganda detectado',
                action=f'Delete: {deleted_ok} ({deleted_reason}) | DM: {dm_ok} ({dm_reason}) | Ban: {ban_ok} ({ban_reason})',
                message_text=texto,
                accent=(190, 72, 72),
                target_channel_id=BAN_LOG_CHANNEL_ID,
            )
            return

        # reações por palavra-chave
        await _apply_keyword_reactions(message)

        # respostas automáticas de contexto
        decision = detect_auto_reply(message)
        if decision:
            cd = cooldown_status(message, decision['intent'])
            if not cd['blocked']:
                await message.reply(decision['reply'], mention_author=False)
                remember_context(message, decision['intent'], decision['score'], decision['matched_groups'], decision['reply'])
                mark_cooldown(message, decision['intent'])
                await log(
                    message.guild,
                    member=message.author,
                    title='Resposta automática',
                    channel_name=getattr(message.channel, 'name', 'desconhecido'),
                    reason=f"Intent: {decision['intent']} | Score: {decision['score']}",
                    action='Resposta enviada',
                    message_text=texto,
                    accent=(88, 154, 255),
                )
                return

        # respostas pessoais
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = bot.user in message.mentions if bot.user else False
        should_respond_personal = is_dm or mentions_bot
        if should_respond_personal:
            if re.search(r'(agradecido|obg|obrigado)', texto, re.IGNORECASE) and _mentions_jeffu(message):
                await message.reply('Não há de que <:amem:1466774899686117426>', mention_author=False)
                return
            if re.search(r'(te amo|amo vc|amo você|amo voce)', texto, re.IGNORECASE) and _mentions_jeffu(message):
                await message.reply('💙 Obrigado... <:shame:1466777359586693376>', mention_author=False)
                return
            if BAD_WORDS_PATTERN.search(texto):
                try:
                    await message.reply('<:looking:1466793665463844894> Me deixa trabalhar, poxa...', mention_author=False)
                except Exception:
                    pass
                return

        await bot.process_commands(message)
    except Exception as e:
        print(f'Erro no on_message: {e}')
        traceback.print_exc()


@bot.event
async def on_ready():
    print(f'[BOT] Logado como {bot.user} (id: {bot.user.id})')
    try:
        ok = await postgres_init()
        if ok:
            total = await postgres_sync_all_guilds()
            print(f'[POSTGRES] Sincronização inicial concluída: {total} usuário(s).')
    except Exception as e:
        print(f'[POSTGRES] Falha na inicialização/sincronização: {e}')
        traceback.print_exc()
    try:
        _load_reaction_rules()
        _ensure_default_rules_for_all_guilds()
    except Exception as e:
        print('[DEFAULT RULES WARN] Falha ao garantir regras padrão:', e)
        traceback.print_exc()
    try:
        for guild in bot.guilds:
            await audit_permission_status(guild)
    except Exception:
        traceback.print_exc()

@bot.event
async def on_member_join(member):
    try:
        await postgres_save_member(member, member.guild)
    except Exception as e:
        print(f'[POSTGRES] Falha ao salvar membro no on_member_join: {e}')
        traceback.print_exc()


@bot.event
async def on_member_update(before, after):
    try:
        await postgres_save_member(after, after.guild)
    except Exception as e:
        print(f'[POSTGRES] Falha ao salvar membro no on_member_update: {e}')
        traceback.print_exc()


TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:
    print('❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN.')
