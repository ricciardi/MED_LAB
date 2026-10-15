# Pesquisa de bases para casos clínicos reais

Pesquisa realizada em 14/10/2026. A biblioteca executável desta entrega ainda contém oito casos fictícios. Este documento identifica fontes para uma ampliação com relatos reais e descreve a adaptação necessária; não declara que centenas de casos já foram traduzidos, revisados ou publicados.

## Fonte recomendada: MedCaseReasoning

O [MedCaseReasoning, de pesquisadores de Stanford e colaboradores](https://arxiv.org/html/2505.11733v2), contém 14.489 exemplos derivados de relatos clínicos publicados, abrangendo mais de 30 especialidades. Ele separa a apresentação do caso (`case_prompt`), o raciocínio diagnóstico (`diagnostic_reasoning`) e o diagnóstico final (`final_diagnosis`), além de fornecer PMCID e endereço do artigo. Essa estrutura o aproxima do objetivo de avaliar tanto a entrevista quanto a conclusão do estudante.

- [Dados oficiais no Hugging Face](https://huggingface.co/datasets/zou-lab/MedCaseReasoning).
- [Repositório dos autores](https://github.com/kevinwu23/Stanford-MedCaseReasoning).
- Revisão do depósito consultada: `469a5365bc534b5b2b9cfbc52b2ef2a10f43a339`.
- Divisões declaradas: 13.092 treino, 500 validação e 897 teste.
- Campos e uma linha real foram consultados pela API de dados do Hugging Face; não foram enviados dados dos usuários do MED LAB.

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

A aplicação atual já guarda a avaliação em seis dimensões e nota de 0 a 100. Para uma importação, cada caso precisa de rubrica explícita com evidência da fonte. Nenhuma das bases pesquisadas fornece uma rubrica OSCE pronta e validada para todas as perguntas de um aluno. A ausência de informação sobre uma dimensão deve ser tratada como não avaliável, com regra de nota documentada, antes de publicar o caso; não preencher com fatos clínicos inventados. O modelo atual de casos exige seis dimensões e deve ser adaptado para essa situação.

Os tratamentos históricos descritos em um relato, inclusive tentativas que falharam, não devem ser automaticamente promovidos a conduta correta atual. O retorno é formativo e deve permitir revisão docente de um resultado contestado.

## Lote inicial proposto

Começar com **300 casos**, distribuídos por especialidade e dificuldade após o filtro de licença, retratação, completude e viabilidade de entrevista. Esse número é uma meta de curadoria, não uma contagem já importada ou validada. A elegibilidade quantitativa de todo o conjunto ainda não foi medida.

O processamento deve ocorrer por lotes retomáveis no Terminal/cPanel, com o resultado em banco, sem depender de uma requisição web longa. Etapas: coletar metadados → verificar fonte/licença → extrair fatos → traduzir e adaptar → montar rubrica → validar estrutura e fidelidade → revisar → publicar. Evitar duplicatas pelo identificador da fonte e manter snapshots das consultas.

A tradução, a geração de roteiros e a execução real da DeepSeek dependem da chave de API e precisam de validação com o provedor. A revisão clínica é distinta dos testes de software. Somente casos publicados e aptos devem contar na biblioteca do aluno.
