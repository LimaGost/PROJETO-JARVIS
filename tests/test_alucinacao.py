import pytest

from jarvis.brain.llm import _ALEGA_ACAO, _PROMETE_ACAO, _mentira


@pytest.mark.parametrize("frase", [
    "O volume foi ajustado para mudo.", "Aqui está. O volume foi ajustado para mudo.", "Abri o Spotify, senhor.",
    "O arquivo foi apagado.", "A pasta foi criada na área de trabalho.", "Pronto, fechei o Discord.",
    "O computador será desligado... a tela está bloqueada.", "Pausei a música.", "Os arquivos foram movidos.",
    "O som está agora silenciado.", "Coloquei o volume em trinta por cento.", "Abro a calculadora para você.",
    "Aumento o volume, senhor.", "Aqui está. O volume foi reduzido para zero.", "Feito, senhor.",
    "O modo escuro foi ativado.", "Memorizei que seu time é o Palmeiras.", "Esqueci que você trabalha como analista.",
    "Anotei, senhor.", "Senhor Carlos, registro com prazer que sua esposa se chama Juliana.", "Está anotado.",
    "Clicando em [17] para limpar a entrada.", "Abrindo o Spotify agora.", "Estou digitando o texto.",
    "Estou abrindo a calculadora, senhor.", "Digitando a conta na calculadora.",
])
def test_detecta_alegacao_de_acao(frase):
    assert _ALEGA_ACAO.search(frase)


@pytest.mark.parametrize("frase", [
    "Vou abrir o Spotify.", "Vou verificar agora.", "Um momento, vou pesquisar.", "Vou buscar os detalhes para ele.",
    "Aguarde, senhor.", "Vou lembrar disso.",
])
def test_detecta_promessa(frase):
    assert _PROMETE_ACAO.search(frase)


@pytest.mark.parametrize("frase", [
    "A capital da Itália é Roma.", "Sete vezes oito é cinquenta e seis.", "Paris tem cerca de dois milhões de habitantes.",
    "Por que o computador foi ao psicólogo?", "Estou aqui para ajudar.", "Não tenho acesso a essa informação.",
    "A inteligência artificial é uma ferramenta extraordinária.", "Até logo, senhor.",
    "Vou bem, obrigado por perguntar.", "Sete vezes oito é cinquenta e seis, senhor.",
    "O Brasil foi descoberto em 1500.", "A Revolução Francesa começou em 1789.", "A cidade foi fundada em 1554.",
    "Sua filha se chama Sofia.", "Você mora em Campinas.", "Seu time é o Palmeiras.",
    "Estou bem, obrigado.", "Estou aqui para ajudar.", "O resultado é cento e quarenta e quatro.",
    "Chovendo bastante em Campinas hoje.", "A calculadora mostra 144.",
])
def test_nao_dispara_em_conversa(frase):
    assert not _ALEGA_ACAO.search(frase) and not _PROMETE_ACAO.search(frase)


def test_alegacao_depois_de_usar_ferramenta_e_legitima_mas_promessa_nao():
    # depois de chamar 'abrir_programa', "Abri o Spotify" é verdade; "vou buscar" sem chamada continua vazio
    assert not _mentira("Abri o Spotify.", vigiar_alegacoes=False, vigiar_promessas=True)
    assert _mentira("Vou buscar os detalhes.", vigiar_alegacoes=False, vigiar_promessas=True)
