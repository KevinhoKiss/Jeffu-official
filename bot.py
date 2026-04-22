
import discord
from discord import app_commands
from discord.ext import commands
import os, re, json, time, traceback, unicodedata
from pathlib import Path
from collections import defaultdict, deque
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    import psycopg2
    from psycopg2.pool import SimpleConnectionPool
except Exception:
    psycopg2 = None
    SimpleConnectionPool = None

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

# PostgreSQL (famílias como fonte principal)
DATABASE_URL = os.getenv('DATABASE_URL')
PG_POOL_MIN = int(os.getenv('PG_POOL_MIN', '1') or 1)
PG_POOL_MAX = int(os.getenv('PG_POOL_MAX', '5') or 5)
PG_SSLMODE = os.getenv('PGSSLMODE', 'require')
FAMILY_IMPORT_JSON_ON_START = str(os.getenv('FAMILY_IMPORT_JSON_ON_START', 'true')).lower() in ('1', 'true', 'yes', 'sim', 'on')

pg_pool = None

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

# ==================== SISTEMA DE FAMÍLIAS (POSTGRESQL) ====================
FAMILY_HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')
FAMILY_URL_RE = re.compile(r'^https?://', re.IGNORECASE)

PG_TABLE_FAMILY_SETTINGS = os.getenv('PG_FAMILY_SETTINGS_TABLE', 'family_settings')
PG_TABLE_FAMILIES = os.getenv('PG_FAMILIES_TABLE', 'families')
PG_TABLE_FAMILY_MEMBERS = os.getenv('PG_FAMILY_MEMBERS_TABLE', 'family_members')
PG_TABLE_FAMILY_INVITES = os.getenv('PG_FAMILY_INVITES_TABLE', 'family_invites')
PG_TABLE_FAMILY_ADMIN_USERS = os.getenv('PG_FAMILY_ADMIN_USERS_TABLE', 'family_admin_users')
PG_TABLE_FAMILY_ADMIN_ROLES = os.getenv('PG_FAMILY_ADMIN_ROLES_TABLE', 'family_admin_roles')
PG_TABLE_FAMILY_AUDIT = os.getenv('PG_FAMILY_AUDIT_TABLE', 'family_audit_logs')


def _pg_safe_table_name(name: str, fallback: str) -> str:
    name = (name or fallback).strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        return fallback
    return name


PG_TABLE_FAMILY_SETTINGS = _pg_safe_table_name(PG_TABLE_FAMILY_SETTINGS, 'family_settings')
PG_TABLE_FAMILIES = _pg_safe_table_name(PG_TABLE_FAMILIES, 'families')
PG_TABLE_FAMILY_MEMBERS = _pg_safe_table_name(PG_TABLE_FAMILY_MEMBERS, 'family_members')
PG_TABLE_FAMILY_INVITES = _pg_safe_table_name(PG_TABLE_FAMILY_INVITES, 'family_invites')
PG_TABLE_FAMILY_ADMIN_USERS = _pg_safe_table_name(PG_TABLE_FAMILY_ADMIN_USERS, 'family_admin_users')
PG_TABLE_FAMILY_ADMIN_ROLES = _pg_safe_table_name(PG_TABLE_FAMILY_ADMIN_ROLES, 'family_admin_roles')
PG_TABLE_FAMILY_AUDIT = _pg_safe_table_name(PG_TABLE_FAMILY_AUDIT, 'family_audit_logs')


class _PGFamilyRow:
    def __init__(self, guild_id, slug, name, role_id, color, leader_id, image_url, created_by):
        self.guild_id = int(guild_id)
        self.slug = slug
        self.name = name
        self.role_id = int(role_id) if role_id is not None else None
        self.color = color
        self.leader_id = int(leader_id)
        self.image_url = image_url or ''
        self.created_by = int(created_by)


def postgres_family_enabled() -> bool:
    return psycopg2 is not None and SimpleConnectionPool is not None and bool(DATABASE_URL)


def postgres_family_init() -> bool:
    global pg_pool
    if pg_pool is not None:
        return True
    if not postgres_family_enabled():
        if psycopg2 is None or SimpleConnectionPool is None:
            print('[POSTGRES] psycopg2 não está instalado. Adicione psycopg2-binary ao projeto.')
        else:
            print('[POSTGRES] DATABASE_URL não definido para o sistema de famílias.')
        return False
    try:
        dsn = DATABASE_URL
        if 'sslmode=' not in dsn:
            sep = '&' if '?' in dsn else '?'
            dsn = f'{dsn}{sep}sslmode={PG_SSLMODE}'
        pg_pool = SimpleConnectionPool(PG_POOL_MIN, PG_POOL_MAX, dsn=dsn)
        return True
    except Exception as e:
        print('[POSTGRES] Falha ao iniciar pool:', e)
        traceback.print_exc()
        return False


def _pg_exec(query: str, params=None, fetch=False, fetchone=False):
    if pg_pool is None:
        raise RuntimeError('Pool PostgreSQL não inicializado')
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetchone:
                row = cur.fetchone()
                conn.commit()
                return row
            if fetch:
                rows = cur.fetchall()
                conn.commit()
                return rows
            conn.commit()
            return None
    except Exception:
        conn.rollback()
        raise
    finally:
        pg_pool.putconn(conn)


