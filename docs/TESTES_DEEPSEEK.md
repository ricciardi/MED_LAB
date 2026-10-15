# Testes reais do paciente e do avaliador — 15/10/2026

Este relatório registra chamadas reais à API DeepSeek, incluindo falhas. O estado final tem sete avaliações revalidadas com respostas reais já pagas: seis com feedback factual limitado e uma com feedback rico auditado, mantendo físico/complementares factuais. A correção final validou todos os identificadores antes de limitar as citações exibidas; não alterou notas nem repetiu chamadas de rede. A conversa melhorou nos cenários repetidos. Ainda há variabilidade de notas e limites semânticos; não há evidência para declarar calibração clínica nem extrapolar a amostra ao acervo inteiro.

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
