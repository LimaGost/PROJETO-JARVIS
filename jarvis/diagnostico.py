"""Diagnóstico ao vivo: testa as peças REAIS do Jarvis neste computador e diz o que está bom e o que não está.

    .venv\\Scripts\\python.exe -m jarvis.diagnostico                 # memória, aprendizado, internet, voz, ações
    .venv\\Scripts\\python.exe -m jarvis.diagnostico --ouvir         # e toca a voz gerada no alto-falante
    .venv\\Scripts\\python.exe -m jarvis.diagnostico --abrir         # e abre/fecha o Bloco de Notas de verdade
    .venv\\Scripts\\python.exe -m jarvis.diagnostico --cerebro       # e faz uma pergunta ao Claude (gasta cota)
    .venv\\Scripts\\python.exe -m jarvis.diagnostico --so voz        # só uma parte: ollama memoria aprendizado connect
                                                                    # internet voz acoes cerebro

Nada aqui mexe nos seus dados: memória e conhecimento são testados em bancos temporários, que são apagados no fim.
Rode com o Jarvis FECHADO (a voz e o Whisper usam a mesma GPU). O relatório fica em data/logs/diagnostico.txt e
a voz gerada em data/logs/diagnostico_voz_<modelo>.wav, para você ouvir. --comparar testa XTTS e Qwen3-TTS.
"""
import argparse
import re
import sys
import tempfile
import time
import traceback
import unicodedata
import wave
from pathlib import Path

import numpy as np

from jarvis.config import RAIZ, carregar

PASTA_LOGS = RAIZ / "data" / "logs"

# perguntas com OUTRAS palavras: é isso que separa busca por significado de busca por palavra igual
FATOS = [("A mãe do usuário se chama Maria.", "pessoa"),
         ("O usuário trabalha como analista de implantação numa franquia da Linx.", "trabalho"),
         ("O usuário torce para o Flamengo.", "preferencia"),
         ("O usuário prefere respostas curtas e diretas.", "preferencia")]
PERGUNTAS_MEMORIA = [("qual é o nome da minha mãe?", "Maria"),
                     ("em que empresa eu trabalho?", "Linx"),
                     ("qual é o meu time de futebol?", "Flamengo")]

MANUAL = """## Emissão de nota fiscal
Para emitir a NF-e de uma venda, abra o módulo Faturamento, escolha o pedido e clique em Emitir NF-e. A nota é
transmitida para a SEFAZ e volta autorizada em poucos segundos. Se for rejeitada, o motivo aparece na coluna Status.

## Inventário de estoque
O inventário rotativo fica no módulo Estoque, opção Inventário. A contagem é feita por seção da loja, e as
divergências só são ajustadas depois que o gerente aprova o fechamento.

## Integração com a loja virtual
A integração B2C usa o webservice do Microvix com uma chave de acesso. O método LinxProdutos baixa o catálogo e o
método LinxPedidosVenda envia os pedidos feitos no site para o ERP.

## Troca de mercadoria
Na troca, o vendedor lança a devolução no PDV, que gera um vale-troca no valor da peça. O vale pode ser usado
na mesma hora ou em até 30 dias em qualquer loja da rede."""
PERGUNTAS_MANUAL = [("como faço para gerar a nota de uma venda?", "Emitir NF-e"),
                    ("onde conto as peças da loja?", "Inventário"),
                    ("como o site manda os pedidos para o sistema?", "LinxPedidosVenda"),
                    ("o cliente quer devolver uma roupa, o que acontece?", "vale-troca")]

FRASES_VOZ = ["Pois não, senhor?",
              "Verifiquei o sistema e está tudo funcionando normalmente.",
              "Senhor, encontrei três compromissos para amanhã, uma reunião às nove com a equipe de implantação, "
              "um almoço ao meio-dia, e no fim da tarde a revisão completa do projeto Jarvis."]


