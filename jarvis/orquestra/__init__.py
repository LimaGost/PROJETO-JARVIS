"""JARVIS AI Command Center: o Jarvis como administrador de agentes de IA.

Você fala só com o Jarvis; ele entende o pedido, monta um plano, escolhe os agentes (Claude Code, Codex, Gemini CLI,
GPT/OpenRouter pela API, Ollama local, o executor de testes), distribui o trabalho, acompanha, valida e entrega um
relatório único.

Módulos (os nomes do pedido do Iury entre parênteses):
    nucleo.py       (Jarvis Core)          a fachada: planejar, aprovar, executar, cancelar, desfazer, modos
    agentes/        (Agent Registry)       um arquivo por agente com @agente; agente novo não mexe no núcleo
    roteador.py     (Intelligent Router)   escolhe o agente por capacidade + semântica + histórico + custo + latência
    planejador.py   (Jarvis Core)          pedido -> plano em passos (LLM sem ferramentas, ou modelos prontos)
    execucao.py     (Task Orchestrator)    DAG com paralelismo, trava de escrita por projeto, retentativa e fallback
    gateway.py      (CLI Gateway)          processos controlados: ambiente limpo, pasta autorizada, limites, pausa
    mcp.py          (MCP Gateway)          cliente MCP (stdio, JSON-RPC 2.0) com permissão por ferramenta
    contexto.py     (Context Manager)      perfil do projeto + quadro compartilhado + só o contexto que cada um precisa
    monitor.py      (Execution Monitor)    eventos, logs, auditoria de comandos, estado ao vivo para a HUD
    seguranca.py    (Security Manager)     pastas autorizadas, segredos fora dos logs, comandos destrutivos, rollback git
    custos.py       (Cost Manager)         custo estimado/cobrado, limites por tarefa, dia e mês
    terminal.py     terminal integrado e gerenciador de CLIs (detectar, versão, login, instalar, doctor)
    painel.py       operações da Central (HUD) · ferramentas.py: o que o cérebro usa · demo.py: demonstração

Regras de segurança (ver docs/ORQUESTRA.md): agente só trabalha em pasta autorizada por você; antes de escrever, o
Jarvis tira um checkpoint git (desfazer); git push, reset --hard e afins ficam bloqueados; chaves nunca vão para o
contexto, os logs ou a HUD; o modo automático respeita os limites de custo.
"""
