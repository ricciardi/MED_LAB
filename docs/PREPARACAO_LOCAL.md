# Preparação do acervo sem chamadas à DeepSeek

O escopo é de **14.359 fontes ativas**: 73 fichas anteriores preservadas e
14.286 novas fichas. Foram excluídos 117 relatos veterinários, incluindo uma
ficha anterior; as 13 fontes previamente excluídas continuam excluídas. Os
registros mínimos de exclusão impedem sua reintrodução automática. Essas
contagens descrevem o escopo, não afirmam que todas as traduções já terminaram.
O inventário não significa que cada especialidade tenha 300 casos.

A preparação extrai a apresentação do paciente, antecedentes, achados de
exames, diagnóstico de referência, conduta histórica e raciocínio diagnóstico.
Não traduz artigos completos. O paciente recebe somente os fatos da entrevista;
o raciocínio de referência fica reservado à avaliação e ao resultado final.

O tradutor funciona localmente, em ambiente separado, com
`Helsinki-NLP/opus-mt-tc-big-en-pt`, revisão
`9f2863d807ecf91a374bdbecb8d01e402e90622e`, destino `>>pob<<`.
Não usa credenciais, APIs de inferência ou saldo da DeepSeek. A aquisição inicial
baixa arquivos públicos do modelo; a tradução posterior usa somente esses arquivos.

## Retomada

Os arquivos privados ficam em `instance/local-preparation/`. Cada etapa registra
identidade da fonte, hashes e versão do processamento. O cache de tradução e o
journal preservam trabalho concluído; a montagem invalida somente fichas cujas
entradas ou código de montagem mudaram.

```sh
.venv/bin/python scripts/local_corpus.py plan
.venv/bin/python scripts/local_corpus.py extract
.venv/bin/python scripts/local_corpus.py queue
instance/local-preparation/venv/bin/python scripts/local_translate_cases.py \
  --input instance/local-preparation/corpus/translation-queue.jsonl \
  --output instance/local-preparation/corpus/translations.jsonl \
  --backend ct2 --device cpu --batch-size 96 --beam-size 1 --threads 6
.venv/bin/python scripts/local_corpus.py build \
  --authorization-reference 'Autorização expressa do proprietário para uso com revisão posterior.'
.venv/bin/python scripts/local_corpus.py audit
```

Não regenere a fila enquanto o tradutor a estiver lendo. Mudanças posteriores
na extração devem usar uma fila adicional; traduções idênticas podem ser
reutilizadas por hash. Erros permanecem explicitamente pendentes até recuperação:
texto original em inglês não é convertido em ficha concluída.

## Incorporação e disponibilidade

`scripts/stream_local_corpus.py` acompanha o progresso local e entrega à montagem
e à incorporação os casos que já estão completos. O banco definitivo recebe cada
ficha pronta durante o processamento, sem esperar a conclusão de todo o acervo.
O acompanhamento deve conferir que a ficha corresponde ao pacote e ao estado de
montagem atuais antes de selecionar seu PMCID.

Na operação atual, o tradutor lê `translation-queue-final.jsonl` e grava
`translations-final.jsonl` (com sufixo `.partial.jsonl` enquanto escreve). O
acompanhamento consome também o primeiro journal para aproveitar imediatamente
as traduções preservadas no cache:

```sh
.venv/bin/python scripts/stream_local_corpus.py \
  --journal instance/local-preparation/corpus/translations.partial.jsonl \
  --journal instance/local-preparation/corpus/translations-final.jsonl \
  --authorization-reference 'Solicitação expressa do proprietário em 15/10/2026 para disponibilizar todas as fichas antes da revisão clínica.' \
  --apply --watch
```

O acompanhamento exige todas as unidades selecionadas de cada caso e lê somente
registros completos. Diários `translations-final-recovery-*.jsonl` entram
automaticamente. Uma unidade com erro ou divergência numérica permanece pendente
até sua recuperação. `stream-state.json` apresenta a contagem confirmada no banco;
o diário e o cache permitem retomar sem traduzir novamente os sucessos.

`scripts/integrate_local_corpus.py --ready-only` verifica apenas as fichas prontas
selecionadas, incluindo as anteriores preservadas. A ausência das demais não
interrompe a incorporação; uma ficha presente, porém inválida, continua sendo um
erro explícito. Sem `--ready-only`, a conferência exige todas as fichas selecionadas.
Sem `--apply`, somente relata o que será incorporado.

Com `--apply`, cria uma cópia consistente do SQLite antes da primeira alteração
da operação, preserva o conteúdo clínico existente e confirma cada ficha em uma
transação própria. O lock compartilhado com a importação evita duas preparações
simultâneas no mesmo diretório. Lotes seguintes com o mesmo `--operation-id`
reutilizam o backup inicial; o marcador privado confere banco, plano e autorização.

```sh
.venv/bin/python scripts/integrate_local_corpus.py \
  --authorization-reference 'Solicitação expressa do proprietário em 15/10/2026 para disponibilizar todas as fichas antes da revisão clínica.' \
  --ready-only --operation-id local-human-corpus-20261015 \
  --pmcid PMC123 --pmcid PMC456 \
  --apply
```

Substitua os PMCIDs de exemplo por um lote explicitamente montado e conferido
contra os pacotes atuais; durante a tradução, prefira o acompanhamento contínuo.
Sem essas
opções, `--ready-only` considera as fichas prontas do plano inteiro. O relatório
separa `expected`, `completed` e `pending`, contagens globais obtidas no banco,
de `selected`, `ready`, `awaitingCase` e `batchCompleted`, referentes à chamada.
`completed` significa fonte traduzida e vinculada a uma ficha; não significa
revisão clínica concluída. Repetir um lote preserva os casos existentes e não cria
duplicatas. Após uma falha, as transações anteriores continuam confirmadas e a
mesma seleção pode ser retomada.

Para chamar o helper em um processo contínuo, crie `app = create_app()` uma vez e
abra `with app.app_context():` em cada lote. Dentro desse contexto, chame
`integrate_corpus(..., ready_only=True, pmcids=ids_prontos, apply=True,
operation_id="local-human-corpus-20261015", authorization_reference=referencia)`.
O helper usa uma sessão exclusiva desse fluxo, confirma e libera sessões por
ficha; não execute alterações pendentes de outra tarefa nessa mesma sessão.
Não é necessário chamar `init-db`, `create_all` ou criar um contexto HTTP por lote.

A disponibilidade solicitada pelo proprietário é registrada em `useAuthorization`.
A ficha continua com `reviewStatus: review_required`; a autorização não cria uma
revisão clínica fictícia. O avaliador usa os dados disponíveis como referência da
simulação, e a revisão docente pode corrigir o conteúdo depois. Dados ausentes
permanecem identificados, sem inventar resultados normais ou antecedentes negativos.

O pacote de implantação contém a aplicação e fichas em português. O ambiente de
tradução e seu modelo não são necessários no cPanel nem no tablet. O arquivo de
retomada guarda separadamente as fontes e etapas privadas; não inclui credenciais,
contas de usuários ou o banco de produção.

## Conferência técnica

`audit-report.json` registra cobertura, validação estrutural, fichas com exames,
referência diagnóstica, sexo conhecido e distribuição por especialidade.
`case-checksums.json` permite conferir o conteúdo exato de cada ficha.
Essas conferências e os testes automatizados locais não constituem revisão clínica
nem testes de inferência com a DeepSeek.
