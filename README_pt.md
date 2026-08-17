# Watermark Generator

Ferramenta para criar e proteger chaves de watermark, manter manifests
assinados e aplicar uma marca estatística de proveniência em **LLMs locais**.

Este projeto funciona exclusivamente com modelos locais que permitam acesso e
modificação dos logits antes da amostragem de cada token. Ele não aplica
watermark estatística em ChatGPT, Codex, Claude, Gemini ou outras LLMs online.

Uma marca indica proveniência declarada. Ela não prova, isoladamente, autoria
jurídica, consentimento, aprovação ou endosso.

## Instalação

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Edite `.env` e defina uma passphrase forte:

```dotenv
WATERMARK_GENERATOR_PASSPHRASE='uma-passphrase-forte-e-unica'
```

O `.env` é ignorado pelo Git. Não o compartilhe nem versione.

## Criar identidade e chaves

```bash
run init --prefix <YOUR_PREFIX>

run \
  --models all \
  --prefix <YOUR_PREFIX> \
  --watermark-file private/watermark.txt
```

A fórmula permanece em `private/watermark.txt`, ignorado pelo Git. O sistema
armazena somente seu hash nos manifests.

## Watermark estatística em LLM local

Para modelos locais compatíveis com Transformers, instale:

```bash
pip install -e '.[local]'
```

Configure no `.env`:

```dotenv
WATERMARK_LOCAL_MODEL='/caminho/para/modelo-local'
WATERMARK_INTENSITY_TABLE='private/intensity.json'
WATERMARK_SESSION_PROVIDER='openai'
WATERMARK_PERIOD=64
WATERMARK_CONTEXT_WIDTH=4
WATERMARK_GAMMA=0.5
WATERMARK_STRENGTH=1.0
WATERMARK_MINIMUM_TOKENS=100
```

`private/intensity.json` deve conter exatamente `WATERMARK_PERIOD` números
finitos e não negativos calculados a partir da sua função privada.

Inicie a sessão:

```bash
run session-local
```

Digite `/exit` para encerrar. Durante a sessão, o programa:

1. deriva a chave ativa diretamente do cofre;
2. obtém os logits reais do modelo local;
3. aplica o bias antes de amostrar cada token;
4. mantém o segredo somente no processo;
5. calcula o escore estatístico do texto gerado.

O status `<YOUR_PREFIX>: APPLIED` é usado somente quando os logits foram
realmente modificados. Ele não equivale a confirmação de autoria ou endosso.

## Segurança dos arquivos

| Caminho | Conteúdo | Compartilhar? |
| --- | --- | --- |
| `.env` | Passphrase e configuração local | **Nunca** |
| `private/vault.json` | Identidade privada e segredo mestre criptografados | **Nunca** |
| `private/watermark.txt` | Sua especificação | Apenas se decidir divulgá-la |
| `exports/<provider>/key.env` | Chave operacional | **Nunca em chats ou Git** |
| `public/identity.json` | Chave pública e fingerprint | Pode divulgar deliberadamente |
| `public/manifests/*.json` | Histórico público assinado | Pode divulgar deliberadamente |

`private/`, `exports/`, `public/`, `.env` e `state.json` são ignorados pelo Git.
Faça backups offline seguros do cofre, estado, identidade pública e manifests.

## Ciclo de vida e verificação

```bash
run status
run rotate --model openai --reason compromised
run revoke --key <YOUR_PREFIX>-OPENAI-01 --reason compromised
run export --model openai
run verify public/manifests/<manifesto>.json --identity public/identity.json
```

Nunca envie `KEY`, `key.env`, `.env`, passphrase, cofre, `master_secret` ou
`identity_private_key` para uma conversa, issue, log ou repositório.

## Testes

```bash
pytest
```

Detalhes técnicos estão em
[`docs/watermark-interface.md`](docs/watermark-interface.md).
