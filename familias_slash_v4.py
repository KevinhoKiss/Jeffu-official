import discord
from discord import app_commands
import os
import json
import re
import traceback
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FAMILIAS_DB_FILE = 'familias_system.json'
HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')


def setup_family_slash_system_v4(bot, owner_id: int, guild_id: int):
    """
    Sistema de famílias via / (slash commands), sincronizado APENAS no servidor informado.
    V4:
    - Painel com seleção de família + seleção de membro
    - Convite por DM com aceitar/recusar
    - Logs de família por IMAGEM com fundo verde
    - Configuração do canal de log de famílias via /familia setlog
    """
    if getattr(bot, '_family_slash_v4_registered', False):
        return
    bot._family_slash_v4_registered = True

    guild_obj = discord.Object(id=int(guild_id))

    # ==================== STORE ====================
    def _now_iso() -> str:
        return datetime.utcnow().isoformat()

    def _slugify(text: str) -> str:
        text = unicodedata.normalize('NFKD', str(text).strip().lower())
        text = ''.join(c for c in text if not unicodedata.combining(c))
        text = re.sub(r'[^a-z0-9\s_-]', '', text)
        text = re.sub(r'[\s_-]+', '_', text).strip('_')
        return text or 'familia'

    def _load_db() -> dict:
        if not os.path.exists(FAMILIAS_DB_FILE):
            return {}
        try:
            with open(FAMILIAS_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            traceback.print_exc()
            return {}

    def _save_db(data: dict):
        with open(FAMILIAS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _guild_bucket(data: dict, gid: int) -> dict:
        key = str(gid)
        if key not in data:
            data[key] = {
                'families': {},
                'authorized_roles': [],
                'authorized_users': [owner_id],
                'pending_invites': {},
                'family_log_channel_id': None,
            }
        data[key].setdefault('families', {})
        data[key].setdefault('authorized_roles', [])
        data[key].setdefault('authorized_users', [owner_id])
        data[key].setdefault('pending_invites', {})
        data[key].setdefault('family_log_channel_id', None)
        if owner_id not in data[key]['authorized_users']:
            data[key]['authorized_users'].append(owner_id)
        return data[key]

    def _find_family(guild_data: dict, family_name: str) -> Tuple[Optional[str], Optional[dict]]:
        slug = _slugify(family_name)
        families = guild_data.get('families', {})
        if slug in families:
            return slug, families[slug]
        family_name_l = family_name.strip().lower()
        for key, fam in families.items():
            if fam.get('name', '').strip().lower() == family_name_l:
                return key, fam
        return None, None

    def _find_family_by_slug(guild_data: dict, slug: str) -> Tuple[Optional[str], Optional[dict]]:
        fam = guild_data.get('families', {}).get(slug)
        return (slug, fam) if fam else (None, None)

    def _parse_color(value: Optional[str]):
        if not value:
            return discord.Colour(0x7c5cff), '#7c5cff'
        value = value.strip()
        if not HEX_COLOR_RE.match(value):
            raise ValueError('Use uma cor no formato #RRGGBB, ex: #7c5cff')
        if not value.startswith('#'):
            value = '#' + value
        return discord.Colour(int(value[1:], 16)), value.lower()

    # ==================== PERMISSIONS ====================
    def _is_owner(interaction: discord.Interaction) -> bool:
        return interaction.user.id == owner_id

    def _is_authorized_role(interaction: discord.Interaction, guild_data: dict) -> bool:
        authorized_roles = set(int(x) for x in guild_data.get('authorized_roles', []))
        member_roles = getattr(interaction.user, 'roles', [])
        return any(getattr(role, 'id', 0) in authorized_roles for role in member_roles)

    def _is_authorized_user(interaction: discord.Interaction, guild_data: dict) -> bool:
        authorized_users = set(int(x) for x in guild_data.get('authorized_users', []))
        return interaction.user.id in authorized_users or _is_owner(interaction)

    def _has_family_admin(interaction: discord.Interaction, guild_data: dict) -> bool:
        return _is_owner(interaction) or _is_authorized_user(interaction, guild_data) or _is_authorized_role(interaction, guild_data)

    def _can_manage_family(interaction: discord.Interaction, guild_data: dict, family: dict) -> bool:
        return _has_family_admin(interaction, guild_data) or int(family.get('leader_id', 0)) == interaction.user.id

    # ==================== IMAGE LOG ====================
    LOG_BG_TOP = (16, 73, 40)
    LOG_BG = (8, 44, 24)
    LOG_CARD = (18, 60, 36)
    LOG_BORDER = (94, 210, 140)
    LOG_TEXT = (239, 252, 244)
    LOG_MUTED = (178, 226, 194)
    LOG_PILL = (28, 88, 52)
    LOG_LINE = (60, 140, 90)
    LOG_SHADOW = (0, 0, 0, 120)

    def _font_candidates(bold=False):
        base = Path(__file__).resolve().parent
        return [
            base / 'fonts' / ('NotoSans-Bold.ttf' if bold else 'NotoSans-Regular.ttf'),
            Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
            Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
        ]

    def _get_font(size, bold=False):
        for path in _font_candidates(bold):
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

    def _wrap_text(draw, text, font, max_width):
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

    def _draw_gradient(canvas):
        w, h = canvas.size
        px = canvas.load()
        tr, tg, tb = LOG_BG_TOP
        br, bg, bb = LOG_BG
        for y in range(h):
            t = y / max(1, h - 1)
            c = (int(tr + (br - tr) * t), int(tg + (bg - tg) * t), int(tb + (bb - tb) * t))
            for x in range(w):
                px[x, y] = c

    async def _build_family_log_image(guild, member=None, title='Log de Família', reason='', action='', message_text=''):
        width, height = 1000, 620
        canvas = Image.new('RGB', (width, height), LOG_BG)
        _draw_gradient(canvas)
        rgba = canvas.convert('RGBA')
        shadow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        sh = ImageDraw.Draw(shadow)
        sh.rounded_rectangle((78, 78, 922, 538), radius=34, fill=LOG_SHADOW)
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        rgba = Image.alpha_composite(rgba, shadow)
        draw = ImageDraw.Draw(rgba)
        draw.rounded_rectangle((70, 70, 930, 530), radius=34, fill=LOG_CARD, outline=LOG_BORDER, width=3)
        draw.rounded_rectangle((86, 86, 914, 514), radius=26, outline=LOG_LINE, width=1)
        draw.rectangle((0, 560, width, height), fill=(10, 32, 18))
        draw.rectangle((0, 602, width, height), fill=(58, 166, 92))
        title_font = _get_font(34, True)
        label_font = _get_font(22, True)
        body_font = _get_font(26, False)
        small_font = _get_font(16, False)
        badge_font = _get_font(18, True)
        display_name = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
        guild_name = (guild.name if guild else 'Discord')[:26]
        draw.rounded_rectangle((96, 92, 350, 146), radius=16, fill=LOG_PILL)
        draw.text((112, 104), guild_name, font=badge_font, fill=LOG_TEXT)
        draw.text((112, 124), 'Family Logs', font=small_font, fill=LOG_MUTED)
        draw.text((110, 184), title, font=title_font, fill=LOG_TEXT)
        draw.text((110, 236), 'Nome:', font=label_font, fill=LOG_MUTED)
        draw.text((300, 236), display_name, font=body_font, fill=LOG_TEXT)
        reason_lines = _wrap_text(draw, reason or 'não informado', body_font, 560)
        action_lines = _wrap_text(draw, action or 'não informada', body_font, 560)
        msg_lines = _wrap_text(draw, message_text or 'sem mensagem', body_font, 560)
        y = 286
        draw.text((110, y), 'Motivo:', font=label_font, fill=LOG_MUTED)
        iy = y
        for line in reason_lines:
            draw.text((300, iy), line, font=body_font, fill=LOG_TEXT)
            iy += 34
        y = iy + 14
        draw.text((110, y), 'Ação:', font=label_font, fill=LOG_MUTED)
        iy = y
        for line in action_lines:
            draw.text((300, iy), line, font=body_font, fill=LOG_TEXT)
            iy += 34
        y = iy + 14
        draw.text((110, y), 'Mensagem:', font=label_font, fill=LOG_MUTED)
        iy = y
        for line in msg_lines:
            draw.text((300, iy), line, font=body_font, fill=LOG_TEXT)
            iy += 34
        stamp = datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')
        draw.text((760, 500), stamp, font=small_font, fill=LOG_MUTED)
        bio = BytesIO()
        rgba.convert('RGB').save(bio, format='PNG')
        bio.seek(0)
        return bio

    async def _send_family_log(guild: discord.Guild, member=None, title='Log de Família', reason='', action='', message_text=''):
        try:
            data = _load_db()
            guild_data = _guild_bucket(data, guild.id)
            channel_id = guild_data.get('family_log_channel_id')
            if not channel_id:
                print('[FAMILIAS V4 LOG] family_log_channel_id não configurado.')
                return
            canal = guild.get_channel(int(channel_id))
            if canal is None:
                print('[FAMILIAS V4 LOG] canal de log de família não encontrado:', channel_id)
                return
            image_bytes = await _build_family_log_image(guild, member=member, title=title, reason=reason, action=action, message_text=message_text)
            await canal.send(file=discord.File(fp=image_bytes, filename='family_log.png'))
        except Exception:
            traceback.print_exc()

    # ==================== HELPERS DE SISTEMA ====================
    async def _remove_member_from_other_families_and_sync(guild: discord.Guild, guild_data: dict, member: discord.Member, keep_slug: Optional[str] = None):
        return await _remove_member_from_other_families(guild, guild_data, member, keep_slug)

    async def _add_member_to_family(guild: discord.Guild, guild_data: dict, slug: str, family: dict, member: discord.Member):
        await _remove_member_from_other_families_and_sync(guild, guild_data, member, keep_slug=slug)
        if member.id not in family['members']:
            family['members'].append(member.id)
        role = guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role and role not in member.roles:
            await member.add_roles(role, reason=f'Adicionado à família {family.get("name")}')

    # ==================== AUTOCOMPLETE / EMBEDS ====================
    def _family_embed(title: str, description: str, color: int = 0x7c5cff) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    # ==================== DM INVITE VIEW ====================
    class FamilyInviteView(discord.ui.View):
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
            data = _load_db()
            guild_data = _guild_bucket(data, self.guild_id)
            invite = guild_data.get('pending_invites', {}).get(str(self.invited_user_id))
            if not invite:
                return await interaction.response.send_message('Esse convite expirou ou já foi usado.', ephemeral=True)
            if invite.get('family_slug') != self.family_slug:
                return await interaction.response.send_message('Convite inválido.', ephemeral=True)
            slug, family = _find_family_by_slug(guild_data, self.family_slug)
            if not family:
                guild_data['pending_invites'].pop(str(self.invited_user_id), None)
                _save_db(data)
                return await interaction.response.send_message('A família não existe mais.', ephemeral=True)
            guild = bot.get_guild(self.guild_id)
            if guild is None:
                return await interaction.response.send_message('Servidor não encontrado.', ephemeral=True)
            member = guild.get_member(self.invited_user_id)
            if member is None:
                return await interaction.response.send_message('Membro não encontrado no servidor.', ephemeral=True)
            await _add_member_to_family(guild, guild_data, slug, family, member)
            guild_data['pending_invites'].pop(str(self.invited_user_id), None)
            _save_db(data)
            await _send_family_log(guild, member=member, title='Convite de família aceito', reason=f'Família: {family.get("name")}', action=f'Usuário aceitou o convite', message_text='Convite aceito via DM')
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=f'✅ Você entrou na família **{family.get("name")}**.', view=self)

        @discord.ui.button(label='❌ Recusar', style=discord.ButtonStyle.danger)
        async def decline_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.invited_user_id:
                return await interaction.response.send_message('Esse convite não é para você.', ephemeral=True)
            data = _load_db()
            guild_data = _guild_bucket(data, self.guild_id)
            guild_data.get('pending_invites', {}).pop(str(self.invited_user_id), None)
            _save_db(data)
            guild = bot.get_guild(self.guild_id)
            if guild:
                await _send_family_log(guild, member=interaction.user, title='Convite de família recusado', reason='Convite recusado', action=f'Família slug: {self.family_slug}', message_text='Convite recusado via DM')
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content='❌ Você recusou o convite.', view=self)

    async def _send_family_invite(interaction: discord.Interaction, selected_slug: str, member: discord.Member):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        slug, family = _find_family_by_slug(guild_data, selected_slug)
        if not family:
            raise ValueError('Família não encontrada.')
        if not _can_manage_family(interaction, guild_data, family):
            raise PermissionError('Você não pode gerenciar essa família.')
        if member.bot:
            raise ValueError('Não é permitido convidar bots.')
        pending = guild_data.setdefault('pending_invites', {})
        existing = pending.get(str(member.id))
        if existing and existing.get('family_slug') == selected_slug:
            raise ValueError('Já existe um convite pendente para esse membro.')
        pending[str(member.id)] = {
            'family_slug': selected_slug,
            'invited_by': interaction.user.id,
            'created_at': _now_iso()
        }
        _save_db(data)
        view = FamilyInviteView(
            invited_user_id=member.id,
            guild_id_i=interaction.guild_id,
            family_slug=selected_slug,
            invited_by_id=interaction.user.id,
        )
        embed = _family_embed(
            '📨 Convite para família',
            (
                f'Você foi convidado para entrar na família **{family.get("name")}**.\n\n'
                f'**Convidado por:** {interaction.user.mention}\n'
                f'**Família:** {family.get("name")}\n'
                f'**Cor:** `{family.get("color")}`\n\n'
                f'Deseja aceitar?'
            ),
            int(family.get('color', '#7c5cff')[1:], 16)
        )
        if family.get('image_url'):
            embed.set_thumbnail(url=family['image_url'])
        try:
            await member.send(embed=embed, view=view)
        except discord.Forbidden:
            pending.pop(str(member.id), None)
            _save_db(data)
            raise ValueError('Não foi possível enviar a DM. O usuário está com DMs fechadas.')
        await _send_family_log(interaction.guild, member=interaction.user, title='Convite de família enviado', reason=f'Família: {family.get("name")}', action=f'Convite enviado para {member}', message_text='Aguardando resposta na DM')
        return family

    # ==================== PANEL MODALS ====================
    class CreateFamilyModal(discord.ui.Modal, title='Criar Família'):
        family_name = discord.ui.TextInput(label='Nome da família', max_length=60, required=True)
        family_color = discord.ui.TextInput(label='Cor (#RRGGBB)', default='#7c5cff', max_length=7, required=False)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                family, role = await _create_family(interaction, str(self.family_name.value), str(self.family_color.value or '#7c5cff'))
                await interaction.response.send_message(embed=_family_embed('✅ Família criada', f'**Nome:** {family["name"]}\n**Cargo:** {role.mention}\n**Cor:** `{family["color"]}`\n**Líder:** {interaction.user.mention}', int(family['color'][1:], 16)), ephemeral=True)
            except PermissionError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
            except Exception:
                traceback.print_exc()
                await interaction.response.send_message('Falha ao criar a família.', ephemeral=True)

    class RenameFamilyModal(discord.ui.Modal, title='Renomear Família'):
        new_name = discord.ui.TextInput(label='Novo nome', max_length=60, required=True)

        def __init__(self, selected_slug: str):
            super().__init__()
            self.selected_slug = selected_slug

        async def on_submit(self, interaction: discord.Interaction):
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            slug, family = _find_family_by_slug(guild_data, self.selected_slug)
            if not family:
                return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
            if not _can_manage_family(interaction, guild_data, family):
                return await interaction.response.send_message('Você não pode renomear essa família.', ephemeral=True)
            new_slug = _slugify(str(self.new_name.value))
            if new_slug != slug and new_slug in guild_data['families']:
                return await interaction.response.send_message('Já existe outra família com esse nome.', ephemeral=True)
            role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
            if role:
                await role.edit(name=str(self.new_name.value), reason=f'Renomeada por {interaction.user}')
            old_name = family['name']
            family['name'] = str(self.new_name.value)
            if new_slug != slug:
                guild_data['families'].pop(slug)
                guild_data['families'][new_slug] = family
            _save_db(data)
            await _send_family_log(interaction.guild, member=interaction.user, title='Família renomeada', reason='Nome alterado', action=f'{old_name} → {self.new_name.value}', message_text='')
            await interaction.response.send_message(f'✅ Família renomeada para **{self.new_name.value}**.', ephemeral=True)

    class ColorFamilyModal(discord.ui.Modal, title='Alterar Cor da Família'):
        new_color = discord.ui.TextInput(label='Nova cor (#RRGGBB)', default='#7c5cff', max_length=7, required=True)

        def __init__(self, selected_slug: str):
            super().__init__()
            self.selected_slug = selected_slug

        async def on_submit(self, interaction: discord.Interaction):
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            slug, family = _find_family_by_slug(guild_data, self.selected_slug)
            if not family:
                return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
            if not _can_manage_family(interaction, guild_data, family):
                return await interaction.response.send_message('Você não pode alterar a cor dessa família.', ephemeral=True)
            try:
                discord_color, hex_color = _parse_color(str(self.new_color.value))
            except ValueError as e:
                return await interaction.response.send_message(str(e), ephemeral=True)
            role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
            if role:
                await role.edit(colour=discord_color, reason=f'Cor da família alterada por {interaction.user}')
            family['color'] = hex_color
            _save_db(data)
            await _send_family_log(interaction.guild, member=interaction.user, title='Cor de família alterada', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
            await interaction.response.send_message(f'✅ Cor da família **{family.get("name")}** alterada para `{hex_color}`.', ephemeral=True)

    class PhotoFamilyModal(discord.ui.Modal, title='Alterar Foto da Família'):
        photo_url = discord.ui.TextInput(label='URL da foto', style=discord.TextStyle.paragraph, required=True)

        def __init__(self, selected_slug: str):
            super().__init__()
            self.selected_slug = selected_slug

        async def on_submit(self, interaction: discord.Interaction):
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            slug, family = _find_family_by_slug(guild_data, self.selected_slug)
            if not family:
                return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
            if not _can_manage_family(interaction, guild_data, family):
                return await interaction.response.send_message('Você não pode alterar a foto dessa família.', ephemeral=True)
            url = str(self.photo_url.value).strip()
            family['image_url'] = url
            _save_db(data)
            await _send_family_log(interaction.guild, member=interaction.user, title='Foto de família alterada', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
            await interaction.response.send_message(f'✅ Foto da família **{family.get("name")}** atualizada.', ephemeral=True)

    class FamilySelect(discord.ui.Select):
        def __init__(self, parent_view: 'FamilyPanelView', interaction: discord.Interaction):
            self.parent_view = parent_view
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            families = guild_data.get('families', {})
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
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            _, family = _find_family_by_slug(guild_data, value)
            if family:
                selected_member = None
                if self.parent_view.selected_member_id:
                    selected_member = interaction.guild.get_member(self.parent_view.selected_member_id)
                embed = _build_family_details_embed(interaction.guild, family, selected_member)
                await interaction.response.edit_message(embed=embed, view=self.parent_view)
            else:
                await interaction.response.send_message('Família não encontrada.', ephemeral=True)

    class MemberUserSelect(discord.ui.UserSelect):
        def __init__(self, parent_view: 'FamilyPanelView'):
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
                data = _load_db()
                guild_data = _guild_bucket(data, interaction.guild_id)
                _, family = _find_family_by_slug(guild_data, self.parent_view.selected_slug)
                if family:
                    embed = _build_family_details_embed(interaction.guild, family, member)
                    return await interaction.response.edit_message(embed=embed, view=self.parent_view)
            await interaction.response.send_message(f'Membro selecionado no painel: {member.mention}', ephemeral=True)

    class FamilyPanelView(discord.ui.View):
        def __init__(self, interaction: discord.Interaction):
            super().__init__(timeout=300)
            self.selected_slug: Optional[str] = None
            self.selected_member_id: Optional[int] = None
            self.add_item(FamilySelect(self, interaction))
            self.add_item(MemberUserSelect(self))

        def _get_selected(self, interaction: discord.Interaction):
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            family = None
            slug = None
            if self.selected_slug:
                slug, family = _find_family_by_slug(guild_data, self.selected_slug)
            member = interaction.guild.get_member(self.selected_member_id) if self.selected_member_id else None
            return data, guild_data, slug, family, member

        @discord.ui.button(label='➕ Criar', style=discord.ButtonStyle.success, row=2)
        async def create_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(CreateFamilyModal())

        @discord.ui.button(label='👁️ Ver', style=discord.ButtonStyle.secondary, row=2)
        async def view_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            embed = _build_family_details_embed(interaction.guild, family, member)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @discord.ui.button(label='✏️ Renomear', style=discord.ButtonStyle.primary, row=2)
        async def rename_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(RenameFamilyModal(slug))

        @discord.ui.button(label='🎨 Cor', style=discord.ButtonStyle.primary, row=2)
        async def recolor_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(ColorFamilyModal(slug))

        @discord.ui.button(label='🖼️ Foto', style=discord.ButtonStyle.primary, row=3)
        async def photo_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(PhotoFamilyModal(slug))

        @discord.ui.button(label='📨 Convidar', style=discord.ButtonStyle.success, row=3)
        async def invite_member_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            if not member:
                return await interaction.response.send_message('Selecione um membro no seletor de usuários primeiro.', ephemeral=True)
            try:
                family = await _send_family_invite(interaction, slug, member)
                embed = _build_family_details_embed(interaction.guild, family, member)
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
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            if not member:
                return await interaction.response.send_message('Selecione um membro no seletor de usuários primeiro.', ephemeral=True)
            try:
                family = await _remove_member_by_panel(interaction, slug, member)
                embed = _build_family_details_embed(interaction.guild, family, member)
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
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            families = list(guild_data.get('families', {}).values())
            if not families:
                return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
            families.sort(key=lambda f: len(f.get('members', [])), reverse=True)
            lines = []
            for idx, fam in enumerate(families[:10], start=1):
                lines.append(f"**{idx}.** {fam.get('name')} — {len(fam.get('members', []))} membro(s)")
            await interaction.response.send_message(embed=_family_embed('Ranking de Famílias', '\n'.join(lines)), ephemeral=True)

        @discord.ui.button(label='🗑️ Deletar', style=discord.ButtonStyle.danger, row=4)
        async def delete_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family, member = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            if not _can_manage_family(interaction, guild_data, family):
                return await interaction.response.send_message('Você não pode deletar essa família.', ephemeral=True)
            role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
            if role:
                try:
                    await role.delete(reason=f'Família deletada por {interaction.user}')
                except Exception:
                    traceback.print_exc()
            guild_data['families'].pop(slug, None)
            _save_db(data)
            self.selected_slug = None
            await _send_family_log(interaction.guild, member=interaction.user, title='Família deletada', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
            await interaction.response.send_message(f'🗑️ Família **{family.get("name")}** deletada com sucesso.', ephemeral=True)

        @discord.ui.button(label='❓ Ajuda', style=discord.ButtonStyle.secondary, row=4)
        async def help_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message('Painel V4: selecione a família no primeiro menu e o membro no seletor de usuários. Depois use o botão 📨 Convidar para enviar um convite na DM da pessoa. A pessoa só entra na família se aceitar.', ephemeral=True)

    # ==================== GROUP / SLASH COMMANDS ====================
    family_group = app_commands.Group(name='familia', description='Sistema de famílias do servidor')

    @family_group.command(name='painel', description='Abre o painel interativo de famílias (V4)')
    async def familia_painel(interaction: discord.Interaction):
        embed = _family_embed(
            'Painel de Famílias • V4',
            'Selecione uma família no menu e um membro no seletor de usuários.\n\nUse o botão 📨 Convidar para enviar um convite na DM da pessoa, e o botão ➖ Remover para retirar membros já participantes.',
        )
        await interaction.response.send_message(embed=embed, view=FamilyPanelView(interaction), ephemeral=True)

    @family_group.command(name='setlog', description='Define o canal de logs das famílias')
    @app_commands.describe(canal='Canal onde os logs de famílias serão enviados')
    async def familia_setlog(interaction: discord.Interaction, canal: discord.TextChannel):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        if not _is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode alterar o canal de log das famílias.', ephemeral=True)
        guild_data['family_log_channel_id'] = canal.id
        _save_db(data)
        await interaction.response.send_message(f'✅ Canal de log das famílias definido para {canal.mention}.', ephemeral=True)

    @family_group.command(name='autorizarcargo', description='Autoriza um cargo para gerenciar famílias')
    @app_commands.describe(cargo='Cargo autorizado a criar/gerenciar famílias')
    async def familia_autorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        if not _is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode autorizar cargos.', ephemeral=True)
        if cargo.id in guild_data['authorized_roles']:
            return await interaction.response.send_message('Esse cargo já está autorizado.', ephemeral=True)
        guild_data['authorized_roles'].append(cargo.id)
        _save_db(data)
        await interaction.response.send_message(f'✅ Cargo autorizado para gerenciar famílias: {cargo.mention}', ephemeral=True)

    @family_group.command(name='desautorizarcargo', description='Remove um cargo autorizado')
    @app_commands.describe(cargo='Cargo a remover da lista de autorizados')
    async def familia_desautorizarcargo(interaction: discord.Interaction, cargo: discord.Role):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        if not _is_owner(interaction):
            return await interaction.response.send_message('Apenas o dono configurado pode desautorizar cargos.', ephemeral=True)
        if cargo.id not in guild_data['authorized_roles']:
            return await interaction.response.send_message('Esse cargo não está autorizado.', ephemeral=True)
        guild_data['authorized_roles'].remove(cargo.id)
        _save_db(data)
        await interaction.response.send_message(f'✅ Cargo removido da lista de autorizados: {cargo.mention}', ephemeral=True)

    @family_group.command(name='autorizados', description='Lista cargos autorizados a gerenciar famílias')
    async def familia_autorizados(interaction: discord.Interaction):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        roles = []
        for role_id in guild_data.get('authorized_roles', []):
            role = interaction.guild.get_role(int(role_id))
            roles.append(role.mention if role else f'`{role_id}` (inexistente)')
        desc = '\n'.join(roles) if roles else 'Nenhum cargo autorizado ainda.'
        log_ch = guild_data.get('family_log_channel_id')
        if log_ch:
            canal = interaction.guild.get_channel(int(log_ch))
            desc += f"\n\n**Canal de log:** {canal.mention if canal else f'`{log_ch}`'}"
        await interaction.response.send_message(embed=_family_embed('Configurações de Famílias', desc), ephemeral=True)

    @family_group.command(name='criar', description='Cria uma nova família')
    @app_commands.describe(nome='Nome da família', cor='Cor da família em #RRGGBB')
    async def familia_criar(interaction: discord.Interaction, nome: str, cor: Optional[str] = '#7c5cff'):
        try:
            family, role = await _create_family(interaction, nome, cor or '#7c5cff')
            await _send_family_log(interaction.guild, member=interaction.user, title='Família criada', reason=f'Família {nome} criada', action=f'Cargo criado: {role.name}', message_text=f'Cor: {family["color"]}')
            await interaction.response.send_message(embed=_family_embed('✅ Família criada', f'**Nome:** {nome}\n**Cargo:** {role.mention}\n**Cor:** `{family["color"]}`\n**Líder:** {interaction.user.mention}', int(family['color'][1:], 16)), ephemeral=True)
        except PermissionError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message('Falha ao criar a família.', ephemeral=True)

    @family_group.command(name='listar', description='Lista todas as famílias cadastradas')
    async def familia_listar(interaction: discord.Interaction):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        families = guild_data.get('families', {})
        if not families:
            return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
        lines = []
        for slug, fam in families.items():
            role = interaction.guild.get_role(int(fam.get('role_id', 0))) if fam.get('role_id') else None
            lines.append(f"• **{fam.get('name')}** — {len(fam.get('members', []))} membro(s) — cargo: {role.mention if role else '`não encontrado`'}")
        await interaction.response.send_message(embed=_family_embed('Famílias cadastradas', '\n'.join(lines)), ephemeral=True)

    @family_group.command(name='ver', description='Mostra os detalhes de uma família')
    @app_commands.describe(nome='Nome da família')
    @app_commands.autocomplete(nome=_family_name_autocomplete)
    async def familia_ver(interaction: discord.Interaction, nome: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        _, family = _find_family(guild_data, nome)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        await interaction.response.send_message(embed=_build_family_details_embed(interaction.guild, family), ephemeral=True)

    @family_group.command(name='renomear', description='Renomeia uma família')
    @app_commands.describe(familia='Família atual', novo_nome='Novo nome da família')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_renomear(interaction: discord.Interaction, familia: str, novo_nome: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        slug, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode renomear essa família.', ephemeral=True)
        new_slug = _slugify(novo_nome)
        if new_slug != slug and new_slug in guild_data['families']:
            return await interaction.response.send_message('Já existe outra família com esse nome.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(name=novo_nome, reason=f'Renomeada por {interaction.user}')
        old_name = family['name']
        family['name'] = novo_nome
        if new_slug != slug:
            guild_data['families'].pop(slug)
            guild_data['families'][new_slug] = family
        _save_db(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família renomeada', reason='Nome alterado', action=f'{old_name} → {novo_nome}', message_text='')
        await interaction.response.send_message(f'✅ Família renomeada para **{novo_nome}**.', ephemeral=True)

    @family_group.command(name='cor', description='Altera a cor da família e do cargo')
    @app_commands.describe(familia='Família alvo', cor='Nova cor em #RRGGBB')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_cor(interaction: discord.Interaction, familia: str, cor: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        _, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode alterar a cor dessa família.', ephemeral=True)
        try:
            discord_color, hex_color = _parse_color(cor)
        except ValueError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            await role.edit(colour=discord_color, reason=f'Cor da família alterada por {interaction.user}')
        family['color'] = hex_color
        _save_db(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Cor de família alterada', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
        await interaction.response.send_message(f'✅ Cor da família **{family.get("name")}** alterada para `{hex_color}`.', ephemeral=True)

    @family_group.command(name='foto', description='Altera a foto da família por URL')
    @app_commands.describe(familia='Família alvo', url='URL da nova imagem')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_foto(interaction: discord.Interaction, familia: str, url: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        _, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode alterar a foto dessa família.', ephemeral=True)
        family['image_url'] = url
        _save_db(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Foto de família alterada', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
        await interaction.response.send_message(f'✅ Foto da família **{family.get("name")}** atualizada.', ephemeral=True)

    @family_group.command(name='add', description='Adiciona um membro à família')
    @app_commands.describe(familia='Família alvo', membro='Membro a adicionar')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_add(interaction: discord.Interaction, familia: str, membro: discord.Member):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        slug, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode gerenciar essa família.', ephemeral=True)
        try:
            family = await _send_family_invite(interaction, slug, membro)
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
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        _, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode gerenciar essa família.', ephemeral=True)
        if membro.id not in family.get('members', []):
            return await interaction.response.send_message('Esse membro não está nessa família.', ephemeral=True)
        family['members'].remove(membro.id)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role and role in membro.roles:
            await membro.remove_roles(role, reason=f'Removido da família {family.get("name")}')
        _save_db(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Membro removido da família', reason=f'Família: {family.get("name")}', action=f'Membro removido: {membro}', message_text='')
        await interaction.response.send_message(f'✅ {membro.mention} foi removido da família **{family.get("name")}**.', ephemeral=True)

    @family_group.command(name='deletar', description='Deleta uma família')
    @app_commands.describe(familia='Família alvo')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_deletar(interaction: discord.Interaction, familia: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        slug, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode deletar essa família.', ephemeral=True)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role:
            try:
                await role.delete(reason=f'Família deletada por {interaction.user}')
            except Exception:
                traceback.print_exc()
        guild_data['families'].pop(slug, None)
        _save_db(data)
        await _send_family_log(interaction.guild, member=interaction.user, title='Família deletada', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
        await interaction.response.send_message(f'🗑️ Família **{family.get("name")}** deletada com sucesso.', ephemeral=True)

    @family_group.command(name='sync', description='Sincroniza membros e cargo da família')
    @app_commands.describe(familia='Família alvo')
    @app_commands.autocomplete(familia=_family_name_autocomplete)
    async def familia_sync(interaction: discord.Interaction, familia: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        _, family = _find_family(guild_data, familia)
        if not family:
            return await interaction.response.send_message('Família não encontrada.', ephemeral=True)
        if not _can_manage_family(interaction, guild_data, family):
            return await interaction.response.send_message('Você não pode sincronizar essa família.', ephemeral=True)
        await _sync_family_role_to_members(interaction.guild, family)
        _save_db(data)
        await interaction.response.send_message(f'🔄 Família **{family.get("name")}** sincronizada com o cargo.', ephemeral=True)

    @family_group.command(name='ranking', description='Mostra o ranking de famílias por quantidade de membros')
    async def familia_ranking(interaction: discord.Interaction):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        families = list(guild_data.get('families', {}).values())
        if not families:
            return await interaction.response.send_message('Nenhuma família cadastrada ainda.', ephemeral=True)
        families.sort(key=lambda f: len(f.get('members', [])), reverse=True)
        lines = []
        for idx, fam in enumerate(families[:10], start=1):
            lines.append(f"**{idx}.** {fam.get('name')} — {len(fam.get('members', []))} membro(s)")
        await interaction.response.send_message(embed=_family_embed('Ranking de Famílias', '\n'.join(lines)), ephemeral=True)

    bot.tree.add_command(family_group, guild=guild_obj)

    async def _family_on_ready_sync():
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            print(f'[FAMILIAS V4] Slash commands sincronizados no servidor {guild_id}: {len(synced)} comando(s)')
        except Exception as e:
            print('[FAMILIAS V4] Falha ao sincronizar slash commands:', e)
            traceback.print_exc()

    bot.add_listener(_family_on_ready_sync, 'on_ready')