def _pg_transaction(callback):
    if pg_pool is None:
        raise RuntimeError('Pool PostgreSQL não inicializado')
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            result = callback(conn, cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        pg_pool.putconn(conn)


def _pg_setup_family_tables() -> None:
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_SETTINGS} (
        guild_id BIGINT PRIMARY KEY,
        family_log_channel_id BIGINT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_ADMIN_USERS} (
        guild_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (guild_id, user_id)
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_ADMIN_ROLES} (
        guild_id BIGINT NOT NULL,
        role_id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (guild_id, role_id)
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILIES} (
        guild_id BIGINT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        role_id BIGINT,
        color TEXT NOT NULL DEFAULT '#7c5cff',
        leader_id BIGINT NOT NULL,
        image_url TEXT NOT NULL DEFAULT '',
        created_by BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (guild_id, slug)
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_MEMBERS} (
        guild_id BIGINT NOT NULL,
        family_slug TEXT NOT NULL,
        user_id BIGINT NOT NULL,
        joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (guild_id, family_slug, user_id)
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_INVITES} (
        guild_id BIGINT NOT NULL,
        family_slug TEXT NOT NULL,
        user_id BIGINT NOT NULL,
        invited_by BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (guild_id, family_slug, user_id)
    )''')
    _pg_exec(f'''CREATE TABLE IF NOT EXISTS {PG_TABLE_FAMILY_AUDIT} (
        id BIGSERIAL PRIMARY KEY,
        guild_id BIGINT NOT NULL,
        family_slug TEXT,
        family_name TEXT,
        action TEXT NOT NULL,
        actor_id BIGINT,
        actor_name TEXT,
        target_id BIGINT,
        target_name TEXT,
        details TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )''')


def _legacy_family_db_load() -> dict:
    if not os.path.exists(ARQUIVO):
        return {}
    try:
        with open(ARQUIVO, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        traceback.print_exc()
        return {}


def _pg_family_count_all() -> int:
    row = _pg_exec(f'SELECT COUNT(*) FROM {PG_TABLE_FAMILIES}', fetchone=True)
    return int(row[0]) if row else 0


def _pg_migrate_legacy_json_if_needed() -> int:
    if not FAMILY_IMPORT_JSON_ON_START:
        return 0
    try:
        if _pg_family_count_all() > 0:
            return 0
    except Exception:
        traceback.print_exc()
        return 0
    data = _legacy_family_db_load()
    if not data:
        return 0
    migrated = 0
    for guild_key, bucket in data.items():
        try:
            guild_id = int(guild_key)
        except Exception:
            continue
        _pg_exec(
            f'''INSERT INTO {PG_TABLE_FAMILY_SETTINGS} (guild_id, family_log_channel_id, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (guild_id) DO UPDATE SET family_log_channel_id = EXCLUDED.family_log_channel_id, updated_at = NOW()''',
            (guild_id, bucket.get('family_log_channel_id')),
        )
        for uid in bucket.get('authorized_users', []) or []:
            try:
                _pg_exec(f'INSERT INTO {PG_TABLE_FAMILY_ADMIN_USERS} (guild_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (guild_id, int(uid)))
            except Exception:
                traceback.print_exc()
        for rid in bucket.get('authorized_roles', []) or []:
            try:
                _pg_exec(f'INSERT INTO {PG_TABLE_FAMILY_ADMIN_ROLES} (guild_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (guild_id, int(rid)))
            except Exception:
                traceback.print_exc()
        for slug, family in (bucket.get('families', {}) or {}).items():
            try:
                _pg_exec(
                    f'''INSERT INTO {PG_TABLE_FAMILIES} (guild_id, slug, name, role_id, color, leader_id, image_url, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (guild_id, slug) DO NOTHING''',
                    (
                        guild_id, slug, family.get('name', slug), family.get('role_id'), family.get('color', '#7c5cff'),
                        family.get('leader_id') or DONO_ID, family.get('image_url', ''), family.get('created_by') or family.get('leader_id') or DONO_ID,
                    ),
                )
                for uid in family.get('members', []) or []:
                    _pg_exec(f'INSERT INTO {PG_TABLE_FAMILY_MEMBERS} (guild_id, family_slug, user_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING', (guild_id, slug, int(uid)))
                migrated += 1
            except Exception:
                traceback.print_exc()
        for invited_user_id, invite_data in (bucket.get('pending_invites', {}) or {}).items():
            try:
                _pg_exec(
                    f'''INSERT INTO {PG_TABLE_FAMILY_INVITES} (guild_id, family_slug, user_id, invited_by)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (guild_id, family_slug, user_id) DO UPDATE SET invited_by = EXCLUDED.invited_by, created_at = NOW()''',
                    (guild_id, invite_data.get('family_slug'), int(invited_user_id), int(invite_data.get('invited_by') or DONO_ID)),
                )
            except Exception:
                traceback.print_exc()
    return migrated


def _family_slug(text: str) -> str:
    text = unicodedata.normalize('NFKD', str(text).strip().lower())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9\s_-]', '', text)
    text = re.sub(r'[\s_-]+', '_', text).strip('_')
    return text or 'familia'


def _family_from_row(row):
    return _PGFamilyRow(*row) if row else None


def _family_get(guild_id: int, slug_or_name: str):
    slug = _family_slug(slug_or_name)
    row = _pg_exec(
        f'''SELECT guild_id, slug, name, role_id, color, leader_id, image_url, created_by
            FROM {PG_TABLE_FAMILIES}
            WHERE guild_id = %s AND (slug = %s OR lower(name) = lower(%s))
            ORDER BY slug = %s DESC LIMIT 1''',
        (guild_id, slug, slug_or_name, slug), fetchone=True)
    return _family_from_row(row)


def _family_list(guild_id: int):
    rows = _pg_exec(f'''SELECT guild_id, slug, name, role_id, color, leader_id, image_url, created_by FROM {PG_TABLE_FAMILIES} WHERE guild_id = %s ORDER BY name''', (guild_id,), fetch=True) or []
    return [_family_from_row(r) for r in rows]


def _family_members(guild_id: int, family_slug: str) -> list[int]:
    rows = _pg_exec(f'''SELECT user_id FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND family_slug = %s ORDER BY joined_at''', (guild_id, family_slug), fetch=True) or []
    return [int(r[0]) for r in rows]


def _family_member_count(guild_id: int, family_slug: str) -> int:
    row = _pg_exec(f'''SELECT COUNT(*) FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND family_slug = %s''', (guild_id, family_slug), fetchone=True)
    return int(row[0]) if row else 0


def _family_current_of_user(guild_id: int, user_id: int):
    row = _pg_exec(
        f'''SELECT f.guild_id, f.slug, f.name, f.role_id, f.color, f.leader_id, f.image_url, f.created_by
            FROM {PG_TABLE_FAMILIES} f JOIN {PG_TABLE_FAMILY_MEMBERS} fm ON fm.guild_id = f.guild_id AND fm.family_slug = f.slug
            WHERE fm.guild_id = %s AND fm.user_id = %s LIMIT 1''',
        (guild_id, user_id), fetchone=True)
    return _family_from_row(row)


def _family_parse_color(value):
    if not value:
        return discord.Colour(0x7c5cff), '#7c5cff'
    value = value.strip()
    if not value.startswith('#'):
        value = '#' + value
    if not FAMILY_HEX_COLOR_RE.match(value):
        raise ValueError('Use uma cor no formato #RRGGBB, ex: #7c5cff')
    return discord.Colour(int(value[1:], 16)), value.lower()


def _family_is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == DONO_ID


def _family_has_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    if _family_is_owner(interaction):
        return True
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild):
        return True
    user_rows = _pg_exec(f'SELECT 1 FROM {PG_TABLE_FAMILY_ADMIN_USERS} WHERE guild_id = %s AND user_id = %s LIMIT 1', (interaction.guild_id, interaction.user.id), fetchone=True)
    if user_rows:
        return True
    role_ids = {getattr(role, 'id', 0) for role in getattr(interaction.user, 'roles', [])}
    if role_ids:
        placeholders = ', '.join(['%s'] * len(role_ids))
        rows = _pg_exec(f'SELECT role_id FROM {PG_TABLE_FAMILY_ADMIN_ROLES} WHERE guild_id = %s AND role_id IN ({placeholders})', [interaction.guild_id, *list(role_ids)], fetch=True) or []
        return bool(rows)
    return False


def _family_can_manage(interaction: discord.Interaction, family: _PGFamilyRow) -> bool:
    return _family_has_admin(interaction) or int(family.leader_id) == interaction.user.id


def _family_log_channel_id(guild_id: int):
    row = _pg_exec(f'SELECT family_log_channel_id FROM {PG_TABLE_FAMILY_SETTINGS} WHERE guild_id = %s', (guild_id,), fetchone=True)
    return int(row[0]) if row and row[0] is not None else None


def _family_audit(guild: discord.Guild, action: str, actor=None, family=None, target=None, details: str = ''):
    try:
        _pg_exec(
            f'''INSERT INTO {PG_TABLE_FAMILY_AUDIT} (guild_id, family_slug, family_name, action, actor_id, actor_name, target_id, target_name, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (guild.id, family.slug if family else None, family.name if family else None, action, getattr(actor, 'id', None), _family_actor_label(actor), getattr(target, 'id', None), _family_actor_label(target), details or ''),
        )
    except Exception:
        traceback.print_exc()


def _family_actor_label(user):
    if user is None:
        return None
    return getattr(user, 'display_name', None) or getattr(user, 'name', None) or str(user)


async def _send_family_log(guild: discord.Guild, member=None, title='Log de Família', reason='', action='', message_text='', family=None, target=None, audit_action=None):
    try:
        if guild:
            _family_audit(guild, audit_action or 'family_log', actor=member, family=family, target=target, details=f'reason={reason} | action={action} | message={message_text}')
    except Exception:
        traceback.print_exc()
    try:
        channel_id = _family_log_channel_id(guild.id)
        if not channel_id:
            return
        canal = guild.get_channel(int(channel_id))
        if canal is None:
            return
        perms = canal.permissions_for(guild.me or guild.get_member(bot.user.id)) if guild and bot.user else None
        if perms and perms.send_messages:
            embed = discord.Embed(title=title, description=f'**Motivo:** {reason or "-"}\n**Ação:** {action or "-"}\n**Mensagem:** {message_text or "-"}', color=int((family.color if family else '#2ecc71')[1:], 16) if family else 0x2ecc71)
            if family and family.image_url:
                embed.set_thumbnail(url=family.image_url)
            await canal.send(embed=embed)
    except Exception:
        traceback.print_exc()


async def _family_remove_member_from_other_families(guild: discord.Guild, member: discord.Member, keep_slug=None):
    rows = _pg_exec(f'''SELECT family_slug FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND user_id = %s''', (guild.id, member.id), fetch=True) or []
    for (family_slug,) in rows:
        if keep_slug and family_slug == keep_slug:
            continue
        old_family = _family_get(guild.id, family_slug)
        _pg_exec(f'''DELETE FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND family_slug = %s AND user_id = %s''', (guild.id, family_slug, member.id))
        if old_family and old_family.role_id:
            role = guild.get_role(old_family.role_id)
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason='Mudança de família')
                except Exception:
                    traceback.print_exc()


async def _family_add_member(guild: discord.Guild, family, member: discord.Member):
    await _family_remove_member_from_other_families(guild, member, keep_slug=family.slug)
    _pg_exec(f'''INSERT INTO {PG_TABLE_FAMILY_MEMBERS} (guild_id, family_slug, user_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING''', (guild.id, family.slug, member.id))
    if family.role_id:
        role = guild.get_role(family.role_id)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason=f'Entrada na família {family.name}')
            except Exception:
                traceback.print_exc()


