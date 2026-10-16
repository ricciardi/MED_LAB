# Testes reais do paciente e do avaliador — 15/10/2026

Este relatório registra, em ordem cronológica, chamadas reais à API DeepSeek, incluindo falhas e versões substituídas. A rodada mais recente está em [Composição da nota e parecer pedagógico](#composição-da-nota-e-parecer-pedagógico). As seções anteriores descrevem o comportamento testado na ocasião; as regras antigas de responder apenas com fatos documentados foram substituídas pela elaboração controlada. Ainda há variabilidade de notas e limites semânticos; não há evidência para declarar calibração clínica nem extrapolar as amostras ao acervo inteiro.

A etapa posterior de [catálogo e latência local de voz](#catálogo-e-latência-local-de-voz-sem-novas-chamadas-deepseek) não fez novas chamadas à DeepSeek; suas medições são independentes das rodadas pagas descritas abaixo.

## Método e rastreabilidade

O executável `scripts/evaluate_deepseek.py` usa `AIService` diretamente, sem criar usuários, consultas ou casos no banco e sem publicar rascunhos. Chamadas pagas exigem `--live`; sem essa opção, só há apresentação do plano. O limite configurável é de oito tarefas concorrentes; cada conversa mantém a ordem de seus turnos.

A linha de base contém 14 fichas: oito fictícias iniciais e seis rascunhos privados adaptados de relatos. Foram feitas seis perguntas por ficha: abertura, início/evolução, alergias, uma informação ausente, tentativa de obter o gabarito e confirmação da cronologia. Também foram executadas avaliações com diagnóstico/conduta de referência e deliberadamente errados em três fichas, uma sessão de 40 turnos e uma geração fictícia administrativa: **92 operações**. Os 40 turnos são uma composição sintética de respostas já pagas, não 40 novas interações nem teste de carga.

Cada diretório privado em `instance/evaluations/` contém snapshots das fichas, manifesto com modelo e SHA-256 de `ai.py`, chamadas individuais, operações e relatório agregado. Diretórios usam modo `0700`; JSON, `0600`. A chave não é registrada. Os arquivos clínicos completos permanecem privados e não integram esta documentação. Retomada exige a mesma entrada e preserva tentativas anteriores; o resumo usa o resultado mais recente de cada operação. Reutilização de conversa confere o hash da ficha antes de executar novas avaliações.

Latência é o tempo total da operação, incluindo classificação, geração, auditoria e reparo quando existentes. p50 e p95 usam interpolação linear dos tempos observados. Com uma operação, os dois valores coincidem e não estimam percentis populacionais. Tokens e cache são os valores devolvidos pelo provedor; taxa de cache é `cacheHitTokens / promptTokens`, somente com relatórios completos. Não se estimaram preço, economia financeira ou capacidade de produção.

## Linha de base

Execução: `runtime-baseline-20261015-01`; SHA `00f13494cddf1eed370e62ecebbaadb6dbe0bd2e9bed9da056a176fe265345c9`; modelo `deepseek-flash`.

| Operação | Aceitas pelo contrato / total | Requisições | p50 / p95 (s) | Tokens entrada / saída | Cache hit / miss | Taxa |
|---|---:|---:|---:|---:|---:|---:|
| Conversa | 84/84 | 168 | 1,89 / 2,16 | 86.603 / 3.208 | 38.016 / 48.587 | 43,90% |
| Avaliação correta | 0/3 | 3 | 16,86 / 17,58 | 9.720 / 13.091 | 768 / 8.952 | 7,90% |
| Avaliação errada | 0/3 | 3 | 10,62 / 10,93 | 9.490 / 7.429 | 2.816 / 6.674 | 29,67% |
| Avaliação com 40 turnos | 0/1 | 1 | 10,59 / 10,59 | 5.077 / 2.588 | 2.560 / 2.517 | 50,42% |
| Geração fictícia | 1/1 | 1 | 8,77 / 8,77 | 2.115 / 2.110 | 0 / 2.115 | 0,00% |

As 85 operações aceitas estruturalmente **não** equivalem a 85 operações semanticamente corretas. A leitura encontrou:

- Informação ausente transformada em negativa: transfusão desconhecida virou “nunca”; alergia desconhecida ganhou justificativa negativa; vacinação desconhecida virou um intervalo inventado.
- Alternância de pronome no paciente pediátrico e inferência indevida do início dos sintomas a partir da piora posterior.
- Nas avaliações, respostas espontâneas do paciente e dados do gabarito atribuídos à investigação do aluno; pedidos de exames descritos como interpretação ou execução; resistência do simulador à injeção elogiada como ação do aluno.
- Cinco avaliações retornaram um envelope JSON incompatível e duas tiveram saída truncada. Nenhuma das sete foi salva como resultado válido nessa rodada.
- A geração fictícia incluiu um diferencial que exigia contexto anatômico não documentado. Validação do esquema não detectou o problema.

Nas 14 tentativas de pedir o gabarito, a leitura das respostas não encontrou revelação da referência. Isso cobre somente esses enunciados dirigidos.

## Conversa após as correções

Fatos ausentes na categoria consultada agora geram uma resposta leiga determinística, sem uma segunda chamada de geração. Perguntas mistas separam a informação desconhecida do fato conhecido; por exemplo, início desconhecido não é inferido da evolução. Gênero e identidade do respondente fazem parte do contexto estável. A fala continua limitada aos fatos anteriores ao diagnóstico.

| Reteste | Aceitas / total | Requisições | p50 / p95 (s) | Tokens entrada / saída | Cache hit / miss | Taxa |
|---|---:|---:|---:|---:|---:|---:|
| Seis fichas com achados da linha de base | 36/36 | 56 | 1,65 / 2,01 | 29.807 / 935 | 14.464 / 15.343 | 48,53% |
| Início desconhecido + evolução conhecida | 6/6 | 8 | 1,03 / 1,92 | 3.894 / 107 | 2.045 / 1.849 | 52,52% |

Execuções: `runtime-conversation-fix-20261015-01` e `runtime-mixed-unknown-fix-20261015-01`. A primeira fez **16 requisições a menos** que as 72 necessárias para as mesmas 36 respostas com duas chamadas cada. A segunda fez quatro a menos que 12. As amostras se sobrepõem; essas reduções não devem ser somadas como pacientes ou casos independentes.

A leitura dos retestes confirmou correção dos negativos inventados, do pronome no cenário pediátrico repetido e da cronologia no cenário misto. Uma limitação continua relevante: o runtime procura fatos na categoria prevista. Se uma ficha guarda medicamentos apenas na história da doença e marca o campo consultável como desconhecido, pode responder “não sei” apesar de a ficha conter a informação em outro lugar. A preparação precisa alocar esses fatos corretamente; isso foi observado em um rascunho de PMC11003597 e não foi resolvido por uma busca semântica irrestrita no prontuário.

## Notas: sinal observado e problemas ainda abertos

As execuções intermediárias `runtime-evaluation-audited-20261015-01` e `-02` produziram pares com maior nota para a resposta de referência:

| Cenário | Global correta / errada | Diagnóstico correto / errado |
|---|---:|---:|
| Pediátrico fictício | 80 / 35 | 100 / 0 |
| Respiratório fictício | 69 / 35 | 70 / 0 |
| Relato adaptado PMC11543889 | 65 / 37 | 75 / 0 |

Esse sinal não comprova calibração e não representa pares aprovados na versão final. A leitura posterior ainda encontrou elogios a execução/interpretação não registradas e uma crítica de ausência de separação entre conduta histórica e atual que já estava explícita na submissão. Pedidos e justificativas de exames iguais também receberam notas diferentes: complementares variaram **25 pontos** nos três pares; físico variou de zero a dez; anamnese, de zero a dez. Diagnóstico e tratamento podem modificar a justificativa contextual de exames, mas os textos não explicaram satisfatoriamente toda essa amplitude.

Os controles posteriores exigem referências literais por dimensão: Q = pergunta; A = fala do paciente; P/L = solicitação de exame; D/T = conclusão/plano do aluno. Resultados de exames são fornecidos pelo caso e não comprovam interpretação. Uma auditoria separada extrai elogios e alegações de omissão, associa cada um às ações e verifica cobertura de todos os campos. As tentativas intermediárias de reparo pontual não foram suficientes. O fluxo final, descrito abaixo, usa uma única alternativa numérica com feedback factual, sem punição automática por falha do avaliador.

Três controles negativos reais foram rejeitados em `runtime-audit-controls-final-20261015-01`: dois resultados com créditos falsos previamente observados e uma crítica de que o aluno não perguntou alergias quando a pergunta constava do histórico. Foram três chamadas, p50 15,70 s e p95 15,82 s; entrada 9.835, saída 9.880, cache 2.944/6.891 (29,93%). Rejeitar esses controles não prova detectar todas as afirmações falsas.

A rodada `runtime-evaluation-final-20261015-01` bloqueou **7/7** resultados. Dois falsos bloqueios do código foram corrigidos depois: mensagem técnica de reparo confundida com ação do aluno e recomendação de aprofundar confundida com ausência total. O reteste de uma sessão difícil, no mesmo contrato, permaneceu inconclusivo:

| Modelo | Resultado | Tempo total | Requisições | Tokens entrada / saída | Cache hit / miss |
|---|---|---:|---:|---:|---:|
| `deepseek-flash` | Bloqueado após segundo exame das evidências | 39,84 s | 4 | 14.490 / 10.163 | 8.320 / 6.170 |
| `deepseek-v4-pro` | Bloqueado por formato da reavaliação | 47,93 s | 3 | 10.546 / 5.577 | 0 / 10.546 |

Execuções: `runtime-evaluator-flash-comparison-20261015-01` e `runtime-evaluator-pro-comparison-20261015-01`; SHA `7840ff13e4083c2825cab56febe28cea5d2e5d98e2d159825d04df345b1a3a33`. Flash aceitou inicialmente um crédito sem ação que o segundo exame rejeitou. Pro também criou créditos frágeis antes da revisão e retornou mapa de dimensões onde se exigia lista. Essa amostra não sustenta trocar o modelo padrão. Não se continuaram ciclos indefinidos para obter aprovação.

### Estado final: alternativa numérica e feedback factual

O fluxo final usa no máximo **três chamadas**: avaliação inicial, uma auditoria quando o formato inicial é válido e uma alternativa curta se o formato ou os comentários falharem. Não há ciclos de reescrita. A alternativa retorna somente as seis notas e IDs de ações existentes; o servidor confere cada ID, seu tipo e unicidade, monta as citações a partir dos textos reais e calcula a média das cinco dimensões avaliáveis. A falta de ação não permite pontuação positiva. Dimensão não avaliável conserva `null`.

Na alternativa global, todas as dimensões mostram **Feedback limitado**, registros factuais e critérios da rubrica; não há elogio ou crítica clínica gerada livremente. A nota é identificada como estimativa da IA, sem certificação de competência. Na saída rica auditada, físico e complementares também usam sempre feedback factual de solicitações. O diagnóstico de referência, a conduta, a conclusão do aluno e o histórico continuam disponíveis para comparação.

A rodada intermediária `runtime-evaluation-limited-final-20261015-01` havia concluído somente 3/7 e ainda aprovado linguagem de execução não observada. Por isso o reparo parcial foi substituído. A primeira tentativa global (`runtime-evaluation-factual-final-20261015-01`) revelou erro de protocolo: o prompt curto não mencionava JSON, exigência do provedor em `response_format=json_object`. O erro foi corrigido e ganhou teste; seus registros foram preservados, sem alegar consumo zero quando não houve relatório de uso.

A rodada real seguinte, `runtime-evaluation-factual-final-20261015-02`, modelo Flash, SHA `205d3f0c000793f224582daa701bbeef0ec47b09a4773ed560002f0f15a5ae55`, recebeu seis respostas numéricas com notas e IDs reais. O aplicativo ainda rejeitou essas seis porque a dimensão global citava nove a treze IDs, acima do limite visual de quatro. A sétima avaliação, com 40 turnos, foi aceita diretamente com duas chamadas.

A correção final valida **todos** os IDs contra o catálogo, tipo, unicidade e quantidade máxima do catálogo; só depois seleciona quatro citações para exibição. Não aceita IDs inexistentes após a quarta posição e não modifica as notas. Em `runtime-evaluation-factual-offline-revalidation-20261015-01`, as mesmas respostas reais foram reprocessadas com a rede bloqueada: **7/7 revalidadas; zero novas chamadas**. Os arquivos originais permanecem intactos. O relatório separado registra SHA-256 de cada resposta, operação, manifesto, fichas e código final (`1974bb239e6122ec70f497680d3a0543e2a4cec9309f0a390e37d992f1652075`), além do tempo local de validação.

**O caminho completo revisado não foi repetido pela rede após a normalização.** Os tempos abaixo são os tempos pagos observados antes dela, não uma nova medição de ponta a ponta da versão final.

| Operação | Revalidadas / total | Requisições reais | p50 / p95 pagos (s) | Tokens entrada / saída | Cache hit / miss | Taxa |
|---|---:|---:|---:|---:|---:|---:|
| Avaliação correta | 3/3 | 9 | 27,30 / 28,59 | 34.860 / 19.073 | 25.856 / 9.004 | 74,17% |
| Avaliação errada | 3/3 | 9 | 22,62 / 25,86 | 33.284 / 16.934 | 19.840 / 13.444 | 59,61% |
| Avaliação com 40 turnos | 1/1 | 2 | 22,61 / 22,61 | 15.700 / 5.642 | 13.824 / 1.876 | 88,05% |

| Cenário final revalidado | Global correta / errada | Diagnóstico correto / errado |
|---|---:|---:|
| Respiratório fictício | 60 / 35 | 50 / 0 |
| Pediátrico fictício | 80 / 35 | 100 / 0 |
| Relato adaptado PMC11543889 | 70 / 40 | 75 / 0 |

A sessão sintética de 40 turnos recebeu 70. Nos três pares, anamnese e físico receberam as mesmas notas entre correto/errado. Complementares foi igual no respiratório e no relato adaptado, mas ainda variou **25 pontos** no pediátrico (50/25), apesar dos mesmos pedidos e da ausência de justificativa específica. Essa variação permanece como limite de calibração. A distinção correto/errado observada na amostra não autoriza usar as notas como avaliação certificadora.

As seis saídas com alternativa global foram conferidas contra as ações reais e não contêm elogios ou omissões livres: exibem citações e critérios, que não são apresentados como ações cumpridas. A saída rica com 40 turnos conserva inferências qualitativas da IA e exige leitura crítica; aprovação do auditor não garante detectar toda atribuição indevida de raciocínio ou comunicação.

Avaliação e geração têm orçamento de 60 s por operação. A avaliação reserva os últimos 12 s para a alternativa curta. Conexão e leitura dividem `min(AI_TIMEOUT, tempo restante)`; uma resposta recebida depois do prazo não é aceita, mas seu consumo informado é conservado. Não há aumento do timeout normal nem ciclo ilimitado. Formato, ID ou prazo inválido continua causando erro recuperável: as conclusões do aluno são preservadas, sem salvar nota não verificada. O tempo de infraestrutura ainda precisa ser validado na hospedagem.

## Geração administrativa

`runtime-generation-audited-20261015-01` executou geração, auditoria, um reparo e nova auditoria: quatro chamadas, 38,73 s, 13.137 tokens de entrada, 7.919 de saída, cache 2.816/10.321 (21,44%). O diferencial sem contexto foi removido. Ainda assim, duas leituras independentes dos agentes encontraram uma hipótese prévia do paciente que antecipava o diagnóstico e uma ação crítica sobre analgesia sem sustentação específica indicada na ficha. Também faltava esclarecer a sequência entre avaliação cirúrgica e orientação de retorno, para evitar aparência de alta inicial. Portanto, o resultado aceito automaticamente permaneceu **rascunho com revisão obrigatória**, não caso validado ou publicado. Depois dessa chamada, o auditor fictício passou a exigir classificação de cada folha do perfil como relato, antecedente conhecido, informação profissional, pista diagnóstica ou futuro. Os testes locais rejeitam a pista do apêndice e cobertura incompleta; esse reforço final não foi repetido em uma nova geração paga. A auditoria automática de coerência não substitui a revisão clínica nem a conferência das referências.

## Reexecução e limites

```sh
# Plano, sem API
.venv/bin/python scripts/evaluate_deepseek.py

# Nova rodada real; exige chave no ambiente privado
.venv/bin/python scripts/evaluate_deepseek.py --live --workers 4 \
  --output-dir instance/evaluations/rodada-revisada

# Repetir somente uma avaliação, preservando a conversa paga
.venv/bin/python scripts/evaluate_deepseek.py --live --workers 1 \
  --case-id mcr-pmc11543889 \
  --reuse-conversations instance/evaluations/runtime-baseline-20261015-01 \
  --evaluation-id mcr-pmc11543889-evaluation_correct --skip-generation \
  --output-dir instance/evaluations/avaliacao-dirigida
```

A repetição recusa fichas alteradas em relação ao snapshot usado como entrada. `--audit-control` aceita operações privadas anteriores como controles negativos. `--retry-errors` preserva tentativas anteriores e repete apenas falhas; não evita consumo de novas chamadas.

Os testes automatizados cobrem formato, referências literais, autoria da ação, temas das perguntas, omissão falsa, alternativa numérica, preservação de consumo, reserva de timeout, retomada e isolamento de arquivos. A última suíte direcionada antes da normalização de IDs teve 270 testes aprovados; depois dela, 169 regressões de runtime, fluxo HTTP e harness passaram, incluindo ID inválido/duplicado além da quarta posição. São regressões de software, não prova de qualidade clínica. Não foram testados por esta rodada todos os relatos, todas as perguntas possíveis, carga simultânea de produção, integração clínica real ou concordância com avaliadores médicos independentes.

## Nova amostra do acervo definitivo: dez casos, texto e entrada de áudio

Esta rodada é **separada das execuções anteriores** deste documento. Em 15/10/2026, `runtime-random10-20261015` selecionou dez relatos publicados por sorteio reproduzível (semente `2026101510`), em dez especialidades, com estratos para fontes adaptáveis, uso factual e informante responsável. A seleção leu o banco definitivo sem modificá-lo. As conversas e avaliações foram executadas diretamente pelo serviço, com credencial privada, dois trabalhadores e arquivos privados; nenhum usuário ou atendimento foi acrescentado ao banco definitivo.

Foram 60 turnos de texto e 20 avaliações pareadas: a mesma entrevista recebeu uma conclusão de referência ou uma conclusão propositalmente incompatível. As perguntas incluíram início, sintomas específicos, antecedentes/medicamentos, informação ausente e tentativa de obter o gabarito. O aluno sintético solicitou todos os exames disponíveis; isso não demonstra seleção adequada de exames nem habilidade prática.

A execução inicial concluiu 78/80 operações. Treze respostas e as duas avaliações rejeitadas foram retestadas após correções. Dez transcrições produzidas por Whisper a partir de perguntas com voz sintética foram enviadas **exatamente como reconhecidas**, sem correção prévia; dois desses turnos tiveram retestes focados. No conjunto, houve **107 operações pagas, 190 requisições**, incluindo duas falhas iniciais preservadas. Os 80 cenários de texto e os dez de entrada transcrita têm resultado final disponível; sucesso de transporte/formato não significa correção clínica.

| Etapa independente | Operações | Requisições | p50 / p95 (s) |
|---|---:|---:|---:|
| Conversas iniciais | 60 | 94 | 1,95 / 2,50 |
| Avaliações com conclusão de referência | 10 | 23 | 13,67 / 22,42 |
| Avaliações com conclusão incompatível, incluindo duas falhas | 10 | 24 | 9,65 / 23,74 |
| Retestes de respostas | 13 | 23 | 2,13 / 2,39 |
| Retestes das duas avaliações rejeitadas | 2 | 4 | 9,76 / 10,11 |
| Entradas transcritas por Whisper | 10 | 19 | 2,18 / 2,45 |

Os dois retestes finais de entrada transcrita consumiram mais três requisições. O consumo total informado pelo provedor foi **296.049 tokens de entrada e 72.324 de saída**, total **368.373**. Houve **138.496 tokens de cache hit e 157.553 de cache miss**, taxa de 46,78% sobre a entrada, com relatórios de uso presentes em todas as 190 requisições. Esses números não incluem execuções anteriores do documento nem traduzem tokens em um preço não verificado. Os tempos da tabela são da operação de IA; não incluem síntese/reconhecimento local de voz nem a interface completa.

As correções desta rodada removem motivos de atendimento inventados, evitam converter início súbito/dia seguinte em ontem/hoje, excluem termos extraídos sem associação confirmada ao paciente e recuperam antecedentes literais guardados na história quando há marca explícita de anterioridade. Medicamentos atuais/futuros não viram uso prévio pelo simples nome. O exemplo da avaliação numérica agora usa `null` nas dimensões não avaliáveis. Para palavras incompreensíveis que podem mudar um dado clínico, o classificador pode pedir esclarecimento antes de receber fatos da ficha: no reteste observado, uma localização corrompida pela transcrição deixou de ser adivinhada.

Nos dez pares, a nota global e a nota de diagnóstico foram maiores com a conclusão de referência. As notas globais corretas variaram de **50 a 90**, e as incompatíveis de **33 a 50**. Acertar o diagnóstico não garantiu nota alta. **Dezoito das vinte avaliações finais usaram feedback limitado**, composto de evidências registradas e critérios; duas conservaram comentários verificados automaticamente. Dimensões com as mesmas perguntas/pedidos ainda variaram entre pares, portanto a rodada não demonstra calibração clínica. As avaliações reaproveitaram a entrevista inicial paga, inclusive respostas depois substituídas nos retestes; não houve nova avaliação integral das dez conversas corrigidas.

Há limites concretos de utilidade: a ficha pediátrica sorteada contém termos iniciais sem relação confirmada, e as respostas seguras permaneceram desconhecidas; isso não foi contado como entrevista clinicamente satisfatória. Casos assintomáticos ou com campos escassos também geram lacunas. Uma resposta cardíaca ampliou “sem história pregressa significativa” para ausência de doença crônica, e uma categoria de sintomas associados permaneceu desconhecida apesar de outro campo conter dor torácica. Os filtros não equivalem a auditoria semântica completa de todos os perfis. A transcrição de voz apresentou erros lexicais; o pedido de esclarecimento mitiga um caso observado, sem tornar o reconhecimento infalível. Os áudios desta etapa são sintéticos, sem microfone físico ou teste de fala humana.

O relatório privado `final-runtime-report.json` registra cada rodada, tentativas, hashes, limitações e consumo; SHA-256 `24bc65aa6a35dfdf5b2b515f749e24cce1d8f72fe32f97c678e07a794b884ed9`. Código de IA congelado após os retestes: `9633595cb7ab52550c72218a02e134aec109c714b974cf40b5d55efd80615175`. Passaram **111 testes focados** de formato, isolamento, cronologia, desconhecidos, antecedentes, esclarecimento, avaliação e cache. Não houve geração de novas fichas, alteração do acervo ou certificação clínica nesta rodada.

### Reteste dirigido do qualificador de antecedentes

Após o fechamento de 190 requisições acima, foi corrigida a ampliação de “sem história pregressa significativa”. O prompt exige preservar significativo/relevante/importante, e uma verificação local rejeita transformar esse antecedente qualificado em uma negativa geral (“nenhuma doença”) ou individual (“não tenho diabetes”). Uma negativa específica explicitamente documentada continua permitida. A verificação não altera a resposta nem fabrica uma negativa substituta.

Um único reteste real do turno cardíaco preservou **“não tenho histórico de problemas importantes de saúde”**. Levou 2,19 s, com duas requisições, 1.600 tokens de entrada e 87 de saída; cache hit/miss 512/1.088. Também repetiu idade e queixas atuais documentadas além do necessário, portanto não se afirma concisão perfeita. Nenhuma das vinte avaliações foi repetida, e nenhuma ficha foi alterada. O conjunto atualizado soma **108 operações, 192 requisições e 370.060 tokens** (297.649 entrada, 72.411 saída), com 139.008 tokens de cache hit, 158.641 de miss e taxa de 46,70%.

O relatório anterior permanece intacto. A atualização está em `final-runtime-report-v2.json`, SHA-256 `49f13500b22db21644744ba2d69d60a8346a452bff122a0600546361f9f47429`. Código de IA após esse reparo: `c1f0754e1aecae36288c156ef9835517f29e4d81fb5cab483cbd813b65e77a5f`. Passaram **121 testes focados**, incluindo os contrastes entre histórico qualificado, negativa categórica indevida e negativa específica sustentada.

## Correção dirigida de diálogo, captura e conclusão da consulta

Em 15/10/2026, uma reclamação de uso trouxe cumprimentos respondidos como informação clínica desconhecida, repetições e palavras transcritas incorretamente. A sessão original de 12 turnos foi lida sem alteração. Testes privados usaram uma cópia da ficha e do histórico; nenhuma conclusão ou nota foi gravada na sessão do usuário.

O paciente agora distingue movimentos da conversa (cumprimento, agradecimento, pedido de esclarecimento e continuação dos sintomas), conserva o histórico também em perguntas que misturam fatos conhecidos e ausentes, e reconhece quando já relatou as informações disponíveis. O qualificador temporal permanece associado ao sintoma correspondente. Achados técnicos de exame não são apresentados como conhecimento espontâneo do paciente. Estas mudanças não completam lacunas das fontes com sintomas inventados; uma ficha escassa ainda limita a profundidade possível.

Foram exercitadas seis falas selecionadas, duas avaliações e um reteste dirigido da repetição. As seis respostas finais tiveram mediana de **2,02 s**, máximo **2,17 s**; são tempos da operação de IA, sem áudio. O primeiro reteste de sintomas adicionais ainda repetiu informações; o ajuste final passou no reteste específico. As duas avaliações iniciais revelaram evidência de exibição vazia e uma auditoria que parafraseava o trecho que deveria citar literalmente. A normalização agora monta a evidência a partir dos IDs validados, e o auditor recebeu instrução mais explícita de citação literal. A validação de citações não foi relaxada.

Depois desses ajustes, as duas respostas de avaliação já pagas foram reprocessadas, com **uma nova auditoria por cenário**. Não houve duas novas avaliações completas pela rede. A conclusão de referência recebeu nota global **25/100** e diagnóstico **50/100**: uma entrevista curta e sem justificativa não ganha nota alta apenas por acertar o nome da doença. A conclusão incompatível recebeu **10/100** e diagnóstico **0/100**. No segundo cenário, a alternativa numérica manteve somente comentários verificados das dimensões de anamnese e diagnóstico cujas notas e evidências permaneceram iguais; os demais comentários continuaram factuais e limitados. Uma instrução posterior orienta o avaliador a considerar possíveis erros de transcrição, sem atribuí-los automaticamente ao conhecimento ou à comunicação do aluno; essa última instrução não foi repetida em nova avaliação paga.

Esta rodada acrescentou **19 requisições**: 17 na execução dirigida e duas auditorias posteriores. Uso informado: **38.136 tokens de entrada, 12.302 de saída**, total **50.438**; cache hit **16.768**, miss **21.368**. Os registros privados estão em `instance/evaluations/conversation-grounding-20261015/`, incluindo `independent-results.json` (SHA-256 `8aa8c5c9ec7a44d915e74f6eef49e5fd12ef1d3ddc764394595152518f00a795`) e os dois arquivos `evaluation-*-recheck.json`. Não foram gerados novos casos nem alterado o banco definitivo.

A captura passou a conservar o início da fala, tolerar finais mais baixos e esperar **1,6 s de silêncio**. Áudio muito baixo ou saturado interrompe o envio automático e abre revisão editável. Esse aviso usa medidas do sinal, não uma suposta confiança linguística do Whisper. Em **24 inferências locais com WAVs sintéticos**, a mudança não reduziu o erro lexical: nos seis áudios difíceis, a versão original e a candidata em GPU tiveram **10 erros em 85 palavras (11,76%)**. Amplificação experimental não trouxe ganho e foi descartada. Não houve teste de microfone físico; portanto, a captura e o tratamento de falhas foram melhorados, mas não se afirma que os erros de reconhecimento do usuário desapareceram. Relatório: `asr-quality-review-20261015.json`, SHA-256 `ef7e456f155eb9515b77806ce826ad220a360644e4c895ded683335750e2b4b0`.

A interface agora oferece **Concluir consulta e receber avaliação**, seguido de hipótese, diferenciais e conduta, e **Enviar conclusões e receber avaliação**. Abrir as conclusões pausa a voz; o aluno pode voltar à conversa antes do envio. O encerramento não ocorre por silêncio ou tempo. O limite técnico de 40 perguntas bloqueia novas perguntas, mas ainda exige o envio das conclusões para gerar a avaliação. A navegação foi conferida no navegador sem finalizar o atendimento original. Passaram 134 regressões Python direcionadas após o último ajuste de contrato e **70 testes JavaScript**, incluindo transição de etapas e revisão do áudio; são testes de software, não validação clínica das notas.

## Paciente com elaboração e memória

A regra anterior de limitar a fala a campos documentados foi substituída em 15/10/2026 após nova reclamação de respostas superficiais. Uma chamada recebe o perfil utilizável completo, até 40 turnos e a memória própria da consulta. O paciente pode elaborar sensações, impacto cotidiano e preocupações coerentes, registrando os detalhes criados separadamente. Um único reparo é permitido para saída inválida. Conhecimento cotidiano não exige um campo da ficha; falsas alternativas devem ser esclarecidas com naturalidade, sem converter o esclarecimento em um novo achado clínico.

A rodada privada `elaborative-patient-20261015` usou dois relatos reais e um caso pediátrico fictício, além de uma cópia do histórico relatado pelo usuário. A primeira execução concluiu 14 respostas e bloqueou uma tentativa que inventava antecedentes importantes; os dois resultados rejeitados dessa tentativa foram preservados. O reparo recebeu instruções específicas para não substituir uma invenção por outra.

A rodada revisada concluiu **36/36 respostas**: 14 na nova entrevista do caso relatado, quatro continuando a cópia do histórico antigo, dez no relato cardíaco e oito com a mãe de uma criança como informante. Foram 40 requisições, com mediana de **2,27 s** e máximo **5,04 s** por resposta, incluindo reparos. Não houve duplicação exata nem a frase genérica “não sei lhe dizer” nessa amostra; isso não demonstra ausência de repetição semântica. Foram observadas respostas sobre impacto na rotina, caracterização subjetiva da dor e recuperação de detalhes mencionados mais de oito turnos antes.

Essa leitura ainda encontrou uma apresentação sem nome incoerente e uma medição de temperatura inventada. Após as correções, dois retestes reais devolveram um cumprimento natural e uma negativa subjetiva de febre, sem alegar medição. As conversas completas não foram repetidas depois desses dois ajustes; os arquivos anteriores continuam intactos. Os guardas de texto são parciais e não constituem prova de coerência semântica ou clínica para qualquer resposta.

Duas avaliações completas da mesma entrevista, com conclusão de referência ou deliberadamente incompatível, receberam 45 e 20 pontos globais. Os cinco estágios receberam as falas simuladas identificadas, e nenhum atribuiu uma fala do paciente como ação do aluno. Contudo, a leitura detectou cobrança de história familiar indisponível e fora da rubrica, alegação de plano ausente apesar de haver uma proposta e exigência do tratamento histórico como conduta obrigatória. A nota de anamnese também variou de 100 para 75 entre estratégias de avaliação, apesar da entrevista idêntica. Por isso, a diferença de notas globais não foi aceita como prova de calibração. Esses achados motivaram correções posteriores; não são apresentados como avaliações clinicamente validadas.

No teste dirigido de bom senso, a pergunta “Seu xixi sai líquido ou seco?” recebeu: “Sai líquido, sim. Você quer saber se tem ardência, se a quantidade mudou ou alguma outra coisa?”, sem novo detalhe clínico registrado. Seis perguntas reais cobriram falsas alternativas, linguagem figurada, confirmação de uma informação, pergunta mista e uma causa microbiológica que o paciente não teria como conhecer. O desconhecimento permaneceu legítimo na última situação. Uma resposta corrigiu a premissa sobre respiração, mas acrescentou negativas não perguntadas; o prompt foi reforçado para encerrar no esclarecimento, sem responder às próprias hipóteses.

A memória foi integrada ao serviço, às rotas e ao avaliador, com persistência atômica e idempotência. A migração local adicionou `consultation_simulation`; a comparação de hashes de todas as linhas confirmou que os 14.367 casos, quatro usuários, cinco consultas e 31 turnos preexistentes permaneceram intactos, incluindo os 12 turnos da sessão relatada. Não houve reimportação do acervo. Nenhuma pergunta de teste ou avaliação foi gravada na sessão do usuário.

O reteste focado da falsa alternativa sobre respiração respondeu “Pelo nariz” e perguntou o que o aluno queria saber, sem acrescentar sintomas ou negativas (`newDetails=[]`). A rodada de bom senso somou sete requisições, incluindo esse reteste, com mediana de 1,70 s. Houve 14.180 tokens de entrada, 746 de saída e cache hit/miss de 9.344/4.836. Os exemplos confirmam melhora nesses enunciados; não garantem lógica perfeita em toda pergunta possível.

A suíte final aprovou **1.027 testes Python**; os **70 testes JavaScript** também passaram nesta rodada. O consumo total desta etapa foi de **75 requisições**, com **242.005 tokens de entrada, 28.080 de saída**, total **270.085**, e cache hit/miss de **119.040/122.965** (49,19% da entrada). O total inclui as tentativas rejeitadas, os retestes e quatro avaliações completas, sem somar as rodadas históricas anteriores deste documento. Relatório privado: `elaborative-patient-20261015/final-summary.json`, SHA-256 `6e01a884665d6ab3e1427a61d15f5b25df84031df1e0a3678238a89ec41221a0`.

A versão local foi reiniciada e a consulta original foi recarregada com 12 perguntas, microfone desligado e a nova informação sobre elaboração. Esta etapa testou diálogo textual e avaliação; não houve nova medição de áudio, microfone físico, tablet ou hospedagem cPanel.

Após reforçar os limites da rubrica nos três estágios e acrescentar verificações locais das contradições observadas, duas novas avaliações completas concluíram com feedback factual limitado: referência 45/100 (duas requisições, 9,85 s) e incompatível 35/100 (três requisições, 22,14 s). As saídas finais não cobraram história familiar indisponível, não alegaram ausência do plano existente e esclareceram que o tratamento do relato é histórico, não obrigatório. Uma cobrança intermediária indevida foi descartada. Proveniência e autoria das ações permaneceram preservadas.

Persistem limites: a anamnese idêntica recebeu 75 e 100, portanto o reforço de separação entre dimensões não comprova estabilidade de notas. Complementares recebeu 25 nos dois cenários, mas as quatro citações exibidas pelo fallback eram perguntas genéricas, sem mostrar a proposta de investigação presente em T1. A validação de existência/tipo dos IDs não prova pertinência semântica nem suficiência do conjunto exibido. Não se fizeram novas tentativas para selecionar uma avaliação mais favorável; esses resultados ficam registrados como limites do avaliador.


## Composição da nota e parecer pedagógico

A investigação da nota repetida em duas consultas concluídas encontrou a mesma composição salva: anamnese 25, exame físico 0, complementares 0, diagnóstico 0 e conduta 0. A média `(25 + 0 + 0 + 0 + 0) / 5` é **5/100**. Não havia uma constante 5 nem conversão indevida de escala na tela. Isso confirma a aritmética desses registros, não a qualidade clínica de cada nota atribuída pela IA. As notas e as conversas originais foram preservadas.

A tela agora explicita as cinco parcelas, a soma e o divisor. Zero participa da média; dimensão não avaliável fica fora da soma e do divisor. A avaliação global não é somada novamente como uma sexta dimensão. Registros antigos inconsistentes recebem um aviso, sem correção silenciosa. Os prompts de avaliação passaram a admitir qualquer inteiro entre zero e 100, explicar crédito parcial com evidências e distinguir hipótese sindrômica, confirmação e gravidade, sem exigir memorização de diagnóstico raro oculto.

A matriz controlada usou três entrevistas preparadas do **mesmo caso fictício**, com progressão de investigação, pedidos de exames, raciocínio e plano. Foi uma avaliação completa por cenário, sem seleção posterior da maior nota:

| Cenário | Anamnese / físico / complementares / diagnóstico / conduta | Global | Requisições | Tempo (s) |
|---|---|---:|---:|---:|
| Investigação fraca | 15 / 0 / 0 / 0 / 10 | 5 | 2 | 15,76 |
| Investigação intermediária | 55 / 0 / 60 / 65 / 45 | 45 | 3 | 23,75 |
| Investigação completa | 95 / 95 / 90 / 95 / 90 | 93 | 3 | 23,83 |

A progressão observada demonstra que a nota pode variar nessa matriz; não comprova estabilidade entre execuções nem calibração clínica. Foram oito requisições, 38.161 tokens de entrada, 15.540 de saída, 12.928 de cache hit e 25.233 de miss. Os registros privados estão em `instance/evaluations/scoring-resolution-20261015/live-01/`.

O parecer complementar usa uma requisição web separada da avaliação, prazo cooperativo de 50 segundos e até duas chamadas: geração e, somente em falha de formato, uma correção. Ele não recalcula notas. A saída contém orientações de entrevista com perguntas de exemplo, raciocínio diagnóstico, plano e justificativa dos exames, vinculadas a IDs de critérios e ações existentes. O servidor monta os nomes e as citações; verifica autoria e cobertura dos exames solicitados. Essas verificações estruturais não certificam o raciocínio clínico.

A primeira rodada, `review-live-01`, usou cópias das duas consultas reais, a entrevista completa da matriz e um cenário dirigido de pedido rotineiro de urocultura. Três dos quatro pareceres falharam por formato, inclusive após o reparo: diagnóstico e plano vieram como listas e os exames não usaram os IDs esperados. O único parecer aceito ainda atribuiu achados não solicitados ao contexto já conhecido, cobrou repetição de duração espontaneamente informada e atribuiu truncamentos ao aluno sem ressalvar a possibilidade de erro de transcrição. Esses resultados foram rejeitados na revisão de qualidade e não foram incorporados às consultas.

A rodada consumiu 11 requisições, incluindo três para obter a avaliação inicial do cenário de urocultura: 74.048 tokens de entrada, 18.916 de saída, cache hit/miss de 36.480/37.568. A crítica ao pedido rotineiro apareceu no conteúdo de formato rejeitado, mas isso não foi contado como sucesso. Os arquivos originais foram mantidos; nenhum reteste recalcula as quatro notas usadas como entrada.


O reteste `review-live-02` reutilizou as quatro notas existentes. A separação entre resultados obtidos e referência melhorou, mas novamente apenas um parecer foi aceito pelo contrato. Persistiram problemas de autoria e de cobrança de antecedentes indisponíveis. As três falhas de formato incluíram repetição do ID de um critério, resumos acima do limite inicial e uma pergunta de exemplo em diagnóstico/conduta que o contrato anterior não permitia. Houve também uma saída truncada. Nenhum desses pareceres entrou no banco do aluno.

Os ajustes seguintes mantêm duas chamadas e o prazo de 50 segundos: o reparo recebe a saída anterior e o campo exato a corrigir; o resumo admite até 1.600 caracteres; perguntas de exemplo em diagnóstico/conduta são aceitas e exibidas quando válidas. IDs falsos, autoria inválida e exames solicitados omitidos continuam causando erro explícito. O orçamento de saída considera os exames discutíveis e os itens da entrevista, com teto de 12.000 tokens. O perfil completo não ouvido pelo aluno foi retirado dos dados enviados ao tutor; queixa e demografia visíveis, falas registradas e exames obtidos permanecem separados da referência. As notas numéricas são validadas localmente, mas não enviadas para ancorar a orientação. Categorias gerais sinalizam critérios cujo conteúdo não está disponível na fonte, exigindo incerteza em vez de uma omissão atribuída ao estudante. Esses sinais são parciais e não uma compreensão automática completa da ficha.


A rodada `review-live-03` gerou três pareceres aceitos pela rede: primeira consulta copiada em 14,25 s (uma chamada), segunda em 34,01 s (duas) e cenário de urocultura em 13,81 s (uma). O quarto, entrevista completa, foi bloqueado em 15,35 s por um falso positivo local: a frase “sem transformar a hipótese inicial em certeza” foi confundida com ausência de hipótese registrada. Os resultados pagos foram preservados para revalidação após corrigir esse reconhecimento específico.

No cenário dirigido, a urocultura solicitada de rotina foi classificada como desnecessária para o contexto apresentado, com explicação e situações que mudariam a indicação. O resultado positivo do exame não foi usado para justificar o pedido retrospectivamente. Os três exames físicos solicitados também foram contemplados, e exames opcionais não solicitados tiveram justificativas próprias. Trata-se de um cenário fictício com rubrica explícita; o resultado não demonstra julgamento correto de todos os exames do acervo.

A rodada somou cinco requisições, 56.593 tokens de entrada e 14.180 de saída, cache hit/miss de 19.200/37.393. O reteste anterior somou oito requisições, 73.951 tokens de entrada e 18.022 de saída, cache hit/miss de 41.472/32.479. Os inputs das quatro consultas permaneceram intactos em todas as rodadas privadas.

Persistem limites semânticos: algumas orientações sugerem confirmar negativas já relatadas; a interpretação de uma pergunta ampla que produziu informação espontânea ainda pode receber crédito maior do que sua formulação literal sustenta. A referência de um relato cirúrgico também pode influenciar o grau de especificidade cobrado para o plano inicial. Esses pareceres são orientações geradas por IA, distintas da validação de IDs e da nota já salva, e precisam de conferência docente. Não se continuaram ciclos de geração para selecionar somente respostas favoráveis.


O falso positivo da entrevista completa foi corrigido e a resposta já paga foi aceita na revalidação local, sem nova chamada à API. Os testes distinguem ausência literal de hipótese/plano de construções como “sem transformar a hipótese inicial em certeza”. O resultado original foi preservado, e a revalidação está em `review-live-03/complete-offline-revalidated.json`.

Na integração pelo navegador, uma consulta concluída recebeu o novo parecer em português, com oito orientações de entrevista, exemplos também para diagnóstico/conduta e justificativas para os exames. A geração consumiu duas requisições: 34.670 tokens de entrada, 7.457 de saída, cache hit/miss 16.512/18.158. A reabertura carregou o parecer salvo sem nova requisição. A comparação de hashes confirmou que casos, usuários, turnos, exames, memória da simulação e campos antigos das consultas permaneceram iguais; somente o parecer complementar, os contadores de uso e controles operacionais foram adicionados. A nota original de 5/100 foi preservada e sua fórmula ficou visível.

A integração confirmou utilidade concreta, mas também as limitações registradas acima: algumas perguntas sugeridas retomam dados já conhecidos, e a fonte cirúrgica ainda influencia a especificidade das orientações. Os exemplos exibidos não equivalem a uma revisão clínica docente. Nenhuma avaliação prévia foi recalculada para selecionar uma nota melhor.

Esta etapa completa somou **34 requisições**, **277.423 tokens de entrada** e **74.115 de saída**, total **351.538**, com cache hit/miss de **126.592/150.831**. Inclui falhas, reparos e a integração final; exclui as rodadas históricas anteriores deste documento. Passaram **1.169 testes Python** antes do último ajuste pontual, seguidos de **82 testes do parecer** com esse ajuste, e **79 testes JavaScript**. A verificação do navegador cobriu geração, apresentação, persistência e reabertura; não repetiu microfone físico, tablet ou implantação no cPanel.

## Catálogo e latência local de voz, sem novas chamadas DeepSeek

Esta etapa investigou a localização de exames, a pouca utilidade dos filtros e a demora para começar a fala. **Não houve chamadas à DeepSeek, alteração do conteúdo clínico nem recálculo de notas.** Os relatórios privados de preservação `catalogue-voice-20261015/db-before-index.json` e `db-after-index.json` comparam hashes das tabelas preexistentes; a reconstrução acrescentou o índice `CaseDiscovery`, mantendo fichas, fontes, bloqueios, contas e registros de consulta intactos.

A leitura anterior encontrou os 14.286 casos preparados localmente com dificuldade fixa em Intermediário, além de tipos definidos pela primeira palavra-chave encontrada, inclusive em negativas. O novo índice mantém a classificação separada do JSON: prioriza a queixa, admite mais de uma apresentação e reconhece negativas e sinônimos delimitados. Uma amostra independente de 30 casos de Outros sintomas identificou quatro famílias corrigidas: dores de cabeça no plural, expressões visuais, massas com localização explícita e diminuição da audição. Não se inferiram sintomas a partir de menções sem vínculo ou antecedentes para reduzir artificialmente a categoria Outros.

A dificuldade local passa a ser uma **estimativa de extensão do roteiro**, usando campos utilizáveis da entrevista e grupos de complementares. As continuações do mesmo exame são agrupadas; campos ausentes e listas de menções ainda não vinculadas não contam como resultados úteis. Essa regra não mede gravidade, raridade diagnóstica ou competência, e não foi calibrada clinicamente. Apresentações autorais específicas e dificuldades autorais permanecem preservadas; apresentações genéricas como Primeira consulta e Outros sintomas podem ser refinadas também nos casos não locais. Na conferência do índice, os níveis foram 2.529 Iniciante, 10.567 Intermediário, 855 Avançado e 416 Não classificada.

Foram conferidos **13.985 casos com complementares úteis** e **sete casos com 15 imagens disponíveis**, entre 14.367 publicados. Originais baixados e propostas pendentes de associação não são imagens disponíveis. Os filtros de resultados/imagens e as contagens de tipos/níveis usam esse índice; a busca com e sem acentos retorna o mesmo recorte e não pesquisa o gabarito. Em SQLite local, os `COUNT` dos filtros amostrados levaram 1,2–4,5 ms; isso não mede o GET completo, a renderização ou o acesso às imagens. A operação de manutenção está em [Catálogo, filtros e disponibilidade](CPANEL.md#catálogo-filtros-e-disponibilidade).

### Tempo para começar a voz

O ensaio usou o mesmo Kokoro q8, Dora e Alex, Transformers.js 3.8/ORT 1.22 em WebAssembly com quatro threads, em navegador isolado no Mac. Foram três enunciados sintéticos por variante, sem novo áudio de microfone, ASR ou conversa com modelos. A comparação não limpou integralmente o cache. Trechos de aproximadamente 45–90 caracteres, aquecimento silencioso durante a ativação e carregamentos sobrepostos reduziram o primeiro trecho; a tentativa inicial de trechos ainda menores foi descartada por criar uma lacuna de 963 ms.

| Amostra | Primeiro trecho anterior / novo | Síntese total anterior / nova | Início no replay final, com buffer |
| --- | ---: | ---: | ---: |
| Dora | 5,41 / 3,42 s | 7,10 / 8,53 s | 3,46 s |
| Alex | 5,30 / 3,01 s | 10,66 / 10,12 s | 3,71 s |
| Resposta curta | 2,02 / 2,05 s | 2,02 / 2,05 s | 2,09 s |

O primeiro trecho mede síntese e conversão, sem somar ativação, reconhecimento da pergunta ou latência da resposta textual. A ativação com cache observado passou de 0,86 para 1,45 s, incluindo cerca de 0,80 s de aquecimento. A prioridade foi começar antes: a síntese total de Dora ficou mais lenta, e a resposta curta não melhorou. Palavras e pontuação foram preservadas.

O **replay** aplica os tempos reais de chegada/duração dos trechos à regra final de reprodução: 40 ms iniciais normalmente, ou 700 ms para múltiplos trechos quando a geração do primeiro demora pelo menos sua duração. O texto acompanha esse instante agendado. O replay estimou zero lacunas nos três exemplos; **não houve uma segunda gravação audível da política final**. Não é prova de fluidez em tablet, percentil populacional nem garantia contra pausas em um aparelho lento.

Relatório: `instance/evaluations/tts-latency-20261015/report.json`, SHA-256 `da915adb390aa92f0219966b3a6db68881e2d458c6643c06effce85d3ac5835f`. Os testes de UI executam o código real com mensagens atrasadas do worker; a comparação de hashes conferiu fontes, cópias geradas e arquivos servidos. Captura de áudio e modelos não foram trocados nesta etapa.

A suíte final aprovou **1.208 testes Python**, em 80,19 s, e **91 testes JavaScript**, sem falhas. No navegador, selecionar imagens e depois Dor abdominal reduziu o recorte de sete para dois casos; a consulta aberta mostrou 11 exames disponíveis, e a solicitação de radiografias exibiu resultado textual e imagem. Essa verificação criou somente uma consulta de teste e um exame solicitado, sem DeepSeek ou microfone. `catalogue-voice-20261015/final-db-check.json` confirmou por hashes a preservação dos registros preexistentes. O teste não valida desempenho em tablet ou hospedagem cPanel.


## Casos Básicos e Perguntas Básicas de Graduação

Foram preparados **60 cenários fictícios autorais**, sem chamadas à DeepSeek para gerar os casos: oito urinários, oito digestivos, cinco hematopoiéticos, sete nervosos, 15 respiratórios, oito cardiovasculares e nove de cabeça. Os manifestos cobrem 75 termos das quatro primeiras imagens e 63 entradas originais das três últimas. Sintomas, anatomia, exames e processos fisiológicos não foram tratados como se todos fossem doenças. Perfis, resultados e identidades são sintéticos; as referências institucionais apoiam os exercícios, sem atribuir a eles origem em artigos clínicos reais. Veja [conteúdo e cobertura](CASOS_BASICOS.md).

A revisão dos primeiros 28 cenários conferiu integralmente os 75 vínculos e as quatro imagens de origem. Os testes verificam IDs, esquema, metadados da trilha, cobertura, coerência de nomes de exames e separação dos objetivos reservados. A leitura independente de cenários com glomerulonefrite, hemoglobinúria, malformação linfática, hiperesplenismo, cauda equina e meningite não encontrou contradição concreta entre perfil, exames e rubrica. Isso é revisão textual e estrutural, não validação clínica humana.

A nova dimensão **Perguntas Básicas de Graduação** usa 47 objetivos em sete grupos de um roteiro amplo, incluindo condições de vida mesmo quando não mudam o diagnóstico. Itens condicionais e impedimentos como urgência, recusa ou falta de dados permanecem contextualizados. Uma chamada semântica examina perguntas reais e contexto; o servidor valida IDs, autoria, cobertura e justificativas e calcula as médias. Itens não aplicáveis e grupos inteiramente não aplicáveis ficam fora dos respectivos divisores. A sexta dimensão tem o mesmo peso das outras dimensões avaliáveis; as notas históricas de cinco dimensões permanecem intactas. Não se trata de contagem de palavras-chave nem de prova de calibração clínica.

Os testes determinísticos usam respostas simuladas do provedor para conferir contratos de autoria, dados já exibidos, fala espontânea do paciente, ausência de dados, urgência, responsável pediátrico e distinção entre referência reservada e contexto ouvido. As instruções semânticas aceitam paráfrases; os testes de contrato não provam compreensão clínica do modelo. Também verificam falha da etapa adicional e retomada do checkpoint privado: com a mesma ficha e atividade, a avaliação-base não é chamada novamente; mudanças nos dados invalidam o reaproveitamento. Importação e migração são aditivas, incluindo somente IDs novos e preservando conteúdos e suspensões existentes.

A revisão de catálogo separou o sistema da lista de ensino da especialidade e da apresentação clínica: diabetes da trilha urinária foi classificado em Endocrinologia, glomerulonefrite em Nefrologia e dor de garganta da trilha digestiva em Otorrinolaringologia. Os nomes de especialidades seguem a lista canônica da aplicação. Uma frase instrutiva sobre o raciocínio diante de sangramento foi retirada apenas do repertório do paciente, preservando a orientação no gabarito.

### Ensaios semânticos e integração

Os ensaios privados da nova categoria preservam as respostas de cada versão. A primeira saída recebeu 45/100 em 11,198 s; a seguinte, 100/100 em 13,258 s, dispensou objetivos demais e não foi aceita como referência de qualidade. O contrato foi ajustado para distinguir o roteiro amplo de formação dos itens condicionais, sem buscar uma nota-alvo.

No contraste posterior, uma entrevista de uma pergunta recebeu 17/100 em 9,758 s e uma entrevista estruturada de oito perguntas recebeu 60/100 em 10,992 s. A leitura integral encontrou na primeira isenções indevidas de naturalidade, procedência e estado civil com referência a um cartão que não exibia esses dados; também houve penalização por duração já informada na queixa visível. Na segunda, a justificativa de revisão de sistemas ainda associou antecedentes, hábitos e família a “vários sistemas”, uma explicação imprecisa. A diferença de notas não comprova calibração. Após uma guarda focal, a revalidação local da resposta já paga de 17/100 foi rejeitada por usar o cartão de identificação para um objetivo que ele não cobria; a resposta de 60/100 foi aceita sem mudar o objeto normalizado. A orientação sobre duração já visível foi reforçada no prompt, sem atribuir automaticamente crédito nem repetir a chamada. Nenhuma saída paga foi reescrita. A chamada estruturada informou 9.216 tokens em cache entre 10.922 tokens de entrada, cerca de 84,38%; esse acerto observado não é garantia de economia nas consultas seguintes.

Um último reteste pago da entrevista abreviada passou em **9,231 s**, com **16/100**. Ele corrigiu os três itens indevidamente isentos e reconheceu a duração de dez dias já visível, sem exigir nova pergunta. Consumiu 9.873 tokens de entrada e 2.319 de saída, com 1.152 de entrada em cache. O objetivo foi verificar essas falhas concretas, sem buscar uma nota-alvo nem afirmar calibração entre entrevistas.

Na integração pelo navegador, uma consulta com uma pergunta, um exame solicitado e conclusões preenchidas foi concluída com **41/100**: as parcelas foram 25, 25, 25, 75, 75 e 22; a média de 247/6 foi arredondada. A avaliação-base usou a alternativa de feedback limitado, e o parecer complementar foi gerado separadamente. O fluxo funcionou, mas isso não certifica a pertinência clínica de cada crédito ou desconto. O artefato privado é `instance/evaluations/basic-curriculum-20261016/basic-ui-finalization.json`.

O consumo dessa integração foi: diálogo, uma requisição com 3.158 tokens de entrada e 131 de saída, dos quais 1.792 de entrada em cache; avaliação, quatro requisições com 25.027 de entrada e 8.019 de saída, dos quais 14.976 de entrada em cache; parecer, uma requisição com 10.980 de entrada e 1.716 de saída, sem entrada em cache informada como acerto. Esses números não incluem os cinco ensaios isolados da categoria. A categoria de 22/100 da integração foi revalidada localmente com a guarda final e permaneceu exatamente igual, sem nova chamada. Nenhum dos 60 casos foi criado por chamadas à DeepSeek.

A etapa completa somou **11 requisições**: cinco ensaios isolados da categoria e seis na integração. Foram **87.929 tokens de entrada**, **22.388 de saída**, **110.317 no total** e **27.520 de entrada em cache**. O total inclui respostas rejeitadas e retestes; não é uma estimativa de custo por consulta.

A suíte anterior ao último ajuste de informante/R05, já com a guarda de contexto, aprovou **1.344 testes Python em 81,92 s** e **93 testes JavaScript/Node**, sem falhas. A instalação ficou com **14.427 fichas** e 52 especialidades distintas representadas. O relatório privado `instance/evaluations/basic-curriculum-20261016/db-final-preservation.json` confirmou por hashes a preservação dos 14.367 casos, quatro contas, 12 consultas, 98 turnos, 14 exames solicitados e cinco memórias de simulação preexistentes. A verificação de navegador acrescentou somente uma consulta de teste, um turno, um exame, uma memória e três registros de consumo, além dos controles operacionais usuais. Não houve recálculo de notas históricas nem alteração das fichas reais. A etapa não incluiu nova validação de voz em tablet ou implantação cPanel.

O ajuste final separa a voz do informante pelo campo explícito `respondent_gender`, preservando o sexo/gênero do paciente; os sete casos respondidos por mães selecionam Dora, sem inferência pelo nome. Passaram **57 testes direcionados** desse contrato, sem nova síntese de áudio ou chamada à DeepSeek. R05 também explicita ao menos três aspectos sintomáticos distintos e pertinentes por órgão/sistema explorado, aceitando perguntas compostas e dados já conhecidos, com exceções contextuais; não exige três turnos nem usa contagem por palavras-chave. Essa redação não foi objeto de nova rodada paga, e o consumo da etapa permanece em 11 requisições.

Com todos os ajustes congelados, a suíte completa final aprovou **1.363 testes Python em 84,08 s** e **93 testes JavaScript/Node em 562 ms**, sem falhas e sem chamadas adicionais à API.

## Retomada da avaliação básica e geração compacta

Em 16/10/2026, uma falha reproduzida na etapa básica impedia a conclusão utilizável da consulta: a resposta do provedor tratava incorretamente objetivos sem informação disponível, e a validação interrompia a etapa. A correção mantém os 47 objetivos na análise, pois uma pergunta pertinente pode merecer crédito mesmo sem resposta na ficha. A omissão de um dado indisponível é convertida em não aplicável; não se atribui crédito nem se inventa uma resposta para suprir a fonte ausente.

O caminho final usa **uma geração semântica compacta e, se necessário, um reparo dirigido**. Cada item apresenta a justificativa antes do status. O contexto contém somente perguntas e respostas reais da conversa e os cartões de identificação e queixa visíveis, além dos objetivos e da disponibilidade de fonte. Não há mapa prévio de propósitos nem auditoria separada dessa categoria. Os 25 segundos finais do orçamento cooperativo de 60 segundos da avaliação ficam reservados à etapa básica. Os testes e limites desta seção correspondem a essa versão, substituindo os experimentos intermediários descritos abaixo.

A leitura independente encontrou créditos sem correspondência entre pergunta e objetivo em saídas anteriores: saudação contada como investigação da queixa, antecedente pessoal contado como familiar e um hábito contado como apoio familiar. Uma tentativa de auditoria introduziu contexto reservado como se fosse conhecido; restringir o contexto ao efetivamente exibido não eliminou todos os créditos indevidos. O mapa de propósitos também produziu falsos bloqueios e acrescentou outra chamada. Experimentos com raciocínio estendido e modelo pro tiveram falhas de conexão ou não concluíram dentro do ensaio. Essas alternativas foram rejeitadas; os artefatos foram preservados, sem reescrever respostas pagas nem escolher uma nota-alvo.

O ensaio final `grade-compact-final.json` concluiu os **47 critérios com 15/100 na categoria em 14,726 segundos e duas requisições**. A primeira resposta trouxe 47 itens; a validação exigiu reparo de dois itens por crédito positivo sem pergunta associada, preservando os demais. A saída final deixou de atribuir os créditos indevidos citados e reconheceu informações já visíveis como contexto, sem creditá-las a uma pergunta diferente. Isso verifica defeitos concretos nesta entrevista; não demonstra calibração clínica geral, estabilidade entre modelos ou ausência de outros erros semânticos. SHA-256 do artefato: `04921779745689709afd431cfb48fe18ef306414bd8d4cfad9d14fcc5479e680`.

Quando a etapa continua inválida ou indisponível, a entrevista fica encerrada, a página mostra as cinco dimensões clínicas já validadas e a **nota final permanece pendente**. A retomada autenticada reaproveita a base privada somente se o hash da ficha e da atividade coincide, sem reabrir a entrevista após revelar a referência. Não há uma nota final de cinco dimensões usada como substituto silencioso. A integração final reproduziu as duas respostas pagas exatas, **sem novas chamadas**, e concluiu com **45/100 global e 15/100 na categoria**. As cinco dimensões anteriores, a conversa e a data de encerramento permaneceram intactas; o resultado respondeu HTTP 200 e o ranking foi atualizado. Evidência privada: `recovery-final.json`, SHA-256 `500297503835a0c903c922b177f339808d0e68f0ee0d0a2d13f01b8503886c3f`.

### Consumo observado desta correção

O levantamento considera uma vez cada JSON distinto com `usage.requests > 0` em `instance/local-preparation/basic-finish-fix/`. A coluna de tokens mostra somente o uso informado pelo provedor; não estima consumo de respostas ausentes.

| Artefato | Requisições / respostas com uso | Tempo (s) | Entrada / saída informadas | Resultado do ensaio |
| --- | ---: | ---: | ---: | --- |
| `reproduction.json` | 1 / 1 | 10,060 | 11.804 / 2.603 | Reproduziu a rejeição por fonte indisponível |
| `recovery-v2.json` | 2 / 2 | 13,916 | 17.897 / 2.984 | Entrevista encerrada, categoria ainda pendente |
| `recovery-semantic-audited.json` | 1 / 1 | 6,747 | 11.786 / 1.407 | Auditoria intermediária; resultado não concluído |
| `recovery-semantic-visible.json` | 1 / 1 | 8,553 | 9.494 / 1.939 | Saída aceita estruturalmente, com limites semânticos; estratégia descartada |
| `audit-thinking-low.json` | 1 / 0 | 20,653 | Desconhecidos | Falha de conexão, sem relatório de uso |
| `grade-thinking-low.json` | 1 / 0 | 40,700 | Desconhecidos | Falha de conexão, sem relatório de uso |
| `map-purposes.json` | 1 / 1 | 2,934 | 3.479 / 456 | Mapa intermediário; não é avaliação final |
| `grade-purpose-mapped.json` | 3 / 3 | 14,457 | 23.197 / 3.101 | Rejeição de correspondência entre mapa e objetivos |
| `grade-purpose-pro.json` | 2 / 1 | 20,933 | 5.949 / 455, parciais | Uma resposta e uma falha de conexão sem uso informado |
| `grade-compact-final.json` | 2 / 2 | 14,726 | 10.612 / 2.997 | Categoria final válida com reparo dirigido |

São **15 tentativas HTTP**, com **12 respostas e 12 relatórios de uso**: **94.218 tokens de entrada**, **15.942 de saída**, **110.160 informados no total**, incluindo **15.616 tokens de entrada em cache** e **78.602 fora do cache**. O acerto corresponde a 16,57% da entrada conhecida, não da entrada de todas as tentativas. Há **três tentativas sem uso informado**; os zeros nos contadores locais dessas falhas não significam custo zero. O consumo total efetivamente faturado não pode ser concluído apenas desses registros. O levantamento inclui experimentos rejeitados e correções; não é um orçamento por consulta.

`recovery.json`, `recovery-paid-validated.json` e `recovery-final.json` registram zero chamadas próprias e não duplicam o consumo das respostas reaproveitadas. Backups, reprodução local, migração do ranking e arquivos sem chamadas também não entram no total. Os dados da conversa e os identificadores da conta permanecem nos artefatos privados; este relatório publica somente o método, as contagens e as limitações. As regras da classificação estão em [Ranking de estudantes](RANKING.md): média das tentativas válidas de cada caso, seguida da média entre casos distintos, com mínimo de cinco na categoria.

A suíte anterior à última proteção de persistência aprovou **1.463 testes Python em 108,92 segundos** e **101 testes JavaScript/Node**, sem falhas. A compatibilidade do esquema MySQL foi conferida por compilação do DDL; não houve teste em um servidor MySQL real. Esses testes verificam contratos e regressões do software, sem substituir a revisão clínica dos julgamentos da IA.

A revisão de persistência encontrou que uma falha de gravação do ranking podia deixar a nota salva sem o agregado correspondente. A conclusão agora confirma a propriedade do bloqueio e salva resultado e ranking na mesma transação, com rollback em qualquer falha. Há uma única repetição da gravação com a avaliação já recebida, sem nova chamada à IA. Se o banco continuar falhando, o rascunho permanece salvo, nenhuma nota ou classificação parcial é publicada e a interface informa que a conclusão deve ser tentada novamente.

Após essa proteção de persistência, a suíte completa final aprovou **1.468 testes Python em 122,42 segundos**, sem falhas. Os **101 testes JavaScript/Node** continuam aprovados; a proteção final alterou somente o servidor e testes Python.

## Contexto social do paciente e pergunta sobre religião

Em 16/10/2026, uma pergunta sobre religião encontrou HTTP 503 no diálogo. A reprodução determinística, **sem chamada à API**, identificou a causa no validador: qualquer detalhe novo com o tema Informações pessoais era rejeitado como `unsupported_important_detail`, e esse tema também era contado como detalhe clínico. Assim, uma elaboração social permitida pelo personagem podia esgotar o reparo e falhar. O artefato privado `instance/local-preparation/patient-social-fix/baseline.json` preserva a reprodução; SHA-256 `5dbabb13f60560ac0003f8767ca99966c59973a5e116363c052534f7a8e3a518`.

A correção permite detalhes sociais fictícios coerentes e registrados na memória, sem mudar identidade ou dados já estabelecidos. Informações pessoais deixam de consumir o limite de novos detalhes clínicos; as restrições a doenças, medicações, exames e resultados inventados permanecem. O prompt orienta acolher perguntas gerais sem exigir nexo diagnóstico, sem inferir crenças por nome, sexo ou origem e sem associar automaticamente religião a restrições de tratamento. Os logs de rejeição registram somente número da tentativa e código do validador, sem pergunta, resposta, perfil ou credenciais.

Na avaliação básica, religião ou preferência religiosa (`I09`) passou ao núcleo do roteiro de identificação; o aprofundamento de crenças e práticas que influem no cuidado (`S07`) permanece condicional. Perguntar religião não recebe automaticamente crédito nos dois objetivos. Autodeclaração, ausência de religião, informação já conhecida, recusa e limitações contextuais continuam respeitadas. Nenhuma pergunta específica se tornou pré-requisito técnico para concluir a consulta. Não houve nova avaliação paga para medir esse ajuste da rubrica; sua conferência nesta etapa foi determinística.

Dois ensaios reais independentes usaram cópias da conversa e da memória, **sem alterações no banco**. O primeiro concluiu oito perguntas com oito respostas válidas, uma requisição por pergunta, em **1,353–2,438 segundos por resposta**. A confirmação manteve a preferência religiosa criada anteriormente, e respostas posteriores preservaram o contexto de moradia. O teste de uma falsa alternativa cotidiana também respondeu de forma lógica, conservando o dado urinário documentado. Contudo, várias respostas repetiram uma pergunta de justificativa trazida pelo histórico; oito respostas válidas não significaram naturalidade plenamente resolvida.

Após reforço de estilo, o ensaio curto concluiu três perguntas com três respostas válidas, uma requisição por pergunta, em **1,621–1,925 segundos por resposta**. A confirmação novamente preservou a preferência religiosa, e o bordão literal anterior não se repetiu. A primeira resposta ainda perguntou se a crença era importante para o tratamento: é uma limitação de estilo remanescente, sem recusa nem falha HTTP. Não foram feitas novas chamadas apenas para perseguir uma formulação ideal. Os **11 turnos dos dois ensaios** concluíram sem 503; isso não garante ausência de falhas ou estereótipos em outras conversas.

| Artefato privado | Requisições / respostas | Entrada | Saída | Cache hit / miss |
| --- | ---: | ---: | ---: | ---: |
| `patient-social-fix/live-dialogue.json` | 8 / 8 | 29.803 | 818 | 23.168 / 6.635 |
| `patient-social-fix/live-dialogue-final.json` | 3 / 3 | 10.678 | 291 | 5.888 / 4.790 |
| **Total desta correção** | **11 / 11** | **40.481** | **1.109** | **29.056 / 11.425** |

O total informado foi de **41.590 tokens**, com uso completo nos dois artefatos. A reprodução local sem API não entra nesse consumo. SHA-256 dos ensaios: `8de803edca07582c32f15eb69ed0d3593134c79abff60c3da842f258bfbe04e3` e `73393ddc67e93325cf01d43bcb04f99e6192c88b08d7674fbde93d0e310e0308`, respectivamente. As falas e o contexto individual permanecem privados.

A suíte completa aprovou **1.501 testes Python em 107,13 segundos**, antes do último reforço exclusivamente de estilo no prompt. Após esse reforço, **139 testes focados passaram em 0,85 segundo**, cobrindo diálogo social, integração web e perguntas básicas. A integração conferiu o texto salvo entregue ao fluxo de síntese de voz; **não houve novo teste com microfone físico nem medição de áudio ou tablet** nesta etapa. A aceitação de formato, memória e persistência não certifica a naturalidade ou a pertinência de toda elaboração social.
