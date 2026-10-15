# Figuras clínicas privadas

As imagens vêm dos artigos originais do PMC. O catálogo relaciona cada `fig`/`graphic` do XML verificado à lista `media_urls` dos metadados oficiais, preservando versão, IDs, licença, atribuição, MD5 de origem e SHA-256 local. A fonte técnica é o [contrato do PMC Cloud](https://pmc-oa-opendata.s3.amazonaws.com/README.txt). Não há busca de imagens na internet, geração de substitutas ou download de links arbitrários contidos nos artigos.

O inventário completo contém **14.476 fontes, 48.273 figuras e 48.338 arquivos de imagem referenciados**. São 662 fontes sem figura, zero referências gráficas sem correspondência e zero falhas de catalogação. Estes números descrevem metadados: não significam que todos os arquivos foram baixados ou aprovados. A triagem textual marcou 49 figuras para conferir privacidade e 75 para conferir direitos específicos; essas categorias podem se sobrepor e não equivalem a revisão dos pixels.

Os originais baixados ficam em `instance/imports/MedCaseReasoning/media/objects/`. Catálogos, atribuições, legendas originais, vínculos e auditorias ficam ao lado, em arquivos privados. A pasta inteira deve permanecer fora de `public_html`; não a mova para `static/`. O aplicativo só entrega bytes por uma rota autenticada, vinculada à consulta do usuário e ao exame solicitado. Até o encerramento, a interface usa legenda factual e atribuição neutra; a referência completa aparece no resultado.

## Catalogar e baixar

```bash
python scripts/prepare_case_media.py
python scripts/prepare_case_media.py --pmcid PMC7550182 --download-limit 3
```

O primeiro comando lê apenas os snapshots locais verificados e o inventário de fontes ativas. `--download-limit` autoriza um número limitado de tentativas de download. Arquivos já íntegros são reutilizados. O download aceita apenas imagens estáticas JPEG, PNG e WebP do bucket oficial, sem redirecionamento, com até 20 MiB e dimensões limitadas; exige correspondência do MD5 publicado. Direitos específicos ou indícios de identificação deixam a figura retida. Metadados textuais embutidos (EXIF/XMP/IPTC/comentários ou equivalentes) também exigem conferência; não são removidos automaticamente.

A licença da imagem é a original do artigo, salvo indicação específica de direitos, que exige conferência. Figuras sob ND permanecem intactas: não são recortadas, retocadas, traduzidas ou redesenhadas. Uma nova legenda descritiva em português não representa tradução da legenda original. A seleção deve manter os avisos, a atribuição e as restrições aplicáveis. Fotografias identificáveis são retidas, sem tentativa automática de apagar identificadores.

## Seleção automática após a ficha estar pronta

```bash
python scripts/review_case_media.py \
  --draft-dir instance/imports/MedCaseReasoning/processed/drafts \
  --workers 4 --limit 50
```

Também é possível fornecer `--cases instance/exports/real_case_drafts.json` e restringir o piloto por `--pmcid` repetido. A chave é lida do ambiente privado da instalação; `DEMO_MODE=false` é obrigatório. **Esta etapa faz chamadas pagas ao DeepSeek e envia os originais selecionados ao provedor.** Não há upload de imagens do aluno ou gravações de consulta.

A rotina usa [`deepseek-flash` com entrada de imagem real](https://api-docs.deepseek.com/guides/vision/). Primeiro seleciona possíveis correspondências entre figuras e exames que já existem na ficha. Depois confere cada original contra o paciente, o momento e o exame. A legenda é definida pelo código apenas com o nome do exame solicitado, sem interpretação ou achados escritos pelo modelo. Uma segunda chamada independente inspeciona somente privacidade, sem texto clínico: marcações pequenas, ilegíveis, datas ou números não explicados impedem aprovação automática.

Todos os critérios precisam ser afirmativos. O vínculo registra `machine_reviewed`, modelo, data, checksum do arquivo e evidência da fonte por hash e posição, sem salvar trechos literais. Esse estado identifica **revisão por máquina**, não revisão humana. O piloto inicial revelou uma data pequena que a primeira chamada deixou passar; a aprovação foi revogada, e a segunda inspeção independente foi acrescentada. Mesmo esse procedimento pode errar e pode reter imagens legítimas; os ensaios mostraram variação entre avaliações. A revisão humana dos rascunhos continua necessária.

A seleção não transforma figuras em novos exames. Fotografia pós-operatória, histologia posterior, esquema, fluxograma, estatística de grupo ou imagem de outro paciente não são associados quando não correspondem ao momento e aos exames disponíveis. Dúvida deixa a imagem pendente e não bloqueia a ficha textual. Os vínculos assistidos já existentes são preservados.

A saída são **cópias privadas** em `instance/imports/MedCaseReasoning/media-prepared-cases/`; não altera o banco, os rascunhos originais, os checkpoints do importador nem a publicação. A incorporação deve conferir a versão da ficha usada na revisão (`caseSha256`), validar `examMedia` e manter o caso como rascunho. Não copie uma ficha antiga sobre uma versão clínica mais recente. A interface serve apenas o vínculo exato entre fonte, paciente, exame e legenda, com revisão de privacidade disponível.

O paralelismo é de 1 a 8 fichas, com padrão 4. Cada ficha é processada em sequência, e as revisões são gravadas pelo coordenador. Erros de transporte/HTTP interrompem novas chamadas; resultados já válidos permanecem. Revisões concluídas são reutilizadas sem chamadas e não consomem o limite do próximo lote geral. `--refresh` refaz a seleção e pode cobrar novamente. Alteração da ficha, fonte ou versão do protocolo invalida o reaproveitamento.

A resposta da etapa de seleção fica em `media/selection-cache/`, com hash do contexto exato e checksum do conteúdo. Trechos de evidência são substituídos por posições e hashes da fonte. Uma retomada pode reutilizar essa seleção sem repetir sua chamada, mesmo quando a etapa visual posterior falhou; imagens ainda pendentes exigem as conferências restantes. IDs duplicados, figuras não permitidas e grupo/nome de exame inválidos geram códigos e índices em `media/reviews/PMC….json`, antes de baixar ou analisar imagens. O cache inválido não é corrigido automaticamente: confira o diagnóstico privado e use `--refresh` apenas quando quiser solicitar outra resposta paga.

O relatório privado `instance/imports/MedCaseReasoning/media-review-report.json` registra requisições, tokens efetivamente informados, cache e latência. A faixa de custo é uma estimativa baseada nas [tarifas publicadas](https://api-docs.deepseek.com/quick_start/pricing/), não o débito confirmado da conta; sem uso completo, a estimativa fica indisponível. Não há promessa de aprovação de todas as figuras ou de custo zero.

## Transporte para cPanel

O pacote de mídia é separado do ZIP do aplicativo e do acervo de textos. Ele contém apenas originais, catálogo, revisões e cache privado da seleção; não inclui chave, usuários, banco ou XML dos artigos.

```bash
python scripts/package_case_media.py export \
  --output dist/medlab-midias-privadas.tar.gz
```

Guarde o SHA-256 exibido com o pacote. No servidor, use uma **pasta de destino nova** dentro da raiz privada da instalação, nunca `public_html`:

```bash
python scripts/package_case_media.py restore \
  --archive /home/USUARIO/transferencias/medlab-midias-privadas.tar.gz \
  --sha256 SHA256_RECEBIDO \
  --media-dir /home/USUARIO/medlab/instance/imports/MedCaseReasoning/media
```

A restauração valida primeiro o hash externo e depois cada arquivo no `MEDIA-MANIFEST.json`, rejeita caminhos estranhos, links e tamanhos excessivos e confere novamente os originais referenciados. Só instala a pasta após todas as verificações. Arquivos recebem modo `0600` e pastas `0700`. O comando recusa sobrescrever uma pasta existente; preserve o progresso/revisões atuais antes de planejar uma atualização. Não há migração de banco nem chamada de API durante exportação/restauração.

O caminho padrão coincide com a instalação; se outro caminho for necessário, configure `CASE_MEDIA_DIR` no ambiente do aplicativo. Restaure também as fichas e o inventário de fontes conforme [CPANEL.md](CPANEL.md). Copiar metadados não comprova que os XML foram verificados no novo servidor: a restauração do acervo de fontes continua separada. O ZIP do aplicativo não publica imagens por conta própria.

O pacote inicial `dist/medlab-midias-privadas.tar.gz` tem 42.072.558 bytes e 62.833 arquivos (169.871.312 bytes descompactados). SHA-256: `8fc1923ed10020fac0076a14f4a60170be89d6b78f05c1afd1bdaf9dad88ebf8`. Sua restauração integral em pasta temporária foi conferida. É um snapshot administrativo; gere outro pacote após novas seleções ou revisões, usando outro nome de saída.