async def _family_remove_member(guild: discord.Guild, family, member: discord.Member):
    _pg_exec(f'''DELETE FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND family_slug = %s AND user_id = %s''', (guild.id, family.slug, member.id))
    if family.role_id:
        role = guild.get_role(family.role_id)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f'Saída da família {family.name}')
            except Exception:
                traceback.print_exc()


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
            return await interaction.response.send_message('❌ Esse convite não é seu.', ephemeral=True)
        guild = bot.get_guild(self.guild_id)
        if guild is None:
            return await interaction.response.send_message('❌ Não encontrei o servidor desse convite.', ephemeral=True)
        invite = _pg_exec(f'''SELECT invited_by FROM {PG_TABLE_FAMILY_INVITES} WHERE guild_id = %s AND family_slug = %s AND user_id = %s''', (self.guild_id, self.family_slug, self.invited_user_id), fetchone=True)
        if not invite:
            return await interaction.response.send_message('❌ Esse convite já expirou ou foi removido.', ephemeral=True)
        family = _family_get(self.guild_id, self.family_slug)
        if family is None:
            return await interaction.response.send_message('❌ A família desse convite não existe mais.', ephemeral=True)
        member = guild.get_member(self.invited_user_id)
        if member is None:
            return await interaction.response.send_message('❌ Você não está mais no servidor.', ephemeral=True)
        _pg_exec(f'''DELETE FROM {PG_TABLE_FAMILY_INVITES} WHERE guild_id = %s AND family_slug = %s AND user_id = %s''', (self.guild_id, self.family_slug, self.invited_user_id))
        await _family_add_member(guild, family, member)
        await _send_family_log(guild, member=member, title='Convite de família aceito', reason=f'Família: {family.name}', action='Usuário aceitou o convite', message_text='Convite aceito via DM', family=family, audit_action='invite_accepted')
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f'✅ Você entrou na família **{family.name}**.', view=self)

    @discord.ui.button(label='❌ Recusar', style=discord.ButtonStyle.danger)
    async def decline_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invited_user_id:
            return await interaction.response.send_message('❌ Esse convite não é seu.', ephemeral=True)
        family = _family_get(self.guild_id, self.family_slug)
        _pg_exec(f'''DELETE FROM {PG_TABLE_FAMILY_INVITES} WHERE guild_id = %s AND family_slug = %s AND user_id = %s''', (self.guild_id, self.family_slug, self.invited_user_id))
        guild = bot.get_guild(self.guild_id)
        if guild and family:
            await _send_family_log(guild, member=interaction.user, title='Convite de família recusado', reason='Convite recusado', action=f'Família: {family.name}', message_text='Convite recusado via DM', family=family, audit_action='invite_declined')
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content='❌ Você recusou o convite.', view=self)


