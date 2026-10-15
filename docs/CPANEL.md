# MED_LAB — instalação e operação no cPanel

O pacote contém a aplicação Flask e os arquivos para publicação por Passenger. A instalação no servidor exige acesso ao cPanel/Terminal, domínio com HTTPS e uma chave DeepSeek configurada no próprio servidor. Preparar e testar o pacote localmente não equivale a publicar a aplicação.

## 1. Verificar a hospedagem

Confirme Python **3.10 ou superior**, ambiente virtual, acesso de saída HTTPS à API DeepSeek e uma das interfaces abaixo. A disponibilidade depende do provedor. O [Application Manager do cPanel](https://docs.cpanel.net/cpanel/software/application-manager/) utiliza Passenger e pode estar desabilitado pelo provedor.

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
python3 scripts/package_release.py
```

O arquivo `dist/medlab-cpanel.zip` contém somente os arquivos de entrega permitidos. O script exclui banco de dados, `.env`, ambientes virtuais, caches, histórico Git e capturas de tela. O `MANIFEST.sha256` interno permite conferir os arquivos extraídos. A versão de origem dos casos SPBench continua incluída para rastreabilidade.

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

| Configuração | Uso em produção |
| --- | --- |
| `SECRET_KEY` | Segredo aleatório próprio da instalação; não reutilizar exemplos. |
| `DEEPSEEK_API_KEY` | Chave do projeto DeepSeek, armazenada somente no servidor. |
| `DEMO_MODE` | `false`. O modo demonstrativo local é explicitamente separado. |
| `ADMIN_PATH` | Prefixo administrativo, por exemplo `/gestao-medlab`. Não incluir barra final. |
| `DATABASE_URL` | URL SQLAlchemy; SQLite privado ou MySQL com PyMySQL. |
| `SESSION_COOKIE_SECURE` | `true` quando o domínio estiver em HTTPS. |

O painel pode aplicar variáveis apenas ao processo web. Os comandos `flask` no Terminal precisam receber a mesma configuração; o `.env` privado compartilhado é uma alternativa quando suportada pela implantação. Nunca coloque senhas/chaves em scripts versionados ou no histórico de comandos.

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

O primeiro comando cria o esquema inicial e importa os casos curados. O segundo solicita os dados da nova conta e a senha de forma interativa. Não existe conta administrativa com senha pública. Use uma conta nova desta instalação.

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

Se surgir erro 500/503, confira versão do Python, ambiente virtual, dependências, variáveis, permissões do banco e o log privado indicado pelo painel. Application Manager documenta `stderr.log` na pasta da aplicação para Python. Não exponha logs ou páginas de debug na internet. Nunca copie chaves para um chamado de suporte.

## 6. Backup, atualização e restauração

Guarde backups de banco, `.env` e código de cada versão em armazenamento privado, fora da pasta pública e fora do ZIP de distribuição. O banco contém contas, conversas e avaliações; defina responsáveis, retenção e controle de acesso de acordo com o uso da instituição. Utilize casos simulados, sem dados identificáveis de pacientes reais.

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

Antes de atualizar: faça backup, reserve uma janela de manutenção e confira as mudanças de esquema. Extraia a nova versão sem sobrescrever `.env` e o banco; atualize dependências, execute apenas a migração indicada para a versão e reinicie Passenger. `init-db` cria tabelas ausentes, mas não substitui migrações de tabelas existentes.

Para restaurar: suspenda escritas, restaure o banco e o código compatíveis, recoloque o `.env` privado, confira permissões e reinicie. Valide login, casos e consultas antes de reabrir. Não use o ZIP de entrega como se fosse um backup de produção.

## 7. Demonstração local e limites de entrega

Para apresentar a interface sem consumir a API, use `DEMO_MODE=true` somente em uma instalação local separada e sem dados reais. Identifique o resultado como demonstrativo. Produção deve manter `DEMO_MODE=false` e chave DeepSeek válida.

A entrega de código e os testes locais não verificam DNS, certificado, disponibilidade de Python/Passenger, limites da hospedagem, credenciais MySQL ou saldo da API. Esses itens precisam ser conferidos na conta de destino. Este guia foi conferido com a documentação oficial do cPanel em outubro de 2026; os nomes dos controles podem variar conforme o provedor.
