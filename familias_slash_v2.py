import discord
from discord import app_commands
import os
import json
import re
import traceback
import unicodedata
from datetime import datetime
from typing import Optional, Tuple

FAMILIAS_DB_FILE = 'familias_system.json'
HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')


def setup_family_slash_system_v2(bot, owner_id: int, guild_id: int, log_func=None):
    """
    Sistema de famílias via / (slash commands), sincronizado APENAS no servidor informado.
    V2: painel com seleção de família + botões de criar, ver, renomear, mudar cor, foto,
    ranking e deletar.
    """
    if getattr(bot, '_family_slash_v2_registered', False):
        return
    bot._family_slash_v2_registered = True

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
            }
        data[key].setdefault('families', {})
        data[key].setdefault('authorized_roles', [])
        data[key].setdefault('authorized_users', [owner_id])
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

    async def _safe_log(guild, member=None, title='Log', channel_name='', reason='', action='', message_text=''):
        if callable(log_func):
            try:
                await log_func(
                    guild,
                    member=member,
                    title=title,
                    channel_name=channel_name,
                    reason=reason,
                    action=action,
                    message_text=message_text,
                )
            except Exception:
                traceback.print_exc()

    async def _remove_member_from_other_families(guild: discord.Guild, guild_data: dict, member: discord.Member, keep_slug: Optional[str] = None):
        changed = False
        for slug, fam in list(guild_data.get('families', {}).items()):
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

    async def _sync_family_role_to_members(guild: discord.Guild, family: dict):
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

    async def _family_name_autocomplete(interaction: discord.Interaction, current: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        families = guild_data.get('families', {})
        current_l = current.lower().strip()
        out = []
        for slug, fam in families.items():
            name = fam.get('name', slug)
            if not current_l or current_l in name.lower():
                out.append(app_commands.Choice(name=name[:100], value=name[:100]))
            if len(out) >= 25:
                break
        return out

    def _family_embed(title: str, description: str, color: int = 0x7c5cff) -> discord.Embed:
        return discord.Embed(title=title, description=description, color=color)

    def _build_family_details_embed(guild: discord.Guild, family: dict) -> discord.Embed:
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
        embed = _family_embed(f"Família • {family.get('name')}", desc, int(family.get('color', '#7c5cff')[1:], 16))
        if family.get('image_url'):
            embed.set_thumbnail(url=family['image_url'])
        return embed

    # ==================== CORE OPS ====================
    async def _create_family(interaction: discord.Interaction, name: str, color_str: str):
        data = _load_db()
        guild_data = _guild_bucket(data, interaction.guild_id)
        if not _has_family_admin(interaction, guild_data):
            raise PermissionError('Você não tem permissão para criar famílias.')
        slug = _slugify(name)
        if slug in guild_data['families']:
            raise ValueError('Já existe uma família com esse nome.')
        discord_color, hex_color = _parse_color(color_str)
        role = await interaction.guild.create_role(name=name, colour=discord_color, reason=f'Família criada por {interaction.user}')
        family = {
            'name': name,
            'role_id': role.id,
            'color': hex_color,
            'image_url': '',
            'members': [interaction.user.id],
            'leader_id': interaction.user.id,
            'created_by': interaction.user.id,
            'created_at': _now_iso(),
        }
        guild_data['families'][slug] = family
        if isinstance(interaction.user, discord.Member):
            await _remove_member_from_other_families(interaction.guild, guild_data, interaction.user, keep_slug=slug)
            try:
                await interaction.user.add_roles(role, reason='Criador da família')
            except Exception:
                traceback.print_exc()
        _save_db(data)
        await _safe_log(interaction.guild, member=interaction.user, title='Família criada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família {name} criada', action=f'Cargo criado: {role.name}', message_text=f'Cor: {hex_color}')
        return family, role

    # ==================== PANEL UI ====================
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
            await _safe_log(interaction.guild, member=interaction.user, title='Família renomeada', channel_name=interaction.channel.name if interaction.channel else '', reason='Nome alterado', action=f'{old_name} → {self.new_name.value}', message_text='')
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
            await _safe_log(interaction.guild, member=interaction.user, title='Cor de família alterada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
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
            await _safe_log(interaction.guild, member=interaction.user, title='Foto de família alterada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
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
            super().__init__(placeholder='Selecione uma família do painel', min_values=1, max_values=1, options=options)

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
                embed = _build_family_details_embed(interaction.guild, family)
                embed.set_footer(text='Família selecionada no painel')
                await interaction.response.edit_message(embed=embed, view=self.parent_view)
            else:
                await interaction.response.send_message('Família não encontrada.', ephemeral=True)

    class FamilyPanelView(discord.ui.View):
        def __init__(self, interaction: discord.Interaction):
            super().__init__(timeout=300)
            self.selected_slug: Optional[str] = None
            self.add_item(FamilySelect(self, interaction))

        def _get_selected(self, interaction: discord.Interaction):
            data = _load_db()
            guild_data = _guild_bucket(data, interaction.guild_id)
            if not self.selected_slug:
                return data, guild_data, None, None
            slug, family = _find_family_by_slug(guild_data, self.selected_slug)
            return data, guild_data, slug, family

        @discord.ui.button(label='➕ Criar', style=discord.ButtonStyle.success, row=1)
        async def create_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(CreateFamilyModal())

        @discord.ui.button(label='👁️ Ver', style=discord.ButtonStyle.secondary, row=1)
        async def view_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            embed = _build_family_details_embed(interaction.guild, family)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @discord.ui.button(label='✏️ Renomear', style=discord.ButtonStyle.primary, row=1)
        async def rename_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(RenameFamilyModal(slug))

        @discord.ui.button(label='🎨 Cor', style=discord.ButtonStyle.primary, row=1)
        async def recolor_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(ColorFamilyModal(slug))

        @discord.ui.button(label='🖼️ Foto', style=discord.ButtonStyle.primary, row=2)
        async def photo_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family = self._get_selected(interaction)
            if not family:
                return await interaction.response.send_message('Selecione uma família primeiro no menu.', ephemeral=True)
            await interaction.response.send_modal(PhotoFamilyModal(slug))

        @discord.ui.button(label='🏆 Ranking', style=discord.ButtonStyle.secondary, row=2)
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

        @discord.ui.button(label='🗑️ Deletar', style=discord.ButtonStyle.danger, row=2)
        async def delete_family(self, interaction: discord.Interaction, button: discord.ui.Button):
            data, guild_data, slug, family = self._get_selected(interaction)
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
            await _safe_log(interaction.guild, member=interaction.user, title='Família deletada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
            self.selected_slug = None
            await interaction.response.send_message(f'🗑️ Família **{family.get("name")}** deletada com sucesso.', ephemeral=True)

        @discord.ui.button(label='❓ Ajuda', style=discord.ButtonStyle.secondary, row=2)
        async def help_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message('Para gerenciar membros, use os comandos `/familia add` e `/familia remove` por enquanto. O painel V2 já cobre criar, ver, renomear, alterar cor, alterar foto, ranking e deletar.', ephemeral=True)

    # ==================== GROUP ====================
    family_group = app_commands.Group(name='familia', description='Sistema de famílias do servidor')

    @family_group.command(name='painel', description='Abre o painel interativo de famílias (V2)')
    async def familia_painel(interaction: discord.Interaction):
        embed = _family_embed(
            'Painel de Famílias • V2',
            'Selecione uma família no menu e use os botões para visualizar/editar.\n\nTambém existem subcomandos em `/familia ...` para operações diretas.',
        )
        await interaction.response.send_message(embed=embed, view=FamilyPanelView(interaction), ephemeral=True)

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
        await interaction.response.send_message(embed=_family_embed('Cargos autorizados', desc), ephemeral=True)

    @family_group.command(name='criar', description='Cria uma nova família')
    @app_commands.describe(nome='Nome da família', cor='Cor da família em #RRGGBB')
    async def familia_criar(interaction: discord.Interaction, nome: str, cor: Optional[str] = '#7c5cff'):
        try:
            family, role = await _create_family(interaction, nome, cor or '#7c5cff')
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
        await _safe_log(interaction.guild, member=interaction.user, title='Família renomeada', channel_name=interaction.channel.name if interaction.channel else '', reason='Nome alterado', action=f'{old_name} → {novo_nome}', message_text='')
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
        await _safe_log(interaction.guild, member=interaction.user, title='Cor de família alterada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action=f'Nova cor: {hex_color}', message_text='')
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
        await _safe_log(interaction.guild, member=interaction.user, title='Foto de família alterada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action='Foto atualizada', message_text=url)
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
        if membro.bot:
            return await interaction.response.send_message('Não é permitido adicionar bots em famílias.', ephemeral=True)
        await _remove_member_from_other_families(interaction.guild, guild_data, membro, keep_slug=slug)
        if membro.id not in family['members']:
            family['members'].append(membro.id)
        role = interaction.guild.get_role(int(family.get('role_id', 0))) if family.get('role_id') else None
        if role and role not in membro.roles:
            await membro.add_roles(role, reason=f'Adicionado à família {family.get("name")}')
        _save_db(data)
        await _safe_log(interaction.guild, member=interaction.user, title='Membro adicionado à família', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action=f'Membro adicionado: {membro}', message_text='')
        await interaction.response.send_message(f'✅ {membro.mention} agora faz parte da família **{family.get("name")}**.', ephemeral=True)

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
        await _safe_log(interaction.guild, member=interaction.user, title='Membro removido da família', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família: {family.get("name")}', action=f'Membro removido: {membro}', message_text='')
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
        await _safe_log(interaction.guild, member=interaction.user, title='Família deletada', channel_name=interaction.channel.name if interaction.channel else '', reason=f'Família removida: {family.get("name")}', action='Cargo e registro apagados', message_text='')
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

    # Register only for this guild and sync on ready
    bot.tree.add_command(family_group, guild=guild_obj)

    async def _family_on_ready_sync():
        try:
            synced = await bot.tree.sync(guild=guild_obj)
            print(f'[FAMILIAS V2] Slash commands sincronizados no servidor {guild_id}: {len(synced)} comando(s)')
        except Exception as e:
            print('[FAMILIAS V2] Falha ao sincronizar slash commands:', e)
            traceback.print_exc()

    bot.add_listener(_family_on_ready_sync, 'on_ready')
