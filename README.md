# BayesCore MedRounds

Plataforma de simulação clínica em português, com pacientes virtuais, avaliação do raciocínio e histórico de aprendizado. Aplicação **Python/Flask para hospedagem cPanel com Passenger**, com interface clara e responsiva para computador, tablet e celular.

Esta instalação destina-se a **uso educacional privado e exclusivamente não comercial**, com `CASE_REUSE_SCOPE=noncommercial`. Cada relato mantém sua licença: fontes CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA podem ser avaliadas; ND permanece fora da tradução/adaptação de texto. Casos CompartilhaIgual conservam a mesma licença e versão da fonte. O inventário permanece privado. O fluxo opcional de fatos clínicos (`CASE_ALLOW_CLINICAL_FACTS=true`) prepara redação própria sem tratar ND como licença para traduzir; artigos retratados e fontes indisponíveis não entram na biblioteca. Veja as [condições por licença e a atribuição](docs/BASES_CLINICAS.md#condições-para-adaptar-e-disponibilizar).

O projeto transforma o núcleo de pesquisa EasyMED deste repositório em um produto web. A conversa identifica os temas abordados e interpreta o paciente na mesma chamada à DeepSeek, seguida da avaliação em seis dimensões pontuadas ao concluir, além de um resumo global sem peso adicional. O paciente acessa seu perfil completo e a memória da consulta, podendo elaborar detalhes plausíveis sem substituir os fatos documentados. Esses detalhes ficam separados da ficha e são identificados no resultado; veja [Paciente simulado e memória da conversa](docs/PACIENTE_SIMULADO.md). O código de pesquisa continua em `EasyMED/` e `SPBench/`; a aplicação utiliza sua própria implementação em português.

## O que está implementado

- Landing page, cadastro, login, logout e painel do estudante.
- Biblioteca com oito casos fictícios iniciais e **60 Casos Básicos** em sete sistemas, além de 14.359 fichas baseadas em relatos: **14.427 casos** na instalação conferida. Filtros e escolha aleatória priorizam casos diferentes dos cinco mais recentes; veja o [guia dos Casos Básicos](docs/CASOS_BASICOS.md).
- Fichas de relatos reais preparadas em português por Codex e por DeepSeek, com evidência da fonte, checkpoints e revisão obrigatória antes da publicação.
- Catálogo de estudo com as 55 especialidades reconhecidas pelo CFM e 22 categorias de apresentação; filtros com contagens reais, busca com ou sem acentos e seleção de casos com resultados complementares ou imagens disponíveis.
- Consulta com diálogo, exame físico, exames complementares, hipótese diagnóstica, diferenciais e plano terapêutico.
- Avaliação com nota e evidências em anamnese, exame físico, exames complementares, raciocínio diagnóstico, plano terapêutico e **Perguntas Básicas de Graduação**. O desempenho global resume o conjunto; os resultados históricos mantêm sua composição original.
- Resultado com memória de cálculo da nota e parecer educacional complementar salvo, com exemplos de perguntas, orientações de raciocínio e conduta e justificativas para os exames.
- Persistência de contas, diálogos, exames solicitados, respostas e avaliações. Cada consulta guarda uma cópia do caso usado, preservando o histórico quando o material é atualizado.
- Figuras originais do PMC em armazenamento privado, associadas a paciente/exame/momento, com revisão de privacidade e atribuição; [limites e operação das mídias](docs/MIDIAS_CLINICAS.md).
- Inventário privado de fontes com registro idempotente, progresso de preparação em português, vínculo ao caso, exclusões duráveis e exportação portátil de metadados.
- Administração separada, sem link na navegação pública: gestão de alunos, edição de casos e geração por IA como rascunho, com revisão antes da publicação. Casos gerados usam o mesmo esquema e processo de avaliação dos casos iniciais.
- DeepSeek configurável no servidor, com chave fora do navegador, limites de uso, tratamento de falhas e modo de demonstração explícito.
- Painel administrativo de consumo observado da IA, com solicitações, tokens de entrada/saída e acertos de cache, distinguindo dados completos e parciais.
- Voz local opcional em português: Whisper transcreve perguntas no navegador e Kokoro lê respostas com uma voz feminina e duas masculinas, conforme o perfil do paciente. Não requer chave de serviço de fala.
- SQLite para instalações pequenas ou MySQL/MariaDB via PyMySQL, sem necessidade de Node, Redis ou serviços de fila.

## Executar localmente

Requer Python 3.10 ou superior.

```bash
git clone https://github.com/ricciardi/MED_LAB.git
cd MED_LAB
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/prepare_voice_assets.py
cp .env.example .env
python -c 'import secrets; print(secrets.token_hex(32))'
```

Edite `.env`: use o segredo gerado em `SECRET_KEY`, configure `DEEPSEEK_API_KEY` e mantenha `DEMO_MODE=false`. Para apresentação local sem chave, defina `DEMO_MODE=true`: as respostas usam regras fixas e as avaliações são identificadas como **demonstração sem IA**. Esse modo não gera casos novos. Em HTTP local, use `SESSION_COOKIE_SECURE=false`; em produção HTTPS, mantenha `true`.

```bash
flask --app app init-db
flask --app app create-admin
flask --app app run --host 127.0.0.1 --port 5055
```

Acesse `http://127.0.0.1:5055`. A administração padrão fica em `/gestao-medlab/login`; personalize `ADMIN_PATH` no ambiente. O endereço discreto complementa a autenticação e a verificação de permissão. Não existe senha administrativa padrão.

`init-db` importa os oito casos iniciais e os 60 Casos Básicos, incluindo somente IDs ainda ausentes e sem sobrescrever edições ou suspensões existentes. `upgrade-db` também inclui os novos Casos Básicos; `flask --app app import-cases` importa especificamente os sete arquivos dessa categoria. Quando o ZIP inclui `medlab/data/source_registry.jsonl` e o diretório fragmentado `medlab/data/real_case_drafts/` (ou uma lista JSON menor), `init-db` e `upgrade-db` também restauram o inventário, os bloqueios de reimportação e as fichas reais compatíveis com a política configurada. Fichas novas com autorização provisória explícita válida (`useAuthorization`) já exportada ficam disponíveis na biblioteca, mantendo `review_required`; rascunhos sem essa autorização continuam privados. A restauração é idempotente e preserva edições, revisões e suspensões deliberadas existentes. Pode disponibilizar um rascunho anterior sem autorização somente quando seu conteúdo coincide integralmente com o exportado, exceto pela nova autorização. Metadados portáteis não comprovam verificação do XML na instalação de destino: fontes restauradas começam com verificação pendente, mesmo quando a ficha está disponível provisoriamente. Não são transferidos usuários, conversas ou bancos. Os cinquenta perfis originais do SPBench permanecem como material de pesquisa: não são publicados automaticamente, pois não contêm todos os gabaritos exigidos pela aplicação.

## Escolher casos e encontrar exames

A categoria **Casos Básicos** oferece os sistemas urinário, digestivo, hematopoiético, nervoso, respiratório, cardiovascular e cabeça. São simulações autorais para praticar conceitos e entrevista; o diagnóstico continua reservado até a conclusão. Texto, voz, solicitação de exames e finalização usam o mesmo fluxo das demais consultas. Os filtros de sistema, apresentação, dificuldade e exames também valem para o sorteio. As opções de tipo e dificuldade mostram quantos casos correspondem aos demais filtros; a busca aceita, por exemplo, `clinica` ou `Clínica`. Ela consulta somente os metadados públicos do catálogo, sem pesquisar o diagnóstico reservado. Um caso pode ter mais de uma apresentação.

O índice separado `CaseDiscovery` organiza essas informações sem reescrever as fichas ou os snapshots das consultas. Nos casos preparados localmente, **dificuldade estimada** significa extensão do roteiro — campos de entrevista disponíveis e grupos de exames complementares —, sem medir gravidade, raridade da doença ou competência clínica. Dados insuficientes ficam como **Não classificada**. Apresentações autorais específicas e dificuldades autorais são preservadas; apresentações genéricas como Primeira consulta e Outros sintomas podem ser refinadas também nos casos não locais.

No snapshot anterior à inclusão dos 60 Casos Básicos, **13.985 dos 14.367 casos publicados tinham resultados complementares úteis** e **sete casos tinham 15 imagens disponíveis**. A contagem atual aparece nos filtros e em `flask --app app catalog-report`; os novos exercícios não incluem imagens clínicas próprias. Arquivos de imagem baixados e propostas pendentes não contam como imagens prontas. Na consulta, abra **Exames** e clique em **Solicitar**: o resultado aparece abaixo do exame, acompanhado da imagem quando houver. Uma pergunta sobre exame no chat não executa essa solicitação.

Em instalações existentes, faça backup e execute `flask --app app upgrade-db`. Para reconstruir somente o índice após atualizar as regras ou a disponibilidade de mídia, use `flask --app app reindex-catalogue`. A operação não chama a DeepSeek nem recalcula notas; veja o [procedimento no cPanel](docs/CPANEL.md#catálogo-filtros-e-disponibilidade).

## Publicar no cPanel

Siga [Instalação e operação no cPanel](docs/CPANEL.md), incluindo configuração do interpretador, HTTPS, variáveis, banco e backup. Use um domínio ou subdomínio dedicado, com a aplicação servida na raiz `/` e código fora de `public_html`.

```bash
python scripts/prepare_voice_assets.py
python scripts/package_release.py
# Para incluir o inventário e os rascunhos previamente preparados:
flask --app app export-sources --output instance/exports/source_registry.jsonl
flask --app app export-real-drafts --output instance/exports/real-case-shards
python scripts/package_release.py \
  --source-registry instance/exports/source_registry.jsonl \
  --case-drafts instance/exports/real-case-shards
```

O comando de empacotamento aceita o diretório com manifesto e arquivos de até 20 MiB (ou uma lista JSON privada pequena); ele valida casos, fontes correspondentes e exclusões, sem gerar conteúdo. O pacote `dist/medlab-cpanel.zip` inclui o ponto WSGI `passenger_wsgi.py:application`, código, casos, testes, documentação, runtimes de voz do navegador e manifesto SHA256. Não inclui `.env`, senhas, banco local, ambiente virtual, capturas de tela nem o cache de fontes clínicas. A hospedagem deve disponibilizar Python 3.10+, Passenger e acesso HTTPS de saída à DeepSeek.

## Configuração da IA

| Variável | Padrão / finalidade |
| --- | --- |
| `DEEPSEEK_API_KEY` | Chave privada da instalação |
| `AI_BASE_URL` | `https://api.deepseek.com` |
| `AI_MODEL` | `deepseek-flash`, configurável |
| `AI_TIMEOUT` | 40 segundos por chamada, até 45 |
| `AI_IMPORT_TIMEOUT` | 120 segundos, até 180; somente classificação/adaptação no Terminal |
| `MAX_DAILY_AI_REQUESTS` | 120 operações de diálogo, avaliação ou parecer por aluno por dia; uma operação pode fazer mais de uma chamada ao provedor |
| `DEMO_MODE` | `false`; `true` usa somente demonstração local, mesmo com uma chave configurada |

A integração usa [JSON Output da DeepSeek](https://api-docs.deepseek.com/guides/json_mode/) e modelo configurável conforme a [documentação do provedor](https://api-docs.deepseek.com/quick_start/pricing/). A geração envia apenas os dados do caso e a atividade simulada necessária; nome da conta, email e senha não fazem parte dos prompts. As respostas de IA passam por validação estrutural antes de serem salvas. Falhas não são substituídas silenciosamente por avaliações demonstrativas.

Os prompts colocam conteúdo estável antes dos dados variáveis para favorecer o cache automático da DeepSeek. O painel administrativo registra o consumo informado pelo provedor; isso não garante desconto, permanência do cache ou custo zero. Veja [cache e consumo observado](docs/CPANEL.md#9-cache-de-prompts-e-consumo-observado).

## Entender a nota e o parecer

A escala é de **0 a 100**. Nas novas avaliações, a nota da consulta inteira é a média simples das seis dimensões avaliáveis: anamnese, exame físico, exames complementares, raciocínio diagnóstico, plano terapêutico e **Perguntas Básicas de Graduação**. Dimensões não avaliáveis, com nota `null`, ficam fora da soma e do divisor; **zero continua na média**. O desempenho global resume o resultado e não acrescenta peso. A página mostra parcelas, soma, divisor e arredondamento. As avaliações históricas de cinco dimensões e suas notas são preservadas, sem recálculo retroativo.

A nova dimensão usa **47 objetivos em sete grupos** de um roteiro amplo de graduação. Dados gerais de condições de vida não são dispensados só por não mudarem o diagnóstico; itens condicionais, informação já conhecida, urgência, recusa e capacidade do informante são considerados. Os grupos aplicáveis têm pesos iguais, sem exigir repetição de dados já visíveis ou conhecidos. O julgamento semântico usa uma chamada adicional à IA. Se ela falhar depois de salvar a avaliação-base, repetir a finalização com os mesmos dados retoma somente a etapa pendente. Alterar a consulta invalida esse reaproveitamento. Essa validação não equivale a calibração clínica; veja [critérios, cálculo e limites](docs/CASOS_BASICOS.md#perguntas-básicas-de-graduação).

Ao abrir um resultado sem parecer salvo, **Para sua próxima consulta** solicita automaticamente uma orientação complementar à IA, em uma requisição separada da avaliação. Ela apresenta exemplos de perguntas, raciocínio diagnóstico, conduta e indicação dos exames, sem mudar a nota. O parecer fica em `evaluation.learningReview`; reabrir um parecer salvo não chama a IA. Se houver falha, a página oferece **Tentar novamente**, sem repetição automática. O modo demonstração não gera esse parecer. São inferências educacionais da IA, sujeitas a erro e sem certificação clínica; os [limites de tempo, cotas e consumo](docs/CPANEL.md#resultado-e-parecer-complementar) estão no guia de operação.

## Configuração de voz

DeepSeek continua responsável pela conversa e mantém sua cobrança própria. **Transcrição e síntese de voz rodam no dispositivo do estudante, sem API paga de fala:** Whisper base multilíngue reconhece a pergunta e Kokoro gera o áudio da resposta salva. `LOCAL_VOICE_ENABLED=true` habilita a opção após preparar os arquivos com `python scripts/prepare_voice_assets.py`; o ZIP já inclui esses runtimes. Não é necessário Node, PyTorch, ffmpeg ou GPU no servidor Python.

O estudante ativa a voz e autoriza o download inicial: aproximadamente **175 MB para transcrição e mais 140 MB para síntese**, incluindo arquivos auxiliares. Esses arquivos executam no aparelho e podem ser reutilizados pelo cache do navegador; limpar os dados do site ou faltar espaço pode exigir novo download. Eles são independentes do cache de prompts da DeepSeek e do acervo clínico privado no servidor. O modo texto não baixa modelos de voz. Whisper tenta WebGPU quando disponível e pode usar WebAssembly; **Kokoro usa sempre WebAssembly**, no processador do dispositivo. A biblioteca por texto continua utilizável se a voz falhar. Com `LOCAL_VOICE_ISOLATION=true` e suporte do navegador, Kokoro utiliza até quatro threads; sem os requisitos de paralelismo, usa uma. Veja os cabeçalhos exigidos no [guia cPanel](docs/CPANEL.md#voz-em-português).

As vozes neurais brasileiras são Dora (`pf_dora`), Alex (`pm_alex`) e Santa (`pm_santa`); as opções seguem o gênero documentado de quem responde; quando o caso explicita `respondent_gender`, a voz do informante é escolhida separadamente, sem mudar o sexo/gênero do paciente nem inferi-lo pelo nome. Perguntas podem ter até 45 segundos, com conversão local para mono a 16 kHz. O áudio não é enviado ao Flask nem ao serviço de modelos. No modo manual, a transcrição pode ser corrigida antes do envio; na [conversa contínua](docs/CONVERSA_CONTINUA.md), cerca de 1,6 segundo de silêncio ao terminar de falar inicia a transcrição e o envio automático. Se a gravação estiver muito baixa ou saturada, o modo contínuo pausa e abre o texto para revisão ou nova gravação; isso avalia o sinal de áudio, não a correção das palavras. O microfone fica desligado enquanto o paciente responde e volta a escutar após a reprodução. A síntese lê respostas já registradas; o texto aparece junto com o primeiro trecho de áudio nesse modo. HTTPS e permissão de microfone são necessários no domínio publicado.

`DEMO_MODE=true` continua desativando a DeepSeek, mas a voz local pode transcrever e ler respostas demonstrativas. A voz é sintética: sotaque, nomes, números e termos clínicos podem apresentar erros. A validação em um Samsung real ainda está pendente; não se garante desempenho uniforme entre celulares. Consulte [créditos, licenças e limites da voz](docs/CREDITOS_VOZ.md).

Uma comparação local no Mac reduziu a geração do primeiro trecho de Dora de **5,41 para 3,42 s** e de Alex de **5,30 para 3,01 s**, com trechos iniciais menores e preparação durante a ativação da voz. Isso não é tempo total da resposta nem promessa de fluidez em tablet. O agendamento final, incluindo buffer, foi conferido por replay dos tempos medidos, sem uma nova gravação da reprodução. Veja [método e limites da medição](docs/TESTES_DEEPSEEK.md#catálogo-e-latência-local-de-voz-sem-novas-chamadas-deepseek).

## Acervo de casos reais

**O produto da preparação é uma ficha de simulação em português, não a tradução integral de um artigo.** Ela reúne o roteiro leigo do paciente, os achados disponíveis para investigação e um gabarito reservado ao avaliador, com perguntas relevantes, diagnóstico de referência, critérios de feedback e nota. O texto original é usado para conferir fatos e preservar a referência; introdução, revisão bibliográfica e outras partes sem função no exercício não são traduzidas para a biblioteca.

O download integral oficial do MedCaseReasoning foi conferido: **14.489 registros, 14.489 PMCIDs distintos e 205.157.823 bytes**. O CSV original em inglês e seu inventário ficam em `instance/imports/MedCaseReasoning/`, fora do Git e do ZIP. Para reproduzir a aquisição, execute:

```bash
python scripts/source_inventory.py
```

O arquivo não traz especialidades. A lista brasileira de 55 é o escopo de estudo do BayesCore MedRounds; a classificação dos registros precisa de revisão. A meta de **300 por especialidade equivale a 16.500 casos únicos**. Após os filtros e a exclusão dos relatos veterinários, restam **14.359 fontes ativas**, deixando um déficit mínimo de **2.141**, antes de avaliar completude clínica e distribuição por área. Esse total de fontes não equivale a simulações disponíveis. Consulte o [inventário e as fontes clínicas](docs/BASES_CLINICAS.md).

A verificação das fontes ativas foi concluída na instalação de preparação: **10.667 para adaptação licenciada e 3.692 para o fluxo opcional de fatos clínicos ND**. Foram excluídos 130 registros — 117 relatos veterinários, 11 artigos retratados e 2 fontes indisponíveis — com identificadores mínimos que impedem reimportação. O escopo atual preserva 73 fichas anteriores e prepara outras 14.286 com tradução local e correções assistidas, sem chamadas à DeepSeek. Os pilotos anteriores com o provedor permanecem documentados separadamente. Todas as fichas continuam sujeitas a revisão clínica humana.

A [preparação local](docs/PREPARACAO_LOCAL.md) grava as fichas progressivamente no banco definitivo. A disponibilização provisória autorizada pelo proprietário conserva `review_required` e a referência dessa autorização; não registra revisão clínica fictícia. Para o progresso atual, consulte `flask --app app source-report`, `flask --app app import-report` e `flask --app app catalog-report`. Fonte verificada, ficha em português, revisão e publicação são contagens distintas.

O arquivo administrativo separado `dist/medlab-acervo-retomada.tar.gz` reúne CSV, snapshots, checkpoints, etapas intermediárias de IA e exportações fragmentadas das fichas, sem chave ou banco. É opcional para preparar mais casos e não é baixado pelo estudante. O manifesto do snapshot informa seu conteúdo e checksum; pacotes anteriores não representam o progresso atual. Restaure somente em instalação nova, seguindo a [conferência de integridade e retomada privada](docs/CPANEL.md#restaurar-o-pacote-privado-de-retomada). Imagens possuem [pacote privado próprio](docs/MIDIAS_CLINICAS.md).

`CASE_ALLOW_CLINICAL_FACTS` é `false` por padrão. A opção `true` está habilitada somente na instalação local autorizada: um extrator produz fatos atômicos, e um gerador separado recebe apenas esses fatos para escrever paciente e rubrica. A licença original permanece na referência, sem atribuir ND automaticamente à ficha nova. Evidências usam hashes/localizadores. O fluxo de adaptação com API exige auditoria e revisão antes de publicar; a disponibilização provisória da preparação local segue a autorização rastreada descrita acima, mantendo a revisão pendente.

Ao receber o pacote com as fichas prontas, restaure os arquivos exportados conforme o guia cPanel; não é necessário repetir a tradução nem a adaptação com IA. Para uma nova aquisição de fontes, após fazer backup de uma instalação existente, execute `flask --app app upgrade-db`. A importação ocorre por lotes retomáveis no Terminal:

```bash
flask --app app import-real-cases --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv --limit 50
flask --app app import-report
flask --app app catalog-report
```

O primeiro comando registra fontes ainda não excluídas e verifica até o limite do lote. A administração mostra as contagens reais. `--all-records` remove a cota de 300 por área; `--limit` continua limitando o lote. `--source-workers 1..8` controla a consulta ao PMC. A preparação paralela exige `--adapt --all-records` e respeita `AI_IMPORT_WORKER_LIMIT` (16 por padrão; no cPanel simples, comece com 1 a 4). Gravações no banco permanecem sequenciais. Cada ficha concluída entra privada em `review_required`. A adaptação usa DeepSeek, exige `DEMO_MODE=false` e consome créditos; os relatórios mostram o uso efetivamente informado. Falhas gerais do provedor interrompem novas chamadas e preservam resultados válidos já recebidos. Veja [execução e retomada no cPanel](docs/CPANEL.md#8-adquirir-fontes-de-casos-reais).

Os checkpoints em `processed/ai-stages/` preservam resultados intermediários para evitar regeneração desnecessária. A retomada revalida hashes, versão do prompt e fonte; resultado rejeitado não equivale a ficha concluída. A exportação `export-real-drafts` divide os rascunhos em arquivos limitados, com manifesto e checksums, para restauração idempotente em hospedagens pequenas. Os comandos são explícitos; instalar o aplicativo não inicia lotes automaticamente.

## Testes e arquitetura

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes automatizados cobrem autenticação, autorização, isolamento, CSRF, histórico, limites, recuperação de falhas, esquema, proveniência, checkpoints, arquivos fragmentados e mídias privadas. Esses testes simulam o provedor. Pilotos separados exercitaram chamadas reais de preparação e visão DeepSeek; isso não substitui revisão clínica nem garante disponibilidade futura ou funcionamento em outra instalação.

O [relatório de testes reais DeepSeek](docs/TESTES_DEEPSEEK.md) distingue chamadas pagas, revalidações locais, falhas e limitações semânticas do paciente e do avaliador. As notas são estimativas educacionais da IA, sem certificação de competência.

Consulte [Arquitetura e decisões](docs/ARQUITETURA.md). A aplicação inclui senhas com hash, cookies protegidos, invalidação de sessões após troca de senha, limites persistentes no banco, proteção CSRF e bloqueio de operações simultâneas na mesma consulta.

A [pesquisa de bases clínicas reais](docs/BASES_CLINICAS.md) compara fontes para ampliar a biblioteca, registra a coleta integral, verificações de licença e os requisitos de adaptação para pacientes virtuais e avaliação. O inventário não traduz nem publica casos.

## Escopo educacional

Os oito casos iniciais e os 60 Casos Básicos são fictícios, acompanhados de referências e rubricas didáticas. Ainda não receberam validação clínica humana. Casos gerados por IA são salvos como rascunhos e exigem revisão administrativa antes de chegar aos estudantes. A avaliação apoia o estudo; não representa certificação de competência ou orientação para atendimento de pacientes reais.

Esta versão não inclui recuperação de senha por email: o operador usa `flask --app app reset-password EMAIL` com entrada protegida no Terminal. A publicação, o banco MySQL, a DeepSeek e o carregamento dos modelos de voz no domínio e nos dispositivos de destino devem ser verificados na instalação.

## Origem e atribuição

Baseado no [EasyMED](https://github.com/FreedomIntelligence/EasyMED), de FreedomIntelligence. O [README original](docs/README-original.md) preserva a documentação, atribuição e declaração de licença MIT do projeto de origem. Os módulos e perfis originais permanecem no repositório.

```bibtex
@article{zhang2025human,
  title={Human or LLM as Standardized Patients? A Comparative Study for Medical Education},
  author={Zhang, Bingquan and Liu, Xiaoxiao and Wang, Yuchi and Zhou, Lei and Xie, Qianqian and Wang, Benyou},
  journal={arXiv preprint arXiv:2511.14783},
  year={2025}
}
```
