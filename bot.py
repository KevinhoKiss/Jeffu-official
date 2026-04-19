import discord
from discord.ext import commands
import os, re, json, time, asyncio, traceback, unicodedata
from collections import defaultdict, deque
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
LOG_CHANNEL_ID = 1495200091974271209
DONO_ID = 766709835701682208
ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"
REACTIONS_FILE = "reactions_rules.json"
BAN_AO_DETECTAR_CONVITE = True
AVISAR_POR_DM_ANTES_DO_BAN = True
MENSAGEM_DM_BAN = (
    "⚠️ Você foi banido automaticamente por enviar convite/propaganda no servidor.\n"
    "Se acreditar que foi um engano, entre em contato com a staff."
)
COOLDOWN_INTENT_SECONDS = 3
COOLDOWN_USER_INTENT_SECONDS = 3
CONTEXT_MAX_AGE_SECONDS = 180
INVITE_REGEX = re.compile(r"(discord(?:\.gg|\.com/invite|app\.com/invite)/[A-Za-z0-9\-]+)", re.IGNORECASE)
BAD_WORDS_PATTERN = re.compile(r"\b(?:cala boca|calaboca|clbc|cbc|fica quieto|quieto|se aquieta)\b(?:.*(?:jeffu|<@!?\d+>))?", re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ==================== LOG VISUAL ====================
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
CHARACTER_ASSET_FILES = ('decor_character.png', '1ONXu.jpg')
_character_asset_cache = None

# Tamanhos de fonte (fácil de alterar)
LOG_BADGE_FONT_SIZE = 42
LOG_TITLE_FONT_SIZE = 72
LOG_SUBTITLE_FONT_SIZE = 50
LOG_BODY_FONT_SIZE = 98
LOG_LABEL_FONT_SIZE = 94
LOG_SMALL_FONT_SIZE = 96
LOG_LINE_HEIGHT = 26
LOG_LABEL_WIDTH = 100


def _agora_brasil_str(fmt: str = "%d/%m/%Y %H:%M"):
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime(fmt)


def _font_paths(bold=False):
    return [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]


def _get_font(size: int, bold: bool = False):
    for path in _font_paths(bold):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
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
            candidate = f"{current} {word}"
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
            q.append((x, 0)); bg[0][x] = True
        if is_light_bg(x, h - 1) and not bg[h - 1][x]:
            q.append((x, h - 1)); bg[h - 1][x] = True
    for y in range(h):
        if is_light_bg(0, y) and not bg[y][0]:
            q.append((0, y)); bg[y][0] = True
        if is_light_bg(w - 1, y) and not bg[y][w - 1]:
            q.append((w - 1, y)); bg[y][w - 1] = True
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
        c = (int(tr + (br - tr) * t), int(tg + (bg - tg) * t), int(tb + (bb - tb) * t))
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
    character = _load_bottom_character()
    if character is not None:
        target_h = 210
        scale = target_h / max(1, character.height)
        target_w = max(1, int(character.width * scale))
        character = character.resize((target_w, target_h), Image.LANCZOS)
        shadow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.ellipse((16, h - 86, 16 + min(target_w, 150), h - 46), fill=(0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        canvas_rgba = canvas.convert('RGBA')
        canvas_rgba = Image.alpha_composite(canvas_rgba, shadow)
        canvas_rgba.paste(character, (14, h - 78 - target_h + 8), character)
        canvas.paste(canvas_rgba.convert('RGB'))


def _paste_glow(canvas, box, color, blur=24, alpha=115, radius=28):
    glow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(box, radius=radius, fill=(*color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(canvas, glow)


async def _build_log_image(guild, member=None, title='Log', channel_name='', reason='', action='', message_text='', accent=None):
    width, height = 1180, 940
    accent = _accent_for_title(title, accent)
    badge_font = _get_font(LOG_BADGE_FONT_SIZE, True)
    hero_font = _get_font(LOG_TITLE_FONT_SIZE, True)
    sub_font = _get_font(LOG_SUBTITLE_FONT_SIZE, False)
    body_font = _get_font(LOG_BODY_FONT_SIZE, False)
    label_font = _get_font(LOG_LABEL_FONT_SIZE, True)
    small_font = _get_font(LOG_SMALL_FONT_SIZE, False)
    name_text = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
    lines = [('Nome', name_text), ('Chat', channel_name or 'sistema'), ('Motivo', reason or 'não informado'), ('Ação', action or 'não informada'), ('Mensagem', message_text or 'sem mensagem')]
    card_w = 980
    card_x = (width - card_w) // 2
    card_y = 96
    avatar_size = 170
    avatar_y = card_y + 22
    pill_top = avatar_y + avatar_size + 14
    sub_top = pill_top + 56
    dummy = Image.new('RGB', (width, height), LOG_IMAGE_BG)
    dummy_draw = ImageDraw.Draw(dummy)
    details_x1, details_x2 = card_x + 34, card_x + card_w - 34
    body_max_w = details_x2 - details_x1 - 300
    rendered = []
    for label, value in lines:
        wrapped = _wrap_text(dummy_draw, value, body_font, body_max_w) or ['']
        rendered.append((label, wrapped[:3 if label == 'Mensagem' else 2]))
    line_h = LOG_LINE_HEIGHT
    detail_rows = sum(len(v) for _, v in rendered)
    details_y1 = sub_top + 72
    content_h = 80 + detail_rows * line_h + 28
    details_y2 = details_y1 + content_h
    card_h = max(560, (details_y2 - card_y) + 40)
    canvas = Image.new('RGB', (width, height), LOG_IMAGE_BG)
    _draw_background(canvas)
    canvas = canvas.convert('RGBA')
    canvas = _paste_glow(canvas, (card_x - 8, card_y - 8, card_x + card_w + 8, card_y + card_h + 8), accent, blur=30, alpha=82, radius=42)
    shadow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    sh_draw = ImageDraw.Draw(shadow)
    sh_draw.rounded_rectangle((card_x + 10, card_y + 16, card_x + card_w + 10, card_y + card_h + 16), radius=40, fill=LOG_IMAGE_SHADOW)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas = Image.alpha_composite(canvas, shadow)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), radius=40, fill=LOG_IMAGE_CARD, outline=LOG_IMAGE_CARD_BORDER, width=4)
    draw.rounded_rectangle((card_x + 8, card_y + 8, card_x + card_w - 8, card_y + card_h - 8), radius=34, outline=LOG_IMAGE_LINE, width=1)
    draw.ellipse((card_x + card_w - 126, card_y - 4, card_x + card_w - 34, card_y + 46), fill=accent)
    badge_text = (guild.name if guild else 'Discord')[:18]
    badge_w = max(180, min(300, int(len(badge_text) * 13) + 90))
    badge_x, badge_y = card_x + 22, card_y + 18
    draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + 64), radius=18, fill=LOG_IMAGE_PILL)
    icon_raw = await _guild_icon_bytes(guild)
    if icon_raw:
        icon_img = _crop_circle(Image.open(BytesIO(icon_raw)), 36)
        canvas.paste(icon_img, (badge_x + 12, badge_y + 11), icon_img)
    draw.text((badge_x + 56, badge_y + 10), 'Discord', font=small_font, fill=LOG_IMAGE_MUTED)
    draw.text((badge_x + 56, badge_y + 30), badge_text, font=badge_font, fill=LOG_IMAGE_TEXT)
    avatar_cx = card_x + card_w // 2
    avatar_ring_box = (avatar_cx - avatar_size // 2 - 10, avatar_y - 10, avatar_cx + avatar_size // 2 + 10, avatar_y + avatar_size + 10)
    canvas = _paste_glow(canvas, avatar_ring_box, accent, blur=18, alpha=70, radius=999)
    draw = ImageDraw.Draw(canvas)
    avatar_raw = await _avatar_bytes(member)
    if avatar_raw:
        avatar_img = _crop_circle(Image.open(BytesIO(avatar_raw)), avatar_size)
    else:
        avatar_img = Image.new('RGBA', (avatar_size, avatar_size), (0, 0, 0, 0))
        av_draw = ImageDraw.Draw(avatar_img)
        av_draw.ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=(40, 36, 83), outline=(16, 14, 35), width=4)
        initials = _initials_from_member(member)
        f = _get_font(44, True)
        tw, th = _text_size(av_draw, initials, f)
        av_draw.text(((avatar_size - tw) / 2, (avatar_size - th) / 2 - 2), initials, font=f, fill=(255, 255, 255))
    canvas.paste(avatar_img, (avatar_cx - avatar_size // 2, avatar_y), avatar_img)
    _draw_centered_pill(draw, avatar_cx, pill_top, title or 'Evento registrado', hero_font, LOG_IMAGE_PILL, LOG_IMAGE_TEXT, h_padding=36, v_padding=12, radius=24, max_width=card_w - 140)
    _draw_centered_pill(draw, avatar_cx, sub_top, name_text[:44], sub_font, (48, 48, 60), LOG_IMAGE_MUTED, h_padding=26, v_padding=10, radius=18, max_width=card_w - 160)
    draw.rounded_rectangle((details_x1, details_y1, details_x2, details_y2), radius=24, fill=LOG_IMAGE_CARD_2)
    draw.text((details_x1 + 20, details_y1 + 16), 'Resumo do evento', font=label_font, fill=LOG_IMAGE_MUTED)
    draw.line((details_x1 + 20, details_y1 + 52, details_x2 - 20, details_y1 + 52), fill=LOG_IMAGE_LINE, width=1)
    label_w = LOG_LABEL_WIDTH
    y = details_y1 + 72
    for label, parts in rendered:
        draw.text((details_x1 + 20, y), f'{label}:', font=label_font, fill=LOG_IMAGE_MUTED)
        inner_y = y
        for seg in parts:
            draw.text((details_x1 + 20 + label_w, inner_y), seg, font=body_font, fill=LOG_IMAGE_TEXT)
            inner_y += line_h
        y = inner_y + 4
    stamp = _agora_brasil_str('%d/%m/%Y %H:%M')
    draw.text((card_x + card_w - 180, card_y + card_h - 24), stamp, font=small_font, fill=LOG_IMAGE_MUTED)
    bio = BytesIO()
    canvas.convert('RGB').save(bio, format='PNG')
    bio.seek(0)
    return bio


async def log(guild, member=None, title='Log', channel_name='', reason='', action='', message_text='', accent=LOG_IMAGE_ACCENT):
    try:
        canal = None
        if guild and LOG_CHANNEL_ID:
            canal = guild.get_channel(LOG_CHANNEL_ID)
        if not canal and guild:
            canal = discord.utils.get(guild.text_channels, name='mod-logs')
        if canal:
            perms = canal.permissions_for(guild.me or guild.get_member(bot.user.id)) if guild and bot.user else None
            try:
                if perms and perms.send_messages and perms.attach_files:
                    image_bytes = await _build_log_image(guild, member=member, title=title, channel_name=channel_name, reason=reason, action=action, message_text=message_text, accent=accent)
                    await canal.send(file=discord.File(fp=image_bytes, filename='log.png'))
                else:
                    await canal.send(f"{title} | Nome: {(getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'} | Chat: {channel_name} | Motivo: {reason} | Ação: {action} | Mensagem: {message_text}")
            except Exception:
                traceback.print_exc()
        else:
            print('[LOG]', title, channel_name, reason, action, message_text)
    except Exception:
        traceback.print_exc()

# ==================== DADOS / PERSISTÊNCIA ====================
def carregar_autorizados():
    if not os.path.exists(AUTORIZADOS_FILE):
        return []
    try:
        with open(AUTORIZADOS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        traceback.print_exc(); return []


def salvar_autorizados(lista):
    try:
        with open(AUTORIZADOS_FILE, 'w', encoding='utf-8') as f:
            json.dump(lista, f, indent=2, ensure_ascii=False)
    except Exception:
        traceback.print_exc()

mongo = None
familias_db = None
try:
    MONGO_URI = os.getenv('MONGO_URI')
    if MONGO_URI and MongoClient:
        mongo = MongoClient(MONGO_URI)
        db = mongo['bot']
        familias_db = db['familias']
        print('[MONGO] Conectado ao MongoDB')
except Exception as e:
    print('[MONGO WARN] Não foi possível conectar ao MongoDB:', e)
    mongo = None
    familias_db = None

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
        {"keywords": ["bis", "bisdov", "bisdov3", "chefe"], "emojis": ["<:FBI:1466776866122629252>"]},
        {"keywords": ["theus", "matheus", "god", "matheuz", "matheuss", "matheuzinho"], "emojis": ["<:suspect:1466766825361641634>"]},
        {"keywords": ["lipe", "lipezinho", "lipezito"], "emojis": ["<:808757471270404098:1466605544143061193>"]},
    ]
    for guild in bot.guilds:
        gk = str(guild.id)
        if gk not in _reaction_rules:
            _reaction_rules[gk] = {"by_keyword": []}
        existing = _reaction_rules[gk].get('by_keyword', [])
        for rule in default_rules:
            for kw in rule['keywords']:
                if not any(ex.get('keyword', '').lower() == kw.lower() for ex in existing):
                    existing.append({"channel_id": 0, "keyword": kw, "is_regex": False, "emojis": rule['emojis']})
        _reaction_rules[gk]['by_keyword'] = existing
    _save_reaction_rules()


async def _try_add_reaction(message, emoji):
    try:
        await message.add_reaction(emoji)
        return True
    except Exception:
        try:
            m = re.match(r"<a?:\w+:(\d+)>", emoji)
            if m:
                await message.add_reaction(discord.PartialEmoji(name=None, id=int(m.group(1)), animated=False))
                return True
        except Exception:
            pass
    return False

# ==================== AUTO-REPLY ====================
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
    record = {'intent': intent, 'score': score, 'matched_groups': matched_groups, 'reply': reply, 'ts': time.time(), 'user_id': message.author.id, 'channel_id': message.channel.id}
    CHANNEL_CONTEXT[_channel_key(message)].append(record)
    USER_CHANNEL_CONTEXT[_user_channel_key(message)].append(record)


def _recent_context(records, max_age=CONTEXT_MAX_AGE_SECONDS):
    now = time.time()
    return [r for r in records if now - r.get('ts', 0) <= max_age]


def get_recent_intents(message):
    return {'channel': _recent_context(CHANNEL_CONTEXT[_channel_key(message)]), 'user_channel': _recent_context(USER_CHANNEL_CONTEXT[_user_channel_key(message)])}


def cooldown_status(message, intent):
    now = time.time()
    channel_key = (_channel_key(message), intent)
    user_key = (_user_channel_key(message), intent)
    channel_wait = max(0, COOLDOWN_INTENT_SECONDS - int(now - LAST_INTENT_REPLY_TS.get(channel_key, 0)))
    user_wait = max(0, COOLDOWN_USER_INTENT_SECONDS - int(now - LAST_USER_INTENT_REPLY_TS.get(user_key, 0)))
    return {'blocked': channel_wait > 0 or user_wait > 0, 'channel_wait': channel_wait, 'user_wait': user_wait}


def mark_cooldown(message, intent):
    now = time.time()
    LAST_INTENT_REPLY_TS[(_channel_key(message), intent)] = now
    LAST_USER_INTENT_REPLY_TS[(_user_channel_key(message), intent)] = now

INTENT_RULES = {
    'site_status': {'reply': '🌐 Veja em <#1409296003034644542>', 'threshold': 7, 'groups': [
        {'name': 'entidade', 'terms': ['site', 'sistema', 'app', 'aplicativo', 'plataforma'], 'weight': 3, 'required': True, 'cap': 1},
        {'name': 'problema', 'terms': ['caiu', 'fora do ar', 'offline', 'nao funciona', 'nao abre', 'saiu do ar', 'instavel', 'lento', 'travando', 'bugado', 'carregando', 'erro'], 'weight': 4, 'required': True, 'cap': 2},
    ], 'followup_terms': ['continua', 'ainda', 'voltou', 'normalizou', 'agora', 'ruim', 'instavel', 'lento', 'fora', 'piorou', 'melhorou'], 'negatives': ['site bonito', 'site lindo', 'gostei do site', 'nome do site'], 'context_boost_user': 5, 'context_boost_channel': 3},
    'support': {'reply': '🔐 Para suporte, vá em <#1479642544429076500>', 'threshold': 6, 'groups': [
        {'name': 'assunto', 'terms': ['login', 'senha', 'acesso', 'conta', 'ticket', 'suporte', 'entrar', 'logar', 'acessar'], 'weight': 3, 'required': True, 'cap': 2},
        {'name': 'problema', 'terms': ['nao consigo', 'não consigo', 'esqueci', 'erro', 'ajuda', 'recuperar', 'sem acesso', 'problema', 'abrir', 'como', 'falhou', 'travou', 'nao entra', 'não entra'], 'weight': 3, 'required': True, 'cap': 2},
    ], 'followup_terms': ['continua', 'ainda', 'deu ruim', 'nao foi', 'não foi', 'nao resolveu', 'não resolveu', 'nao deu', 'não deu', 'continua igual'], 'negatives': ['minha senha e forte', 'gostei da senha', 'troquei minha senha e pronto'], 'context_boost_user': 5, 'context_boost_channel': 2},
    'obra_suggestion': {'reply': '📚 Sugestões de obras é em <#1466087941506990171>', 'threshold': 6, 'groups': [
        {'name': 'midia', 'terms': ['obra', 'obras', 'manga', 'manhwa', 'novel', 'titulo', 'titulos'], 'weight': 2, 'required': True, 'cap': 2},
        {'name': 'intencao', 'terms': ['sugestao', 'sugestoes', 'indicar', 'indicacao', 'recomendar', 'recomendacao'], 'weight': 4, 'required': True, 'cap': 2},
    ], 'followup_terms': ['onde sugiro', 'onde mando', 'tem canal', 'posso indicar'], 'negatives': ['obra boa', 'essa obra e ruim', 'terminei a obra'], 'context_boost_user': 4, 'context_boost_channel': 2},
    'missing_chapters': {'reply': '<#1452799882149761144>', 'threshold': 7, 'groups': [
        {'name': 'assunto', 'terms': ['capitulo', 'capitulos'], 'weight': 3, 'required': True, 'cap': 2},
        {'name': 'problema', 'terms': ['faltando', 'faltam', 'sumiu', 'sumiram', 'nao tem', 'incompleto', 'cade', 'onde estao', 'faltou', 'nao veio'], 'weight': 4, 'required': True, 'cap': 2},
    ], 'followup_terms': ['continua', 'ainda', 'sumiu', 'faltando', 'sem', 'nao veio', 'segue faltando'], 'negatives': ['esse capitulo foi bom', 'li o capitulo', 'gostei do capitulo'], 'context_boost_user': 5, 'context_boost_channel': 3},
}
GREETING_REPLIES = {
    'bom dia': 'Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?',
    'boa tarde': 'Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>',
    'boa noite': 'Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>',
}

def humanize_intent(intent):
    return {
        'greeting': 'saudação',
        'support': 'suporte',
        'site_status': 'status do site',
        'obra_suggestion': 'sugestão de obra',
        'missing_chapters': 'capítulos faltando',
    }.get(intent, intent.replace('_', ' '))


def score_intent(normalized_text, intent_name, rule, context):
    score, matched_groups, missing_required = 0, {}, []
    for negative in rule.get('negatives', []):
        if contains_term(normalized_text, negative):
            return {'intent': intent_name, 'score': -999, 'matched_groups': {}, 'missing_required': [], 'context_used': None, 'negative_hit': negative}
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
        boost = int(rule.get('context_boost_user', 0)); score += boost; matched_groups['contexto_usuario_canal'] = {'matches': followup_matches, 'score': boost}; context_used = 'user_channel'
    elif channel_same_intent and followup_matches:
        boost = int(rule.get('context_boost_channel', 0)); score += boost; matched_groups['contexto_canal'] = {'matches': followup_matches, 'score': boost}; context_used = 'channel'
    if missing_required and context_used is None:
        score -= 2 * len(missing_required)
    return {'intent': intent_name, 'score': score, 'matched_groups': matched_groups, 'missing_required': missing_required, 'context_used': context_used, 'negative_hit': None}


def detect_auto_reply(message):
    raw_text = message.content or ''
    text = normalize_text(raw_text)
    if not text:
        return None
    greeting = short_greeting_type(text)
    if greeting:
        return {'intent': 'greeting', 'reply': GREETING_REPLIES[greeting], 'score': 999, 'matched_groups': {'greeting': {'matches': [greeting], 'score': 999}}, 'context_used': None, 'threshold': 1, 'negative_hit': None, 'missing_required': []}
    context = get_recent_intents(message)
    candidates = []
    for intent_name, rule in INTENT_RULES.items():
        scored = score_intent(text, intent_name, rule, context)
        scored['reply'] = rule['reply']; scored['threshold'] = rule['threshold']; candidates.append(scored)
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
        return {'manage_messages': False, 'manage_roles': False, 'ban_members': False, 'send_messages': False, 'attach_files': False, 'read_message_history': False}
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
    if not snapshot.get('manage_messages'): missing.append('Gerenciar Mensagens')
    if not snapshot.get('manage_roles'): missing.append('Gerenciar Cargos')
    if not snapshot.get('ban_members'): missing.append('Banir Membros')
    if not snapshot.get('send_messages'): missing.append('Enviar Mensagens')
    if not snapshot.get('attach_files'): missing.append('Anexar Arquivos')
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
    if not bot_member.guild_permissions.manage_messages: missing.append('Gerenciar Mensagens')
    if not bot_member.guild_permissions.ban_members: missing.append('Banir Membros')
    if missing:
        print('[PERMS WARN] Bot sem permissões globais importantes:', ', '.join(missing))

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

        # reações automáticas
        try:
            guild = message.guild
            if guild:
                gk = str(guild.id)
                rules = _reaction_rules.get(gk, {})
                for kw in rules.get('by_keyword', []):
                    try:
                        ch_id = int(kw.get('channel_id', 0))
                        if ch_id != 0 and ch_id != message.channel.id:
                            continue
                        content = (message.content or '')
                        if not content:
                            continue
                        if kw.get('is_regex'):
                            try:
                                if re.search(kw.get('keyword', ''), content, re.IGNORECASE):
                                    for em in kw.get('emojis', []):
                                        await _try_add_reaction(message, em)
                            except re.error:
                                print('[REACTIONS WARN] Regex inválida para regra:', kw.get('keyword'))
                        else:
                            if kw.get('keyword', '').lower() in content.lower():
                                for em in kw.get('emojis', []):
                                    await _try_add_reaction(message, em)
                    except Exception:
                        pass
        except Exception as e:
            print('[REACTIONS ERROR] ao aplicar regras:', e)
            traceback.print_exc()

        texto = (message.content or '').strip()

        # BAN automático por convite com DM antes e log específico de ban
        if message.guild and BAN_AO_DETECTAR_CONVITE and INVITE_REGEX.search(texto):
            if message.author.guild_permissions.administrator or message.author.id == DONO_ID:
                await bot.process_commands(message)
                return
            delete_ok, delete_note = await try_delete_message(message)
            dm_ok, dm_note = False, ''
            ban_ok, ban_note = False, ''
            member_obj = message.guild.get_member(message.author.id)
            if member_obj:
                dm_ok, dm_note = await try_send_dm_warning(member_obj, message.content or 'sem mensagem', getattr(message.channel, 'name', 'desconhecido'), 'Envio de convite/propaganda detectado')
                ban_ok, ban_note = await try_ban_member(message.guild, member_obj, reason='Ban automático por envio de convite')
            else:
                dm_note = 'membro não encontrado'
                ban_note = 'membro não encontrado'
            action_parts = []
            action_parts.append('mensagem removida' if delete_ok else f'mensagem não removida ({delete_note})')
            action_parts.append(dm_note if dm_note else ('aviso por DM enviado' if dm_ok else 'DM não enviada'))
            action_parts.append(ban_note if ban_note else ('usuário banido' if ban_ok else 'ban não aplicado'))
            await log(
                message.guild,
                member=message.author,
                title='Ban automático',
                channel_name=getattr(message.channel, 'name', 'desconhecido'),
                reason=build_missing_perms_reason(channel_perm_snapshot(message)) if (not delete_ok or not ban_ok) else 'Convite detectado na mensagem',
                action='; '.join([p for p in action_parts if p]),
                message_text=(message.content or 'sem mensagem'),
            )
            return

        # respostas automáticas contextuais
        result = detect_auto_reply(message)
        if result:
            cd = cooldown_status(message, result['intent'])
            if cd['blocked']:
                why_blocked = []
                if cd['channel_wait'] > 0: why_blocked.append(f"cooldown_canal={cd['channel_wait']}s")
                if cd['user_wait'] > 0: why_blocked.append(f"cooldown_usuario={cd['user_wait']}s")
                if message.guild:
                    await log(message.guild, member=message.author, title='Resposta automática bloqueada', channel_name=getattr(message.channel, 'name', 'desconhecido'), reason=f"Cooldown ativo: {' ; '.join(why_blocked)}", action=f"Resposta da intenção {humanize_intent(result['intent'])} não foi enviada", message_text=(message.content or 'sem mensagem'))
            else:
                remember_context(message, result['intent'], result['score'], result['matched_groups'], result['reply'])
                mark_cooldown(message, result['intent'])
                try:
                    await message.reply(result['reply'], mention_author=False)
                except Exception as e:
                    print('[AUTO-REPLY WARN] Falha ao enviar resposta automática:', e)
                    traceback.print_exc()
                if message.guild:
                    await log(message.guild, member=message.author, title='Resposta automática enviada', channel_name=getattr(message.channel, 'name', 'desconhecido'), reason=f"Intenção detectada: {humanize_intent(result['intent'])}", action='Resposta automática enviada com sucesso', message_text=(message.content or 'sem mensagem'))
                return

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
