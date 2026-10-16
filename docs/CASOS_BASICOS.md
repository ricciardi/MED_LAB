# Casos Básicos

O BayesCore MedRounds inclui **60 cenários fictícios em português**, organizados em sete sistemas. Eles foram escritos para praticar entrevista, vocabulário e raciocínio de graduação a partir das listas de termos fornecidas. Não são relatos de pacientes reais nem adaptações dos artigos do acervo MedCaseReasoning. Identidades, rotinas, contexto e valores de exames são sintéticos e coerentes com cada exercício.

| Sistema | Cenários | Arquivo |
| --- | ---: | --- |
| Urinário | 8 | `medlab/data/basic_cases/urinary.json` |
| Digestivo | 8 | `medlab/data/basic_cases/digestive.json` |
| Hematopoiético | 5 | `medlab/data/basic_cases/hematopoietic.json` |
| Nervoso | 7 | `medlab/data/basic_cases/nervous.json` |
| Respiratório | 15 | `medlab/data/basic_cases/respiratory.json` |
| Cardiovascular | 8 | `medlab/data/basic_cases/cardiovascular.json` |
| Cabeça | 9 | `medlab/data/basic_cases/head.json` |
| **Total** | **60** | **Sete arquivos** |

Esses exercícios se somam aos oito casos fictícios iniciais e aos 14.359 casos baseados em relatos, totalizando **14.427 fichas** na instalação conferida. Os relatos reais conservam suas referências e estados de revisão próprios; os 60 exercícios não aumentam a contagem de fontes clínicas reais.

## Escolher e praticar

Abra **Casos Básicos** no catálogo e selecione **Sistema para praticar**. Os filtros de apresentação, dificuldade e exames continuam disponíveis, e o sorteio respeita o recorte escolhido. O sistema acompanha a organização das listas de ensino; a especialidade e a apresentação são classificadas conforme cada cenário. Assim, um exercício de diabetes da trilha urinária pertence à Endocrinologia, e uma dor de garganta da trilha digestiva pode pertencer à Otorrinolaringologia. O sistema orienta a escolha sem revelar o diagnóstico. A apresentação pública usa a queixa leiga; objetivos, diferenciais, diagnóstico e conduta de referência ficam reservados para a avaliação e o resultado.

A consulta usa o fluxo habitual: conversar por texto ou voz, abrir **Exames**, solicitar os achados desejados e finalizar com hipótese, diferenciais e plano. Perguntar sobre um exame no chat não executa sua solicitação. Os resultados são textos sintéticos preparados na ficha; estes 60 exercícios não incluem imagens clínicas próprias. Exames opcionais têm contexto específico e não são automaticamente exigidos para pontuar. Quando há informante com `respondent_gender` explícito, a voz corresponde a essa pessoa: nos sete cenários respondidos por mães, usa-se Dora, preservando o sexo/gênero do paciente.

“Básico” identifica a trilha de formação, não baixa gravidade. Há cenários com sinais de urgência; reconhecer a prioridade é parte do exercício. A dificuldade exibida tampouco certifica complexidade ou competência clínica.

## Cobertura dos termos

Os manifestos ligam cada termo a objetivos e trechos reservados da ficha:

- `medlab/data/basic_cases/coverage_delivery.json`: **75 termos** das imagens 1–4, distribuídos pelos 28 cenários urinários, digestivos, hematopoiéticos e nervosos.
- `medlab/data/basic_cases/coverage-root.json`: **63 entradas originais** das imagens 5–7, distribuídas pelos 32 cenários respiratórios, cardiovasculares e de cabeça.

A contagem respeita as listas de origem: “Proteinorraquia; Glicorraquia” tem dois objetivos no primeiro manifesto, enquanto “Hipóxia; hipoxemia” é uma entrada original no segundo. Não são 138 doenças distintas. Há doenças, sintomas, achados, exames, estruturas anatômicas e processos fisiológicos. Um termo pode aparecer como contraste, diferencial ou indicação seletiva, sem estar presente como diagnóstico do paciente. Conceitos relacionados podem compartilhar o mesmo cenário; vínculos entre sistemas também são possíveis, como bilirrubinúria em um caso digestivo.

Os cenários têm redação original e referências institucionais no gabarito. Entre as fontes consultadas estão NIDDK/NIH, CDC, NHLBI, NIDCR, NIDCD, NHS, NICE, ISSVA e RadiologyInfo/ACR-RSNA. Os links específicos acompanham cada ficha. As listas foram conferidas contra as imagens fornecidas; revisão de esquema, coerência textual e cobertura não equivale a validação clínica humana.

## Perguntas Básicas de Graduação

As **novas avaliações**, inclusive fora desta trilha, acrescentam uma sexta dimensão pontuada: **Perguntas Básicas de Graduação**. Ela avalia semanticamente 47 objetivos em sete grupos:

| Grupo | Objetivos |
| --- | ---: |
| Identificação e informante | 11 |
| Queixa principal | 2 |
| História da doença atual | 12 |
| Revisão de sistemas | 5 |
| Antecedentes familiares | 2 |
| Hábitos e estilo de vida | 6 |
| Condições socioeconômicas e culturais | 9 |
| **Total** | **47** |

É um roteiro amplo de formação na graduação, além da investigação focal da queixa. Dados gerais de moradia, saneamento, apoio e escolaridade não ficam dispensados apenas por não alterarem o diagnóstico. Recursos e acesso ao cuidado podem ser explorados respeitosamente, sem exigir renda exata. **Religião ou preferência religiosa (`I09`) integra o núcleo de identificação**, mesmo sem nexo diagnóstico: aceita autodeclaração, ausência de religião e paráfrases respeitosas, sem exigir detalhes íntimos. Aprofundar crenças, tradições ou práticas que influem no cuidado (`S07`) continua condicional; uma pergunta sobre religião não recebe automaticamente crédito nos dois objetivos. Outros itens condicionais, como etnia/cor, nome da mãe, encaminhamento ou exposição a rios, dependem de finalidade, necessidade e preferência compatíveis com o contexto.

