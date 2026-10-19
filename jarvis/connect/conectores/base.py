"""Contrato de um conector. Serviço novo = um arquivo em conectores/ com uma classe @conector; o núcleo não muda.

situacao (o que existe de verdade no código):
  implementado        funciona hoje, com as credenciais certas
  em_desenvolvimento  parte pronta (ex.: login), recursos ainda não
  planejado           só no catálogo, com a documentação e os requisitos levantados
estado (o que está acontecendo agora na sua máquina): conectado | desconectado | erro | aguardando
"""
from dataclasses import dataclass, field

CATEGORIAS = ("Comunicação", "Produtividade", "Inteligência Artificial", "Armazenamento", "Automação",
              "Desenvolvimento", "Casa Inteligente")
SITUACOES = ("implementado", "em_desenvolvimento", "planejado")
ESTADOS = ("conectado", "desconectado", "erro", "aguardando")
AUTENTICACOES = {"oauth2_pkce": "OAuth 2.0 + PKCE", "oauth2": "OAuth 2.0", "api_key": "Chave de API",
                 "token": "Token", "webhook": "Webhook", "local": "Local (sem login)"}

REGISTRO: dict[str, "Conector"] = {}


@dataclass
class Campo:
    """Algo que o usuário preenche na Central (ex.: chave de API). Segredos vão para o cofre e nunca voltam à HUD."""
    id: str
    rotulo: str
    segredo: bool = False
    padrao: str = ""
    dica: str = ""


@dataclass
class Status:
    estado: str
    detalhe: str = ""


class Conector:
    id = ""
    nome = ""
    categoria = "Produtividade"
    icone = "plug"                 # nome do ícone Lucide
    descricao = ""
    autenticacao = "local"
    situacao = "planejado"
    docs = ""                      # documentação oficial
    requisitos = ""                # o que é preciso para usar
    custo = ""
    limites = ""
    permissoes: tuple = ()         # o que o Jarvis pode fazer com ele (o mínimo necessário)
    recursos: tuple = ()           # o que já faz (implementado) ou fará
    campos: tuple = ()             # configuração pela Central

    def __init__(self, cofre=None, cfg: dict | None = None):
        self._cofre = cofre
        self.cfg = cfg or {}

    @property
    def cofre(self):
        if self._cofre is None:
            from jarvis.connect.cofre import cofre
            self._cofre = cofre()
        return self._cofre

    # ------------------------------------------------------------ o que cada conector implementa
    def status(self) -> Status:
        if self.situacao == "planejado":
            return Status("desconectado", "ainda não implementado no Jarvis")
        return Status("desconectado")

    def testar(self) -> tuple[bool, str]:
        """Teste de conexão de verdade (chama o serviço). (ok, explicação para o usuário)."""
        st = self.status()
        return st.estado == "conectado", st.detalhe or st.estado

    def configurar(self, valores: dict) -> str:
        """Grava os campos: segredos no cofre, o resto também no cofre (fica tudo junto, fora do config.yaml)."""
        if not self.campos:
            raise ValueError(f"{self.nome} não tem o que configurar por aqui")
        conhecidos = {c.id for c in self.campos}
        limpos = {k: str(v).strip()[:500] for k, v in (valores or {}).items() if k in conhecidos and str(v).strip()}
        if not limpos:
            raise ValueError("nenhum campo preenchido")
        self.cofre.guardar(self.id, limpos)
        return f"{self.nome}: configuração salva."

    def conectar(self) -> str:
        """Início da autorização (OAuth abre o navegador). Os de chave de API usam configurar()."""
        raise ValueError(f"{self.nome}: " + ("preencha os campos e salve" if self.campos else "ainda não disponível"))

    def desconectar(self) -> str:
        self.cofre.apagar(self.id)
        return f"{self.nome} desconectado: credenciais apagadas deste PC."

    # ------------------------------------------------------------ para a Central
    def valores_visiveis(self) -> dict:
        guardado = self.cofre.ler(self.id) if self.campos else {}
        saida = {}
        for c in self.campos:
            v = guardado.get(c.id, "")
            saida[c.id] = self.cofre.mascarar(v) if c.segredo else (v or c.padrao)
        return saida

    def cartao(self, banco=None) -> dict:
        try:
            st = self.status()
        except Exception as e:      # um conector com problema não derruba a Central
            st = Status("erro", f"{type(e).__name__}: {e}"[:200])
        sinc = banco.ultima_sincronizacao(self.id) if banco is not None else None
        return {
            "id": self.id, "nome": self.nome, "categoria": self.categoria, "icone": self.icone,
            "descricao": self.descricao, "autenticacao": self.autenticacao,
            "autenticacao_nome": AUTENTICACOES.get(self.autenticacao, self.autenticacao),
            "situacao": self.situacao, "estado": st.estado, "detalhe": st.detalhe, "docs": self.docs,
            "requisitos": self.requisitos, "custo": self.custo, "limites": self.limites,
            "permissoes": list(self.permissoes), "recursos": list(self.recursos),
            "campos": [{"id": c.id, "rotulo": c.rotulo, "segredo": c.segredo, "dica": c.dica} for c in self.campos],
            "valores": self.valores_visiveis(), "pode_conectar": type(self).conectar is not Conector.conectar,
            "ultima_sincronizacao": sinc,
        }


def conector(classe):
    """Decorador: põe o conector no catálogo."""
    assert classe.id and classe.categoria in CATEGORIAS and classe.situacao in SITUACOES, classe
    REGISTRO[classe.id] = classe
    return classe
