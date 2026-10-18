from jarvis.tools.spotify import _ordenar


def _f(nome, artista, pop=0):
    return {"name": nome, "artists": [{"name": artista}], "popularity": pop}


def test_titulo_exato_vence_busca_por_relevancia():
    faixas = [_f("333", "Matuê", 80), _f("Máquina do Tempo", "Matuê", 90), _f("1993", "Matuê", 60),
              _f("1993", "Outro Artista", 99)]
    assert _ordenar("1993 Matuê", faixas)[0] is faixas[2]


def test_so_artista_ordena_por_popularidade():
    faixas = [_f("A", "Matuê", 10), _f("B", "Matuê", 90)]
    assert _ordenar("Matuê", faixas)[0] is faixas[1]
