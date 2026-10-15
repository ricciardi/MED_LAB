# Pesquisa de bases para casos clínicos reais

Pesquisa e coleta realizadas em 14/10/2026 (15/10 em UTC). O acervo original completo do MedCaseReasoning foi baixado e conferido: **14.489 registros reais de fonte**, ainda em inglês. A biblioteca inicial do estudante contém oito casos fictícios. Download de fonte, adaptação em português, revisão clínica e publicação são etapas e contagens distintas.

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

**300 casos únicos por especialidade representam 16.500 casos.** Este acervo, com 14.489, é insuficiente por pelo menos 2.011 casos antes de qualquer exclusão por licença, incompletude ou inadequação pedagógica. O déficit efetivo por área só pode ser calculado após classificar, filtrar e revisar. Não há evidência de que todas as 55 áreas estejam representadas nem de que alguma já possua 300 casos aptos.

Uma classificação por DeepSeek deve registrar modelo, versão do esquema, justificativa e estado de revisão. É um rótulo gerado pela aplicação até ser revisado, nunca uma etiqueta oficial da fonte. Para controlar a meta, cada caso tem uma especialidade principal; temas secundários podem ser tags sem aumentar a contagem de casos únicos. Os 15 tipos de queixa em `CASE_TYPES` são uma organização pedagógica própria, independente da relação do CFM.

Os casos são derivados de relatos reais, mas a preparação dos exemplos foi assistida por modelos de linguagem. O estudo descreve avaliação humana de uma amostra de 100 casos por quatro médicos; isso não significa revisão individual de todos os exemplos, nem validação de uma tradução ou de um roteiro de paciente virtual. Há concentração de apresentações raras e complexas, que deve ser considerada ao selecionar casos para iniciantes.

## Licença: verificar o artigo, além do depósito

O GitHub dos autores declara CC BY 4.0 para os dados e MIT para o código; o cartão do Hugging Face está marcado MIT. Essa divergência não deve apagar a licença dos artigos originais. O [PMC determina explicitamente que as condições variam por artigo](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/).

Amostras verificadas no serviço oficial PMC Cloud:

| PMCID | Licença do artigo indicada nos metadados | Decisão para uma biblioteca reutilizável comercialmente |
| --- | --- | --- |
| [PMC4863315](https://pmc.ncbi.nlm.nih.gov/articles/PMC4863315/) | CC BY; XML especifica CC BY 4.0 | Candidato, com atribuição e identificação das adaptações |
| [PMC5728002](https://pmc.ncbi.nlm.nih.gov/articles/PMC5728002/) | CC BY-NC | Excluir desse recorte |
| [PMC4217568](https://pmc.ncbi.nlm.nih.gov/articles/PMC4217568/) | CC BY-NC; editor especifica CC BY-NC 3.0 | Excluir desse recorte |

Metadados consultados: [PMC4863315.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC4863315.1.json), [PMC5728002.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC5728002.1.json), [PMC4217568.1](https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC4217568.1.json). A seleção proposta aceita somente fontes com CC BY ou CC0 confirmadas e registra a versão específica. Não presume elegibilidade dos demais artigos nem que todos tenham uma versão `.1` disponível.

Também foram consultados os metadados dos **20 primeiros registros do CSV**, como amostra de conveniência: 9 CC BY, 5 CC BY-NC, 3 CC BY-NC-SA e 3 CC BY-NC-ND. Não é uma amostra representativa e seus percentuais não devem ser extrapolados aos 14.489 registros. Os nove CC BY são candidatos segundo o código de licença, não casos aprovados. JSONs oficiais, URLs, ETags, Last-Modified e hashes estão preservados em `instance/imports/MedCaseReasoning/pmc_audit_sample/`; `audit.json` contém o manifesto. A licença exata no XML, o conteúdo e a fidelidade da adaptação ainda precisam ser examinados por caso.

### Auditoria executada pelo importador

Em seguida, o importador examinou os **300 primeiros registros consecutivos** do CSV, consultando metadados e XML oficiais do PMC. O relatório local `instance/imports/MedCaseReasoning/processed/report.json` registra:

| Etapa | Quantidade |
| --- | ---: |
| Fontes verificadas e aceitas pelo filtro de licença | 146 |
| Fontes rejeitadas pelo filtro | 154 |
| Fontes ainda não verificadas | 14.189 |
| Fontes classificadas por especialidade | 0 |
| Traduções / rascunhos adaptados / revisões clínicas deste lote | 0 / 0 / 0 |

Esse lote também é uma amostra de conveniência; não estimar a elegibilidade do restante a partir de sua proporção. As 146 fontes aceitas possuem snapshots privados dos bytes originais do XML e dos metadados JSON, identificados por hashes. O manifesto preserva a versão, URLs e checksums examinados. Uma mesma versão no serviço de origem pode ser atualizada: o snapshot permite reproduzir a decisão tomada, mas não substitui a revalidação antes de publicar.

A aceitação da fonte não valida o conteúdo clínico nem cria uma simulação. Nenhum caso real deste lote foi traduzido, adaptado ou publicado. A contagem de publicações deve ser consultada no banco da aplicação; o relatório de preparação não a infere de arquivos brutos.

Para coletar conteúdo, usar os serviços permitidos pelo PMC: Cloud, OAI-PMH, E-Utilities ou BioC. O antigo OA Web Service foi [desativado em 25/08/2026](https://pmc.ncbi.nlm.nih.gov/tools/oa-service/), portanto um importador novo não deve depender de `oa.fcgi`. O [README do serviço Cloud](https://pmc-oa-opendata.s3.amazonaws.com/README.txt) documenta os arquivos e metadados, incluindo DOI, citação, código de licença, retratação e checksum do XML.

## Outras bases consideradas

| Base | Quantidade e utilidade | Limitação relevante |
| --- | --- | --- |
| [CUPCase](https://huggingface.co/datasets/ofir408/CupCase) | 3.562 casos derivados do Journal of Medical Case Reports/BMC, com diagnóstico livre e alternativas | Os cinco campos distribuídos não incluem PMCID, DOI, URL ou raciocínio; a fonte precisa ser recuperada. Distratores baseados em similaridade ICD não são diferenciais clínicos validados. O depósito declara Apache 2.0, sem substituir a atribuição do material original. [Artigo](https://arxiv.org/abs/2503.06204). |
| [MACCROBAT](https://figshare.com/articles/dataset/MACCROBAT2018/9764942) | 200 relatos com entidades clínicas anotadas; depósito CC BY 4.0 | Foi construído para anotação de entidades, não para pontuação de anamnese. As edições 2018 e 2020 não devem ser somadas como 400 pacientes independentes. |
| [PMC-Patients](https://huggingface.co/datasets/zhengyun21/PMC-Patients) | V2 com 250.294 resumos; versão anterior com cerca de 167 mil | Conjunto CC BY-NC-SA 4.0; não tem diagnóstico separado como no MedCaseReasoning. Não é a primeira escolha para uma instalação com possível uso comercial. |
| [MultiCaRe v3.0.1](https://zenodo.org/records/20416562) | Mais de 98 mil casos, com metadados e imagens | Depósito CC BY-NC-SA 4.0 e licenças individuais variadas. Interessante para futura expansão multimodal; exige extração do diagnóstico e dos critérios de avaliação. |

## Transformação em paciente virtual

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

A meta é **300 casos por especialidade**, com publicação progressiva por lotes após filtros de licença, retratação, completude e viabilidade de entrevista. A elegibilidade quantitativa do acervo completo ainda não foi medida. Novas fontes serão necessárias para atingir a meta de 16.500 casos únicos, e o planejamento deve considerar os déficits de cada especialidade, sem duplicar um caso para preencher cotas.

O processamento deve ocorrer por lotes retomáveis no Terminal/cPanel, com o resultado em banco, sem depender de uma requisição web longa. Etapas: coletar metadados → verificar fonte/licença → extrair fatos → traduzir e adaptar → montar rubrica → validar estrutura e fidelidade → revisar → publicar. Evitar duplicatas pelo identificador da fonte e manter snapshots das consultas.

A tradução, a geração de roteiros e a execução real da DeepSeek dependem da chave de API e precisam de validação com o provedor. A revisão clínica é distinta dos testes de software. Somente casos publicados e aptos devem contar na biblioteca do aluno.