async def _family_send_invite(interaction: discord.Interaction, selected_slug: str, member: discord.Member):
    family = _family_get(interaction.guild_id, selected_slug)
    if not family:
        raise ValueError('Família não encontrada.')
    if not _family_can_manage(interaction, family):
        raise PermissionError('Você não pode gerenciar essa família.')
    if member.bot:
        raise ValueError('Não é permitido convidar bots.')
    _pg_exec(f'''INSERT INTO {PG_TABLE_FAMILY_INVITES} (guild_id, family_slug, user_id, invited_by) VALUES (%s, %s, %s, %s) ON CONFLICT (guild_id, family_slug, user_id) DO UPDATE SET invited_by = EXCLUDED.invited_by, created_at = NOW()''', (interaction.guild_id, selected_slug, member.id, interaction.user.id))
    embed = discord.Embed(title='📨 Convite para família', description=(f'Você foi convidado para entrar na família **{family.name}**.\n\n**Convidado por:** {interaction.user.mention}\n**Família:** {family.name}\n**Cor:** `{family.color}`\n\nDeseja aceitar?'), color=int(family.color[1:], 16))
    if family.image_url:
        embed.set_thumbnail(url=family.image_url)
    view = _FamilyInviteView(member.id, interaction.guild_id, selected_slug, interaction.user.id)
    try:
        await member.send(embed=embed, view=view)
    except discord.Forbidden:
        raise ValueError('Não foi possível enviar a DM. O usuário está com DMs fechadas.')
    await _send_family_log(interaction.guild, member=interaction.user, title='Convite de família enviado', reason=f'Família: {family.name}', action=f'Convite enviado para {member}', message_text='Aguardando resposta na DM', family=family, target=member, audit_action='invite_sent')
    return family


