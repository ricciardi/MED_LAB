# MED_LAB — arquitetura e origem pedagógica

MED_LAB é uma aplicação web em português para treinamento com pacientes simulados. O estudante abre uma consulta, conversa com o paciente, solicita exames e registra hipótese diagnóstica, diferenciais e plano de tratamento. A avaliação é educacional e depende do conteúdo do caso e das ações registradas.

## Estrutura da aplicação

| Camada | Responsabilidade |
| --- | --- |
| `app.py`, `passenger_wsgi.py` | Entradas de desenvolvimento/CLI e WSGI para Passenger. |
| `medlab.create_app(config=None)` | Fábrica Flask: configura ambiente, extensões, rotas e comandos. |
| `medlab.extensions` | SQLAlchemy, autenticação e proteção CSRF. |
| `medlab.models` | Usuários, casos, consultas, turnos, exames, inventário privado de fontes e identificadores de exclusão opcional. |
| `medlab.source_registry` | Registro idempotente por base/PMCID, hashes e sincronização dos checkpoints; exportação schema 2 de metadados e bloqueios de reimportação, sem texto integral ou alegações de tradução. |
| `medlab.web`, `medlab.admin` | Fluxo do estudante, isolamento das consultas por usuário e administração autenticada. |
| `medlab.ai` | Integração de servidor com DeepSeek e modo de demonstração explícito. |
| `medlab.usage` | Registro dos contadores informados pelo provedor nas operações web, separado dos relatórios da importação. |
| `medlab.speech` | Configuração de voz local e opções coerentes com o perfil do paciente. |
| `frontend/`, `medlab/static/voice.js` | Workers de Whisper/Kokoro e controles de gravação, revisão e reprodução no navegador. |
| `scripts/prepare_voice_assets.py` | Aquisição conferida por checksum dos runtimes estáticos de voz, sem compilação no cPanel. |
| `medlab.catalog`, `medlab.catalogue` | Taxonomia, filtros compartilhados, apresentação pública neutra e contagens. |
| `medlab.importer` | Preparação PMC, proveniência e adaptação retomável de relatos reais em rascunhos privados. |
| `medlab.migrations` | Atualização idempotente do esquema de catálogo existente. |
| `medlab/data/cases_pt.json` | Casos didáticos curados para a experiência em português. |
| `medlab/data/source_registry.jsonl`, `medlab/data/real_case_drafts.json` | Arquivos opcionais da entrega: metadados/exclusões e fichas reais privadas, restaurados separadamente e sem publicar automaticamente. |
| `EasyMED/` e `SPBench/` | Código e dados originais preservados como referência. |
| `tests/` | Verificação automatizada com banco temporário e IA simulada. |

O navegador envia ações autenticadas ao Flask. O servidor identifica a consulta e seu proprietário antes de acessar os dados. Perguntas e respostas persistem no banco; o serviço de IA recebe o conteúdo necessário do caso e do histórico. A chave DeepSeek fica no ambiente do servidor. A avaliação concluída é salva para consulta posterior.

A voz é opcional e processada no navegador após ativação pelo estudante. Whisper tiny multilíngue transcreve o áudio local; a pessoa revisa o texto e o envia pelo fluxo normal da consulta. Kokoro sintetiza as respostas já salvas, com as vozes brasileiras Dora, Alex e Santa. O servidor entrega configuração e texto autorizado, sem receber o áudio da gravação ou executar os modelos. Os pesos são baixados do Hugging Face e o runtime JavaScript/WebAssembly é servido pela própria aplicação. O cache do navegador pode reutilizar os arquivos, sem garantir funcionamento permanente offline.

Whisper tenta WebGPU quando disponível, com alternativa WebAssembly em uma thread. Kokoro usa exclusivamente WebAssembly em uma thread: o teste de TTS quantizado em WebGPU produziu fala que não preservou o conteúdo e esse caminho foi desativado. Whisper e Kokoro usam runtimes JavaScript separados, com versões fixadas e conferidas no manifesto. Não é necessário isolamento de origem ou infraestrutura de GPU no cPanel; a instalação Python permanece sem PyTorch, ffmpeg ou Node. Os downloads iniciais somam aproximadamente 100 MB para reconhecimento e 140 MB adicionais para síntese; desempenho, memória disponível e permissões variam por dispositivo. O teste no Samsung real ainda está pendente. [Créditos e licenças da voz](CREDITOS_VOZ.md) descrevem os componentes, as medições locais e suas condições.