# ------------------------------------------------------------------ relatório
class Relatorio:
    def __init__(self):
        self.itens: list[tuple[str, str, str, str]] = []   # (parte, situação, detalhe, dica)
        self.linhas: list[str] = []

    def secao(self, titulo: str):
        self._escrever(f"\n=== {titulo} ===")

    def item(self, parte: str, ok: bool | None, detalhe: str, dica: str = ""):
        icone = {True: "✅", False: "❌", None: "⚠️"}[ok]
        self.itens.append((parte, icone, detalhe, dica))
        self._escrever(f"{icone} {parte}: {detalhe}" + (f"\n     → {dica}" if dica and ok is not True else ""))

    def info(self, texto: str):
        self._escrever(f"   {texto}")

    def _escrever(self, texto: str):
        print(texto, flush=True)
        self.linhas.append(texto)

    def gravar(self):
        PASTA_LOGS.mkdir(parents=True, exist_ok=True)
        cab = f"Diagnóstico do Jarvis — {time.strftime('%Y-%m-%d %H:%M:%S')}"
        (PASTA_LOGS / "diagnostico.txt").write_text(cab + "\n" + "\n".join(self.linhas) + "\n", encoding="utf-8")

    def resumo(self):
        ok = sum(1 for i in self.itens if i[1] == "✅")
        falhas = [i for i in self.itens if i[1] == "❌"]
        avisos = [i for i in self.itens if i[1] == "⚠️"]
        self._escrever(f"\n=== Resumo: {ok} ok, {len(avisos)} aviso(s), {len(falhas)} falha(s) ===")
        for parte, icone, detalhe, dica in falhas + avisos:
            self._escrever(f"{icone} {parte}: {dica or detalhe}")
        self._escrever(f"Relatório salvo em {PASTA_LOGS / 'diagnostico.txt'}")


def _normalizar(texto: str) -> list[str]:
    sem = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", sem)


def _cliente_ollama(cfg):
    import ollama
    return ollama.Client(host=cfg["llm"]["host"].replace("localhost", "127.0.0.1"))


# ------------------------------------------------------------------ partes
def testar_ollama(cfg, r: Relatorio) -> bool:
    r.secao("Ollama (modelo local e embeddings)")
    try:
        t = time.time()
        modelos = {m.model for m in _cliente_ollama(cfg).list().models}
        r.item("Ollama", True, f"respondendo em {time.time() - t:.2f}s")
    except Exception as e:
        r.item("Ollama", False, f"não respondeu ({type(e).__name__})", "abra o Ollama (ícone na bandeja) e rode de novo")
        return False
    for nome in (cfg["llm"]["modelo"], cfg.get("memoria", {}).get("embeddings", "embeddinggemma")):
        achou = any(m == nome or m.startswith(nome + ":") or m.split(":")[0] == nome for m in modelos)
        r.item(f"modelo {nome}", achou, "instalado" if achou else "não instalado", f"ollama pull {nome}")
    return True


def _embedder(cfg):
    from jarvis.memoria.embeddings import Embedder
    return Embedder(cfg["llm"]["host"], cfg.get("memoria", {}).get("embeddings", "embeddinggemma"))


def testar_memoria(cfg, r: Relatorio, pasta: Path):
    r.secao("Memória (fatos sobre você, busca por significado)")
    from jarvis.memoria.banco import Memoria
    try:
        emb = _embedder(cfg)
        t = time.time()
        emb.aquecer()
        r.item("embeddings", True, f"modelo carregado em {time.time() - t:.1f}s (na CPU)")
    except Exception as e:
        r.item("embeddings", False, f"falhou ({type(e).__name__})", "confira o Ollama e o modelo embeddinggemma")
        return
    mem = Memoria(str(pasta / "memoria_teste.db"), emb)
    for texto, cat in FATOS:
        mem.adicionar_fato(texto, cat)
    acertos = 0
    for pergunta, esperado in PERGUNTAS_MEMORIA:
        t = time.time()
        achados = mem.buscar(pergunta, k=1, minimo=0.0)
        ms = (time.time() - t) * 1000
        certo = bool(achados) and esperado in achados[0][0].texto
        acertos += certo
        r.info(f"{'✓' if certo else '✗'} \"{pergunta}\" → {achados[0][0].texto if achados else '(nada)'} "
               f"[{achados[0][1]:.2f}, {ms:.0f} ms]" if achados else f"✗ \"{pergunta}\" → nada")
    r.item("lembrar pelo significado", acertos == len(PERGUNTAS_MEMORIA) or (None if acertos else False),
           f"{acertos}/{len(PERGUNTAS_MEMORIA)} perguntas com outras palavras acharam o fato certo",
           "com menos de 3/3 o modelo de embeddings não está entendendo bem o português")
    contexto = mem.contexto("qual é o nome da minha mãe?")
    r.item("contexto do pedido", "Maria" in contexto, "o fato certo entra sozinho no pedido ao cérebro"
           if "Maria" in contexto else "o fato não entrou no contexto do pedido")
    segredo = mem.adicionar_fato("A senha do banco do usuário é 123456.", "outro")
    r.item("proteção de segredos", segredo is None, "senha recusada" if segredo is None else "GUARDOU UMA SENHA",
           "o filtro de segredos falhou: avise o desenvolvedor")


