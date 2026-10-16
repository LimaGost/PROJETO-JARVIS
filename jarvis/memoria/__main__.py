"""Ver e editar o que o Jarvis sabe sobre você.

  python -m jarvis.memoria listar            todos os fatos
  python -m jarvis.memoria buscar "texto"    fatos mais parecidos com o texto
  python -m jarvis.memoria apagar ID         apaga um fato
  python -m jarvis.memoria dias              resumos de cada dia
  python -m jarvis.memoria refletir [--hoje] roda a reflexão agora (--hoje inclui as conversas de hoje)
"""
import sys

from jarvis.config import carregar
from jarvis.memoria import criar


def main(args: list[str]):
    if not args or args[0] not in ("listar", "buscar", "apagar", "dias", "refletir"):
        print(__doc__)
        return
    memoria, reflexao = criar(carregar())
    if memoria is None:
        print("A memória está desligada no config.yaml.")
        return
    comando = args[0]
    if comando == "listar":
        fatos = memoria.fatos()
        for f in fatos:
            print(f"[{f.id:>3}] ({f.categoria}) {f.texto}   — {f.atualizado[:10]}")
        print(f"\n{len(fatos)} fatos. Pendentes de reflexão: {', '.join(memoria.dias_pendentes(True)) or 'nenhum'}")
    elif comando == "buscar":
        for f, s in memoria.buscar(" ".join(args[1:]), k=10, minimo=0.0):
            print(f"{s:.2f} [{f.id}] {f.texto}")
    elif comando == "apagar":
        f = memoria.fato(int(args[1]))
        print(f"Apagado: {f.texto}" if f and memoria.apagar_fato(f.id) else "Não existe esse id.")
    elif comando == "dias":
        for dia, resumo in memoria.episodios():
            print(f"{dia}: {resumo}\n")
    elif comando == "refletir":
        r = reflexao.executar(incluir_hoje="--hoje" in args)
        print(f"Dias: {', '.join(r['dias']) or 'nenhum pendente'} | novos {r['novos']}, "
              f"atualizados {r['atualizados']}, repetidos {r['repetidos']}. Detalhes em data/logs/reflexao.log")


if __name__ == "__main__":
    main(sys.argv[1:])
