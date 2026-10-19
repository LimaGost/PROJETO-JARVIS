---
name: depuracao-sistematica
description: Quando aparecer um erro, bug, teste falhando, integração/API que não responde ou qualquer comportamento estranho (no código, no Jarvis ou no Microvix), ANTES de propor uma correção. Acha a causa raiz em vez de chutar.
origem: voce
credito: adaptada de systematic-debugging (github.com/obra/superpowers, licença MIT)
---

# Depuração sistemática

**Regra de ferro: nenhuma correção sem antes achar a causa raiz.** Corrigir o sintoma é falhar.
Use principalmente quando estiver com pressa, quando "é só uma mudancinha" parecer óbvio, ou quando uma correção já
não funcionou.

## Fase 1: investigar a causa
1. **Leia a mensagem de erro inteira** (traceback, código de status HTTP, linha e arquivo). Ela costuma dizer a resposta.
2. **Reproduza:** quais passos exatos disparam o problema? Acontece sempre?
3. **O que mudou?** Último commit, configuração, versão de biblioteca, dado de entrada, credencial que expirou.
4. **Em sistemas com várias camadas** (ex.: Jarvis → API → Microvix), descubra EM QUAL camada quebra: confira a
   entrada e a saída de cada uma antes de mexer em qualquer coisa.
5. **Siga o dado para trás:** de onde veio o valor errado? Corrija na origem, não onde o erro apareceu.

## Fase 2: comparar com o que funciona
- Ache um caso parecido que funciona (outro endpoint, outra loja, outro teste) e liste TODAS as diferenças.
- Se for uma API ou biblioteca, leia a documentação/exemplo de referência por inteiro, não por cima.

## Fase 3: hipótese e teste
- Diga uma hipótese só: "acho que X é a causa porque Y".
- Teste com a MENOR mudança possível, uma variável por vez.
- Não deu certo? Nova hipótese. Não empilhe correções.

## Fase 4: corrigir
1. Se possível, escreva primeiro um teste (ou um passo de reprodução) que falha.
2. Faça UMA correção, na causa raiz.
3. Confirme: o teste passa e nada mais quebrou (veja a habilidade verificar-antes-de-concluir).
4. **Três correções falharam?** Pare. O problema provavelmente é de arquitetura; explique ao usuário o que descobriu
   e discuta antes de tentar a quarta.

## Sinais de que você saiu do processo
"Vou tentar isso rapidinho", "deve ser X, vou corrigir", várias mudanças de uma vez, propor solução sem ter rastreado
o dado. Se pegar um desses: volte para a Fase 1.

## Ao explicar para o usuário
Conte a causa em linguagem simples, o que foi feito e como confirmou. Ele está aprendendo a programar: vale dizer
qual pista levou à causa.
