import os
import traceback
import discord
from discord.ext import commands

# ==================== CONFIG ====================
SEU_ID_DO_SERVIDOR = 1409292663752228960
DONO_ID = 766709835701682208
LOG_CHANNEL_ID = 1495200091974271209
BAN_LOG_CHANNEL_ID = 1466542559730991164

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


async def log(guild, member=None, title='Log', channel_name='', reason='', action='', message_text='', target_channel_id=None, **kwargs):
    """Log simples de fallback. Se você já tiver um sistema de log visual, substitua por ele."""
    try:
        if guild is None:
            return
        channel_id = target_channel_id or LOG_CHANNEL_ID
        canal = guild.get_channel(int(channel_id)) if channel_id else None
        if canal is None:
            return
        nome = (getattr(member, 'display_name', None) or getattr(member, 'name', None) or 'Sistema') if member else 'Sistema'
        embed = discord.Embed(title=title, color=0x7c5cff)
        embed.add_field(name='Nome', value=nome, inline=False)
        embed.add_field(name='Chat', value=channel_name or 'sistema', inline=False)
        embed.add_field(name='Motivo', value=reason or 'não informado', inline=False)
        embed.add_field(name='Ação', value=action or 'não informada', inline=False)
        embed.add_field(name='Mensagem', value=(message_text or 'sem mensagem')[:1024], inline=False)
        await canal.send(embed=embed)
    except Exception:
        traceback.print_exc()


from familias_slash_v2 import setup_family_slash_system_v2
setup_family_slash_system_v2(bot, DONO_ID, SEU_ID_DO_SERVIDOR, log)


@bot.event
async def on_ready():
    print(f'[BOT] Logado como {bot.user} (id: {bot.user.id})')


TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:
    print('❌ Token não encontrado! Defina a variável de ambiente DISCORD_TOKEN.')
