# MED LAB

Plataforma de simulação clínica em português, com pacientes virtuais, avaliação do raciocínio e histórico de aprendizado. Aplicação **Python/Flask para hospedagem cPanel com Passenger**, com interface clara e responsiva para computador, tablet e celular.

Esta instalação destina-se a **uso educacional privado e exclusivamente não comercial**, com `CASE_REUSE_SCOPE=noncommercial`. Cada relato mantém sua licença: fontes CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA podem ser avaliadas; ND permanece fora da tradução/adaptação de texto. Casos CompartilhaIgual conservam a mesma licença e versão da fonte. O inventário permanece privado. O fluxo opcional de fatos clínicos (`CASE_ALLOW_CLINICAL_FACTS=true`) prepara redação própria sem tratar ND como licença para traduzir; artigos retratados e fontes indisponíveis não entram na biblioteca. Veja as [condições por licença e a atribuição](docs/BASES_CLINICAS.md#condições-para-adaptar-e-disponibilizar).

O projeto transforma o núcleo de pesquisa EasyMED deste repositório em um produto web. Preserva o processo **identificação da intenção → paciente simulado → avaliação em seis dimensões**. O código de pesquisa continua em `EasyMED/` e `SPBench/`; a aplicação utiliza sua própria implementação em português, com validação de dados e integração DeepSeek.

## O que está implementado

- Landing page, cadastro, login, logout e painel do estudante.
- Biblioteca com oito casos fictícios em português, filtros e escolha aleatória que prioriza casos diferentes dos cinco mais recentes.
- Seis fichas de relatos reais preparadas em português por Codex, com revisão independente assistida, mantidas como rascunhos privados; revisão clínica humana pendente e nenhum caso real publicado.
- Catálogo de estudo com as 55 especialidades reconhecidas pelo CFM e 15 tipos de queixa; a disponibilidade depende dos casos efetivamente publicados.
- Consulta com diálogo, exame físico, exames complementares, hipótese diagnóstica, diferenciais e plano terapêutico.
- Avaliação com nota, evidências, acertos, oportunidades de melhoria e próximos passos nas dimensões de anamnese, exame físico, exames complementares, raciocínio diagnóstico, plano terapêutico e desempenho global.
- Persistência de contas, diálogos, exames solicitados, respostas e avaliações. Cada consulta guarda uma cópia do caso usado, preservando o histórico quando o material é atualizado.
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

`init-db` importa os oito casos iniciais uma única vez, sem sobrescrever edições existentes. Quando o ZIP inclui `medlab/data/source_registry.jsonl` e `medlab/data/real_case_drafts.json`, `init-db` e `upgrade-db` também restauram o inventário, os bloqueios de reimportação e os rascunhos reais compatíveis com a política configurada. A restauração é idempotente, conserva a revisão local e não publica rascunhos. Metadados portáteis não comprovam verificação do XML na instalação de destino: fontes restauradas começam com verificação pendente, mesmo quando já existe ficha em português. Não são transferidos usuários, conversas ou bancos. Os cinquenta perfis originais do SPBench permanecem como material de pesquisa: não são publicados automaticamente, pois não contêm todos os gabaritos exigidos pela aplicação.

## Publicar no cPanel

Siga [Instalação e operação no cPanel](docs/CPANEL.md), incluindo configuração do interpretador, HTTPS, variáveis, banco e backup. Use um domínio ou subdomínio dedicado, com a aplicação servida na raiz `/` e código fora de `public_html`.

```bash
python scripts/prepare_voice_assets.py
python scripts/package_release.py
# Para incluir o inventário e os rascunhos previamente preparados:
flask --app app export-sources --output instance/exports/source_registry.jsonl
python scripts/package_release.py \
  --source-registry instance/exports/source_registry.jsonl \
  --case-drafts instance/exports/real_case_drafts.json
```

O segundo comando de empacotamento exige a lista JSON privada dos rascunhos já preparada; ele valida casos, fontes correspondentes e exclusões, sem gerar conteúdo. O pacote `dist/medlab-cpanel.zip` inclui o ponto WSGI `passenger_wsgi.py:application`, código, casos, testes, documentação, runtimes de voz do navegador e manifesto SHA256. Não inclui `.env`, senhas, banco local, ambiente virtual, capturas de tela nem o cache de fontes clínicas. A hospedagem deve disponibilizar Python 3.10+, Passenger e acesso HTTPS de saída à DeepSeek.

## Configuração da IA

| Variável | Padrão / finalidade |
| --- | --- |
| `DEEPSEEK_API_KEY` | Chave privada da instalação |
| `AI_BASE_URL` | `https://api.deepseek.com` |
| `AI_MODEL` | `deepseek-flash`, configurável |
| `AI_TIMEOUT` | 40 segundos por chamada, até 45 |
| `AI_IMPORT_TIMEOUT` | 120 segundos, até 180; somente classificação/adaptação no Terminal |
| `MAX_DAILY_AI_REQUESTS` | 120 operações de diálogo/avaliação por aluno por dia; um diálogo pode fazer duas chamadas ao provedor |
| `DEMO_MODE` | `false`; `true` usa somente demonstração local, mesmo com uma chave configurada |

A integração usa [JSON Output da DeepSeek](https://api-docs.deepseek.com/guides/json_mode/) e modelo configurável conforme a [documentação do provedor](https://api-docs.deepseek.com/quick_start/pricing/). A geração envia apenas os dados do caso e a atividade simulada necessária; nome da conta, email e senha não fazem parte dos prompts. As respostas de IA passam por validação estrutural antes de serem salvas. Falhas não são substituídas silenciosamente por avaliações demonstrativas.

Os prompts colocam conteúdo estável antes dos dados variáveis para favorecer o cache automático da DeepSeek. O painel administrativo registra o consumo informado pelo provedor; isso não garante desconto, permanência do cache ou custo zero. Veja [cache e consumo observado](docs/CPANEL.md#9-cache-de-prompts-e-consumo-observado).

## Configuração de voz

DeepSeek continua responsável pela conversa e mantém sua cobrança própria. **Transcrição e síntese de voz rodam no dispositivo do estudante, sem API paga de fala:** Whisper tiny multilíngue reconhece a pergunta e Kokoro gera o áudio da resposta salva. `LOCAL_VOICE_ENABLED=true` habilita a opção após preparar os arquivos com `python scripts/prepare_voice_assets.py`; o ZIP já inclui esses runtimes. Não é necessário Node, PyTorch, ffmpeg ou GPU no servidor Python.

O estudante ativa a voz e autoriza o download inicial: aproximadamente **100 MB para transcrição e mais 140 MB para síntese**, incluindo arquivos auxiliares. Esses arquivos executam no aparelho e podem ser reutilizados pelo cache do navegador; limpar os dados do site ou faltar espaço pode exigir novo download. Eles são independentes do cache de prompts da DeepSeek e do acervo clínico privado no servidor. O modo texto não baixa modelos de voz. Whisper tenta WebGPU quando disponível e pode usar WebAssembly; **Kokoro usa sempre WebAssembly**, no processador do dispositivo. A biblioteca por texto continua utilizável se a voz falhar.

As vozes neurais brasileiras são Dora (`pf_dora`), Alex (`pm_alex`) e Santa (`pm_santa`); as opções seguem o gênero documentado no perfil. Perguntas podem ter até 45 segundos, com conversão local para mono a 16 kHz. O áudio não é enviado ao Flask nem ao serviço de modelos; a transcrição pode ser corrigida antes de ser enviada como pergunta e salva no histórico. A síntese lê respostas já registradas. HTTPS e permissão de microfone são necessários no domínio publicado.

`DEMO_MODE=true` continua desativando a DeepSeek, mas a voz local pode transcrever e ler respostas demonstrativas. A voz é sintética: sotaque, nomes, números e termos clínicos podem apresentar erros. A validação em um Samsung real ainda está pendente; não se garante desempenho uniforme entre celulares. Consulte [créditos, licenças e limites da voz](docs/CREDITOS_VOZ.md).

## Acervo de casos reais

**O produto da preparação é uma ficha de simulação em português, não a tradução integral de um artigo.** Ela reúne o roteiro leigo do paciente, os achados disponíveis para investigação e um gabarito reservado ao avaliador, com perguntas relevantes, diagnóstico de referência, critérios de feedback e nota. O texto original é usado para conferir fatos e preservar a referência; introdução, revisão bibliográfica e outras partes sem função no exercício não são traduzidas para a biblioteca.

O download integral oficial do MedCaseReasoning foi conferido: **14.489 registros, 14.489 PMCIDs distintos e 205.157.823 bytes**. O CSV original em inglês e seu inventário ficam em `instance/imports/MedCaseReasoning/`, fora do Git e do ZIP. Para reproduzir a aquisição, execute:

```bash
python scripts/source_inventory.py
```

O arquivo não traz especialidades. A lista brasileira de 55 é o escopo de estudo do MED LAB; a distribuição dos registros ainda precisa ser produzida e revisada. A meta de **300 por especialidade equivale a 16.500 casos únicos**. Após os filtros, restam **14.476 fontes ativas**, deixando um déficit mínimo de **2.024**, antes de avaliar completude clínica e distribuição por área. Esse total de fontes não equivale a simulações disponíveis. Consulte o [inventário e as fontes clínicas](docs/BASES_CLINICAS.md).

A verificação das fontes foi concluída na instalação de preparação: **10.761 para adaptação licenciada e 3.715 para o fluxo opcional de fatos clínicos ND**. Foram excluídos explicitamente 13 registros — 11 artigos retratados e 2 fontes indisponíveis — com identificadores mínimos que impedem reimportação. Há **seis fichas reais em português preparadas por Codex**, revisadas por outro agente e mantidas privadas em `review_required`; **14.470 fontes aguardam preparação**. A revisão clínica humana permanece pendente. São **zero casos reais publicados e oito fictícios publicados**. A chave DeepSeek autenticou, mas a indisponibilidade do provedor impediu validar a geração por essa API; as seis fichas não foram produzidas pela DeepSeek.

O arquivo administrativo separado `dist/medlab-acervo-retomada.tar.gz` reúne CSV, snapshots, progresso e as seis fichas para retomar o trabalho: cerca de **270 MB comprimidos e 1,05 GB extraídos**, sem chave ou banco. Ele é opcional para preparar mais casos e não é baixado pelo estudante. Restaure somente em instalação nova, seguindo a [conferência de checksum e retomada privada](docs/CPANEL.md#restaurar-o-pacote-privado-de-retomada).

`CASE_ALLOW_CLINICAL_FACTS` é `false` por padrão. A opção `true` está habilitada somente na instalação local autorizada: um extrator produz fatos atômicos, e um gerador separado recebe apenas esses fatos para escrever paciente e rubrica. A licença original permanece na referência, sem atribuir ND automaticamente à ficha nova. Evidências usam hashes/localizadores; a ficha exige auditoria e revisão antes de publicar.

Após fazer backup de uma instalação existente, execute `flask --app app upgrade-db`. A importação ocorre por lotes retomáveis no Terminal:

```bash
flask --app app import-real-cases --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv --limit 50
flask --app app import-report
flask --app app catalog-report
```

O primeiro comando registra fontes ainda não excluídas e verifica até o limite do lote. A administração mostra o inventário operacional e as contagens reais. `--all-records` remove a cota de 300 por área; `--limit` continua limitando o lote. `--source-workers 1..8` controla a consulta ao PMC. `--adapt-workers 1..16` controla casos preparados em paralelo; valores acima de 1 exigem `--adapt --all-records`. Gravações no banco permanecem sequenciais. Cada ficha concluída entra como rascunho privado, com origem e revisão obrigatória. A adaptação usa DeepSeek, exige `DEMO_MODE=false` e consome créditos; o relatório registra tokens observados, sem presumir custo zero quando faltam dados de uso. Falhas gerais do provedor interrompem novas chamadas e preservam resultados válidos já recebidos. Veja [execução, exclusão e retomada no cPanel](docs/CPANEL.md#8-adquirir-fontes-de-casos-reais).

O procedimento está pronto para retomada explícita quando o provedor voltar; nenhum lote permanece em execução ou agendado automaticamente.

## Testes e arquitetura

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes cobrem autenticação, autorização, isolamento entre usuários, CSRF, histórico, aleatoriedade, limites de uso, recuperação após falha, geração e publicação, esquema dos casos e contratos do provedor. Chamadas ao provedor são simuladas nos testes: uma chave válida é necessária para validar a API real no servidor.

Consulte [Arquitetura e decisões](docs/ARQUITETURA.md). A aplicação inclui senhas com hash, cookies protegidos, invalidação de sessões após troca de senha, limites persistentes no banco, proteção CSRF e bloqueio de operações simultâneas na mesma consulta.

A [pesquisa de bases clínicas reais](docs/BASES_CLINICAS.md) compara fontes para ampliar a biblioteca, registra a coleta integral, verificações de licença e os requisitos de adaptação para pacientes virtuais e avaliação. O inventário não traduz nem publica casos.

## Escopo educacional

Os oito casos iniciais são fictícios, acompanhados de referências e rubricas didáticas. Ainda não receberam validação clínica humana. Casos gerados por IA são salvos como rascunhos e exigem revisão administrativa antes de chegar aos estudantes. A avaliação apoia o estudo; não representa certificação de competência ou orientação para atendimento de pacientes reais.

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
