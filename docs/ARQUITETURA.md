# MED_LAB — arquitetura e origem pedagógica

MED_LAB é uma aplicação web em português para treinamento com pacientes simulados. O estudante abre uma consulta, conversa com o paciente, solicita exames e registra hipótese diagnóstica, diferenciais e plano de tratamento. A avaliação é educacional e depende do conteúdo do caso e das ações registradas.

## Estrutura da aplicação

| Camada | Responsabilidade |
| --- | --- |
| `app.py`, `passenger_wsgi.py` | Entradas de desenvolvimento/CLI e WSGI para Passenger. |
| `medlab.create_app(config=None)` | Fábrica Flask: configura ambiente, extensões, rotas e comandos. |
| `medlab.extensions` | SQLAlchemy, autenticação e proteção CSRF. |
| `medlab.models` | Usuários, casos, consultas, turnos e exames realizados. |
| `medlab.web` | Fluxo do estudante, isolamento das consultas por usuário e área administrativa. |
| `medlab.ai` | Integração de servidor com DeepSeek e modo de demonstração explícito. |
| `medlab/data/cases_pt.json` | Casos didáticos curados para a experiência em português. |
| `EasyMED/` e `SPBench/` | Código e dados originais preservados como referência. |
| `tests/` | Verificação automatizada com banco temporário e IA simulada. |

O navegador envia ações autenticadas ao Flask. O servidor identifica a consulta e seu proprietário antes de acessar os dados. Perguntas e respostas persistem no banco; o serviço de IA recebe o conteúdo necessário do caso e do histórico. A chave DeepSeek fica no ambiente do servidor. A avaliação concluída é salva para consulta posterior.

## Modelo de dados

- **User:** nome, email, hash de senha, estado da conta e permissão administrativa. O cadastro público cria estudantes.
- **ClinicalCase:** identificador, título, especialidade, dificuldade, resumo, conteúdo estruturado e estado de publicação.
- **Consultation:** proprietário, caso de origem, cópia do caso no início da sessão, estado, datas, diagnóstico, diferenciais, tratamento, avaliação e nota.
- **Turn:** pergunta, resposta, intenção clínica e identificador da requisição dentro de uma consulta.
- **PerformedExam:** exame solicitado, tipo e resultado vinculado à consulta.

A cópia do caso na consulta evita que edições futuras no catálogo alterem retroativamente o material usado em uma sessão. A repetição de uma requisição de mensagem usa um identificador próprio para evitar turnos duplicados. Controle de ocupação da consulta coordena operações de IA concorrentes.

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

O SPBench original também define **oito dimensões de qualidade do paciente virtual**: compreensão da pergunta, precisão da informação, divulgação controlada, completude da resposta, coerência narrativa, linguagem simples, consistência de memória e cooperação. Essas dimensões avaliam o comportamento do simulador. As seis dimensões acima avaliam o estudante e têm outra finalidade.

## Preservação e limites dos casos originais

Os **50 arquivos JSON**, `SPBench/SPBench_case/01.json` a `50.json`, permanecem disponíveis como fonte de pesquisa. Todos contêm `caseId`, `caseTitle`, `caseDescription` e `patientProfile`; cada perfil tem 26 campos. Os textos originais estão majoritariamente em chinês e abrangem cenários clínicos, cirúrgicos, pediátricos, ginecológicos, psiquiátricos, odontológicos e de medicina tradicional chinesa.

Os arquivos fornecem perfil e história do paciente, mas **não incluem os gabaritos estruturados completos** esperados pelo avaliador original, como `mustConsultionItems`, `physicalExams`, `auxiliaryLabs`, `diagnoses` e `treatments`. Por isso, disponibilizar esses arquivos não equivale a afirmar que todos estão traduzidos, validados ou prontos para avaliação nas seis dimensões.

A experiência web utiliza oito casos didáticos em português: tosse e febre, ardor ao urinar, cefaleia recorrente, dor lombar após esforço, desânimo e perda de interesse, sede e cansaço, diarreia infantil e chiado noturno. O catálogo inclui perfis, achados de exames, diagnóstico de referência, objetivos, fontes e rubricas nas seis dimensões. Esses casos iniciais têm estado `educational_seed`; essa identificação não significa validação clínica independente.

Casos gerados por IA precisam de revisão editorial e clínica antes de publicação. Revisão, correções e publicação pertencem ao administrador; a geração automática não estabelece validade clínica. O código original permanece como referência e não é executado diretamente como servidor web.

## Autenticação, persistência e falhas

As senhas são armazenadas como hashes. Flask-Login mantém a sessão autenticada e Flask-WTF protege ações mutáveis com CSRF. Cada acesso a uma consulta verifica o proprietário; conhecer o UUID não concede acesso. A administração tem prefixo configurável e exige permissão de administrador, independentemente de o endereço estar visível na interface.

A IA é chamada no servidor. Falhas de rede, limite de requisições e respostas inválidas precisam ser expostas como falhas recuperáveis; não devem virar avaliações inventadas ou duplicar o histórico. O modo demonstrativo só é habilitado por configuração explícita. O teste automatizado usa serviços simulados e não comprova disponibilidade externa da DeepSeek.

SQLite facilita o início e MySQL/PyMySQL permite usar o banco gerenciado pela hospedagem. O esquema inicial é criado por CLI; mudanças posteriores de esquema exigem estratégia de migração. O [guia de cPanel](CPANEL.md) descreve configuração, inicialização, administração, backup e restauração.
