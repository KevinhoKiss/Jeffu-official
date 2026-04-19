# bot.py
import discord
from discord.ext import commands
import os
import traceback
import re
import json
import time
import asyncio
import unicodedata
from collections import defaultdict, deque
from io import BytesIO
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None

# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
LOG_CHANNEL_ID = 1495200091974271209

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

DONO_ID = 766709835701682208

CARGOS_AUTORIZADOS = [
    1464361173305655389,
    1409338610854920374,
    1409306638548209826
]

ARQUIVO = "familias.json"
AUTORIZADOS_FILE = "autorizados.json"

convites = {}  # convites temporários: {user_id: {"dono": dono_id, "tempo": timestamp}}

# ==================== CONFIG DE RESPOSTAS CONTEXTUAIS ====================
COOLDOWN_INTENT_SECONDS = 45       # mesmo intent no mesmo canal
COOLDOWN_USER_INTENT_SECONDS = 25  # mesmo intent pelo mesmo usuário no canal
CONTEXT_MAX_AGE_SECONDS = 180      # contexto recente considerado válido

# ==================== LOG ====================
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


def _font_paths(bold: bool = False):
    if bold:
        return [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    return [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]


def _get_font(size: int, bold: bool = False):
    for path in _font_paths(bold):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]


def _text_size(draw: ImageDraw.ImageDraw, text: str, font):
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        return _text_width(draw, text, font), getattr(font, 'size', 20)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int):
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


