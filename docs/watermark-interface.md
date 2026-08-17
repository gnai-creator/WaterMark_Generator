# Watermark Runtime Interface

## 1. Objetivo

Esta interface define como um adaptador local deve aplicar e detectar uma
watermark estatística durante a geração de texto.

O adaptador deve ser agnóstico ao provedor e não deve armazenar, imprimir,
registrar ou versionar segredos.

## 2. Limites de segurança

O runtime deve ler exclusivamente das variáveis de ambiente:

- `KEY`: Watermark Key secreta;
- `KEY_ID`: identificador da chave ativa.

Regras obrigatórias:

- nunca imprimir `KEY`;
- nunca registrar `KEY`;
- nunca incluir `KEY` em exceções ou tracebacks;
- nunca persistir `KEY`;
- nunca incluir `KEY` em manifests;
- nunca enviar `KEY` por rede;
- nunca usar `KEY` como Identity Private Key;
- apagar referências ao segredo da memória assim que possível;
- falhar de forma segura quando uma variável estiver ausente.

Os testes devem usar somente chaves aleatórias descartáveis.

## 3. Runtime de destino

Runtime inicial:

`LOCAL_TEST_RUNTIME`

O primeiro adaptador deve operar sobre uma interface abstrata de logits, sem
depender diretamente de OpenAI, Anthropic, Google ou xAI.

Uma integração concreta futura deverá implementar:

```python
class LogitsRuntime(Protocol):
    @property
    def vocabulary_size(self) -> int:
        ...

    def encode(self, text: str) -> list[int]:
        ...

    def decode(self, token_ids: list[int]) -> str:
        ...

    def next_token_logits(self, token_ids: list[int]) -> list[float]:
        ...
```

## 4. Configuração pública

A configuração pública deve ser recebida por um objeto semelhante a:

```python
@dataclass(frozen=True)
class WatermarkConfig:
    protocol_version: str
    period: int
    context_width: int
    gamma: float
    strength: float
    minimum_tokens: int
```

Valores iniciais para testes:

```text
protocol_version = FMM-0.1
period = 64
context_width = 4
gamma = 0.5
strength = 1.0
minimum_tokens = 100
```

Esses valores são experimentais e não constituem calibração definitiva.

## 5. Função de intensidade

A função específica de intensidade não deve ser incluída neste documento.

O adaptador deve recebê-la por uma interface:

```python
class IntensityFunction(Protocol):
    def __call__(self, position: int, period: int) -> float:
        ...
```

Requisitos:

- retornar um número finito;
- retornar valor maior ou igual a zero;
- ser determinística;
- não acessar a Watermark Key;
- não alterar tokens, textos ou arquivos;
- não executar operações de rede;
- falhar diante de valores inválidos.

Para testes, use uma implementação pública descartável, por exemplo:

```python
def constant_test_intensity(position: int, period: int) -> float:
    return 1.0
```

Essa função de teste não representa a watermark de produção.

## 6. Derivação por documento

Para cada documento:

```text
document_seed =
    HMAC-SHA256(
        KEY,
        domain_separator
        || protocol_version
        || KEY_ID
        || document_id
        || timestamp
    )
```

Separador de domínio:

```text
watermark-generator/v1/document-seed
```

Requisitos:

- `document_id` deve ser explícito;
- `timestamp` deve ser fornecido pelo chamador;
- geração e detecção devem receber os mesmos metadados;
- o segredo não pode ser derivado da equação;
- documentos diferentes devem possuir seeds diferentes.

## 7. Partição do vocabulário

Para cada posição e token candidato:

```text
context_digest =
    HMAC-SHA256(
        document_seed,
        domain_separator
        || previous_context_tokens
        || position
    )
```

Separador:

```text
watermark-generator/v1/token-partition
```

A classificação de um token candidato deve usar:

```text
candidate_digest =
    HMAC-SHA256(
        context_digest,
        candidate_token_id
    )
```

O token pertence ao conjunto favorecido quando o valor normalizado derivado do
digest for menor que `gamma`.

A serialização de inteiros deve usar unsigned big-endian de 64 bits.

Para evitar ambiguidades, campos textuais são codificados em UTF-8 e precedidos
por seu comprimento unsigned big-endian de 64 bits. O contexto inclui primeiro
a quantidade de tokens e depois cada token no mesmo formato.

## 8. Aplicação aos logits

Para cada posição:

```text
bias = strength * intensity(position, period)
```

Para cada token pertencente ao conjunto favorecido:

```text
modified_logit[token_id] =
    original_logit[token_id] + bias
```

Os demais logits devem permanecer inalterados.

Requisitos:

- não modificar a entrada original in-place;
- rejeitar NaN e infinito;
- preservar o tamanho do vocabulário;
- não modificar tokens já gerados;
- não alterar código, hashes ou dados depois da geração;
- permitir desativação explícita;
- não alegar aplicação quando não houver acesso real aos logits.

