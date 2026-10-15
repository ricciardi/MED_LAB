# MED_LAB — instalação e operação no cPanel

O pacote contém a aplicação Flask e os arquivos para publicação por Passenger. A instalação no servidor exige acesso ao cPanel/Terminal, domínio com HTTPS e uma chave DeepSeek configurada no próprio servidor. A voz é processada no navegador, sem chave de serviço de fala. Preparar e testar o pacote localmente não equivale a publicar a aplicação.

## 1. Verificar a hospedagem

Confirme Python **3.10 ou superior**, ambiente virtual, acesso de saída HTTPS à API DeepSeek e uma das interfaces abaixo. A disponibilidade depende do provedor. O [Application Manager do cPanel](https://docs.cpanel.net/cpanel/software/application-manager/) utiliza Passenger e pode estar desabilitado pelo provedor. A voz não requer Node, PyTorch, ffmpeg, Redis ou GPU no servidor: os modelos executam no computador ou celular do estudante.

| Interface disponível | Como registrar MED_LAB |
| --- | --- |
| **Setup Python App**, normalmente em CloudLinux | Selecione Python 3.10+, raiz `medlab`, URL do domínio, arquivo inicial `passenger_wsgi.py` e objeto de entrada `application`. |
| **Application Manager** | Registre a pasta `medlab`, relativa à pasta pessoal da conta, no domínio desejado e selecione **Production**. O provedor deve configurar o interpretador Python/ambiente virtual usado por Passenger. |

Os campos de Setup Python App estão descritos no [guia oficial do Python Selector](https://support.cpanel.net/hc/en-us/articles/360057849753-How-to-register-an-application-with-Python-Selector). O [guia oficial de Python WSGI](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-python-wsgi-application/) detalha o ambiente virtual e o arquivo Passenger. Não execute alterações de configuração global do servidor sem a participação do provedor.

Prefira um subdomínio dedicado, como `medlab.seudominio.com.br`, servido na raiz `/`. A pasta com código, `.env`, banco e logs deve ficar fora de `public_html`; o domínio é associado à aplicação pelo painel.

Instale um certificado válido e ative **Force HTTPS Redirect** em **Domains**, conforme o [guia oficial de domínios](https://docs.cpanel.net/cpanel/domains/domains/). Se o controle não estiver disponível, peça ao provedor o redirecionamento equivalente no servidor. Teste uma URL `http://` antes de liberar o acesso: ela deve redirecionar para `https://` antes de exibir formulários. Cookies seguros e HSTS não substituem esse redirecionamento no primeiro acesso.

## 2. Enviar e instalar

Gere o pacote no computador de desenvolvimento:

```bash
python3 scripts/prepare_voice_assets.py
python3 scripts/package_release.py
```

O arquivo `dist/medlab-cpanel.zip` contém somente os arquivos de entrega permitidos, incluindo os runtimes estáticos de voz previamente preparados. O script exclui banco de dados, `.env`, ambientes virtuais, caches, histórico Git e capturas de tela. O acervo clínico bruto em `instance/imports/` também não faz parte do ZIP. O `MANIFEST.sha256` interno permite conferir os arquivos extraídos. Para incluir inventário e rascunhos reais previamente preparados:

```bash
flask --app app export-sources --output instance/exports/source_registry.jsonl
python scripts/package_release.py \
  --source-registry instance/exports/source_registry.jsonl \
  --case-drafts instance/exports/real_case_drafts.json
```

`--case-drafts` recebe uma lista JSON privada de fichas `review_required`, como a exportação dos seis rascunhos desta entrega; não prepara novos casos. O empacotador valida o esquema, a fonte correspondente e os identificadores excluídos. Inclui `medlab/data/source_registry.jsonl` e `medlab/data/real_case_drafts.json` para restauração por `init-db`/`upgrade-db`. Fontes e rascunhos permanecem privados, com permissão `0600`; usuários, conversas e banco não são transferidos. Os arquivos SPBench de origem continuam incluídos para rastreabilidade.

1. Envie o ZIP pelo Gerenciador de Arquivos ou SFTP e extraia em `/home/SEU_USUARIO/medlab`.
2. Crie/registre a aplicação conforme a interface disponível.
3. Copie do painel o comando de ativação do ambiente virtual e execute no Terminal. Esse caminho é específico da conta e da versão Python.
4. Na pasta que contém `app.py`, instale as dependências:

```bash
cd /home/SEU_USUARIO/medlab
python --version
python -m pip install -r requirements.txt
```

Se `sha256sum` estiver disponível no servidor, confira a integridade da extração com `sha256sum -c MANIFEST.sha256` antes de editar os arquivos da instalação.

O interpretador usado por Passenger precisa ser o mesmo ambiente no qual as dependências foram instaladas. Não use `flask run` como servidor de produção. O ponto WSGI fornecido é `passenger_wsgi.py:application`.

O diálogo pode fazer duas chamadas sequenciais à IA, com até 45 segundos cada. Confira com o provedor se o timeout de requisições permite ao menos 120 segundos; reduza `AI_TIMEOUT` se a hospedagem impuser um limite menor. As chamadas são síncronas para manter a instalação simples, e cada chamada ocupa um processo Passenger enquanto aguarda a resposta.

## 3. Configurar ambiente

Configure as variáveis no painel ou copie `.env.example` para `.env` na raiz privada da aplicação. Preencha os valores no servidor. Não publique `.env` nem envie a chave pela interface do estudante.

```bash
cp .env.example .env
chmod 600 .env
python -c 'import secrets; print(secrets.token_hex(32))'
```

Use o valor gerado para `SECRET_KEY`. A sessão depende dessa chave; trocá-la encerra as sessões existentes. As variáveis disponíveis e seus valores padrão estão em `.env.example`.

Para inserir a chave DeepSeek, use o Terminal interativo com o ambiente virtual ativo:

```bash
python scripts/configure_deepseek.py
python scripts/configure_deepseek.py --check
```

O primeiro comando solicita a chave com **entrada oculta**, preserva as demais variáveis do `.env`, grava o arquivo com permissão `0600` e define `DEMO_MODE=false`. Não passe a chave como argumento, por pipe ou pelo chat. O segundo informa apenas se existe uma chave no `.env`; não exibe seu valor, não faz chamada à API e não confirma saldo ou validade. Reinicie Passenger depois da configuração. Variáveis já definidas no painel podem prevalecer sobre o `.env`; a verificação desse comando se limita ao arquivo privado desta instalação.

| Configuração | Uso em produção |
| --- | --- |
| `SECRET_KEY` | Segredo aleatório próprio da instalação; não reutilizar exemplos. |
| `DEEPSEEK_API_KEY` | Chave do projeto DeepSeek, armazenada somente no servidor. |
| `LOCAL_VOICE_ENABLED` | `true` para oferecer a voz local; exige os runtimes estáticos preparados. |
| `DEMO_MODE` | `false`. O modo demonstrativo local é explicitamente separado. |
| `CASE_REUSE_SCOPE` | `noncommercial` nesta instalação educacional privada; define quais licenças de fontes podem seguir para adaptação. |
| `CASE_ALLOW_CLINICAL_FACTS` | `false` por padrão; `true` somente quando se deseja o fluxo separado de fatos clínicos, com fonte verificada e redação independente. |
| `ADMIN_PATH` | Prefixo administrativo, por exemplo `/gestao-medlab`. Não incluir barra final. |
| `DATABASE_URL` | URL SQLAlchemy; SQLite privado ou MySQL com PyMySQL. |
| `SESSION_COOKIE_SECURE` | `true` quando o domínio estiver em HTTPS. |

O painel pode aplicar variáveis apenas ao processo web. Os comandos `flask` no Terminal precisam receber a mesma configuração; o `.env` privado compartilhado é uma alternativa quando suportada pela implantação. Nunca coloque senhas/chaves em scripts versionados ou no histórico de comandos.

### Voz em português

Mantenha `LOCAL_VOICE_ENABLED=true`. O ZIP inclui os arquivos JavaScript e WebAssembly usados por Whisper e Kokoro. Se instalar diretamente a partir do repositório, ou precisar conferir/restaurar esses arquivos, execute na raiz privada:

```bash
python scripts/prepare_voice_assets.py
```

O script usa Python padrão, baixa versões fixadas em `scripts/voice_assets.json`, confere tamanho e SHA256 e prepara `medlab/static/voice-local/`. Não compila modelos nem instala um servidor de inferência. Para essa preparação, o computador que executa o script precisa alcançar os endereços oficiais indicados no manifesto; isso pode ser feito antes do envio ao cPanel.

O estudante ativa a voz antes do download dos modelos: cerca de **100 MB para Whisper tiny multilíngue** e **140 MB adicionais para Kokoro**, contando arquivos auxiliares. Os pesos vêm do Hugging Face, por HTTPS, diretamente ao navegador; as requisições de download não incluem o áudio. O cache local pode reutilizar os arquivos, mas pode ser apagado pelo navegador. Em redes institucionais, verifique o acesso ao Hugging Face e aos endereços de entrega usados pelo serviço. O download inicial e o consumo de memória não desaparecem por a inferência ser gratuita.

Whisper transcreve perguntas de até **45 segundos**, convertidas localmente para mono a 16 kHz. O estudante pode corrigir a transcrição antes de enviá-la como texto. O áudio não é enviado ao Flask e não é armazenado no banco; a pergunta enviada passa a integrar o histórico textual. Kokoro lê respostas já salvas, usando Dora (`pf_dora`, feminina), Alex (`pm_alex`) ou Santa (`pm_santa`, masculinas), com opções limitadas ao gênero documentado no caso. O microfone exige HTTPS e permissão do navegador.

Whisper tenta WebGPU quando disponível, com alternativa WebAssembly em uma thread. **A síntese Kokoro usa sempre WebAssembly em uma thread**, no processador do computador ou celular. Não é necessário habilitar COOP/COEP para a aplicação; não remova a política CSP fornecida. Confira no domínio publicado se os arquivos `.js`, `.mjs`, `.wasm` e dados auxiliares em `/static/voice-local/` retornam HTTP 200 com tipo de conteúdo correto; um erro de MIME, CSP ou permissão pode impedir o carregamento. Nesses casos, a consulta por texto continua disponível.

A voz não usa chave nem créditos de um provedor de fala; a conversa e a adaptação de casos continuam usando a DeepSeek paga. `DEMO_MODE=true` desativa DeepSeek, mas permite voz local sobre respostas demonstrativas. Voz sintética pode errar pronúncia, números e termos médicos; a transcrição precisa ser conferida pelo estudante. O teste em Samsung real ainda está pendente, e o desempenho deve ser verificado nos aparelhos da instituição. Consulte [créditos, fontes e licenças](CREDITOS_VOZ.md).

### Banco de dados

SQLite é a opção inicial para uso pequeno. A pasta do arquivo deve ser gravável pelo usuário da aplicação e não deve ser pública. Para volume maior de escritas simultâneas, use o MySQL/MariaDB fornecido pela hospedagem.

Exemplo de endereço MySQL, usando credenciais criadas no cPanel:

```text
mysql+pymysql://USUARIO:SENHA_CODIFICADA@localhost/BANCO?charset=utf8mb4
```

Substitua os marcadores. Caracteres especiais da senha precisam de codificação de URL. Crie banco e usuário no assistente de bancos do cPanel, associe o usuário ao banco e use os nomes completos, incluindo o prefixo da conta. Teste permissões e conectividade antes de liberar acesso aos estudantes.

## 4. Inicializar e criar a administração

Com o ambiente virtual e a configuração corretos:

```bash
flask --app app init-db
flask --app app create-admin
```

O primeiro comando cria o esquema inicial, importa os oito casos fictícios e restaura o inventário e os rascunhos reais incluídos no ZIP, quando presentes e compatíveis com a configuração. Para restaurar também as fichas baseadas somente em fatos ND, é necessário optar explicitamente por `CASE_ALLOW_CLINICAL_FACTS=true`; mantenha `CASE_REUSE_SCOPE=noncommercial` no escopo desta instalação. A restauração é idempotente, preserva casos e revisões existentes, respeita exclusões e mantém `review_required`, sem publicar os seis rascunhos. O segundo comando solicita os dados da nova conta e a senha de forma interativa. Não existe conta administrativa com senha pública. Use uma conta nova desta instalação.

A restauração dos metadados não substitui a conferência do XML: no servidor novo, a fonte começa com verificação pendente, mesmo quando a ficha em português já está vinculada. As contagens de fontes verificadas da máquina de preparação não são uma verificação feita no destino. Confira os relatórios e revalide fonte e conteúdo antes de publicar.

O endereço administrativo padrão é `https://SEU_DOMINIO/gestao-medlab/login`; se `ADMIN_PATH` mudar, ajuste esse endereço. Não há link administrativo na navegação pública. O caminho discreto reduz sua exposição, mas a proteção efetiva continua sendo autenticação e permissão de administrador no servidor.

Para recuperar o acesso de uma conta, use o Terminal da hospedagem. A nova senha será solicitada sem aparecer na tela:

```bash
flask --app app reset-password EMAIL_DA_CONTA
```

A redefinição invalida as sessões anteriores da conta; o usuário precisa entrar novamente. Não há envio automático de recuperação por email nesta versão. O administrador também pode desativar contas de estudantes pela área privada. Senhas aceitam de 10 a 128 caracteres.

## 5. Reiniciar e validar

Use **Restart** no painel quando disponível. Para instalações Passenger, a atualização de `tmp/restart.txt` na pasta da aplicação solicita reinício:

```bash
mkdir -p tmp
touch tmp/restart.txt
```

Após reiniciar, faça uma verificação no domínio HTTPS:

- A página inicial carrega e o cadastro cria uma conta de estudante.
- O endereço HTTP redireciona para HTTPS e o certificado é válido.
- Login e logout funcionam; uma segunda conta não acessa consultas da primeira.
- É possível iniciar um caso, enviar perguntas, solicitar exames e concluir uma consulta.
- A avaliação salva e reaparece no histórico após sair e entrar novamente.
- A área administrativa exige conta administrativa e permite gerenciar casos.
- A IA responde com `DEMO_MODE=false`; falta de chave/indisponibilidade aparece como erro recuperável, sem apresentar resposta de demonstração como avaliação real.
- Ao ativar voz local, o download mostra progresso, o microfone grava, a transcrição pode ser corrigida e a resposta pode ser ouvida. Verifique também as vozes masculina e feminina nos dispositivos de destino. Negar a permissão, cancelar ou falhar o download mantém o envio por texto disponível.

Se surgir erro 500/503, confira versão do Python, ambiente virtual, dependências, variáveis, permissões do banco e o log privado indicado pelo painel. Application Manager documenta `stderr.log` na pasta da aplicação para Python. Não exponha logs ou páginas de debug na internet. Nunca copie chaves para um chamado de suporte.

## 6. Backup, atualização e restauração

Guarde backups de banco, `.env` e código de cada versão em armazenamento privado, fora da pasta pública e fora do ZIP de distribuição. Se usar importação de relatos publicados, preserve também os manifestos, metadados de licença, versões das fontes e rascunhos em `instance/imports/`; eles documentam a origem das adaptações. O banco contém contas, conversas e avaliações; defina responsáveis, retenção e controle de acesso de acordo com o uso da instituição. Utilize casos simulados, sem inserir dados identificáveis de pacientes atendidos pela instituição.

Para SQLite, use a API de backup para produzir uma cópia consistente. Ajuste o caminho abaixo ao arquivo efetivamente configurado e escolha uma pasta privada existente:

```bash
python - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path('/home/SEU_USUARIO/medlab/instance/medlab.db')
destination = Path('/home/SEU_USUARIO/backups') / (
    'medlab-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.sqlite3'
)
if not source.is_file():
    raise SystemExit('Confira o caminho do banco antes de copiar.')
with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
    original.backup(backup)
print(destination)
PY
```

Para MySQL, use o backup do banco pelo cPanel ou `mysqldump` com senha solicitada interativamente. Confirme a política de backups do provedor e teste a restauração em uma instalação isolada.

Antes de atualizar: faça backup, reserve uma janela de manutenção e confira as mudanças de esquema. Extraia a nova versão sem sobrescrever `.env` e o banco; atualize dependências e execute, nesta versão:

```bash
flask --app app upgrade-db
```

Essa atualização acrescenta o tipo de apresentação ao catálogo e ajusta nomes de especialidade existentes, preservando os snapshots, diálogos e avaliações das consultas. É idempotente. `init-db` também aplica a atualização necessária ao inicializar uma instalação; não executa uma migração geral de qualquer esquema futuro. Reinicie Passenger após a atualização e valide o histórico antes de reabrir o acesso.

Para restaurar: suspenda escritas, restaure o banco e o código compatíveis, recoloque o `.env` privado, confira permissões e reinicie. Valide login, casos e consultas antes de reabrir. Não use o ZIP de entrega como se fosse um backup de produção.

## 7. Demonstração local e limites de entrega

Para apresentar a interface sem consumir a API, use `DEMO_MODE=true` somente em uma instalação local separada e sem dados reais. Identifique o resultado como demonstrativo. Produção deve manter `DEMO_MODE=false` e chave DeepSeek válida. A voz local pode funcionar nos dois modos, após ativação pelo estudante e download dos modelos, sem chave adicional.

A entrega de código e os testes locais não verificam DNS, certificado, disponibilidade de Python/Passenger, limites da hospedagem, credenciais MySQL ou saldo da API. Esses itens precisam ser conferidos na conta de destino. Este guia foi conferido com a documentação oficial do cPanel em outubro de 2026; os nomes dos controles podem variar conforme o provedor.

## 8. Adquirir fontes de casos reais

**O produto da preparação é uma ficha de simulação em português, não a tradução integral de um artigo.** Ela reúne o roteiro leigo do paciente, os achados disponíveis para investigação e um gabarito reservado ao avaliador, com perguntas relevantes, diagnóstico de referência, critérios de feedback e nota. O texto original é usado para conferir fatos e preservar a referência; introdução, revisão bibliográfica e outras partes sem função no exercício não são traduzidas para a biblioteca.

Configure `CASE_REUSE_SCOPE=noncommercial` no mesmo ambiente usado pelo Passenger e pelo Terminal. Esse é o escopo de uso **exclusivamente não comercial** definido para esta instalação. O filtro de adaptação licenciada admite CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA quando confirmadas por artigo. Fontes ND não seguem para tradução/adaptação do texto. `CASE_ALLOW_CLINICAL_FACTS=false` é o padrão; a instalação local autorizada usa `true` para um fluxo distinto, com extração de fatos atômicos e gerador isolado que recebe apenas esses fatos. A referência mantém a licença original, sem dizer que ND autoriza traduzir e sem aplicar ND automaticamente à ficha nova. Evidências usam hashes/localizadores e a publicação continua exigindo revisão. Artigos retratados e fontes indisponíveis permanecem fora da biblioteca. Consulte o [fluxo de fatos clínicos](BASES_CLINICAS.md#fluxo-opcional-baseado-somente-em-fatos-clínicos).

Na raiz privada do projeto, após o backup e a preparação do banco, use o ambiente virtual ativo:

```bash
python scripts/download_case_corpus.py
flask --app app register-sources \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv
```

O download usa somente a biblioteca padrão do Python e não requer chave de IA. Obtém o CSV completo de **205.157.823 bytes** da revisão fixada do MedCaseReasoning, confere tamanho e SHA256 antes da substituição atômica e salva `acquisition.json` em `instance/imports/MedCaseReasoning/`. Um arquivo já válido é conferido e reutilizado sem acesso à rede; um download interrompido pode ser repetido, começando novamente, sem instalar arquivo parcial. O CSV e seus metadados ficam privados, com permissão `0600`.

Se o inventário já veio no ZIP, `init-db` já o registrou. Para consultar, use `flask --app app source-report` ou a página `/<ADMIN_PATH>/acervo`, acessível somente pela administração. Uma exportação separada pode ser restaurada com `flask --app app restore-sources --source caminho/source_registry.jsonl`. O formato portátil schema 2 restaura metadados com verificação pendente e identificadores de exclusão; sozinho, não restaura fichas em português nem publica casos. Os rascunhos são transportados separadamente por `--case-drafts`, como descrito na seção 2.

`register-sources` percorre os **14.489 registros de fonte**, sem rede, tradução ou publicação, e respeita os bloqueios de reimportação. Repetir o comando preserva registros já existentes e informa conflitos. Nesta entrega foram excluídos explicitamente **13 registros: 11 retratados e 2 indisponíveis**, restando **14.476 ativos**. Os identificadores mínimos de exclusão acompanham a exportação para impedir sua reintrodução. Não há exclusão automática de todas as fontes ND. Para o inventário detalhado do CSV, o comando opcional `python scripts/source_inventory.py` continua disponível. CSV e snapshots ficam privados.

**Os 205 MB do acervo clínico ficam no servidor, não no tablet do estudante.** Esse CSV não faz parte dos modelos de voz baixados no navegador. Reserve espaço adicional no servidor para XMLs, metadados, rascunhos e backups. A hospedagem precisa alcançar Hugging Face para o download e PMC Cloud para verificar fontes. É possível preparar o acervo no computador de administração e transferi-lo para a pasta privada do servidor por SFTP.

O acervo bruto não contém especialidades e não é automaticamente uma biblioteca de pacientes em português. O aplicativo suporta as 55 especialidades do CFM; a meta de 300 por área exige 16.500 casos únicos, **no mínimo 2.024 além das 14.476 fontes ativas**. Na instalação de preparação, a verificação foi concluída para 10.761 fontes de adaptação licenciada e 3.715 de preparação factual ND. Completude clínica e distribuição por área ainda precisam ser apuradas; não há promessa de 300 casos em cada especialidade.

O estado desta entrega é **seis fichas reais em português preparadas por Codex e revisadas por outro agente, privadas em `review_required`; 14.470 fontes ainda sem ficha; zero reais publicados e oito fictícios publicados**. Revisão clínica humana permanece pendente. A autenticação DeepSeek foi confirmada, mas a geração pela API não foi validada durante a indisponibilidade do provedor. Os comandos abaixo deixam a retomada pronta para execução explícita quando o serviço estiver disponível; nenhum lote fica rodando ou agendado automaticamente.

A preparação preserva PMCID, versão, licença e evidências da fonte. Depois do backup e de `upgrade-db`, consulte a contagem efetiva do banco e prepare um primeiro lote de fontes sem chamar IA:

```bash
flask --app app catalog-report
flask --app app import-real-cases \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --limit 50
flask --app app import-report
```

O diretório de trabalho padrão é `instance/imports/MedCaseReasoning/processed`. Ele contém `manifest.json`, `records/`, `pmc-cache/`, `drafts/` e `report.json`. Use `--work-dir CAMINHO_PRIVADO` em ambos os comandos de importação/relatório para escolher outro diretório. O checksum vincula esse diretório ao CSV original; uma fonte diferente exige outro diretório. Não execute dois lotes simultâneos sobre o mesmo diretório.

Quando o serviço estiver disponível, após configurar a chave pelo comando de entrada oculta da seção 3 e manter `DEMO_MODE=false`, execute o lote de adaptação:

```bash
flask --app app import-real-cases \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --all-records \
  --adapt \
  --limit 50
```

`--adapt` classifica, prepara a ficha em português pelo fluxo aplicável à fonte e audita por IA. **Esse passo consome créditos**, e cada caso pode exigir várias chamadas; `--limit` limita registros trabalhados, não tokens nem valor monetário. `--all-records` remove o teto de 300 rascunhos por especialidade, mas mantém os filtros de fonte/licença, eventuais especialidades selecionadas e o limite de 50 registros deste lote. Repita o comando para continuar: ele não traduz todo o acervo em uma única execução com `--limit 50`.

`--source-workers N` permite de 1 a 8 verificações de fontes em paralelo; o padrão é 1. Esse parâmetro só paraleliza consultas ao PMC. `--adapt-workers N` aceita de 1 a 16 casos em preparação simultânea; acima de 1 exige `--adapt --all-records`. Gravações no banco, checkpoints e atualização do progresso permanecem coordenados sequencialmente. Comece com poucos casos em paralelo e ajuste à capacidade da hospedagem e aos limites do provedor. Por exemplo:

```bash
flask --app app import-real-cases \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --all-records --adapt --adapt-workers 4 --source-workers 4 --limit 50
```

Falhas gerais de autenticação, saldo, limite de uso, indisponibilidade ou transporte interrompem novas chamadas. Requisições já enviadas podem terminar, e resultados válidos recebidos são preservados. O relatório indica interrupção e trabalho efetivamente concluído; paralelismo não garante que todos os casos enviados terminem. Depois de resolver a causa, acrescente `--retry-errors` quando houver registros em erro:

```bash
flask --app app import-real-cases \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --all-records --adapt --limit 50 --retry-errors
flask --app app import-report
flask --app app source-report
flask --app app catalog-report
```

`aiUsageThisRun` resume o lote; `aiUsage` resume os dados persistidos. Os contadores incluem requisições e tokens de entrada, saída e cache informados pelo provedor. `usageComplete=false` ou `cacheUsageComplete=false` sinaliza informação incompleta: ausência de estatística não significa custo zero. O relatório não estima a cobrança nem impõe teto monetário; `--limit` continua sendo limite de registros.

`AI_IMPORT_TIMEOUT` controla o tempo de espera por chamada da adaptação no Terminal: padrão 120 segundos, máximo 180. Essa configuração não aumenta o timeout da conversa web, que continua limitado a 45 segundos por chamada.

Para trabalhar com a meta de 300 em áreas específicas, omita `--all-records`. Por exemplo:

```bash
flask --app app import-real-cases \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --specialty Cardiologia \
  --specialty Pneumologia \
  --target-per-specialty 300 \
  --limit 10 \
  --adapt
```

Sem `--specialty`, o escopo é a lista completa de 55 especialidades. O filtro não identifica previamente as áreas no CSV: a classificação ainda precisa examinar registros. Registros já resolvidos no cache não consomem o limite do lote. A execução pode ser retomada repetindo o comando; erros anteriores são tentados novamente apenas com `--retry-errors`. `--refresh-sources` reconsulta fontes ainda não adaptadas, inclusive rejeitadas, sem reescrever rascunhos concluídos. Sem chave ou em modo demonstrativo, `--adapt` registra a adaptação bloqueada e conserva a preparação das fontes; não inventa uma tradução.

Rascunhos válidos são inseridos no banco **privados**, sem substituir casos já existentes. Revise o conteúdo e a fonte pela administração antes de publicar. A auditoria por IA não equivale a revisão clínica humana. Licença e retratação são snapshots da consulta ao PMC; revalide-os antes da publicação. `import-report` separa fonte, classificação e rascunhos, e `catalog-report` distingue casos reais publicados, reais em revisão e fictícios. Apenas os publicados chegam à biblioteca do estudante.

Preserve a atribuição e a identificação das alterações. Adaptações SA devem acompanhar a mesma licença e versão da fonte, inclusive em eventual arquivo de caso exportado. A finalidade não comercial da instalação não acrescenta NC a uma fonte CC BY-SA. O resultado apresenta a origem; confira os créditos e as condições antes da publicação, conforme [licenças e atribuição dos casos](BASES_CLINICAS.md#condições-para-adaptar-e-disponibilizar). O inventário portátil é apenas um registro de metadados e não substitui esses créditos.

Depois de alterar o escopo ou habilitar fatos clínicos, reavalie as fontes antes de preparar fichas; `--refresh-sources` permite consultar novamente as ainda não adaptadas. Não edite contagens ou estados manualmente para converter rejeições em aprovações. A opção `commercial` conserva o filtro CC BY/CC0, mas trocar a variável não apaga obrigações de casos NC/SA que já estejam no banco.

Execute lotes no Terminal, sem ocupar uma requisição web longa. Os seis rascunhos preparados por Codex não comprovam funcionamento da geração DeepSeek. Consulte [fontes, inventário e limites de cobertura](BASES_CLINICAS.md) antes de publicar casos derivados de artigos.

### Restaurar o pacote privado de retomada

`dist/medlab-acervo-retomada.tar.gz` é um arquivo administrativo separado: **269.704.016 bytes, aproximadamente 270 MB comprimidos e 1,05 GB de dados extraídos**. Contém o CSV fixado, progresso e snapshots das 14.476 fontes ativas, seis fichas privadas e exportações com os 13 bloqueios de reimportação. Não contém `.env`, chave, banco, contas ou conversas. Não é necessário para estudar ou executar o aplicativo; serve para continuar a preparação no Terminal sem baixar novamente as fontes já conferidas. Esses 270 MB ficam no servidor privado, separados dos downloads opcionais de voz no navegador.

Use somente em **instalação nova**, com o aplicativo já instalado e sem um diretório `instance/imports/MedCaseReasoning/processed` contendo progresso próprio. Não extraia sobre checkpoints mais recentes. Envie o arquivo por SFTP para uma pasta privada da conta, fora de `public_html`. Confira seu SHA256 antes de extrair:

```bash
sha256sum /home/SEU_USUARIO/medlab-acervo-retomada.tar.gz
```

O resultado esperado para esta entrega é `89bfab4697d23e20fac5884819c6f88e5e3811beceb9983e6f72b780d8013a0c`. Somente depois de conferir, extraia na raiz privada do aplicativo e valide os arquivos internos:

```bash
cd /home/SEU_USUARIO/medlab
tar -xzf /home/SEU_USUARIO/medlab-acervo-retomada.tar.gz
sha256sum -c MANIFEST-ACERVO.sha256
```

O manifesto do acervo tem nome próprio e não substitui `MANIFEST.sha256` do aplicativo. Leia também `RETOMADA-LEIA-ME.txt`. Configure `CASE_REUSE_SCOPE=noncommercial` e opte por `CASE_ALLOW_CLINICAL_FACTS=true` para restaurar os dois fluxos; configure a chave localmente pelo comando da seção 3. Com o ambiente virtual ativo:

```bash
flask --app app init-db
flask --app app restore-sources --source instance/exports/source_registry.jsonl
flask --app app register-sources \
  --source instance/imports/MedCaseReasoning/medcasereasoning_core.csv \
  --work-dir instance/imports/MedCaseReasoning/processed
flask --app app upgrade-db
flask --app app source-report
flask --app app catalog-report
```

Nesse caminho, a sincronização confere os snapshots e checkpoints transferidos antes de refletir o progresso; é diferente de restaurar apenas metadados do ZIP. Confira 14.476 fontes ativas, seis rascunhos reais privados e zero reais publicados antes de preparar novos lotes. O pacote não publica fichas nem inicia a DeepSeek. Quando o provedor estiver operacional, comece com `--limit 5 --retry-errors` no comando `import-real-cases --all-records --adapt` mostrado acima, confira o relatório e só então aumente o lote.

## 9. Cache de prompts e consumo observado

A DeepSeek habilita o cache automaticamente, sem uma flag de ativação. O acerto exige correspondência exata com uma unidade de prefixo já persistida; conteúdo comum entre duas chamadas não garante acerto imediato. A construção leva segundos e entradas sem uso podem ser removidas após horas ou dias. O serviço é de melhor esforço, sem taxa garantida ou permanência. [Guia oficial de cache](https://api-docs.deepseek.com/guides/kv_cache/).

O MED LAB serializa mapas de forma determinística e coloca instruções, formatos e listas estáveis antes do caso, histórico e pergunta variáveis. Isso favorece reaproveitamento sem ampliar os dados clínicos enviados ao paciente. Não há chamadas de aquecimento nem repetição paga automática para criar cache. Os valores `prompt_cache_hit_tokens` e `prompt_cache_miss_tokens` informam o uso observado; armazenamento em cache não elimina a geração da saída nem garante custo zero.

Na administração, **Uso da IA** (`ADMIN_PATH/uso-ia`, por padrão `/gestao-medlab/uso-ia`) mostra conversas, avaliações e geração de casos pelo site, com filtros de 7 dias, 30 dias ou todo o período registrado. Uma operação pode fazer várias solicitações. O painel cobre chamadas desde a ativação deste registro, incluindo operações não concluídas; importações pelo Terminal continuam nos relatórios `aiUsage`/`aiUsageThisRun`.

A taxa é `cache hit ÷ (cache hit + cache miss)` e só aparece quando todas as chamadas da seleção informam cache e existe denominador positivo. Sem estatísticas completas, o painel sinaliza dados parciais ou não informados; ausência de dados não significa gasto zero. São armazenados apenas contadores, data, operação e identificadores internos, sem prompts, respostas, diagnóstico ou credenciais nesse registro. Confira a cobrança no provedor: o painel não é uma fatura nem impõe um teto monetário.

Esse cache pertence ao provedor de IA. Ele é separado do cache de fontes PMC no servidor e dos modelos de voz baixados no dispositivo. O consumo de memória e o download inicial de Whisper/Kokoro no navegador continuam existindo, independentemente do cache de prompts.
