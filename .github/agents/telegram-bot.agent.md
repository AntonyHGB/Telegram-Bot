---
name: telegram-bot
description: "Agente para manter o bot Telegram experimental, seus handlers Python, configuracao, integracoes HTTP e testes."
---

# Escopo

Voce trabalha no bot definido em `bot_tele_parrot.py`.

## Regras

- Nunca mantenha token, chave ou segredo no codigo; use variaveis de ambiente.
- Antes de atualizar a biblioteca Telegram, confirme a API usada e migre handlers de forma consistente.
- Adicione timeout e tratamento de erro para toda chamada HTTP externa.
- Prefira extrair funcoes puras para permitir testes sem rede.
- Crie pinagem de dependencias antes de adicionar funcionalidades maiores.
- Nao executar o bot real nem publicar alteracoes sem configuracao explicita do usuario.
- Nao publique nem execute `git push`.
