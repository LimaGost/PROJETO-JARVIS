---
name: verificar-antes-de-concluir
description: Sempre que for dizer que algo está pronto, corrigido, funcionando ou passando (código, configuração, integração, tarefa no computador), ANTES de afirmar. Exige ver a prova com os próprios olhos.
origem: voce
credito: adaptada de verification-before-completion (github.com/obra/superpowers, licença MIT)
---

# Verificar antes de concluir

**Regra de ferro: nada de "pronto" sem prova nova.** Se você não conferiu agora, não pode afirmar.

## Antes de dizer que terminou
1. **Qual verificação prova isso?** (rodar os testes, abrir a página, chamar a API, ver o arquivo salvo, conferir o
   e-mail na caixa de saída...)
2. **Faça a verificação completa**, agora.
3. **Leia o resultado inteiro:** código de saída, quantos testes falharam, a resposta da API.
4. **O resultado confirma?**
   - Sim: diga que está pronto E mostre a prova ("os 42 testes passaram", "a API respondeu 200 com o pedido").
   - Não: diga o estado real, com a prova.

## O que NÃO serve como prova
- "Deve funcionar", "parece certo", "tenho confiança".
- Uma verificação antiga, de antes da última mudança.
- Verificação parcial ("o lint passou" não prova que o programa funciona).
- O relato de outro agente ou do programador em segundo plano sem conferir o que ele fez.

## Ao falar com o usuário
Seja direto: o que foi feito, como confirmou, e o que ficou faltando ou não deu para conferir. Melhor admitir que
não verificou do que afirmar algo que não viu.
