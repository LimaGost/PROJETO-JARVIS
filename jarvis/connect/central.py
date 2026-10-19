"""Pedidos da Central (gaveta na HUD) ao Connect. Tudo que chega aqui é entrada não confiável: valida e devolve dados
prontos para mostrar. Segredos nunca saem daqui (a HUD só vê '••••••1234')."""
import time

from jarvis.connect import motor as m
from jarvis.connect.conectores import CATEGORIAS, catalogo, instancia
from jarvis.connect.modelos import MODELOS

MAX_TEXTO = 4000
CACHE_S = 15         # o status de cada conector chama o serviço (Ollama, Evolution): não repetir a cada clique


def _int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).isdigit():
        raise ValueError("id inválido")
    return int(v)


def _texto(v, limite=MAX_TEXTO) -> str:
    return str(v or "")[:limite]


class Central:
    def __init__(self, motor: m.Motor, cofre):
        self.motor, self.banco, self.cofre = motor, motor.banco, cofre
        self._cache: tuple[float, list] | None = None

    def _catalogo(self) -> list[dict]:
        if self._cache is None or time.monotonic() - self._cache[0] > CACHE_S:
            self._cache = (time.monotonic(), catalogo(self.banco, self.cofre, self.motor.cfg))
        return self._cache[1]

    def tratar(self, pedido: dict) -> dict:
        op = str(pedido.get("op", ""))
        resposta = {"tipo": "central", "op": op, "pedido": pedido.get("pedido")}
        metodo = getattr(self, "op_" + op, None) if op.isidentifier() else None
        if metodo is None:
            return {**resposta, "ok": False, "erro": "operação desconhecida"}
        try:
            return {**resposta, "ok": True, "dados": metodo(pedido)}
        except (ValueError, KeyError) as e:
            return {**resposta, "ok": False, "erro": str(e).strip("'")[:300]}
        except Exception as e:
            return {**resposta, "ok": False, "erro": f"{type(e).__name__}: {e}"[:300]}

    # ------------------------------------------------------------ painel
    def op_painel(self, p):
        cartoes = self._catalogo()
        return {
            **self.banco.resumo(),
            "integracoes_ativas": sum(c["estado"] == "conectado" for c in cartoes),
            "integracoes_total": sum(c["situacao"] != "planejado" for c in cartoes),
            "com_erro": [{"id": c["id"], "nome": c["nome"], "detalhe": c["detalhe"]} for c in cartoes
                         if c["estado"] == "erro"],
            "proximas": self.banco.proximas(8),
            "linha_do_tempo": self.banco.eventos(40),
            "alertas": [e for e in self.banco.eventos(60) if e["nivel"] in ("erro", "alerta")][:10],
            "consumo": self.banco.consumo(7),
        }

    # ------------------------------------------------------------ integrações
    def op_integracoes(self, p):
        if p.get("atualizar"):
            self._cache = None
        return {"categorias": list(CATEGORIAS), "itens": self._catalogo()}

    def _conector(self, p):
        self._cache = None               # vai mexer num conector: o próximo catálogo é lido de novo
        return instancia(_texto(p.get("id"), 40), self.cofre, self.motor.cfg)

    def op_historico(self, p):
        c = self._conector(p)
        return {"id": c.id, "eventos": self.banco.eventos(30, conector=c.id)}

    def op_testar(self, p):
        c = self._conector(p)
        ok, msg = c.testar()
        self.banco.sincronizou(c.id, ok, msg)
        self.banco.evento("teste", f"teste de conexão: {'ok' if ok else 'falhou'} ({msg[:150]})", c.id,
                          "info" if ok else "erro")
        return {"ok": ok, "mensagem": msg, "cartao": c.cartao(self.banco)}

    def op_configurar(self, p):
        c = self._conector(p)
        valores = p.get("valores") if isinstance(p.get("valores"), dict) else {}
        # campo de segredo que voltou mascarado (o usuário não mexeu) não sobrescreve o guardado
        limpos = {k: _texto(v, 500) for k, v in valores.items() if not str(v).startswith("••••••")}
        msg = c.configurar(limpos)
        self.banco.evento("configuracao", f"configuração salva ({', '.join(sorted(limpos))})", c.id)
        return {"mensagem": msg, "cartao": c.cartao(self.banco)}

    def op_conectar(self, p):
        c = self._conector(p)
        r = c.conectar()
        r = r if isinstance(r, dict) else {"mensagem": str(r)}
        self.banco.evento("conexao", f"conexão iniciada: {r.get('mensagem', '')[:120]}", c.id)
        return {**r, "cartao": c.cartao(self.banco)}

    def op_desconectar(self, p):
        c = self._conector(p)
        msg = c.desconectar()
        self.banco.evento("conexao", "desconectado pela Central", c.id, "alerta")
        return {"mensagem": msg, "cartao": c.cartao(self.banco)}

    # ------------------------------------------------------------ tarefas
    def op_tarefas(self, p):
        status = p.get("status") if p.get("status") in ("pendente", "concluida", "cancelada", "todas") else "pendente"
        return {"itens": self.banco.tarefas(status, 200), "resumo": self.banco.resumo()}

    def op_tarefa_salvar(self, p):
        t = p.get("tarefa") if isinstance(p.get("tarefa"), dict) else {}
        titulo = _texto(t.get("titulo"), 200).strip()
        if not titulo:
            raise ValueError("dê um título à tarefa")
        quando = _texto(t.get("quando"), 30).strip() or None
        if quando:
            from datetime import datetime
            try:
                quando = datetime.fromisoformat(quando).isoformat(timespec="minutes")
            except ValueError:
                raise ValueError("data/hora inválida")
        campos = {"titulo": titulo, "descricao": _texto(t.get("descricao"), 2000),
                  "prioridade": t.get("prioridade") if t.get("prioridade") in ("baixa", "normal", "alta", "urgente")
                  else "normal", "quando": quando}
        if t.get("id"):
            tarefa = self.banco.mudar_tarefa(_int(t["id"]), **campos)
        else:
            tarefa = self.banco.criar_tarefa(**campos)
            self.banco.evento("tarefa", f"tarefa criada: {titulo}")
        self.motor._avisar_hud()
        return tarefa

    def op_tarefa_status(self, p):
        status = p.get("status")
        if status not in ("pendente", "concluida", "cancelada"):
            raise ValueError("status inválido")
        t = self.banco.mudar_tarefa(_int(p.get("id")), status=status)
        if t and status == "concluida":
            self.banco.evento("tarefa", f"tarefa concluída: {t['titulo']}")
        self.motor._avisar_hud()
        return t

    def op_tarefa_apagar(self, p):
        ok = self.banco.apagar_tarefa(_int(p.get("id")))
        self.motor._avisar_hud()
        return {"apagada": ok}

    # ------------------------------------------------------------ automações
    def _descrever(self, a: dict) -> dict:
        envios = []
        try:
            envios = m.analisar_envios(a["fluxo"])
        except ValueError:
            pass
        return {**a, "gatilho_texto": m.descrever_gatilho(a["fluxo"]["gatilho"]), "hash": m.hash_fluxo(a["fluxo"]),
                "precisa_aprovar": self.motor.precisa_aprovar(a), "envios": envios,
                "resumo_aprovacao": m.resumo_aprovacao(a["nome"], a["fluxo"]) if envios else ""}

    def op_automacoes(self, p):
        return {"itens": [self._descrever(a) for a in self.banco.automacoes()],
                "modelos": MODELOS,
                "tipos_acao": {k: {"nome": t.nome, "descricao": t.descricao, "envia": t.envia, "campos": list(t.campos)}
                               for k, t in m.TIPOS_ACAO.items()},
                "eventos": list(m.EVENTOS), "operadores": list(m.OPERADORES), "repeticoes": list(m.REPETICOES),
                "variaveis_seguras": sorted(m.VARIAVEIS_SEGURAS)}

    def op_automacao_salvar(self, p):
        nome = _texto(p.get("nome"), 120).strip() or "Automação"
        fluxo = p.get("fluxo") if isinstance(p.get("fluxo"), dict) else {}
        descricao = _texto(p.get("descricao"), 500)
        if p.get("id"):
            auto = self.motor.atualizar(_int(p["id"]), nome, fluxo, descricao)
        else:
            auto = self.motor.criar(nome, fluxo, descricao, aprovado=False, ativa=bool(p.get("ativa", True)))
        return self._descrever(auto)

    def op_automacao_aprovar(self, p):
        return self._descrever(self.motor.aprovar(_int(p.get("id")), _texto(p.get("hash"), 80)))

    def op_automacao_ativar(self, p):
        return self._descrever(self.motor.ativar(_int(p.get("id")), bool(p.get("ativa"))))

    def op_automacao_testar(self, p):
        return self.motor.testar(_int(p.get("id")))

    def op_automacao_rodar(self, p):
        id_ = _int(p.get("id"))
        self.motor.rodar_agora(id_)
        return (self.banco.execucoes(id_, 1) or [{}])[0]

    def op_automacao_apagar(self, p):
        id_ = _int(p.get("id"))
        a = self.banco.automacao(id_)
        ok = self.banco.apagar_automacao(id_)
        if a:
            self.banco.evento("automacao", f"automação apagada: {a['nome']}")
        self.motor._avisar_hud()
        return {"apagada": ok}

    def op_automacao_execucoes(self, p):
        return {"itens": self.banco.execucoes(_int(p.get("id")), 20)}
