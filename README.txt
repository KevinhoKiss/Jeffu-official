Arquivos:
- bot.py (já atualizado com integração da V2)
- familias_slash_v2.py

Se você já tiver um bot.py grande e não quiser substituir inteiro, copie estas linhas:
from familias_slash_v2 import setup_family_slash_system_v2
setup_family_slash_system_v2(bot, DONO_ID, SEU_ID_DO_SERVIDOR, log)