def _crop_circle(img: Image.Image, size: int = 112) -> Image.Image:
    img = img.convert('RGB').resize((size, size))
    mask = Image.new('L', (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    out.putalpha(mask)
    return out


CHARACTER_ASSET_FILES = ('decor_character.png', '1ONXu.jpg')
_character_asset_cache = None


def _remove_light_background(img: Image.Image) -> Image.Image:
    img = img.convert('RGBA')
    w, h = img.size
    px = img.load()

    def is_light_bg(x: int, y: int) -> bool:
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


def _load_bottom_character() -> Image.Image | None:
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


async def _avatar_bytes(member) -> bytes | None:
    if not member:
        return None
    try:
        return await member.display_avatar.with_size(256).read()
    except Exception:
        return None


async def _guild_icon_bytes(guild) -> bytes | None:
    if not guild or not getattr(guild, 'icon', None):
        return None
    try:
        return await guild.icon.with_size(128).read()
    except Exception:
        return None


def _initials_from_member(member) -> str:
    if not member:
        return '?'
    name = getattr(member, 'display_name', None) or getattr(member, 'name', None) or str(member)
    parts = [p for p in str(name).split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return str(name)[:2].upper() if name else '?'


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
    return pill_h


def _accent_for_title(title: str, accent=None):
    title = (title or '').lower()
    if accent:
        return accent
    if 'invite' in title or 'mute' in title or 'bloque' in title:
        return (141, 98, 255)
    if 'desmute' in title:
        return (84, 198, 147)
    if 'auto-reply' in title:
        return (88, 154, 255)
    return LOG_IMAGE_ACCENT


def _draw_vertical_gradient(canvas: Image.Image, top_color, bottom_color):
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


def _draw_background(canvas: Image.Image):
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
        sx1 = 16
        sy1 = h - 86
        sx2 = 16 + min(target_w, 150)
        sy2 = h - 46
        shadow_draw.ellipse((sx1, sy1, sx2, sy2), fill=(0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        canvas_rgba = canvas.convert('RGBA')
        canvas_rgba = Image.alpha_composite(canvas_rgba, shadow)

        x = 14
        y = h - 78 - target_h + 8
        canvas_rgba.paste(character, (x, y), character)
        canvas.paste(canvas_rgba.convert('RGB'))
    else:
        draw.ellipse((82, h - 128, 168, h - 54), fill=(85, 166, 222))
        draw.ellipse((106, h - 118, 146, h - 82), fill=(130, 204, 240))


def _draw_blob(draw, x, y, fill, outline=None):
    draw.ellipse((x, y + 10, x + 92, y + 60), fill=fill, outline=outline, width=3 if outline else 0)
    draw.ellipse((x + 48, y - 2, x + 110, y + 50), fill=fill, outline=outline, width=3 if outline else 0)
    draw.ellipse((x + 12, y - 10, x + 58, y + 28), fill=fill, outline=outline, width=3 if outline else 0)


def _paste_glow(canvas: Image.Image, box, color, blur=24, alpha=115, radius=28):
    glow = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(box, radius=radius, fill=(*color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(canvas, glow)


async def _build_log_image(guild: discord.Guild, member=None, title: str = 'Log', channel_name: str = '', reason: str = '', action: str = '', message_text: str = '', accent=None) -> BytesIO:
    width, height = 1000, 820
    accent = _accent_for_title(title, accent)

    badge_font = _get_font(22, bold=True)
    hero_font = _get_font(52, bold=True)
    sub_font = _get_font(30, bold=False)
    body_font = _get_font(28, bold=False)
    label_font = _get_font(24, bold=True)
    small_font = _get_font(16, bold=False)

    name_text = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
    lines = [
        ('Nome', name_text),
        ('Chat', channel_name or 'sistema'),
        ('Motivo', reason or 'não informado'),
        ('Ação', action or 'não informada'),
        ('Mensagem', message_text or 'sem mensagem'),
    ]

    card_w = 860
    card_x = (width - card_w) // 2
    card_y = 96
    avatar_size = 170
    avatar_y = card_y + 22
    pill_top = avatar_y + avatar_size + 14
    sub_top = pill_top + 56

    dummy = Image.new('RGB', (width, height), LOG_IMAGE_BG)
    dummy_draw = ImageDraw.Draw(dummy)
    details_x1 = card_x + 34
    details_x2 = card_x + card_w - 34
    body_max_w = details_x2 - details_x1 - 220
    rendered = []
    for label, value in lines:
        wrapped = _wrap_text(dummy_draw, value, body_font, body_max_w)
        if not wrapped:
            wrapped = ['']
        max_lines = 3 if label == 'Mensagem' else 2
        rendered.append((label, wrapped[:max_lines]))
    line_h = 36
    detail_rows = sum(len(v) for _, v in rendered)
    details_y1 = sub_top + 72
    content_h = 80 + detail_rows * line_h + 28
    details_y2 = details_y1 + content_h
    card_h = max(520, (details_y2 - card_y) + 40)

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
    _draw_blob(draw, card_x + card_w - 126, card_y - 14, fill=accent, outline=LOG_IMAGE_CARD_BORDER)

    badge_text = (guild.name if guild else 'Discord')[:18]
    badge_w = max(180, min(300, int(len(badge_text) * 13) + 90))
    badge_h = 64
    badge_x = card_x + 22
    badge_y = card_y + 18
    draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), radius=18, fill=LOG_IMAGE_PILL)
    icon_raw = await _guild_icon_bytes(guild)
    icon_size = 36
    if icon_raw:
        icon_img = _crop_circle(Image.open(BytesIO(icon_raw)), icon_size)
        canvas.paste(icon_img, (badge_x + 12, badge_y + 11), icon_img)
    else:
        draw.ellipse((badge_x + 12, badge_y + 11, badge_x + 46, badge_y + 45), fill=(88, 81, 148))
    draw.text((badge_x + 56, badge_y + 10), 'Discord', font=small_font, fill=LOG_IMAGE_MUTED)
    draw.text((badge_x + 56, badge_y + 30), badge_text, font=badge_font, fill=LOG_IMAGE_TEXT)

    cx = card_x + card_w - 62
    cy = card_y + 36
    draw.line((cx - 12, cy, cx, cy + 12), fill=LOG_IMAGE_MUTED, width=6)
    draw.line((cx + 12, cy, cx, cy + 12), fill=LOG_IMAGE_MUTED, width=6)

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
        f = _get_font(44, bold=True)
        tw, th = _text_size(av_draw, initials, f)
        av_draw.text(((avatar_size - tw) / 2, (avatar_size - th) / 2 - 2), initials, font=f, fill=(255, 255, 255))
    canvas.paste(avatar_img, (avatar_cx - avatar_size // 2, avatar_y), avatar_img)
    draw.ellipse((avatar_cx - avatar_size // 2 - 4, avatar_y - 4, avatar_cx + avatar_size // 2 + 4, avatar_y + avatar_size + 4), outline=(14, 12, 32), width=4)
    draw.ellipse((avatar_cx - avatar_size // 2 - 10, avatar_y - 10, avatar_cx + avatar_size // 2 + 10, avatar_y + avatar_size + 10), outline=accent, width=2)

    _draw_centered_pill(draw, avatar_cx, pill_top, title or 'Evento registrado', hero_font, LOG_IMAGE_PILL, LOG_IMAGE_TEXT, h_padding=36, v_padding=12, radius=24, max_width=card_w - 140)
    _draw_centered_pill(draw, avatar_cx, sub_top, name_text[:44], sub_font, (48, 48, 60), LOG_IMAGE_MUTED, h_padding=26, v_padding=10, radius=18, max_width=card_w - 160)

    draw.rounded_rectangle((details_x1, details_y1, details_x2, details_y2), radius=24, fill=LOG_IMAGE_CARD_2)
    draw.text((details_x1 + 20, details_y1 + 16), 'Resumo do evento', font=label_font, fill=LOG_IMAGE_MUTED)
    draw.line((details_x1 + 20, details_y1 + 52, details_x2 - 20, details_y1 + 52), fill=LOG_IMAGE_LINE, width=1)

    label_w = 145
    y = details_y1 + 72
    for label, parts in rendered:
        draw.text((details_x1 + 20, y), f'{label}:', font=label_font, fill=LOG_IMAGE_MUTED)
        inner_y = y
        for seg in parts:
            draw.text((details_x1 + 20 + label_w, inner_y), seg, font=body_font, fill=LOG_IMAGE_TEXT)
            inner_y += line_h
        y = inner_y + 4

    stamp = datetime.now().strftime('%d/%m/%Y %H:%M')
    draw.text((card_x + card_w - 150, card_y + card_h - 22), stamp, font=small_font, fill=LOG_IMAGE_MUTED)

    bio = BytesIO()
    canvas.convert('RGB').save(bio, format='PNG')
    bio.seek(0)
    return bio


async def log(guild: discord.Guild, member=None, title: str = 'Log', channel_name: str = '', reason: str = '', action: str = '', message_text: str = '', accent=LOG_IMAGE_ACCENT):
    try:
        canal = None
        if guild and LOG_CHANNEL_ID:
            canal = guild.get_channel(LOG_CHANNEL_ID)
        if not canal and guild:
            canal = discord.utils.get(guild.text_channels, name='mod-logs')
        if canal:
            try:
                image_bytes = await _build_log_image(guild, member=member, title=title, channel_name=channel_name, reason=reason, action=action, message_text=message_text, accent=accent)
                arquivo = discord.File(fp=image_bytes, filename='log.png')
                await canal.send(file=arquivo)
            except Exception as img_err:
                print('[LOG WARN] Falha ao gerar/enviar log em imagem:', img_err)
                traceback.print_exc()
                resumo = f"{title} | Nome: {(getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'} | Chat: {channel_name or 'sistema'} | Motivo: {reason} | Ação: {action} | Mensagem: {message_text}"
                await canal.send(resumo)
        else:
            print('[LOG]', title, channel_name, reason, action, message_text)
    except Exception:
        print('[LOG ERROR] Falha ao enviar log:')
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
        bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if not bot_member or not bot_member.guild_permissions.manage_roles:
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
                await log(guild, member=member, title="Usuário mutado", channel_name="sistema", reason="Envio de invite/propaganda", action="Cargo Muted aplicado", message_text="sem mensagem disponível")
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
        bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if not bot_member or not bot_member.guild_permissions.manage_roles:
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
                    await log(guild, member=member, title="Desmute automático", channel_name="sistema", reason="Tempo de 10 minutos expirou", action="Cargo Muted removido", message_text="sem mensagem disponível")
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
            await log(guild, member=member, title="Mute automático", channel_name="sistema", reason="Envio de invite detectado", action="Mute de 10 minutos aplicado", message_text="sem mensagem disponível")
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
            await log(guild, member=member, title="Desmute manual", channel_name="sistema", reason="Comando de moderação", action="Cargo Muted removido", message_text="sem mensagem disponível")
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

    bot_member = ctx.guild.me or (ctx.guild.get_member(bot.user.id) if bot.user else None)
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and bot_member and bot_member.top_role <= muted_role:
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
                    if ex.get("keyword", "").lower() == kw.lower() and set(ex.get("emojis", [])) == set(rule["emojis"]):
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


def normalize_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"<@!?\d+>|<#\d+>|<a?:\w+:\d+>", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_term(text: str, term: str) -> bool:
    text = normalize_text(text)
    term = normalize_text(term)
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def find_matches(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if contains_term(text, term)]


def short_greeting_type(text: str):
    text = normalize_text(text)
    greetings = {
        "bom dia": ["bom dia", "bom dia gente", "bom dia pessoal"],
        "boa tarde": ["boa tarde", "boa tarde gente", "boa tarde pessoal"],
        "boa noite": ["boa noite", "boa noite gente", "boa noite pessoal"],
    }
    for label, variants in greetings.items():
        if text in variants:
            return label
    return None


# ==================== CONTEXTO + COOLDOWN ====================
CHANNEL_CONTEXT = defaultdict(lambda: deque(maxlen=12))
USER_CHANNEL_CONTEXT = defaultdict(lambda: deque(maxlen=8))
LAST_INTENT_REPLY_TS = {}
LAST_USER_INTENT_REPLY_TS = {}


def _channel_key(message: discord.Message):
    guild_id = message.guild.id if message.guild else 0
    return (guild_id, message.channel.id)


def _user_channel_key(message: discord.Message):
    guild_id = message.guild.id if message.guild else 0
    return (guild_id, message.channel.id, message.author.id)


def remember_context(message: discord.Message, intent: str, score: int, matched_groups: dict, reply: str):
    record = {
        "intent": intent,
        "score": score,
        "matched_groups": matched_groups,
        "reply": reply,
        "ts": time.time(),
        "user_id": message.author.id,
        "channel_id": message.channel.id,
    }
    CHANNEL_CONTEXT[_channel_key(message)].append(record)
    USER_CHANNEL_CONTEXT[_user_channel_key(message)].append(record)


def _recent_context(records, max_age: int = CONTEXT_MAX_AGE_SECONDS):
    now = time.time()
    return [r for r in records if now - r.get("ts", 0) <= max_age]


def get_recent_intents(message: discord.Message):
    return {
        "channel": _recent_context(CHANNEL_CONTEXT[_channel_key(message)]),
        "user_channel": _recent_context(USER_CHANNEL_CONTEXT[_user_channel_key(message)]),
    }


def cooldown_status(message: discord.Message, intent: str):
    now = time.time()
    channel_key = (_channel_key(message), intent)
    user_key = (_user_channel_key(message), intent)
    channel_wait = max(0, COOLDOWN_INTENT_SECONDS - int(now - LAST_INTENT_REPLY_TS.get(channel_key, 0)))
    user_wait = max(0, COOLDOWN_USER_INTENT_SECONDS - int(now - LAST_USER_INTENT_REPLY_TS.get(user_key, 0)))
    return {
        "blocked": channel_wait > 0 or user_wait > 0,
        "channel_wait": channel_wait,
        "user_wait": user_wait,
    }


def mark_cooldown(message: discord.Message, intent: str):
    now = time.time()
    LAST_INTENT_REPLY_TS[(_channel_key(message), intent)] = now
    LAST_USER_INTENT_REPLY_TS[(_user_channel_key(message), intent)] = now


# ==================== MOTOR DE INTENÇÃO ====================
INTENT_RULES = {
    "site_status": {
        "reply": "🌐 Veja em <#1409296003034644542>",
        "threshold": 7,
        "groups": [
            {"name": "entidade", "terms": ["site", "sistema", "app", "aplicativo", "plataforma"], "weight": 3, "required": True, "cap": 1},
            {"name": "problema", "terms": ["caiu", "fora do ar", "offline", "nao funciona", "nao abre", "saiu do ar", "instavel", "lento", "travando", "bugado", "carregando", "erro"], "weight": 4, "required": True, "cap": 2},
        ],
        "followup_terms": ["continua", "ainda", "voltou", "normalizou", "agora", "ruim", "instavel", "lento", "fora", "piorou", "melhorou"],
        "negatives": ["site bonito", "site lindo", "gostei do site", "nome do site"],
        "context_boost_user": 5,
        "context_boost_channel": 3,
    },
    "support": {
        "reply": "🔐 Para suporte, vá em <#1479642544429076500>",
        "threshold": 7,
        "groups": [
            {"name": "assunto", "terms": ["login", "senha", "acesso", "conta", "ticket", "suporte", "entrar", "logar", "acessar"], "weight": 3, "required": True, "cap": 2},
            {"name": "problema", "terms": ["nao consigo", "esqueci", "erro", "ajuda", "recuperar", "sem acesso", "problema", "abrir", "como", "falhou", "travou"], "weight": 3, "required": True, "cap": 2},
        ],
        "followup_terms": ["continua", "ainda", "deu ruim", "nao foi", "nao resolveu", "nao deu", "continua igual"],
        "negatives": ["minha senha e forte", "gostei da senha", "troquei minha senha e pronto"],
        "context_boost_user": 5,
        "context_boost_channel": 2,
    },
    "obra_suggestion": {
        "reply": "📚 Sugestões de obras é em <#1466087941506990171>",
        "threshold": 6,
        "groups": [
            {"name": "midia", "terms": ["obra", "obras", "manga", "manhwa", "novel", "titulo", "titulos"], "weight": 2, "required": True, "cap": 2},
            {"name": "intencao", "terms": ["sugestao", "sugestoes", "indicar", "indicacao", "recomendar", "recomendacao"], "weight": 4, "required": True, "cap": 2},
        ],
        "followup_terms": ["onde sugiro", "onde mando", "tem canal", "posso indicar"],
        "negatives": ["obra boa", "essa obra e ruim", "terminei a obra"],
        "context_boost_user": 4,
        "context_boost_channel": 2,
    },
    "missing_chapters": {
        "reply": "<#1452799882149761144>",
        "threshold": 7,
        "groups": [
            {"name": "assunto", "terms": ["capitulo", "capitulos"], "weight": 3, "required": True, "cap": 2},
            {"name": "problema", "terms": ["faltando", "faltam", "sumiu", "sumiram", "nao tem", "incompleto", "cade", "onde estao", "faltou", "nao veio"], "weight": 4, "required": True, "cap": 2},
        ],
        "followup_terms": ["continua", "ainda", "sumiu", "faltando", "sem", "nao veio", "segue faltando"],
        "negatives": ["esse capitulo foi bom", "li o capitulo", "gostei do capitulo"],
        "context_boost_user": 5,
        "context_boost_channel": 3,
    },
}

GREETING_REPLIES = {
    "bom dia": "Bom diia! <:shame:1466765431137370379> como foi sua noite? Dormiu bem?",
    "boa tarde": "Boa tarde! Espero que esteja tendo um bom dia! <:amem:1466774899686117426> Já se hidratou hoje? <:FBI:1466776866122629252>",
    "boa noite": "Boa noite! Como foi seu dia hoje? Espero que esteja tendo uma noite maravilhosa como você! <a:emoji_3:1466600609502204058>",
}


def score_intent(normalized_text: str, intent_name: str, rule: dict, context: dict):
    score = 0
    matched_groups = {}
    missing_required = []

    for negative in rule.get("negatives", []):
        if contains_term(normalized_text, negative):
            return {
                "intent": intent_name,
                "score": -999,
                "matched_groups": {},
                "missing_required": [],
                "context_used": None,
                "negative_hit": negative,
            }

    for group in rule.get("groups", []):
        matches = find_matches(normalized_text, group.get("terms", []))
        if matches:
            capped_len = min(len(matches), int(group.get("cap", len(matches))))
            group_score = capped_len * int(group.get("weight", 1))
            score += group_score
            matched_groups[group["name"]] = {
                "matches": matches,
                "score": group_score,
            }
        elif group.get("required"):
            missing_required.append(group["name"])

    context_used = None
    user_recent = context.get("user_channel", [])
    chan_recent = context.get("channel", [])

    user_same_intent = any(item.get("intent") == intent_name for item in user_recent)
    channel_same_intent = any(item.get("intent") == intent_name for item in chan_recent)
    followup_matches = find_matches(normalized_text, rule.get("followup_terms", []))

    if user_same_intent and followup_matches:
        score += int(rule.get("context_boost_user", 0))
        matched_groups["contexto_usuario_canal"] = {
            "matches": followup_matches,
            "score": int(rule.get("context_boost_user", 0)),
        }
        context_used = "user_channel"
    elif channel_same_intent and followup_matches:
        score += int(rule.get("context_boost_channel", 0))
        matched_groups["contexto_canal"] = {
            "matches": followup_matches,
            "score": int(rule.get("context_boost_channel", 0)),
        }
        context_used = "channel"

    if missing_required and context_used is None:
        score -= 2 * len(missing_required)

    return {
        "intent": intent_name,
        "score": score,
        "matched_groups": matched_groups,
        "missing_required": missing_required,
        "context_used": context_used,
        "negative_hit": None,
    }


def detect_auto_reply(message: discord.Message):
    raw_text = message.content or ""
    text = normalize_text(raw_text)
    if not text:
        return None

    greeting = short_greeting_type(text)
    if greeting:
        return {
            "intent": "greeting",
            "reply": GREETING_REPLIES[greeting],
            "score": 999,
            "matched_groups": {"greeting": {"matches": [greeting], "score": 999}},
            "context_used": None,
            "threshold": 1,
            "negative_hit": None,
            "missing_required": [],
        }

    context = get_recent_intents(message)
    candidates = []
    for intent_name, rule in INTENT_RULES.items():
        scored = score_intent(text, intent_name, rule, context)
        scored["reply"] = rule["reply"]
        scored["threshold"] = rule["threshold"]
        candidates.append(scored)

    candidates = [c for c in candidates if c["score"] > -999]
    if not candidates:
        return None

    best = max(candidates, key=lambda x: x["score"])
    if best["score"] < best["threshold"]:
        return None
    return best


def humanize_intent(intent: str) -> str:
    mapping = {
        'greeting': 'saudação',
        'support': 'suporte',
        'site_status': 'status do site',
        'obra_suggestion': 'sugestão de obra',
        'missing_chapters': 'capítulos faltando',
    }
    return mapping.get(intent, intent.replace('_', ' '))


def explain_reason(result: dict) -> str:
    parts = [f"intent={result['intent']}", f"score={result['score']}/{result.get('threshold', '?')}"]
    if result.get("context_used"):
        parts.append(f"contexto={result['context_used']}")
    if result.get("missing_required"):
        parts.append(f"faltando={','.join(result['missing_required'])}")
    if result.get("negative_hit"):
        parts.append(f"negativo={result['negative_hit']}")

    groups_desc = []
    for group_name, data in result.get("matched_groups", {}).items():
        groups_desc.append(f"{group_name}: {', '.join(data.get('matches', []))} (+{data.get('score', 0)})")
    if groups_desc:
        parts.append("grupos=[" + " | ".join(groups_desc) + "]")
    return " ; ".join(parts)

# ==================== EVENTOS E MODERAÇÃO (ÚNICO on_message) ====================
INVITE_REGEX = re.compile(r"(discord(?:\.gg|\.com\/invite|app\.com\/invite)\/[A-Za-z0-9\-]+)", re.IGNORECASE)

BAD_WORDS_PATTERN = re.compile(
    r"\b(?:cala boca|calaboca|clbc|cbc|fica quieto|quieto|se aquieta)\b(?:.*(?:jeffu|<@!?\d+>))?",
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

        # BLOQUEIO DE INVITES (10m automático)
        if message.guild and INVITE_REGEX.search(texto):
            if message.guild and (message.author.guild_permissions.administrator or message.author.id == DONO_ID):
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
                    await log(message.guild, member=message.author, title="Convite bloqueado", channel_name=getattr(message.channel, "name", "desconhecido"), reason="Convite detectado na mensagem", action="Mensagem removida e mute de 10 minutos aplicado", message_text=(message.content or "sem mensagem"))
            except Exception as e:
                print("[BLOQUEIO WARN] Erro ao processar invite:", e)
                traceback.print_exc()
            return

        # RESPOSTAS AUTOMÁTICAS COM PONTUAÇÃO + CONTEXTO + COOLDOWN + LOGS
        result = detect_auto_reply(message)
        if result:
            cd = cooldown_status(message, result["intent"])
            if cd["blocked"]:
                why_blocked = []
                if cd["channel_wait"] > 0:
                    why_blocked.append(f"cooldown_canal={cd['channel_wait']}s")
                if cd["user_wait"] > 0:
                    why_blocked.append(f"cooldown_usuario={cd['user_wait']}s")
                if message.guild:
                    await log(message.guild, member=message.author, title="Resposta automática bloqueada", channel_name=getattr(message.channel, "name", "desconhecido"), reason=f"Cooldown ativo: {' ; '.join(why_blocked)}", action=f"Resposta da intenção {humanize_intent(result['intent'])} não foi enviada", message_text=(message.content or "sem mensagem"))
            else:
                remember_context(message, result["intent"], result["score"], result["matched_groups"], result["reply"])
                mark_cooldown(message, result["intent"])
                try:
                    await message.reply(result["reply"], mention_author=False)
                except Exception as e:
                    print("[AUTO-REPLY WARN] Falha ao enviar resposta automática:", e)
                    traceback.print_exc()
                if message.guild:
                    await log(message.guild, member=message.author, title="Resposta automática enviada", channel_name=getattr(message.channel, "name", "desconhecido"), reason=f"Intenção detectada: {humanize_intent(result['intent'])}", action="Resposta automática enviada com sucesso", message_text=(message.content or "sem mensagem"))
                return

        # Interações dirigidas ao bot (DM ou menção)
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = bot.user in message.mentions if bot.user else False
        should_respond_personal = is_dm or mentions_bot

        if should_respond_personal:
            # agradecimentos e "te amo" exigem menção/substring 'jeffu'
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
                                    await log(guild, member=member, title="Desmute pós-restart", channel_name="sistema", reason="Tempo expirado durante reinício", action="Cargo Muted removido", message_text="sem mensagem disponível")
                                except Exception:
                                    pass
                    try:
                        del _active_mutes[guild_key][member_id]
                        if not _active_mutes[guild_key]:
                            del _active_mutes[guild_key]
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