## Modelo de dados

- **User:** nome, email, hash de senha, estado da conta e permissão administrativa. O cadastro público cria estudantes.
- **ClinicalCase:** identificador, título privado, especialidade, tipo de queixa, dificuldade, resumo privado, conteúdo estruturado e estado de publicação. Relatos adaptados guardam proveniência dentro do conteúdo.
- **Consultation:** proprietário, caso de origem, cópia do caso no início da sessão, estado, datas, diagnóstico, diferenciais, tratamento, avaliação e nota.
- **Turn:** pergunta, resposta, intenção clínica e identificador da requisição dentro de uma consulta.
- **PerformedExam:** exame solicitado, tipo e resultado vinculado à consulta.
- **SourceRecord:** metadados privados da fonte, estado de preparação e vínculo ao caso, sem artigo integral.
- **SourceExclusion:** identificador mínimo de uma fonte explicitamente excluída, para impedir reintrodução por registro, sincronização ou restauração.
- **AIUsage:** contadores por operação web, datas e vínculos internos, sem prompts ou respostas; distingue consumo informado, parcial e ausente.

A cópia do caso na consulta evita que edições futuras no catálogo alterem retroativamente o material usado em uma sessão. A repetição de uma requisição de mensagem usa um identificador próprio para evitar turnos duplicados. Controle de ocupação da consulta coordena operações de IA concorrentes.

O estudante vê um título neutro e resumo baseado no tipo de queixa. Busca e sorteio usam os mesmos filtros de especialidade, tipo e dificuldade; a busca não consulta títulos originais, artigos, referências ou gabaritos. A biblioteca e o histórico são paginados. Referência diagnóstica e atribuição detalhada da fonte aparecem após concluir a consulta.

## Fluxo educacional e seis dimensões

