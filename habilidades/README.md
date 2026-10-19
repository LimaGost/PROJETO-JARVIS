# Habilidades do Jarvis

Cada pasta é uma habilidade no formato das **Claude Skills**: um `SKILL.md` com `name` e `description` no cabeçalho e
o passo a passo em Markdown. Opcionalmente, uma pasta `references/` com textos de apoio.

- **A cada pedido** o Jarvis vê só o nome e a descrição de cada habilidade; as instruções completas só entram quando
  ele chama `usar_habilidade` (por isso a descrição deve dizer **quando** usar).
- **Ensinar por voz:** "Jarvis, aprende: sempre que eu pedir X, faça assim..." vira uma habilidade nova (`criar_habilidade`).
- **Instalar de fora:** "instala a habilidade deste link" com uma pasta do GitHub, ex.:
  `https://github.com/anthropics/skills/tree/main/skills/pdf`, ou o id do catálogo `anthropics/skills/pdf` (sempre pede
  confirmação; vem a pasta com os textos de apoio, sem scripts). Ou copie a pasta para cá.
- **Procurar:** "tem habilidade para X?" usa `buscar_habilidade` no catálogo aberto skills.sh.
- **Scripts nunca são executados** por aqui: o Jarvis só lê o texto. Ações que a habilidade mandar fazer passam
  pelas permissões de sempre.
- Habilidades apagadas vão para `data/habilidades_apagadas/`.