## 9. Resultado da aplicação

O aplicador deve retornar:

```python
@dataclass(frozen=True)
class ApplicationResult:
    logits: tuple[float, ...]
    position: int
    favored_token_count: int
    key_id: str
    applied: bool
```

Nunca deve retornar a Watermark Key.

## 10. Detector

O detector deve reconstruir a mesma partição usando:

- `KEY`;
- `KEY_ID`;
- `document_id`;
- `timestamp`;
- versão do protocolo;
- sequência de tokens;
- configuração pública.

Para cada token observado:

```text
g_t = 1 se o token pertence ao conjunto favorecido
g_t = 0 caso contrário
```

Calcular:

```text
numerator =
    sum(intensity_t * (g_t - gamma))

denominator =
    sqrt(
        gamma
        * (1 - gamma)
        * sum(intensity_t ** 2)
    )

z_score = numerator / denominator
```

O detector deve retornar:

```python
@dataclass(frozen=True)
class DetectionResult:
    z_score: float
    token_count: int
    favored_token_count: int
    weighted_score: float
    sufficient_sample: bool
    key_id: str
```

O detector não deve classificar automaticamente um documento como autêntico.

## 11. Threshold

Nenhum threshold deve ser tratado como universal.

Para testes apenas:

```text
experimental_threshold = 4.0
```

O resultado deve ser descrito como evidência estatística experimental, não como:

- prova jurídica;
- confirmação de autoria;
- consentimento;
- aprovação;
- endosso;
- autorização de publicação.

## 12. Textos curtos

Quando:

```text
token_count < minimum_tokens
```

o resultado deve conter:

```text
sufficient_sample = false
```

Nesse caso, a CLI não deve emitir uma conclusão de alta confiança.

## 13. CLI esperada

Aplicação sobre logits de teste:

```bash
watermark-generator apply \
  --document-id DOCUMENTO-001 \
  --timestamp 2026-08-17T12:00:00Z \
  --tokens tokens.json \
  --logits logits.json
```

Detecção:

```bash
watermark-generator detect \
  --document-id DOCUMENTO-001 \
  --timestamp 2026-08-17T12:00:00Z \
  --tokens tokens.json
```

A CLI nunca deve aceitar `KEY` como argumento de linha de comando, pois
argumentos podem aparecer no histórico do shell e na lista de processos.

## 14. Estados honestos

O adaptador deve usar:

```text
APPLIED
NOT_APPLIED_TECHNICAL_LIMITATION
DISABLED
INVALID_CONFIGURATION
INSUFFICIENT_SAMPLE
```

`APPLIED` somente pode ser retornado quando os logits foram realmente
modificados antes da amostragem.

## 15. Testes obrigatórios

Os testes devem demonstrar que:

1. `KEY` ausente causa falha segura;
2. `KEY_ID` ausente causa falha segura;
3. nenhum segredo aparece em stdout ou stderr;
4. nenhum segredo aparece em exceções;
5. chaves diferentes produzem partições diferentes;
6. documentos diferentes produzem seeds diferentes;
7. contextos diferentes produzem partições diferentes;
8. a mesma entrada produz resultado determinístico;
9. somente tokens favorecidos recebem bias;
10. logits originais não são modificados;
11. geração e detecção reconstroem a mesma partição;
12. adulteração de tokens altera o resultado;
13. textos curtos retornam `INSUFFICIENT_SAMPLE`;
14. NaN e infinito são rejeitados;
15. `gamma` deve estar estritamente entre 0 e 1;
16. `period`, `context_width` e `minimum_tokens` devem ser positivos;
17. a função de teste não é apresentada como watermark de produção;
18. os testes usam exclusivamente chaves descartáveis.

## 16. Fora de escopo inicial

Não implementar inicialmente:

- integração direta com APIs remotas;
- envio de chaves para provedores;
- persistência da Watermark Key;
- leitura automática de `exports/*/key.env`;
- carregamento de `.env` pelo adaptador;
- parser de equações;
- execução dinâmica de código vindo de arquivos;
- alegações de robustez contra tradução, paráfrase ou edição;
- autenticação jurídica baseada apenas no detector.

## 17. Implementação privada da intensidade

A fórmula privada pode permanecer em `private/watermark.txt`, mas o adaptador
inicial não deve tentar interpretar equações textuais nem executar código
dinâmico vindo desse arquivo.

Para aplicação real, deve-se escolher explicitamente uma destas estratégias:

1. módulo privado local ignorado pelo Git que implemente `IntensityFunction`;
2. tabela privada e validada de intensidades;
3. implementação pública cuja divulgação tenha sido autorizada.

O carregamento de uma implementação privada deve usar uma interface fixa,
validação de resultados e configuração explícita do runtime. Ele não deve
executar arbitrariamente conteúdo textual fornecido como especificação.
