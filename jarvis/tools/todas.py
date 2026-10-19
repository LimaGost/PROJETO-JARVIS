"""Importar este módulo registra todas as ferramentas em REGISTRO."""
from jarvis.tools import (agenda, apps, arquivos, calculo, conhecimento, gmail, habilidades, interface,  # noqa: F401
                          memoria_tools, midia, programador, shell, sistema, spotify, tela, uso, web, whatsapp)
from jarvis.connect import ferramentas as _connect  # noqa: F401,E402  (tarefas, automações, integrações)
from jarvis.orquestra import ferramentas as _orquestra  # noqa: F401,E402  (AI Command Center: agentes de IA, MCP)
from jarvis.tools.base import REGISTRO  # noqa: F401
