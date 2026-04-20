Arquivos:
- bot.py (já atualizado com integração da V3)
- familias_slash_v3.py

Painel V3: gerenciamento de membros pelo painel com seletor de usuários.
Se você já tiver um bot.py maior e não quiser substituir inteiro, copie estas linhas:
from familias_slash_v3 import setup_family_slash_system_v3
setup_family_slash_system_v3(bot, DONO_ID, SEU_ID_DO_SERVIDOR, log)