def testar_aprendizado(cfg, r: Relatorio, pasta: Path):
    r.secao("Aprendizado (base de conhecimento, RAG)")
    from jarvis.memoria.conhecimento import Conhecimento
    try:
        base = Conhecimento(str(pasta / "conhecimento_teste.db"), _embedder(cfg))
        t = time.time()
        # cada seção é um documento: só acerta qual seção responde quem entende o significado (não é sorte)
        secoes = [s.strip() for s in MANUAL.split("## ") if s.strip()]
        for i, secao in enumerate(secoes):
            base.estudar(f"Manual Microvix: {secao.splitlines()[0]}", f"diagnostico-{i}", secao)
        r.item("estudar", True, f"{len(secoes)} seções guardadas em {time.time() - t:.1f}s")
    except Exception as e:
        r.item("estudar", False, f"falhou ({type(e).__name__}: {e})", "confira o Ollama e o modelo embeddinggemma")
        return
    acertos = 0
    for pergunta, esperado in PERGUNTAS_MANUAL:
        achados = base.buscar(pergunta, k=2, minimo=0.0)
        certo = any(esperado in a.texto for a in achados[:1])
        acertos += certo
        r.info(f"{'✓' if certo else '✗'} \"{pergunta}\" → "
               + (f"{achados[0].texto[:70]!r}... [{achados[0].nota:.2f}]" if achados else "(nada)"))
    r.item("responder pelo que estudou", acertos >= len(PERGUNTAS_MANUAL) - 1 or (None if acertos else False),
           f"{acertos}/{len(PERGUNTAS_MANUAL)} perguntas acharam o trecho certo do manual",
           "trechos errados: o modelo de embeddings pode não estar carregado direito")


def testar_internet(cfg, r: Relatorio, url: str):
    r.secao("Leitura da internet")
    from jarvis.tools import leitura
    try:
        t = time.time()
        texto, titulo, origem = leitura.ler(url)
        r.item("ler página", True, f"'{titulo}' — {len(texto)} caracteres em {time.time() - t:.1f}s")
    except Exception as e:
        r.item("ler página", False, f"{url}: {e}", "sem internet, ou o site bloqueou; tente outro com --url")
    try:
        leitura.validar_url("http://127.0.0.1:8765/")
        r.item("bloqueio da rede local", False, "deixou ler 127.0.0.1", "falha de segurança: avise o desenvolvedor")
    except Exception:
        r.item("bloqueio da rede local", True, "endereços internos são recusados")


def _linha_do_tempo(chegadas: list[tuple[float, int]], taxa: int, reserva_s: float) -> tuple[float, float, int]:
    """Simula a reprodução com a reserva do tts.py: (início do som, segundos de silêncio no meio, nº de engasgos)."""
    from jarvis.audio.tts import FOLGA_VELOCIDADE
    juntos, inicio = 0, None
    for t, n in chegadas:
        juntos += n
        if juntos >= reserva_s * taxa or (juntos / taxa >= 0.3 and juntos / taxa >= FOLGA_VELOCIDADE * t):
            inicio = t
            break
    if inicio is None:
        inicio = chegadas[-1][0]
    relogio, silencio, engasgos = inicio, 0.0, 0
    for t, n in chegadas:
        if t > relogio + 0.02:
            silencio += t - relogio
            engasgos += 1
            relogio = t
        relogio += n / taxa
    return inicio, silencio, engasgos


