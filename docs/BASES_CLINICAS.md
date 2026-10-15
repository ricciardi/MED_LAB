# Pesquisa de bases para casos clínicos reais

Pesquisa e coleta iniciadas em 14/10/2026 (15/10 em UTC). O acervo original completo do MedCaseReasoning foi baixado e conferido: **14.489 registros reais de fonte**, em inglês. Após excluir explicitamente 11 artigos retratados e 2 fontes indisponíveis, o inventário privado contém **14.476 fontes ativas verificadas**: **10.761 para adaptação licenciada e 3.715 para o fluxo opcional de fatos clínicos ND**. Fichas em português estão sendo preparadas e corrigidas por lotes, com chamadas reais DeepSeek e revisão assistida. Consulte `source-report`, `import-report` e `catalog-report` para os totais atuais; verificação da fonte, ficha preparada, revisão clínica e publicação são etapas distintas.

## Fonte recomendada: MedCaseReasoning

O [MedCaseReasoning, de pesquisadores de Stanford e colaboradores](https://arxiv.org/html/2505.11733v2), contém exemplos derivados de relatos clínicos publicados. Os autores afirmam cobertura de mais de 30 especialidades, mas **não distribuem rótulos de especialidade no arquivo**; não é possível confirmar uma contagem por especialidade a partir desse dado. Ele separa a apresentação do caso (`case_prompt`), o raciocínio diagnóstico (`diagnostic_reasoning`) e o diagnóstico final (`final_diagnosis`), além de fornecer PMCID e endereço do artigo.

- [Dados oficiais no Hugging Face](https://huggingface.co/datasets/zou-lab/MedCaseReasoning).
- [Repositório dos autores](https://github.com/kevinwu23/Stanford-MedCaseReasoning).
- Revisão do depósito consultada: `469a5365bc534b5b2b9cfbc52b2ef2a10f43a339`.
- Divisões declaradas: 13.092 treino, 500 validação e 897 teste.
- Arquivo CSV integral obtido da revisão acima; não foram enviados dados dos usuários do MED LAB.

### Inventário local reproduzível

```bash
python scripts/source_inventory.py
```

O comando usa `requests` e o leitor CSV da biblioteca padrão, sem pandas/pyarrow. Baixa o arquivo para `instance/imports/MedCaseReasoning/medcasereasoning_core.csv`, confere tamanho e SHA256 contra os metadados LFS oficiais e gera `inventory.json`, `acquisition.json`, `hf_metadata.json` e `hf_files.json` na mesma pasta. Ao executar novamente, reutiliza o arquivo somente após conferir sua integridade. O cache `instance/` está ignorado pelo Git e é excluído do ZIP de publicação.

| Medida conferida no arquivo completo | Resultado |
| --- | ---: |
| Registros / PMCIDs distintos / índices originais distintos | 14.489 / 14.489 / 14.489 |
| Registros treino / validação / teste | 13.092 / 500 / 897 |
| Tamanho CSV em bytes | 205.157.823 |
| Periódicos distintos, por texto exato do campo | 813 |
| Diagnósticos finais distintos, por texto exato, sem normalização clínica | 8.861 |
| Campos vazios / PMCIDs duplicados | 0 / 0 |
| Colunas de especialidade | Nenhuma |
| Traduções, revisões clínicas ou casos de aplicação criados pelo inventário | 0 |

SHA256 do CSV: `887274683b81d81f48f335ff0f87d56dac6ed8ea09b4bb5aa2b3979f5a1a88b8`.

O CSV tem **11 campos**: índice original sem nome, `pmcid`, `title`, `journal`, `article_link`, `publication_date`, `text`, `case_prompt`, `diagnostic_reasoning`, `final_diagnosis` e `split`. O cartão/parquet expõe o índice como `Unnamed: 0` e usa partições para o split; não confundir seu esquema de dez campos com o CSV. Os textos contêm quebras de linha dentro de células: contar linhas do arquivo não conta casos.

O inventário registra a distribuição completa por periódico e ano informado. Exemplos conferidos: Journal of Medical Case Reports, 1.183; Clinical Case Reports, 1.098; International Journal of Surgery Case Reports, 991; Radiology Case Reports, 925. **Periódico não é rótulo de especialidade.** Não foi aplicada classificação heurística ao acervo.

### Especialidades suportadas e meta de cobertura

O MED LAB adota as **55 especialidades** da [Resolução CFM nº 2.380/2024, páginas 2–3](https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2024/2380_2024.pdf), disponíveis em `medlab/catalog.py`. A consulta ao [portal da Comissão Mista de Especialidades](https://portal.cfm.org.br/?page_id=156910) não identificou norma posterior substituindo essa relação; o próprio CFM ainda menciona 55 em [publicação de 06/10/2026](https://portal.cfm.org.br/noticias/cfm-abre-o-i-forum-de-medicina-paliativa-com-defesa-do-debate-etico-e-do-cuidado-integral-ao-paciente/). Esse é o escopo brasileiro escolhido para estudo, não um catálogo de rótulos fornecido pelo MedCaseReasoning.

**300 casos únicos por especialidade representam 16.500 casos.** As 14.476 fontes ativas verificadas deixam um déficit mínimo de **2.024**, antes de considerar conteúdo incompleto ou inadequado ao exercício. Esse total inclui 10.761 fontes para adaptação licenciada e 3.715 para preparação factual com a opção explícita habilitada. O déficit efetivo por área só pode ser calculado após classificar e revisar. Não há evidência de que todas as 55 áreas estejam representadas nem de que alguma já possua 300 casos aptos.

Uma classificação por DeepSeek deve registrar modelo, versão do esquema, justificativa e estado de revisão. É um rótulo gerado pela aplicação até ser revisado, nunca uma etiqueta oficial da fonte. Para controlar a meta, cada caso tem uma especialidade principal; temas secundários podem ser tags sem aumentar a contagem de casos únicos. Os 15 tipos de queixa em `CASE_TYPES` são uma organização pedagógica própria, independente da relação do CFM.

Os casos são derivados de relatos reais, mas a preparação dos exemplos foi assistida por modelos de linguagem. O estudo descreve avaliação humana de uma amostra de 100 casos por quatro médicos; isso não significa revisão individual de todos os exemplos, nem validação de uma tradução ou de um roteiro de paciente virtual. Há concentração de apresentações raras e complexas, que deve ser considerada ao selecionar casos para iniciantes.

## Licença: verificar o artigo, além do depósito

Esta instalação foi definida para **uso educacional privado, exclusivamente não comercial**, com `CASE_REUSE_SCOPE=noncommercial`. O escopo permite avaliar fontes CC BY, CC0, CC BY-NC, CC BY-SA e CC BY-NC-SA; não dispensa a conferência da licença exata de cada artigo. A finalidade não comercial é uma condição de uso desta instalação, não uma nova licença aplicada indistintamente a todo o acervo.

O GitHub dos autores declara CC BY 4.0 para os dados e MIT para o código; o cartão do Hugging Face está marcado MIT. Essa divergência não deve apagar a licença dos artigos originais. O [PMC determina explicitamente que as condições variam por artigo](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/).

Amostras verificadas no serviço oficial PMC Cloud:

| PMCID | Licença do artigo indicada nos metadados | Decisão no escopo não comercial |
| --- | --- | --- |
| [PMC4863315](https://pmc.ncbi.nlm.nih.gov/articles/PMC4863315/) | CC BY; XML especifica CC BY 4.0 | Candidato, com atribuição e identificação das adaptações |
| [PMC5728002](https://pmc.ncbi.nlm.nih.gov/articles/PMC5728002/) | CC BY-NC | Candidato após confirmar termos e versão no XML; somente uso não comercial |
| [PMC4217568](https://pmc.ncbi.nlm.nih.gov/articles/PMC4217568/) | CC BY-NC; editor especifica CC BY-NC 3.0 | Candidato, preservando CC BY-NC 3.0 e atribuição |

Metadados consultados: [PMC4863315.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC4863315.1.json), [PMC5728002.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC5728002.1.json), [PMC4217568.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC4217568.1.json). O importador exige concordância entre metadados e declaração de licença no XML, registra a versão específica e mantém o texto encontrado. Não presume elegibilidade dos demais artigos nem que todos tenham uma versão `.1` disponível.

Antes da auditoria integral, foram consultados os metadados dos **20 primeiros registros do CSV**, como amostra de conveniência: 9 CC BY, 5 CC BY-NC, 3 CC BY-NC-SA e 3 CC BY-NC-ND. Seus percentuais não devem ser extrapolados aos 14.489 registros; os totais finais estão na seção de auditoria abaixo. JSONs oficiais, URLs, ETags, Last-Modified e hashes dessa etapa estão preservados em `instance/imports/MedCaseReasoning/pmc_audit_sample/`; `audit.json` contém o manifesto. A verificação de uma licença não aprova automaticamente o conteúdo clínico ou a fidelidade de uma ficha.

### Condições para adaptar e disponibilizar

| Licença confirmada | Tratamento neste projeto |
| --- | --- |
| CC BY | Manter autoria, fonte, licença e indicação de alterações. |
| CC0 | Manter a proveniência científica; CC0 não impõe a atribuição das licenças BY. |
| CC BY-NC | Manter atribuição e uso exclusivamente não comercial. |
| CC BY-SA | Disponibilizar a adaptação sob a mesma licença e versão da fonte, com atribuição. Não acrescentar NC. |
| CC BY-NC-SA | Manter atribuição, uso não comercial e a mesma licença e versão na adaptação. |
| CC BY-ND / CC BY-NC-ND | Não admitir tradução/adaptação do texto. Com a opção explícita de fatos clínicos, verificar a fonte e preparar somente fatos atômicos para redação independente; sem aprovação automática. |

A tradução e a reformulação do texto do relato são tratadas como adaptações. ND não permite distribuir esse material adaptado, mesmo sem cobrança; o fluxo opcional de fatos descrito abaixo não usa essa licença como autorização para traduzir. Licenças não reconhecidas, ausentes ou conflitantes também impedem a adaptação automática. [Condições oficiais ND](https://creativecommons.org/licenses/by-nc-nd/4.0/).

Para SA, o MED LAB escolhe conservar o código, a versão e a URL exatos da fonte, sem tentar combinar licenças ou converter todas para 4.0. A licença acompanha o conteúdo adaptado; não altera automaticamente a licença do código da aplicação. A exigência de CompartilhaIgual e a proibição de restrições adicionais constam das seções 3(b) de [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en#s3) e [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en#s3).

Na apresentação da fonte e no resultado, conservar autores, título, citação, PMCID/DOI, link do artigo, licença com versão e link, avisos fornecidos e identificação das mudanças. Distinguir relato original, tradução e encenação do MED LAB, sem sugerir endosso dos autores. Esses créditos e a licença aplicável acompanham também cada ficha incluída no pacote privado por `--case-drafts`. A exportação `--source-registry` contém apenas metadados e não substitui a proveniência da ficha. [Requisitos oficiais de atribuição, seção 3(a)](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en#s3).

A biblioteca pode continuar protegida por login. Isso não exige publicar rascunhos nem abrir o acervo integral; o controle de acesso não deve retirar dos destinatários os direitos da licença sobre o material recebido. A Creative Commons confirma esse uso em [sites protegidos por senha](https://creativecommons.org/faq/#can-i-share-cc-licensed-material-on-password-protected-sites). “Não comercial” diz respeito à finalidade do uso, não apenas ao tipo da instituição; a configuração registra a decisão adotada para esta instalação. [Definição oficial, seção 1(k)](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en#s1).

### Fluxo opcional baseado somente em fatos clínicos

`CASE_ALLOW_CLINICAL_FACTS=false` é o padrão. A instalação local autorizada usa `true`; essa opção não muda a licença da fonte nem substitui sua verificação. A Creative Commons distingue fatos e ideias da expressão protegida e explica que o uso de dados factuais pode ficar fora das condições da licença quando não reproduz conteúdo ou estrutura protegidos. Isso não autoriza traduzir ou parafrasear um relato ND. [Orientação oficial sobre fatos e bases de dados](https://creativecommons.org/faq/#how-do-i-know-whether-a-particular-use-of-a-database-is-restricted-by-copyright).

O extrator produz fatos atômicos normalizados: sintomas, cronologia, valores e unidades, achados, diagnóstico documentado e conduta descrita. Dados ausentes permanecem ausentes. O gerador de paciente e rubrica recebe **somente esses fatos**, sem artigo, `case_prompt` ou raciocínio narrativo original. A ficha usa redação independente e não reproduz citações do relato. Evidências na ficha são hashes e localizadores verificáveis; o XML original permanece no cache privado para auditoria.

A referência conserva autores, fonte, versão e licença original. Ela identifica de onde vieram os fatos, sem afirmar que ND licenciou a tradução nem atribuir automaticamente ND à nova ficha. Auditoria de conteúdo e revisão humana continuam necessárias; um rótulo de “fatos” não transforma por si só uma paráfrase em texto independente. Artigos retratados ou sem fonte verificável não entram por essa opção.

### Auditoria executada pelo importador

O importador examinou os **14.489 registros**, consultando metadados oficiais do PMC e o XML das fontes elegíveis aos dois fluxos. As correções conservadoras de formatação de licença foram revalidadas contra snapshots locais, sem alterar os critérios de aceitação. A conferência final da instalação de preparação registra:

| Etapa | Quantidade |
| --- | ---: |
| Registros no CSV original | 14.489 |
| Fontes ativas com verificação concluída | 14.476 |
| Fontes verificadas para adaptação licenciada | 10.761 |
| Fontes verificadas para preparação factual ND | 3.715 |
| Divisão dessas fontes: CC BY-NC-ND / CC BY-ND | 3.698 / 17 |
| Exclusões aplicadas: artigos retratados / fontes indisponíveis | 11 / 2 |
| Fichas preparadas, pendentes e publicadas | Consultar relatórios operacionais |

As 3.715 fontes factuais tiveram o XML conferido; isso verifica origem e elegibilidade para o fluxo, não a validade clínica de uma futura ficha. A licença ND permanece na proveniência original. Os 13 registros excluídos não contam novamente nas categorias ativas. Identificadores mínimos de exclusão impedem sua reintrodução pelo CSV, sincronização ou restauração. A exclusão preserva eventuais casos e históricos já vinculados; os artigos ND ativos foram mantidos.

A auditoria recuperou 12 falsos negativos técnicos da primeira passada: oito URLs CC BY 2.0 com marcação residual, duas URLs CC BY-NC 4.0 com diferença de maiúsculas e duas declarações separadas de CC BY para o artigo e CC0 para dados. No fluxo factual, outras sete variantes foram verificadas pontualmente: quatro declarações CC BY-NC-ND 3.0 US e três declarações em texto de permissões. Foram preservados código, versão e jurisdição, sem transformar uma licença desconhecida em licença aceita. Em 56 fontes havia uma versão sem código e outra com ND; isso não acrescenta 56 exclusões.

ND limita a distribuição de tradução/adaptação do texto; não é um defeito técnico do artigo nem uma proibição de sua leitura ou de toda utilização de fatos. Retratação e indisponibilidade são motivos distintos. O comando `exclude-restricted-sources` exige seleção explícita de PMCIDs e foi usado somente para os 13 registros acima. O CSV e os snapshots permanecem privados, fora do ZIP da aplicação e do acesso dos estudantes.

As primeiras fichas foram preparadas por Codex a partir de snapshots verificados, com conferência independente assistida. O fluxo DeepSeek também foi exercitado com chamadas reais de classificação, preparação, auditoria e correção. Fichas estruturadas conservam paciente, avaliador, evidências e proveniência; não são traduções integrais de artigos. As factuais guardam hashes/localizadores, sem citações do relato ND. Todos os resultados permanecem sujeitos a revisão clínica humana; aprovação estrutural e auditoria por IA não a substituem.

O manifesto preserva versões, URLs e checksums. Uma mesma versão na origem pode ser atualizada: o snapshot reproduz a decisão tomada, mas não substitui revalidação antes de publicar. `source-report`, `import-report` e `catalog-report` são a referência operacional para inventário, trabalho concluído, pendências e casos publicados. O [processamento por lotes](CPANEL.md#8-adquirir-fontes-de-casos-reais) é iniciado explicitamente; a instalação não agenda trabalho por conta própria.

O ZIP pode transportar o inventário (`--source-registry`) e rascunhos (`--case-drafts`) em arquivos separados. `export-real-drafts` produz lotes de até 20 MiB com manifesto e checksums; a restauração valida o conjunto e aplica cada lote de forma atômica e idempotente, preservando casos/revisões existentes. Bloqueios de reimportação permanecem ativos e rascunhos não são publicados. Sem XML local conferido, a verificação da fonte permanece pendente no host novo, mesmo quando a ficha em português foi restaurada.

O arquivo administrativo opcional `medlab-acervo-retomada.tar.gz` transporta CSV, snapshots, checkpoints, etapas privadas de IA e exportações fragmentadas, sem banco ou credenciais. Quantidades e hashes pertencem a cada snapshot, não ao estado futuro do acervo. Restaure apenas em instalação nova, sem sobrescrever progresso recente. A sincronização valida os snapshots antes de refletir seus estados. O [procedimento de retomada](CPANEL.md#restaurar-o-pacote-privado-de-retomada) distingue esse arquivo do [pacote separado de imagens](MIDIAS_CLINICAS.md).

Para coletar conteúdo, usar os serviços permitidos pelo PMC: Cloud, OAI-PMH, E-Utilities ou BioC. O antigo OA Web Service foi [desativado em 25/08/2026](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/), portanto um importador novo não deve depender de `oa.fcgi`. O [README do serviço Cloud](https://pmc-oa-opendata.s3.amazonaws.com/README.txt) documenta os arquivos e metadados, incluindo DOI, citação, código de licença, retratação e checksum do XML.

## Outras bases consideradas

| Base | Quantidade e utilidade | Limitação relevante |
| --- | --- | --- |
| [CUPCase](https://huggingface.co/datasets/ofir408/CupCase) | 3.562 casos derivados do Journal of Medical Case Reports/BMC, com diagnóstico livre e alternativas | Os cinco campos distribuídos não incluem PMCID, DOI, URL ou raciocínio; a fonte precisa ser recuperada. Distratores baseados em similaridade ICD não são diferenciais clínicos validados. O depósito declara Apache 2.0, sem substituir a atribuição do material original. [Artigo](https://arxiv.org/abs/2503.06204). |
| [MACCROBAT](https://figshare.com/articles/dataset/MACCROBAT2018/9764942) | 200 relatos com entidades clínicas anotadas; depósito CC BY 4.0 | Foi construído para anotação de entidades, não para pontuação de anamnese. As edições 2018 e 2020 não devem ser somadas como 400 pacientes independentes. |
| [PMC-Patients](https://huggingface.co/datasets/zhengyun21/PMC-Patients) | V2 com 250.294 resumos; versão anterior com cerca de 167 mil | Conjunto CC BY-NC-SA 4.0, compatível com o escopo não comercial mediante atribuição e CompartilhaIgual; não tem diagnóstico separado como no MedCaseReasoning e exige conferir as fontes individuais. |
| [MultiCaRe v3.0.1](https://zenodo.org/records/20416562) | Mais de 98 mil casos, com metadados e imagens | Depósito CC BY-NC-SA 4.0 e licenças individuais variadas. Interessante para futura expansão multimodal; exige extração do diagnóstico e dos critérios de avaliação. |

## Transformação em paciente virtual

**O produto da preparação é uma ficha de simulação em português, não a tradução integral de um artigo.** Ela reúne o roteiro leigo do paciente, os achados disponíveis para investigação e um gabarito reservado ao avaliador, com perguntas relevantes, diagnóstico de referência, critérios de feedback e nota. O texto original é usado para conferir fatos e preservar a referência; introdução, revisão bibliográfica e outras partes sem função no exercício não são traduzidas para a biblioteca.

Uma apresentação diagnóstica contém informações que um paciente não conhece. A adaptação deve escolher um momento do atendimento e separar quatro blocos:

1. **O que o paciente sabe:** queixa, cronologia, sensações, limitações, preocupações e antecedentes explicitamente documentados. A DeepSeek recebe apenas os fatos pertinentes à pergunta e responde em primeira pessoa, em português brasileiro, com vocabulário leigo.
2. **O que depende de investigação:** achados do exame físico, laboratório e imagem. Esses resultados aparecem quando solicitados no fluxo de exames; não são recitados pelo paciente como conhecimento médico.
3. **Gabarito reservado:** diagnóstico, diferenciais, raciocínio e evidências do relato. Não podem aparecer no HTML, JSON, busca ou título público antes da finalização.
4. **Proveniência e adaptação:** PMCID, DOI, autores, artigo, licença, versão, checksum, momento clínico escolhido e alterações educacionais. A referência detalhada aparece ao revisar o resultado.

O agente do paciente deve falar, por exemplo, “Fico sem ar quando subo a escada”, quando isso corresponde ao relato, sem apresentar uma interpretação técnica. Se o dado não foi documentado, não deve transformar a ausência em “não tenho”, “normal” ou “nego”. Uma resposta possível é “Não sei lhe dizer”. A avaliação também precisa reconhecer a informação indisponível, para não punir o aluno por uma lacuna da fonte.

Nome e outros elementos de identidade usados na dramatização devem ser fictícios. A descrição adequada é **simulação educacional adaptada de relato clínico real publicado**, distinguindo os fatos da fonte dos elementos de encenação. Não usar informações identificáveis adicionais.

## Avaliação e nota

O avaliador recebe a entrevista integral, os exames solicitados, o diagnóstico e o plano do estudante, junto do gabarito reservado. Deve apontar perguntas relevantes realizadas e omitidas, diferenciais sustentados pelas evidências, compatibilidade do diagnóstico e correções concretas. Aceitar sinônimos diagnósticos e variações justificáveis, sem pontuar somente por palavras-chave.

A aplicação guarda seis dimensões e nota de 0 a 100. Para uma importação, cada caso precisa de rubrica explícita com evidência da fonte. Nenhuma das bases pesquisadas fornece uma rubrica OSCE pronta e validada para todas as perguntas de um aluno. O esquema permite marcar exame físico, exames complementares ou tratamento como não avaliáveis (`assessable=false`), com justificativa e sem critérios pontuáveis, quando faltam fatos. Essas dimensões ficam sem nota e são excluídas do cálculo global. Anamnese, diagnóstico e desempenho global continuam exigindo fatos suficientes; não preencher lacunas com dados clínicos inventados.

Os tratamentos históricos descritos em um relato, inclusive tentativas que falharam, não devem ser automaticamente promovidos a conduta correta atual. O retorno é formativo e deve permitir revisão docente de um resultado contestado.

## Processamento e publicação

A meta mínima é **300 casos por especialidade**. A opção `--all-records` permite preparar todo o acervo elegível além dessa meta, com publicação progressiva por lotes após filtros de licença, retratação, completude e viabilidade de entrevista. Novas fontes serão necessárias para atingir a meta de 16.500 casos únicos, e o planejamento deve considerar os déficits de cada especialidade, sem duplicar um caso para preencher cotas.

O processamento deve ocorrer por lotes retomáveis no Terminal/cPanel, com o resultado em banco, sem depender de uma requisição web longa. Etapas: coletar metadados → verificar fonte/licença → selecionar adaptação licenciada ou fluxo factual → preparar paciente e rubrica → validar estrutura e fidelidade → revisar → publicar. Evitar duplicatas pelo identificador da fonte e manter snapshots das consultas.

`--source-workers` aceita de 1 a 8 consultas ao PMC. `--adapt-workers` respeita o teto configurado em `AI_IMPORT_WORKER_LIMIT` (padrão 16); acima de 1 exige `--adapt --all-records`. O teto pode ser ampliado em uma máquina de preparação, mas no cPanel simples recomenda-se começar com 1 a 4. Cada tarefa usa seu serviço de IA, e gravações, callbacks e banco são coordenados sequencialmente. Falhas gerais do provedor interrompem novas chamadas; resultados válidos de requisições em andamento são preservados. A retomada é explícita, com `--retry-errors` quando necessário.

Os relatórios guardam requisições e tokens de entrada, saída e cache informados pelo provedor. `usageComplete` e `cacheUsageComplete` indicam se os dados cobrem todas as requisições observadas. Uma resposta sem estatística de uso não comprova consumo zero; esses contadores não substituem a cobrança do provedor. Paralelismo não garante uma velocidade fixa nem aumenta o saldo disponível.

A preparação real foi exercitada com a API DeepSeek e continua dependente de chave, saldo e disponibilidade. Checkpoints em `processed/ai-stages/` conservam respostas intermediárias e seus hashes; retomada e reparos limitados conferem novamente o resultado. Etapas rejeitadas não são fichas aprovadas. A revisão clínica é distinta dos testes de software e da auditoria por IA; só casos publicados devem aparecer na biblioteca do aluno.

## Registro no banco e portabilidade

`register-sources` cadastra registros válidos que não estejam na lista de exclusões. Mantém título/diagnóstico originais privados, base, revisão, PMCID, linha e hashes; não duplica PMCIDs nem sobrescreve conflitos. `excluded_pmcids` bloqueia reimportação; `sync_import_records` respeita as exclusões e confere manifesto e snapshots antes de refletir o progresso no banco. Esses estados refletem a última sincronização validada, não vigilância contínua dos arquivos.

A administração oferece busca e filtros no inventário operacional sem expor gabaritos aos alunos. A tradução/adaptação aprovada pela validação estrutural e pela auditoria de IA cria um `ClinicalCase` privado, vinculado à fonte; revisão e publicação continuam etapas separadas. `source-report` consulta essas contagens no banco.

`export-sources` gera JSONL de metadados no formato portátil **schema 2**, com fontes não excluídas e identificadores mínimos de bloqueio. O arquivo não inclui artigo, roteiro, usuários ou alegações de processamento. O empacotador aceita esse arquivo em `--source-registry`; `init-db`/`upgrade-db` restauram o inventário com verificação pendente e mantêm as exclusões. As fichas são transportadas separadamente por `--case-drafts`; copiar apenas metadados não restaura português preparado nem verificação local do XML.

O inventário preserva as fontes enquanto não houver exclusão explícita. Para exclusões registradas pelo operador, o banco e a exportação guardam identificadores mínimos de bloqueio, sem o registro clínico removido. O CSV original e os snapshots ficam em armazenamento privado de auditoria; IDs bloqueados não participam da adaptação ou do sorteio. `CASE_REUSE_SCOPE=noncommercial` define o filtro desta instalação, enquanto `commercial` mantém o recorte conservador CC BY/CC0 para outras configurações. Mudar o filtro não remove exclusões nem condições de licenças já incorporadas e não torna comercialmente reutilizáveis casos NC existentes.

## Exames e imagens da fonte

O catálogo de figuras foi extraído dos XML/metadados oficiais de todas as fontes ativas, com IDs e checksums. Figura de artigo não equivale a exame de um paciente: a associação exige mesmo paciente, exame existente e momento clínico compatível. Os originais ficam privados e intactos, com atribuição/licença; indícios de identificação, direitos de terceiros ou dúvida retêm o arquivo. A seleção e a visão automática registram `machine_reviewed`, sem alegar conferência humana; uma segunda inspeção independente examina privacidade e a legenda é neutra por construção. Casos sem imagem adequada continuam disponíveis em texto. Veja [preparação, testes reais e limites das mídias](MIDIAS_CLINICAS.md).
