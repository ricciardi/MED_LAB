# MED_LAB — arquitetura e origem pedagógica

MED_LAB é uma aplicação web em português para treinamento com pacientes simulados. O estudante abre uma consulta, conversa com o paciente, solicita exames e registra hipótese diagnóstica, diferenciais e plano de tratamento. A avaliação é educacional e depende do conteúdo do caso e das ações registradas.

## Estrutura da aplicação

| Camada | Responsabilidade |
| --- | --- |
| `app.py`, `passenger_wsgi.py` | Entradas de desenvolvimento/CLI e WSGI para Passenger. |
| `medlab.create_app(config=None)` | Fábrica Flask: configura ambiente, extensões, rotas e comandos. |
| `medlab.extensions` | SQLAlchemy, autenticação e proteção CSRF. |
| `medlab.models` | Usuários, casos, consultas, turnos e exames realizados. |
| `medlab.web`, `medlab.admin` | Fluxo do estudante, isolamento das consultas por usuário e administração autenticada. |
| `medlab.ai` | Integração de servidor com DeepSeek e modo de demonstração explícito. |
| `medlab.speech` | Configuração de voz local e opções coerentes com o perfil do paciente. |
| `frontend/`, `medlab/static/voice.js` | Workers de Whisper/Kokoro e controles de gravação, revisão e reprodução no navegador. |
| `scripts/prepare_voice_assets.py` | Aquisição conferida por checksum dos runtimes estáticos de voz, sem compilação no cPanel. |
| `medlab.catalog`, `medlab.catalogue` | Taxonomia, filtros compartilhados, apresentação pública neutra e contagens. |
| `medlab.importer` | Preparação PMC, proveniência e adaptação retomável de relatos reais em rascunhos privados. |
| `medlab.migrations` | Atualização idempotente do esquema de catálogo existente. |
| `medlab/data/cases_pt.json` | Casos didáticos curados para a experiência em português. |
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

O acervo original MedCaseReasoning foi adquirido integralmente em cache privado: 14.489 registros. Não acompanha o ZIP nem é publicado pelo download. O importador consulta metadados e XML oficiais do PMC, verifica licença e checksum, e usa a DeepSeek para classificação, adaptação e uma segunda auditoria por IA. Trechos de evidência são comparados ao texto da fonte. Cada rascunho precisa de revisão humana antes da publicação. O catálogo suporta 55 especialidades do CFM; isso não implica 55 especialidades rotuladas no corpus nem cobertura de 300 casos por área. Consulte [o inventário e os limites da base](BASES_CLINICAS.md).

## Autenticação, persistência e falhas

As senhas são armazenadas como hashes. Flask-Login mantém a sessão autenticada e Flask-WTF protege ações mutáveis com CSRF. Cada acesso a uma consulta verifica o proprietário; conhecer o UUID não concede acesso. A administração tem prefixo configurável e exige permissão de administrador, independentemente de o endereço estar visível na interface.

A DeepSeek é chamada no servidor. Falhas de rede, limite de requisições e respostas inválidas precisam ser expostas como falhas recuperáveis; não devem virar avaliações inventadas ou duplicar o histórico. O modo demonstrativo só é habilitado por configuração explícita e desativa chamadas à DeepSeek. A voz local pode funcionar nesse modo, lendo respostas identificadas como demonstração. Falha no download ou na inferência de voz preserva o fluxo por texto. Testes automatizados com IA simulada não comprovam disponibilidade externa do provedor, e o funcionamento da voz deve ser verificado nos dispositivos usados pela instituição.

SQLite facilita o início e MySQL/PyMySQL permite usar o banco gerenciado pela hospedagem. O esquema inicial é criado por CLI; `upgrade-db` acrescenta o campo e índice de tipo de caso em instalações anteriores, preservando consultas e notas. Faça backup antes da migração. O [guia de cPanel](CPANEL.md) descreve configuração, inicialização, administração, importação, backup e restauração.