def testar_voz(cfg, r: Relatorio, ouvir: bool, modelo: str | None = None) -> dict | None:
    """Gera as frases de teste, mede e confere com o Whisper. Devolve as medidas (para --comparar)."""
    import copy
    t_cfg = copy.deepcopy(cfg["tts"])
    if modelo:
        t_cfg.setdefault("clone", {})["modelo"] = modelo
    modelo = t_cfg.get("clone", {}).get("modelo", "xtts")
    r.secao(f"Voz: {modelo} (voz clonada + conferência pelo Whisper)")
    from jarvis.audio.tts import TAXA_CLONE, dividir_frase
    if t_cfg.get("motor") != "clone":
        r.item("voz clonada", None, "tts.motor não é 'clone'; pulando", "")
        return None
    from jarvis.audio.clone import VozClonada
    medidas = {"modelo": modelo}
    try:
        t = time.time()
        voz = VozClonada(t_cfg)
        pronta = voz.esperar_pronta(240)
        partida = time.time() - t
    except Exception as e:
        r.item(f"voz {modelo}", False, f"não subiu ({e})", "confira o ambiente da voz (README) e a referência")
        return None
    if not pronta:
        r.item(f"voz {modelo}", False, "não ficou pronta em 4 min", "veja data/logs/voz_clonada.log")
        voz.encerrar()
        return None
    medidas["partida"] = partida
    r.item("partida da voz", partida < 60 or None, f"pronta em {partida:.0f}s", "acima de 60 s: veja as fases em "
           "data/logs/voz_clonada.log")
    reserva = float(t_cfg.get("clone", {}).get("reserva_s", 1.2))
    maximo = int(t_cfg.get("clone", {}).get("max_trecho", 110))
    audios, textos, piores, inicios, velocidades = [], [], [], [], []
    try:
        for frase in FRASES_VOZ:
            for trecho in dividir_frase(frase, maximo):
                chegadas, pedacos, t0 = [], [], time.time()
                for pedaco in voz.pedir(trecho):
                    chegadas.append((time.time() - t0, len(pedaco)))
                    pedacos.append(pedaco)
                if not pedacos:
                    r.item("gerar fala", False, f"nada gerado para '{trecho[:40]}'", "veja data/logs/voz_clonada.log")
                    continue
                audio = np.concatenate(pedacos)
                duracao, geracao = len(audio) / TAXA_CLONE, chegadas[-1][0]
                inicio, silencio, engasgos = _linha_do_tempo(chegadas, TAXA_CLONE, reserva)
                piores.append((engasgos, silencio))
                inicios.append(inicio)
                velocidades.append(duracao / max(geracao, 1e-3))
                r.info(f"\"{trecho[:50]}{'…' if len(trecho) > 50 else ''}\": {duracao:.1f}s de fala gerados em "
                       f"{geracao:.1f}s ({duracao / max(geracao, 1e-3):.1f}x o tempo real), 1º som em {inicio:.2f}s, "
                       f"engasgos previstos: {engasgos}")
                audios.append(audio)
                textos.append(trecho)
    finally:
        voz.encerrar()
    if not audios:
        return medidas
    engasgos = sum(e for e, _ in piores)
    medidas.update(engasgos=engasgos, silencio=sum(s for _, s in piores), primeiro_som=max(inicios),
                   velocidade=min(velocidades))
    silencio = sum(s for _, s in piores)
    r.item("fluidez", engasgos == 0 or (None if silencio < 0.5 else False),
           "sem engasgos previstos" if engasgos == 0 else f"{engasgos} engasgo(s), {silencio:.1f}s de silêncio no meio",
           "a GPU está gerando mais devagar que a fala: feche apps de GPU, use o Claude (não o Qwen) e/ou "
           "suba tts.clone.reserva_s para 2.0")
    wav = PASTA_LOGS / f"diagnostico_voz_{modelo}.wav"
    _gravar_wav(audios, TAXA_CLONE, wav)
    r.info(f"áudio salvo em {wav} (ouça para julgar se parece o Jarvis)")
    if ouvir:
        try:
            import sounddevice as sd
            sd.play(np.concatenate([np.concatenate([a, np.zeros(TAXA_CLONE // 3, np.int16)]) for a in audios]),
                    TAXA_CLONE)
            sd.wait()
        except Exception as e:
            r.item("tocar no alto-falante", False, str(e), "confira a saída de áudio do Windows")
    medidas["nitidez"] = _conferir_com_whisper(cfg, r, audios, textos)
    return medidas


def comparar_vozes(cfg, r: Relatorio, ouvir: bool):
    """Testa cada motor instalado com as mesmas frases e diz qual usar. Fluidez primeiro (voz fiel que trava não
    serve para conversar); entre as fluidas, a mais nítida. Semelhança com o Jarvis: ouça os WAVs."""
    from jarvis.audio.clone import MODELOS
    resultados = [m for m in (testar_voz(cfg, r, ouvir, modelo) for modelo in MODELOS) if m and "engasgos" in m]
    r.secao("Comparação das vozes")
    if not resultados:
        r.item("comparação", False, "nenhum motor de voz funcionou", "instale ao menos um (README, passo 5)")
        return
    for m in resultados:
        r.info(f"{m['modelo']}: engasgos {m['engasgos']} ({m['silencio']:.1f}s), pior velocidade "
               f"{m['velocidade']:.1f}x o tempo real, 1º som em até {m['primeiro_som']:.2f}s, "
               f"nitidez {m['nitidez'] if m.get('nitidez') is not None else '?'}%, partida {m['partida']:.0f}s")
    fluidas = [m for m in resultados if m["engasgos"] == 0] or sorted(resultados, key=lambda m: m["silencio"])[:1]
    melhor = max(fluidas, key=lambda m: ((m.get("nitidez") or 0) >= 85, m["modelo"] == "qwen3", m.get("nitidez") or 0))
    atual = cfg["tts"].get("clone", {}).get("modelo", "xtts")
    dica = "" if melhor["modelo"] == atual else f"troque no config.yaml: tts.clone.modelo: {melhor['modelo']}"
    r.item("voz recomendada", True if melhor["modelo"] == atual else None,
           f"{melhor['modelo']} (fluida e mais nítida/fiel neste PC); hoje está {atual}", dica)
    r.info("Antes de trocar, ouça data/logs/diagnostico_voz_*.wav: qual soa mais como o Jarvis?")


def _gravar_wav(audios, taxa, destino: Path):
    destino.parent.mkdir(parents=True, exist_ok=True)
    pausa = np.zeros(taxa // 3, dtype=np.int16)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(np.concatenate([np.concatenate([a, pausa]) for a in audios]).astype("<i2").tobytes())


def _conferir_com_whisper(cfg, r: Relatorio, audios, textos):
    """O próprio Whisper do Jarvis transcreve a voz gerada: se ele entende, a fala está nítida."""
    try:
        from jarvis.audio.stt import Ouvido
        ouvido = Ouvido(cfg)
    except Exception as e:
        r.item("conferência pelo Whisper", None, f"Whisper não carregou ({type(e).__name__})", "")
        return None
    total, acertos = 0, 0
    for audio, texto in zip(audios, textos):
        x24 = audio.astype(np.float32)
        n16 = int(len(x24) * 16000 / 24000)
        x16 = np.interp(np.linspace(0, len(x24) - 1, n16), np.arange(len(x24)), x24).astype(np.int16)
        ouvido_texto = ouvido.transcrever(x16)
        esperadas, ouvidas = _normalizar(texto), set(_normalizar(ouvido_texto))
        total += len(esperadas)
        acertos += sum(p in ouvidas for p in esperadas)
        r.info(f"falou: \"{texto[:60]}\" | Whisper ouviu: \"{ouvido_texto[:60]}\"")
    pct = 100 * acertos / max(total, 1)
    r.item("nitidez da fala", pct >= 85 or (None if pct >= 65 else False), f"{pct:.0f}% das palavras entendidas",
           "fala embolada: tente tts.clone.temperatura 0.55 e confira a referência (preparar_voz)")
    return round(pct)


def testar_acoes(cfg, r: Relatorio, abrir: bool, pasta: Path):
    r.secao("Ações (ferramentas e permissões)")
    from jarvis.tools.base import REGISTRO, Nivel
    from jarvis.tools.executor import NEGADO, Executor
    try:
        from jarvis.tools.todas import REGISTRO as _  # noqa: F401
    except Exception as e:
        r.item("carregar ferramentas", False, f"{type(e).__name__}: {e}", "rode pip install -r requirements.lock")
        return
    perguntas = []
    ex = Executor(lambda resumo, **k: perguntas.append(resumo) or False, str(pasta / "logs"), avisar=lambda t: None)
    r.item("ferramentas registradas", len(REGISTRO) >= 40, f"{len(REGISTRO)} ferramentas")
    for nome, args, esperado in (("hora_data", {}, r"\d{4}"), ("calcular", {"expressao": "37*91"}, "3367"),
                                 ("info_sistema", {}, "(?i)cpu")):
        t = time.time()
        res = ex.executar(nome, args)
        ok = bool(re.search(esperado, res)) and not res.startswith("Erro")
        r.item(f"🟢 {nome}", ok, f"{res[:70]!r} ({time.time() - t:.2f}s)")
    alvo = pasta / "nao_apagar.txt"
    alvo.write_text("teste", encoding="utf-8")
    try:
        nivel = REGISTRO["apagar"].avaliacao({"caminho": str(alvo)}).nivel
        res = ex.executar("apagar", {"caminho": str(alvo)})
        ok = nivel == Nivel.CONFIRMAR and res == NEGADO and alvo.exists() and perguntas
        r.item("🔴 apagar sem 'sim'", ok, "pediu confirmação, recebeu não e NÃO apagou" if ok else
               f"nível {nivel.name}, resultado {res[:60]!r}", "falha de segurança: avise o desenvolvedor")
    except Exception as e:
        r.item("🔴 apagar sem 'sim'", False, f"{type(e).__name__}: {e}")
    if abrir:
        res = ex.executar("abrir_programa", {"nome": "bloco de notas"})
        r.item("abrir programa", not res.startswith("Erro"), res[:80])
        time.sleep(2.0)
        res = ex.executar("fechar_programa", {"nome": "bloco de notas"})
        r.info(f"fechar: {res[:80]} (fechar é 🟡/🔴; se pediu confirmação, aqui a resposta é sempre não)")


def testar_cerebro(cfg, r: Relatorio, pasta: Path):
    r.secao("Cérebro Claude (pergunta real com ferramenta; gasta cota)")
    from jarvis.tools.executor import Executor
    try:
        from jarvis.tools.todas import REGISTRO as _  # noqa: F401
        from jarvis.brain.claude_cli import CerebroClaudeCLI
        ex = Executor(lambda *a, **k: False, str(pasta / "logs"), avisar=lambda t: None)
        cerebro = CerebroClaudeCLI(cfg, ex, None)
        t = time.time()
        cerebro.aquecer()
        r.item("Claude (assinatura)", True, f"conectado em {time.time() - t:.1f}s ({cerebro.rotulo})")
    except Exception as e:
        r.item("Claude (assinatura)", False, f"{type(e).__name__}: {str(e)[:120]}", "rode 'claude' e faça o login")
        return
    try:
        t = time.time()
        primeira, frases = None, []
        for frase in cerebro.responder("Quanto é 3847 vezes 29? Use a calculadora."):
            primeira = primeira or time.time() - t
            frases.append(frase)
        resposta = " ".join(frases)
        log = (pasta / "logs" / "acoes.jsonl")
        usou = log.exists() and "calcular" in log.read_text(encoding="utf-8")
        certo = "111563" in resposta.replace(".", "").replace(" ", "")
        r.item("pensar + agir", usou and certo, f"usou a ferramenta: {'sim' if usou else 'não'}; resposta: "
               f"{resposta[:80]!r}; 1ª frase em {primeira or 0:.1f}s, total {time.time() - t:.1f}s",
               "ele respondeu sem a ferramenta ou errou a conta")
    finally:
        try:
            cerebro.encerrar()
        except Exception:
            pass


# ------------------------------------------------------------------ principal
PARTES = ("ollama", "memoria", "aprendizado", "internet", "voz", "acoes", "connect", "cerebro")


def testar_connect(cfg, r: Relatorio, pasta: Path):
    """JARVIS Connect: cofre (DPAPI), banco, motor com uma automação de teste, webhooks e as integrações reais."""
    r.secao("JARVIS Connect")
    from jarvis.connect import motor as m
    from jarvis.connect.banco import Banco
    from jarvis.connect.cofre import Cofre, cofre, protecao_padrao
    teste = Cofre(pasta / "cofre.json")
    teste.guardar("diag", {"chave": "segredo-de-teste"})
    bruto = (pasta / "cofre.json").read_text(encoding="utf-8")
    ok = teste.ler("diag").get("chave") == "segredo-de-teste" and "segredo-de-teste" not in bruto
    r.item("cofre de credenciais", ok if protecao_padrao().nome == "dpapi" else None,
           f"proteção: {protecao_padrao().nome}" + (" (cifrado e lido de volta)" if ok else ""),
           "fora do Windows o cofre não é cifrado" if protecao_padrao().nome != "dpapi" else "a DPAPI falhou: avise")
    banco = Banco(pasta / "connect.db")
    falas = []
    mo = m.Motor(banco, {"anunciar": lambda t, f="": falas.append(t)}, cfg)
    auto = mo.criar("Diagnóstico", {"gatilho": {"tipo": "manual"},
                                    "acoes": [{"tipo": "criar_tarefa", "params": {"titulo": "tarefa {data}"}},
                                              {"tipo": "avisar", "params": {"texto": "ok"}, "paralelo": True}]})
    mo.rodar_agora(auto["id"])
    ex = banco.execucoes(auto["id"])[0]
    r.item("motor de automações", ex["status"] == "ok" and len(banco.tarefas()) == 1 and falas == ["[automação Diagnóstico] ok"],
           f"execução {ex['status']}, {len(banco.tarefas())} tarefa criada, aviso {'dado' if falas else 'NÃO dado'}")
    try:
        m.validar_fluxo({"gatilho": {"tipo": "manual"}, "acoes": [{"tipo": "whatsapp", "params": {
            "para": "5511900000000", "texto": "{resultado.x}"}}]})
        r.item("política de envio", False, "aceitou texto de IA para terceiro", "falha de segurança: avise")
    except ValueError:
        r.item("política de envio", True, "texto variável para terceiros é recusado")
    import urllib.request
    porta = int((cfg.get("connect", {}) or {}).get("porta_webhooks", 8766))
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{porta}/saude", timeout=2).read()
        r.item("servidor de webhooks", True, f"respondendo em 127.0.0.1:{porta}")
    except Exception:
        r.item("servidor de webhooks", None, f"nada em 127.0.0.1:{porta}", "ele sobe junto com o Jarvis (main.py); "
               "rode o diagnóstico com o Jarvis aberto para testar")
    from jarvis.connect.conectores import catalogo
    for c in catalogo(None, cofre(), cfg):
        if c["situacao"] == "implementado" and c["id"] in ("evolution", "gmail", "agenda", "claude", "ollama"):
            r.item(f"integração: {c['nome']}", {"conectado": True, "erro": False}.get(c["estado"]),
                   f"{c['estado']}" + (f" ({c['detalhe']})" if c["detalhe"] else ""), c["requisitos"][:160])


def main(argv=None):
    p = argparse.ArgumentParser(description="Diagnóstico ao vivo do Jarvis")
    p.add_argument("--ouvir", action="store_true", help="tocar a voz gerada no alto-falante")
    p.add_argument("--abrir", action="store_true", help="abrir e fechar o Bloco de Notas de verdade")
    p.add_argument("--cerebro", action="store_true", help="fazer uma pergunta real ao Claude (gasta cota)")
    p.add_argument("--comparar", action="store_true", help="testar XTTS e Qwen3-TTS e dizer qual usar")
    p.add_argument("--so", nargs="+", choices=PARTES, help="rodar só estas partes")
    p.add_argument("--url", default="https://pt.wikipedia.org/wiki/J.A.R.V.I.S.", help="página para testar a leitura")
    args = p.parse_args(argv)
    partes = set(args.so or [x for x in PARTES if x != "cerebro" or args.cerebro])
    cfg = carregar()
    r = Relatorio()
    with tempfile.TemporaryDirectory(prefix="jarvis_diag_") as tmp:
        pasta = Path(tmp)
        etapas = [("ollama", lambda: testar_ollama(cfg, r)),
                  ("memoria", lambda: testar_memoria(cfg, r, pasta)),
                  ("aprendizado", lambda: testar_aprendizado(cfg, r, pasta)),
                  ("internet", lambda: testar_internet(cfg, r, args.url)),
                  ("voz", lambda: comparar_vozes(cfg, r, args.ouvir) if args.comparar else testar_voz(cfg, r, args.ouvir)),
                  ("acoes", lambda: testar_acoes(cfg, r, args.abrir, pasta)),
                  ("connect", lambda: testar_connect(cfg, r, pasta)),
                  ("cerebro", lambda: testar_cerebro(cfg, r, pasta))]
        for nome, etapa in etapas:
            if nome not in partes:
                continue
            try:
                etapa()
            except KeyboardInterrupt:
                raise
            except Exception as e:     # uma parte quebrada não impede as outras
                r.item(nome, False, f"erro inesperado: {type(e).__name__}: {e}", "veja o traceback acima")
                traceback.print_exc()
    r.resumo()
    r.gravar()
    return 1 if any(i[1] == "❌" for i in r.itens) else 0


if __name__ == "__main__":
    sys.exit(main())