class _FamilyPanelView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.requester_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message('❌ Esse painel não é seu.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='📚 Listar famílias', style=discord.ButtonStyle.primary)
    async def listar(self, interaction: discord.Interaction, button: discord.ui.Button):
        familias = _family_list(interaction.guild_id)
        if not familias:
            return await interaction.response.send_message('📭 Nenhuma família cadastrada.', ephemeral=True)
        embed = discord.Embed(title='Famílias cadastradas', color=0x7c5cff)
        for fam in familias[:25]:
            embed.add_field(name=fam.name, value=f'`{fam.slug}` • {_family_member_count(interaction.guild_id, fam.slug)} membro(s)', inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label='👤 Minha família', style=discord.ButtonStyle.secondary)
    async def minha_familia(self, interaction: discord.Interaction, button: discord.ui.Button):
        fam = _family_current_of_user(interaction.guild_id, interaction.user.id)
        if not fam:
            return await interaction.response.send_message('📭 Você não está em nenhuma família.', ephemeral=True)
        members = _family_member_count(interaction.guild_id, fam.slug)
        await interaction.response.send_message(f'🏡 Você está na família **{fam.name}** (`{fam.slug}`) com **{members}** membro(s).', ephemeral=True)

    @discord.ui.button(label='🧾 Últimos logs', style=discord.ButtonStyle.success)
    async def ultimos_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = _pg_exec(f'''SELECT action, family_name, created_at FROM {PG_TABLE_FAMILY_AUDIT} WHERE guild_id = %s ORDER BY created_at DESC LIMIT 10''', (interaction.guild_id,), fetch=True) or []
        if not rows:
            return await interaction.response.send_message('📭 Nenhum log registrado ainda.', ephemeral=True)
        text = '\n'.join([f'`{r[2]}` • **{r[0]}** • {r[1] or "-"}' for r in rows])
        await interaction.response.send_message(text[:1900], ephemeral=True)


async def _family_name_autocomplete(interaction: discord.Interaction, current: str):
    if not interaction.guild_id:
        return []
    current_l = normalize_text(current)
    out = []
    for fam in _family_list(interaction.guild_id):
        if not current_l or current_l in normalize_text(fam.name) or current_l in fam.slug:
            out.append(app_commands.Choice(name=fam.name[:100], value=fam.slug[:100]))
        if len(out) >= 25:
            break
    return out


def setup_family_system():
    if getattr(bot, '_family_pg_registered', False):
        return
    bot._family_pg_registered = True
    guild_obj = discord.Object(id=int(SEU_ID_DO_SERVIDOR)) if SEU_ID_DO_SERVIDOR else None
    family_group = app_commands.Group(name='familia', description='Sistema de famílias do servidor (PostgreSQL)')

    @family_group.command(name='painel', description='Abre o painel interativo de famílias')
    async def familia_painel(interaction: discord.Interaction):
        embed = discord.Embed(title='Painel de Famílias (PostgreSQL)', description='Use os botões abaixo para consultas rápidas e os comandos /familia para gerenciar tudo.', color=0x7c5cff)
        await interaction.response.send_message(embed=embed, view=_FamilyPanelView(interaction), ephemeral=True)

    @family_group.command(name='setlog', description='Define o canal de logs das famílias')
    @app_commands.describe(canal='Canal onde os logs de famílias serão enviados')
    async def familia_setlog(interaction: discord.Interaction, canal: discord.TextChannel):
        if not _family_has_admin(interaction):
            return await interaction.response.send_message('❌ Você não tem permissão para definir o canal de log das famílias.', ephemeral=True)
        _pg_exec(f'''INSERT INTO {PG_TABLE_FAMILY_SETTINGS} (guild_id, family_log_channel_id, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (guild_id) DO UPDATE SET family_log_channel_id = EXCLUDED.family_log_channel_id, updated_at = NOW()''', (interaction.guild_id, canal.id))
        _family_audit(interaction.guild, 'set_log_channel', actor=interaction.user, details=f'channel_id={canal.id}')
        await interaction.response.send_message(f'✅ Canal de log das famílias definido para {canal.mention}.', ephemeral=True)

    @family_group.command(name='autorizarcargo', description='Autoriza um cargo para gerenciar famílias')
    @app_commands.describe(cargo='Cargo autorizado a criar/gerenciar famílias')
    async def familia_autorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('❌ Apenas o dono configurado pode autorizar cargos.', ephemeral=True)
        _pg_exec(f'INSERT INTO {PG_TABLE_FAMILY_ADMIN_ROLES} (guild_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (interaction.guild_id, cargo.id))
        await interaction.response.send_message(f'✅ Cargo autorizado para gerenciar famílias: {cargo.mention}', ephemeral=True)

    @family_group.command(name='desautorizarcargo', description='Remove um cargo autorizado')
    @app_commands.describe(cargo='Cargo a remover da lista de autorizados')
    async def familia_desautorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('❌ Apenas o dono configurado pode desautorizar cargos.', ephemeral=True)
        _pg_exec(f'DELETE FROM {PG_TABLE_FAMILY_ADMIN_ROLES} WHERE guild_id = %s AND role_id = %s', (interaction.guild_id, cargo.id))
        await interaction.response.send_message(f'✅ Cargo removido da lista de autorizados: {cargo.mention}', ephemeral=True)

    @family_group.command(name='autorizarusuario', description='Autoriza um usuário para gerenciar famílias')
    @app_commands.describe(usuario='Usuário autorizado a criar/gerenciar famílias')
    async def familia_autorizarusuario(interaction: discord.Interaction, usuario: discord.Member):
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('❌ Apenas o dono configurado pode autorizar usuários.', ephemeral=True)
        _pg_exec(f'INSERT INTO {PG_TABLE_FAMILY_ADMIN_USERS} (guild_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (interaction.guild_id, usuario.id))
        await interaction.response.send_message(f'✅ Usuário autorizado para gerenciar famílias: {usuario.mention}', ephemeral=True)

    @family_group.command(name='desautorizarusuario', description='Remove um usuário autorizado')
    @app_commands.describe(usuario='Usuário a remover da lista de autorizados')
    async def familia_desautorizarusuario(interaction: discord.Interaction, usuario: discord.Member):
        if not _family_is_owner(interaction):
            return await interaction.response.send_message('❌ Apenas o dono configurado pode desautorizar usuários.', ephemeral=True)
        _pg_exec(f'DELETE FROM {PG_TABLE_FAMILY_ADMIN_USERS} WHERE guild_id = %s AND user_id = %s', (interaction.guild_id, usuario.id))
        await interaction.response.send_message(f'✅ Usuário removido da lista de autorizados: {usuario.mention}', ephemeral=True)

    @family_group.command(name='criar', description='Cria uma nova família')
    @app_commands.describe(nome='Nome da família', cor='Cor do cargo (#RRGGBB)')
    async def familia_criar(interaction: discord.Interaction, nome: str, cor: str = '#7c5cff'):
        if not _family_has_admin(interaction):
            return await interaction.response.send_message('❌ Você não tem permissão para criar famílias.', ephemeral=True)
        slug = _family_slug(nome)
        if _family_get(interaction.guild_id, slug):
            return await interaction.response.send_message('❌ Já existe uma família com esse nome.', ephemeral=True)
        try:
            discord_color, hex_color = _family_parse_color(cor)
        except ValueError as e:
            return await interaction.response.send_message(f'❌ {e}', ephemeral=True)
        role = await interaction.guild.create_role(name=nome, colour=discord_color, reason=f'Família criada por {interaction.user}')
        await _family_remove_member_from_other_families(interaction.guild, interaction.user, keep_slug=slug)
        def _tx(conn, cur):
            cur.execute(f'''INSERT INTO {PG_TABLE_FAMILIES} (guild_id, slug, name, role_id, color, leader_id, image_url, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', (interaction.guild.id, slug, nome, role.id, hex_color, interaction.user.id, '', interaction.user.id))
            cur.execute(f'''INSERT INTO {PG_TABLE_FAMILY_MEMBERS} (guild_id, family_slug, user_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING''', (interaction.guild.id, slug, interaction.user.id))
        _pg_transaction(_tx)
        family = _family_get(interaction.guild.id, slug)
        if role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role, reason='Criador da família')
            except Exception:
                traceback.print_exc()
        await _send_family_log(interaction.guild, member=interaction.user, title='Família criada', reason=f'Família {nome} criada', action=f'Cargo criado: {role.name}', message_text=f'Cor: {hex_color}', family=family, audit_action='family_created')
        await interaction.response.send_message(f'✅ Família **{nome}** criada com sucesso.', ephemeral=True)

    @family_group.command(name='listar', description='Lista as famílias do servidor')
    async def familia_listar(interaction: discord.Interaction):
        familias = _family_list(interaction.guild_id)
        if not familias:
            return await interaction.response.send_message('📭 Nenhuma família cadastrada.', ephemeral=True)
        embed = discord.Embed(title='Famílias cadastradas', color=0x7c5cff)
        for fam in familias[:25]:
            embed.add_field(name=fam.name, value=f'Slug: `{fam.slug}` • Membros: **{_family_member_count(interaction.guild_id, fam.slug)}**', inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @family_group.command(name='info', description='Mostra informações de uma família')
    @app_commands.describe(familia='Nome ou slug da família')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_info(interaction: discord.Interaction, familia: str):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        leader = interaction.guild.get_member(fam.leader_id)
        members = _family_members(interaction.guild_id, fam.slug)
        mentions = []
        for uid in members[:25]:
            m = interaction.guild.get_member(uid)
            mentions.append(m.mention if m else str(uid))
        embed = discord.Embed(title=f'Família • {fam.name}', color=int(fam.color[1:], 16))
        embed.add_field(name='Slug', value=f'`{fam.slug}`', inline=True)
        embed.add_field(name='Líder', value=leader.mention if leader else str(fam.leader_id), inline=True)
        embed.add_field(name='Membros', value=str(len(members)), inline=True)
        embed.add_field(name='Lista', value=' '.join(mentions) if mentions else 'Nenhum', inline=False)
        if fam.image_url:
            embed.set_thumbnail(url=fam.image_url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @family_group.command(name='convidar', description='Envia convite de família na DM do usuário')
    @app_commands.describe(familia='Família', membro='Membro a convidar')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_convidar(interaction: discord.Interaction, familia: str, membro: discord.Member):
        try:
            fam = await _family_send_invite(interaction, familia, membro)
        except (ValueError, PermissionError) as e:
            return await interaction.response.send_message(f'❌ {e}', ephemeral=True)
        await interaction.response.send_message(f'✅ Convite enviado para {membro.mention} na família **{fam.name}**.', ephemeral=True)

    @family_group.command(name='remover', description='Remove um membro da família')
    @app_commands.describe(familia='Família', membro='Membro a remover')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_remover(interaction: discord.Interaction, familia: str, membro: discord.Member):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, fam):
            return await interaction.response.send_message('❌ Você não pode gerenciar essa família.', ephemeral=True)
        if membro.id == fam.leader_id:
            return await interaction.response.send_message('❌ Não é possível remover o líder com esse comando.', ephemeral=True)
        current = _family_current_of_user(interaction.guild_id, membro.id)
        if not current or current.slug != fam.slug:
            return await interaction.response.send_message('❌ Esse membro não está nessa família.', ephemeral=True)
        await _family_remove_member(interaction.guild, fam, membro)
        await _send_family_log(interaction.guild, member=interaction.user, title='Membro removido da família', reason=f'Família: {fam.name}', action=f'Membro removido: {membro}', message_text='', family=fam, target=membro, audit_action='member_removed')
        await interaction.response.send_message(f'✅ {membro.mention} foi removido da família **{fam.name}**.', ephemeral=True)

    @family_group.command(name='renomear', description='Renomeia uma família')
    @app_commands.describe(familia='Família', novo_nome='Novo nome')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_renomear(interaction: discord.Interaction, familia: str, novo_nome: str):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, fam):
            return await interaction.response.send_message('❌ Você não pode gerenciar essa família.', ephemeral=True)
        novo_slug = _family_slug(novo_nome)
        existing = _family_get(interaction.guild_id, novo_slug)
        if existing and existing.slug != fam.slug:
            return await interaction.response.send_message('❌ Já existe outra família com esse nome.', ephemeral=True)
        old_name, old_slug = fam.name, fam.slug
        def _tx(conn, cur):
            cur.execute(f'UPDATE {PG_TABLE_FAMILIES} SET slug = %s, name = %s, updated_at = NOW() WHERE guild_id = %s AND slug = %s', (novo_slug, novo_nome, interaction.guild_id, old_slug))
            cur.execute(f'UPDATE {PG_TABLE_FAMILY_MEMBERS} SET family_slug = %s WHERE guild_id = %s AND family_slug = %s', (novo_slug, interaction.guild_id, old_slug))
            cur.execute(f'UPDATE {PG_TABLE_FAMILY_INVITES} SET family_slug = %s WHERE guild_id = %s AND family_slug = %s', (novo_slug, interaction.guild_id, old_slug))
        _pg_transaction(_tx)
        if fam.role_id:
            role = interaction.guild.get_role(fam.role_id)
            if role:
                try:
                    await role.edit(name=novo_nome, reason=f'Família renomeada por {interaction.user}')
                except Exception:
                    traceback.print_exc()
        fam2 = _family_get(interaction.guild_id, novo_slug)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família renomeada', reason='Nome alterado', action=f'{old_name} → {novo_nome}', message_text='', family=fam2, audit_action='family_renamed')
        await interaction.response.send_message(f'✅ Família renomeada para **{novo_nome}**.', ephemeral=True)

    @family_group.command(name='cor', description='Altera a cor da família')
    @app_commands.describe(familia='Família', cor='Nova cor (#RRGGBB)')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_cor(interaction: discord.Interaction, familia: str, cor: str):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, fam):
            return await interaction.response.send_message('❌ Você não pode gerenciar essa família.', ephemeral=True)
        try:
            discord_color, hex_color = _family_parse_color(cor)
        except ValueError as e:
            return await interaction.response.send_message(f'❌ {e}', ephemeral=True)
        _pg_exec(f'UPDATE {PG_TABLE_FAMILIES} SET color = %s, updated_at = NOW() WHERE guild_id = %s AND slug = %s', (hex_color, interaction.guild_id, fam.slug))
        if fam.role_id:
            role = interaction.guild.get_role(fam.role_id)
            if role:
                try:
                    await role.edit(colour=discord_color, reason=f'Cor da família alterada por {interaction.user}')
                except Exception:
                    traceback.print_exc()
        fam2 = _family_get(interaction.guild_id, fam.slug)
        await _send_family_log(interaction.guild, member=interaction.user, title='Cor de família alterada', reason=f'Família: {fam.name}', action=f'Nova cor: {hex_color}', message_text='', family=fam2, audit_action='family_color_updated')
        await interaction.response.send_message(f'✅ Cor da família **{fam.name}** alterada para `{hex_color}`.', ephemeral=True)

    @family_group.command(name='foto', description='Altera a foto da família')
    @app_commands.describe(familia='Família', url='URL da foto')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_foto(interaction: discord.Interaction, familia: str, url: str):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, fam):
            return await interaction.response.send_message('❌ Você não pode gerenciar essa família.', ephemeral=True)
        if not FAMILY_URL_RE.match(url):
            return await interaction.response.send_message('❌ Envie uma URL válida iniciando com http:// ou https://', ephemeral=True)
        _pg_exec(f'UPDATE {PG_TABLE_FAMILIES} SET image_url = %s, updated_at = NOW() WHERE guild_id = %s AND slug = %s', (url.strip(), interaction.guild_id, fam.slug))
        fam2 = _family_get(interaction.guild_id, fam.slug)
        await _send_family_log(interaction.guild, member=interaction.user, title='Foto de família alterada', reason=f'Família: {fam.name}', action='Foto atualizada', message_text=url.strip(), family=fam2, audit_action='family_photo_updated')
        await interaction.response.send_message(f'✅ Foto da família **{fam.name}** atualizada.', ephemeral=True)

    @family_group.command(name='deletar', description='Apaga uma família')
    @app_commands.describe(familia='Família')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_deletar(interaction: discord.Interaction, familia: str):
        fam = _family_get(interaction.guild_id, familia)
        if not fam:
            return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
        if not _family_can_manage(interaction, fam):
            return await interaction.response.send_message('❌ Você não pode gerenciar essa família.', ephemeral=True)
        role = interaction.guild.get_role(fam.role_id) if fam.role_id else None
        members = _family_members(interaction.guild_id, fam.slug)
        def _tx(conn, cur):
            cur.execute(f'DELETE FROM {PG_TABLE_FAMILY_MEMBERS} WHERE guild_id = %s AND family_slug = %s', (interaction.guild_id, fam.slug))
            cur.execute(f'DELETE FROM {PG_TABLE_FAMILY_INVITES} WHERE guild_id = %s AND family_slug = %s', (interaction.guild_id, fam.slug))
            cur.execute(f'DELETE FROM {PG_TABLE_FAMILIES} WHERE guild_id = %s AND slug = %s', (interaction.guild_id, fam.slug))
        _pg_transaction(_tx)
        for uid in members:
            member = interaction.guild.get_member(uid)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f'Família {fam.name} apagada')
                except Exception:
                    traceback.print_exc()
        if role:
            try:
                await role.delete(reason=f'Família {fam.name} deletada por {interaction.user}')
            except Exception:
                traceback.print_exc()
        await _send_family_log(interaction.guild, member=interaction.user, title='Família deletada', reason=f'Família removida: {fam.name}', action='Cargo e registro apagados', message_text='', family=fam, audit_action='family_deleted')
        await interaction.response.send_message(f'✅ Família **{fam.name}** deletada com sucesso.', ephemeral=True)

    @family_group.command(name='historico', description='Mostra o histórico recente das famílias')
    @app_commands.describe(familia='Família (opcional)', limite='Quantidade de registros')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_historico(interaction: discord.Interaction, familia: str = None, limite: app_commands.Range[int, 1, 20] = 10):
        params = [interaction.guild_id]
        where = 'WHERE guild_id = %s'
        fam = None
        if familia:
            fam = _family_get(interaction.guild_id, familia)
            if not fam:
                return await interaction.response.send_message('❌ Família não encontrada.', ephemeral=True)
            where += ' AND family_slug = %s'
            params.append(fam.slug)
        params.append(int(limite))
        rows = _pg_exec(f'''SELECT action, actor_name, target_name, family_name, details, created_at FROM {PG_TABLE_FAMILY_AUDIT} {where} ORDER BY created_at DESC LIMIT %s''', tuple(params), fetch=True) or []
        if not rows:
            return await interaction.response.send_message('📭 Nenhum registro encontrado.', ephemeral=True)
        embed = discord.Embed(title='Histórico de famílias', color=0x2ecc71)
        if fam:
            embed.description = f'Família: **{fam.name}**'
        for action_i, actor_name, target_name, family_name, details, created_at in rows:
            line = f'**Ação:** `{action_i}`\n**Família:** {family_name or "-"}\n**Autor:** {actor_name or "-"}'
            if target_name:
                line += f'\n**Alvo:** {target_name}'
            if details:
                line += f'\n**Detalhes:** {details[:200]}'
            embed.add_field(name=str(created_at), value=line[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    try:
        if guild_obj:
            bot.tree.add_command(family_group, guild=guild_obj)
        else:
            bot.tree.add_command(family_group)
    except Exception:
        pass

    async def _family_on_ready_sync():
        try:
            if postgres_family_init():
                _pg_setup_family_tables()
                migrated = _pg_migrate_legacy_json_if_needed()
                print(f'[FAMILIAS PG] Sistema pronto. Migração executada: {migrated} família(s).')
            if guild_obj:
                synced = await bot.tree.sync(guild=guild_obj)
            else:
                synced = await bot.tree.sync()
            print(f'[FAMILIAS PG] Slash commands sincronizados: {len(synced)}')
        except Exception as e:
            print('[FAMILIAS PG] Falha ao inicializar/sincronizar:', e)
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


TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:
    print('❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN.')