A referência original é o projeto [EasyMED, da FreedomIntelligence](https://github.com/FreedomIntelligence/EasyMED), preservado neste repositório. Ele separa reconhecimento de intenção, simulação do paciente e avaliação da consulta. O módulo `EasyMED/evaluation.py` define seis dimensões de desempenho do estudante:

| Chave | Dimensão em português | Evidência avaliada |
| --- | --- | --- |
| `consultation` | Anamnese | Completude e pertinência das perguntas em relação aos dados e itens de referência do caso. |
| `physical` | Exame físico | Escolha de avaliações físicas e uso dos achados disponíveis. |
| `auxiliary` | Exames complementares | Seleção e interpretação de exames laboratoriais e de imagem. |
| `diagnosis` | Raciocínio diagnóstico | Hipótese principal, diferenciais e justificativas compatíveis com o caso. |
| `treatment` | Plano terapêutico | Adequação do plano apresentado no contexto do cenário simulado. |
| `overall` | Desempenho global | Síntese de pontos fortes, lacunas e sugestões para estudo. |

O relatório deve refletir ações observáveis na consulta. Não realizar um exame não é o mesmo que um exame normal; ausência de dados no caso não comprova ausência de doença. A qualidade do feedback depende da curadoria do caso e deve ser revisada por docentes antes de uso avaliativo formal.

Em relatos com dados incompletos, as rubricas de exame físico, exames complementares ou tratamento podem declarar `assessable=false`, sem critérios pontuáveis e com justificativa. A nota global é a média das dimensões avaliáveis entre as primeiras cinco; a dimensão global repete essa nota. Dados não informados permanecem desconhecidos, e não são convertidos em achados normais para preencher o esquema.

O SPBench original também define **oito dimensões de qualidade do paciente virtual**: compreensão da pergunta, precisão da informação, divulgação controlada, completude da resposta, coerência narrativa, linguagem simples, consistência de memória e cooperação. Essas dimensões avaliam o comportamento do simulador. As seis dimensões acima avaliam o estudante e têm outra finalidade.

## Preservação e limites dos casos originais

Os **50 arquivos JSON**, `SPBench/SPBench_case/01.json` a `50.json`, permanecem disponíveis como fonte de pesquisa. Todos contêm `caseId`, `caseTitle`, `caseDescription` e `patientProfile`; cada perfil tem 26 campos. Os textos originais estão majoritariamente em chinês e abrangem cenários clínicos, cirúrgicos, pediátricos, ginecológicos, psiquiátricos, odontológicos e de medicina tradicional chinesa.

Os arquivos fornecem perfil e história do paciente, mas **não incluem os gabaritos estruturados completos** esperados pelo avaliador original, como `mustConsultionItems`, `physicalExams`, `auxiliaryLabs`, `diagnoses` e `treatments`. Por isso, disponibilizar esses arquivos não equivale a afirmar que todos estão traduzidos, validados ou prontos para avaliação nas seis dimensões.

A experiência web utiliza oito casos didáticos em português: tosse e febre, ardor ao urinar, cefaleia recorrente, dor lombar após esforço, desânimo e perda de interesse, sede e cansaço, diarreia infantil e chiado noturno. O catálogo inclui perfis, achados de exames, diagnóstico de referência, objetivos, fontes e rubricas nas seis dimensões. Esses casos iniciais têm estado `educational_seed`; essa identificação não significa validação clínica independente.

Casos gerados por IA precisam de revisão editorial e clínica antes de publicação. Revisão, correções e publicação pertencem ao administrador; a geração automática não estabelece validade clínica. O código original permanece como referência e não é executado diretamente como servidor web.

O acervo original MedCaseReasoning foi adquirido integralmente em cache privado: **14.489 registros**. Depois da exclusão explícita de 11 artigos retratados e 2 fontes indisponíveis, há **14.476 fontes ativas verificadas na instalação de preparação**: 10.761 para adaptação licenciada e 3.715 para preparação factual ND. A meta de 55 especialidades × 300 casos exige 16.500 fichas únicas, com déficit mínimo de 2.024 fontes antes de considerar completude e distribuição. O CSV não contém rótulos de especialidade; esses totais não comprovam cobertura de todas as áreas.

O CSV bruto permanece privado e não acompanha o ZIP da aplicação. O empacotador pode incluir metadados/exclusões (`--source-registry`) e uma lista de rascunhos (`--case-drafts`), validando suas fontes correspondentes. `init-db`/`upgrade-db` restauram esses arquivos de forma idempotente, respeitando a política de reutilização e preservando casos/revisões existentes. Os rascunhos entram privados em `review_required`. Uma ficha já preparada não torna a fonte verificada no servidor novo: sem XML local conferido, os metadados restaurados mantêm a verificação pendente. Usuários, conversas, banco e artigos integrais não são transferidos pelo ZIP.

O arquivo administrativo separado `medlab-acervo-retomada.tar.gz` conserva CSV, snapshots, checkpoints e exportações, sem banco ou credenciais. Destina-se a uma instalação nova; a sincronização valida os arquivos transferidos antes de restaurar o progresso. O manifesto `MANIFEST-ACERVO.sha256` é independente do manifesto do aplicativo. Esse arquivo de aproximadamente 270 MB fica no servidor privado e não faz parte dos modelos de voz baixados pelo navegador.

A página administrativa `/acervo`, dentro de `ADMIN_PATH`, distingue registro, verificação, português preparado, rascunhos e publicação; o aluno vê somente casos publicados. O estado desta entrega é **seis fichas reais preparadas por Codex e revisadas por outro agente, privadas; 14.470 fontes ainda sem ficha; zero casos reais publicados e oito fictícios publicados**. A revisão clínica humana permanece pendente. O importador usa metadados e XML oficiais do PMC, verifica licença/checksum e dispõe do fluxo DeepSeek de classificação, preparação e auditoria por IA. As evidências são conferidas contra a fonte e cada rascunho requer revisão antes de publicar. Consulte [o inventário e os limites da base](BASES_CLINICAS.md).

O produto é uma ficha em português para paciente simulado e avaliador: fatos disponíveis, fala leiga, exames, diagnóstico de referência e rubrica. Não é uma tradução integral do artigo. O artigo original permanece privado para verificação. Com `CASE_ALLOW_CLINICAL_FACTS=true` (padrão `false`), um extrator produz fatos atômicos e um gerador isolado recebe somente esses fatos para escrever paciente e rubrica. A ficha conserva hashes/localizadores de evidência e a referência da fonte, sem reproduzir o texto do relato nem atribuir automaticamente sua licença ND à redação nova. Esse fluxo exige verificação e revisão próprias; não equivale a liberar ND para tradução. As 3.715 fontes ND foram verificadas e conservadas nesse fluxo. Artigos retratados e fontes não verificáveis foram excluídos explicitamente; `excluded_pmcids` impede sua reimportação. O formato portátil schema 2 conserva os bloqueios mínimos sem restaurar os registros removidos e sem apagar casos/históricos já vinculados.

O processamento no Terminal usa dois limites independentes: `--source-workers 1..8` para PMC e `--adapt-workers 1..16` para casos com IA. Adaptação paralela exige `--adapt --all-records`; cada caso possui serviço de IA próprio. Resultados são devolvidos ao coordenador, que grava checkpoints, chama a sincronização e escreve no banco sequencialmente. Erros gerais do provedor param novas chamadas, preservando resultados válidos já recebidos; requisições já em voo não podem ser desfeitas. A retomada exige ação explícita, sem repetição automática de chamadas pagas.

O relatório separa tarefas enviadas, terminadas e interrompidas, além dos tokens de entrada, saída e cache informados pelo provedor. Indicadores de completude evitam tratar uma resposta sem estatísticas como consumo zero. Esses dados não são uma estimativa de preço nem comprovam a execução de etapas que falharam. Nesta entrega, a autenticação da chave foi confirmada, mas a geração pela DeepSeek não foi validada durante a indisponibilidade do provedor. Os seis rascunhos foram preparados por Codex a partir de snapshots verificados. O procedimento de retomada está documentado; nenhum lote fica executando ou agendado automaticamente.

## Organização dos prompts e cache

O serviço de IA serializa objetos JSON de forma determinística, ordenando mapas recursivamente e preservando a ordem de listas. Instruções e formatos reutilizáveis precedem o conteúdo variável de caso, sessão, histórico e pergunta. Isso abrange intenção, resposta do paciente, avaliação, geração/preparação e auditorias. O paciente continua recebendo apenas os fatos pertinentes à pergunta: favorecer cache não autoriza incluir diagnóstico ou gabarito no seu contexto.

O avaliador recebe clínica, rubrica, momento clínico e dados ausentes, sem autoria, licença, hashes, citações de evidência ou datas da importação. A proveniência completa permanece no caso armazenado e no fluxo de revisão. O cache é administrado pela DeepSeek, não pelo banco do MED LAB; o aplicativo não força persistência nem faz aquecimento pago. Veja [regras atuais, consumo observado e limites](CPANEL.md#9-cache-de-prompts-e-consumo-observado).

O painel privado `ADMIN_PATH/uso-ia` agrega conversas, avaliações e geração de casos pelo site. O registro usa a diferença dos contadores antes/depois de cada operação e uma transação própria para conservar métricas após falhas, sem confirmar conteúdo pendente da consulta. Não inclui consumo histórico anterior à ativação nem os lotes do Terminal. O modo demonstrativo não cria consumo de API. A taxa de acerto só é calculada quando as estatísticas de cache estão completas; os tokens observados não substituem a fatura do provedor.

## Autenticação, persistência e falhas

As senhas são armazenadas como hashes. Flask-Login mantém a sessão autenticada e Flask-WTF protege ações mutáveis com CSRF. Cada acesso a uma consulta verifica o proprietário; conhecer o UUID não concede acesso. A administração tem prefixo configurável e exige permissão de administrador, independentemente de o endereço estar visível na interface.

A DeepSeek é chamada no servidor. Falhas de rede, limite de requisições e respostas inválidas precisam ser expostas como falhas recuperáveis; não devem virar avaliações inventadas ou duplicar o histórico. O modo demonstrativo só é habilitado por configuração explícita e desativa chamadas à DeepSeek. A voz local pode funcionar nesse modo, lendo respostas identificadas como demonstração. Falha no download ou na inferência de voz preserva o fluxo por texto. Testes automatizados com IA simulada não comprovam disponibilidade externa do provedor, e o funcionamento da voz deve ser verificado nos dispositivos usados pela instituição.

SQLite facilita o início e MySQL/PyMySQL permite usar o banco gerenciado pela hospedagem. O esquema inicial é criado por CLI; `upgrade-db` acrescenta o campo e índice de tipo de caso em instalações anteriores, preservando consultas e notas. Faça backup antes da migração. O [guia de cPanel](CPANEL.md) descreve configuração, inicialização, administração, importação, backup e restauração.
