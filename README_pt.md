# Watermark Generator

CLI local e agnóstica de protocolo para gerar, rotacionar, revogar, exportar e
verificar chaves criptográficas destinadas a watermarks de LLMs.

O sistema separa dois papéis:

- a **Identity Root**, baseada em Ed25519, autoriza criação, rotação e revogação;
- as **Watermark Keys** são segredos operacionais independentes, derivados para
  cada watermark, provedor e geração.

Assim, o vazamento de uma Watermark Key não permite, por si só, autorizar
legitimamente sua sucessora.

## Fórmula MTW não incluída

A fórmula MTW específica do titular foi intencionalmente removida deste
repositório. Ela não é distribuída como exemplo nem como autorização de uso.

Use uma especificação de watermark criada por você ou para a qual possua
autorização. Os placeholders da documentação devem ser substituídos antes do
uso. Este aviso registra a intenção e as condições de disponibilização do
projeto; eventuais direitos sobre fórmulas, textos, marcas ou implementações
dependem da legislação aplicável.

Uma watermark representa apenas um indício técnico de proveniência. Ela não é,
isoladamente, prova conclusiva de autoria jurídica, consentimento, aprovação,
endosso ou autorização de publicação.

## Arquitetura de segurança

- Identity Private Key Ed25519 e segredo mestre aleatório de 256 bits ficam em
  um cofre local criptografado com AES-256-GCM.
- A chave do cofre é derivada da passphrase por scrypt (`N=32768, r=8, p=1`).
- Chaves de provedor são derivadas com HKDF-SHA256 e separação explícita de
  domínio, watermark, prefixo, provedor e geração.
- Manifests são serializados como JSON canônico, assinados com Ed25519 e
  encadeados pelo hash SHA-256 do manifesto anterior.
- Transições são irreversíveis: `ACTIVE -> REVOKED` ou `ACTIVE -> RETIRED`.
- Atualizações com sequência inferior ao estado local são rejeitadas como
  rollback.

Segredos não são gravados em texto puro por padrão. `.env`, o cofre, exports e
arquivos sensíveis são ignorados pelo Git.

## Instalação

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Inicialização

```bash
run init --prefix DEMO
```

Guarde uma cópia offline segura da Identity Private Key e do cofre. A perda da
Identity Root impede provar continuidade criptográfica em futuras rotações.

## Gerar chaves

Com uma especificação própria:

```bash
run \
  --models all \
  --prefix DEMO \
  --watermark 'SUA_ESPECIFICACAO_DE_WATERMARK'
```

Ou por arquivo:

```bash
run \
  --models openai,anthropic \
  --watermark-file examples/mtw.txt
```

Os segredos não aparecem no terminal. `--show-secrets` exige uma decisão
explícita e deve ser usado somente em ambiente controlado.

## Rotação, revogação e estado

```bash
run rotate --model openai --reason compromised
run revoke --key DEMO-OPENAI-01 --reason compromised
run status
```

## Verificação e exportação

```bash
run verify public/manifests/<manifesto>.json \
  --identity public/identity.json

run export --model openai
run verification-bundle
```

Uma nova sessão de LLM não conhece automaticamente a identidade. Para verificar
continuidade, forneça a chave pública previamente confiada e a cadeia completa
de manifests assinados.

## Testes

```bash
pytest
```

O projeto utiliza a biblioteca madura `cryptography`; nenhuma primitiva
criptográfica é implementada manualmente.