O julgamento também considera disponibilidade do dado, informação já exibida ou espontaneamente relatada, urgência, recusa e capacidade do paciente ou informante; não exige repetição mecânica nem deve atrasar atendimento urgente. **Nenhuma pergunta específica é pré-requisito técnico para finalizar a consulta.** Paráfrases e linguagem leiga podem cumprir o objetivo; mencionar uma palavra não comprova que o aluno fez a pergunta. Falas do paciente servem como contexto, não como ação do estudante. A história reservada ao avaliador não é prova de informação já conhecida pelo aluno. Na revisão dos órgãos/sistemas explorados, o roteiro orienta ao menos três aspectos sintomáticos distintos e pertinentes, aceitando perguntas compostas e informações já conhecidas, com adaptação à urgência, recusa ou falta de dados; não são três turnos obrigatórios nem contagem por palavras-chave.

Cada objetivo recebe **completo (100)**, **parcial (50)**, **não atendido (0)** ou **não aplicável (N/A)**, com justificativa e referências aos registros. Primeiro se calcula a média dos itens aplicáveis de cada grupo; depois, a média dos grupos aplicáveis, com peso igual entre eles. Assim, a lista de identificação não domina a categoria. Grupos sem itens aplicáveis ficam fora do divisor.

A nota final é a média simples das dimensões avaliáveis: anamnese, exame físico, exames complementares, raciocínio diagnóstico, plano terapêutico e perguntas básicas. Zero participa da média; uma dimensão inteiramente não aplicável fica fora. O resumo global não acrescenta outro peso. Por exemplo, seis dimensões com 60, 80, 40, 70, 50 e 90 resultam em 65/100. Se a última for N/A, as cinco primeiras resultam em 60/100. **Avaliações históricas de cinco dimensões não são recalculadas.**

A anamnese continua seguindo a rubrica específica do caso; perguntas básicas examinam a formação geral contextual. A orientação ao avaliador evita replicar automaticamente uma mesma penalização nas duas dimensões. A pertinência e o crédito parcial são julgamentos da IA, sujeitos a erro; validação estrutural, autoria dos IDs e aritmética correta não comprovam calibração clínica.

## Falhas e retomada

A etapa de perguntas básicas usa **uma geração semântica compacta, com até um reparo dirigido**. Para cada um dos 47 objetivos, a IA escreve primeiro a justificativa (`reason`) e depois o status. Ela recebe somente as perguntas e respostas reais da conversa e os cartões de identificação e queixa efetivamente visíveis (`K1`/`K2`), além dos objetivos e da indicação de disponibilidade de fonte. Não recebe o perfil reservado como se o aluno o conhecesse, nem usa mapa prévio de propósitos ou uma chamada separada de auditoria dessa categoria. O servidor valida os IDs, a autoria e a estrutura; um reparo pede apenas os itens inválidos e conserva os demais. Essa etapa não usa regras por palavras-chave nem recalcula as cinco dimensões clínicas já validadas.

Uma pergunta pertinente pode receber crédito mesmo quando a ficha não traz a resposta. A ausência de informação na ficha impede descontar pela omissão desse item: nesse caso, o servidor registra a exclusão como não aplicável, sem inventar crédito ou resposta. As falas do paciente e os cartões visíveis podem fundamentar contexto já conhecido, mas não são ações do estudante. Os últimos **25 segundos do orçamento cooperativo de 60 segundos** da avaliação ficam reservados à categoria; geração e eventual reparo compartilham esse tempo.

Se a etapa continuar inválida ou indisponível, a entrevista é encerrada e a página de resultado apresenta as cinco dimensões já validadas, com **nota final pendente**. Isso não equivale a isentar a categoria nem calcular uma nota final somente com cinco dimensões. O botão de retomada processa apenas a categoria pendente, usando a conversa e as conclusões já salvas; não permite editar a entrevista após revelar a referência. O checkpoint é privado e seu hash precisa corresponder à atividade salva antes de chamar a IA. Uma tentativa que chegou ao provedor pode ter consumo mesmo quando não produziu resultado utilizável.

O modo demonstração não chama a IA para essa categoria e a mantém não pontuada. O parecer **Para sua próxima consulta** continua sendo uma etapa separada, posterior à nota, sem recalculá-la. Detalhes de prazo, cotas e atualização estão no [guia cPanel](CPANEL.md#resultado-e-parecer-complementar); as medições e limitações estão no [relatório de testes](TESTES_DEEPSEEK.md#casos-básicos-e-perguntas-básicas-de-graduação).

## Instalação e manutenção

`init-db` e `upgrade-db` incluem os sete arquivos autorais. Em instalação existente, faça backup e use `flask --app app upgrade-db`; a migração acrescenta `learning_track` e `body_system` ao índice `case_discovery` quando ausentes. Para importar somente os Casos Básicos, execute `flask --app app import-cases`.

A importação valida o lote e inclui apenas IDs novos. Conteúdo, estado de revisão e suspensão de um ID existente são preservados. Os exercícios novos ficam publicados como material educacional `educational_seed`, sem registrar revisão clínica fictícia. Os manifestos de cobertura não são casos importáveis. Nenhuma dessas operações chama a DeepSeek para escrever ou importar os 60 cenários.
