# Postgresql - Backup de block volume em Cloud Oracle OCI

## Objetivo

O objetivo deste script é realizar um backup consistente e seguro do banco de dados PostgreSQL utilizando snapshots de discos (block volumes) na Oracle Cloud Infrastructure (OCI). O script automatiza o processo de criação e gestão dos backups dos volumes de disco, assegurando que todos os dados do PostgreSQL estejam sincronizados no momento do backup. Além disso, ele envia notificações por e-mail detalhando o status do backup, garantindo que a equipe responsável esteja sempre informada. Esse método de backup permite uma recuperação rápida, precisa e confiável, sendo especialmente eficaz para bases de dados de grande porte, superando outros métodos de backup do PostgreSQL.

## Requisitos mínimos

- O diretório de dados do PostgreSQL (Arquivos de bancos de dados, tabelas, arquivos wal, etc) deve residir em um único volume de disco. Isso é crucial para que todos os dados estejam sincronizados na mesma linha do tempo durante a criação do snapshot/backup.

- Evitar o uso de TABLESPACES, para simplicar a estrutura dos dados.

- O usuário de conexão ao PostgreSQL deve ter permissões adequadas para iniciar e finalizar backups (pg_backup_start e pg_backup_stop).

- O script deve ter permissão para acessar e criar backups de volume na OCI.

## Configurações

### PostgreSQL

1. Criar usuário comum com o seguinte comando:

   ```console
   # sudo -iu postgres createuser --pwprompt backup_db
   ```

2. Permissões especiais para o usuário:

   ```console
   # sudo -iu postgres psql -c "GRANT EXECUTE ON FUNCTION pg_backup_start(text, boolean) to backup_db; GRANT EXECUTE ON FUNCTION pg_backup_stop(boolean) to backup_db;"
   ```

### Cloud Oracle OCI

Deve-se criar uma conta com privilégios mínimos:

> **Note**
>
> **Referências:**
> - [Required Keys and OCIDs](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm#Required_Keys_and_OCIDs)
> - [Advanced Policy Features](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policyadvancedfeatures.htm)
> - [Detalhes para Combinações de Verbo + Tipo de Recurso (volume-family)](https://docs.oracle.com/pt-br/iaas/Content/Identity/Reference/corepolicyreference.htm#For3)
> - [Controle de Acesso Baseado em Tag](https://docs.oracle.com/pt-br/iaas/Content/Identity/Concepts/policyadvancedfeatures.htm#TagBased_Access_Control)

1) Console Oracle OCI

2) Criar grupo:
   - Identity & Security -> Identity -> Domains -> Selecione o Compartimento **root** -> Selecionar o domínio **`Default`** -> Groups -> Create group:
     - Name: `backup_db`
     - Description: `Grupo de usuários com acesso para realizar backup de volume de block de bancos de dados`

3) Criar e configurar usuário:
   - Identity & Security -> Identity -> Domains -> Selecione o Compartimento **root** -> Selecionar o domínio **`Default`** -> Users -> Create User:
     - Last name: `backup_db_001`
     - Username: `backup_db_001`
     - Use the email address as the username: `Desmarcar`
     - Groups: `backup_db`

   - Edit User Capabilities -> Deixar somente `API Keys`

   - Resources -> API Keys -> Add API Key -> Generate API Key Pair -> Realizar o download das chave "PRIVADA" e "PÚBLICA" -> Add

     > **Warning**
     >
     > **IMPORTANTE:** Armazene os arquivos em um local seguro.

   - Será gerado uma prévia do arquivo de configuração para usar na ferramenta OCI-CLI e ou Biblioteca/API, COPIE o mesmo para facilitar a configuração posterior.

     Abaixo um exemplo:

     ```ini
     [DEFAULT]
     user=ocid1.user.oc1..aaaaaaaa43iyd......
     fingerprint=b2:e8:86:2e:e3:3e:75........
     tenancy=ocid1.tenancy.oc1..aaaaa........
     region=sa-saopaulo-1
     key_file=<path to your private keyfile> # TODO
     ```

4) Criar política de acesso:

   - Identity & Security -> Identity -> Policies -> Selecione o Compartimento **root** -> Create Policy:
     - Name: `BackupDB`
     - Description: Política de acesso ao usuários do grupo `backup_db`
     - Show manual editor: `habilitar`
     - Policy:

       > **Note**
       >
       > - Substitua a string `<COMPARTIMENT-XXX>` com o nome do compartimento em que se encontra o block volume.
       > - Substitua a string `<BACKUP-XXX>` com o valor da Tag Definida `backup.access`. Essa tag será utilizada para restringir o acesso a um block volume específico.
       > - Necessário incluir o verbo `inspect` para os locais `compartiments`, `volumes` e `volume-backups` sem definir tag de acesso, para que seja possível listar todos os backups dos volumes.

       ```txt
       Allow group backup_db to inspect compartments in compartment <COMPARTIMENT-XXX>
       Allow group backup_db to inspect volumes in compartment <COMPARTIMENT-XXX>
       Allow group backup_db to inspect volume-backups in compartment <COMPARTIMENT-XXX>
       Allow group backup_db to use volumes in compartment <COMPARTIMENT-XXX> where target.resource.tag.backup.access = '<BACKUP-XXX>'
       Allow group backup_db to manage volume-backups in compartment <COMPARTIMENT-XXX> where target.resource.tag.backup.access = '<BACKUP-XXX>'
       ```

5) Criar Tag Definida para gerenciar o acesso ao backup de um block volume específico:

   - Governance & Administration -> Tenancy Management -> Tag Namespaces -> Selecione o Compartimento **root** -> Create Tag Namespace:
     - Namespace Definition Name: `backup`
     - Description: `Tags para gerenciar o acesso a backup de block volumes`
   - Acessar o namespace `backup` -> Create Tag Key Definition:
     - Tag Key: `access`
     - Description: `Controle de acesso`

6) Atribuir a tag ao block volume que deseja controlar o acesso:

   > **Note**
    >
    > Substitua o valor da tag `<BACKUP-XXX>` com o valor definido previamente na política de acesso.

   - Storage -> Block Volumes -> Selecione o Compartimento correto -> Acessar o Block Volume que irá receber a nova tag -> Add tags:
     - Tag namespace: `backup`
     - Tag key: `access`
     - Tag value: `<BACKUP-XXX>`

### Script `pg_backup_oci`

1. Dependências:

   Se faz necessário instalar biblioteca para acesso a banco dados PostgreSQL e o SDK da Oracle OCI.

   ```console
   # apt install --no-install-recommends --no-install-suggests python3-pip
   # UMASK=$(umask)
   # umask 022
   # sudo pip3 install --upgrade psycopg2-binary oci
   # umask $UMASK
   ```

2. Realizar o download do projeto para o diretório **/etc**:

   ```console
   # wget https://github.com/cleberb/pg_backup_oci/archive/refs/heads/main.zip
   # unzip main.zip
   # mv pg_backup_oci-main /etc/pg_backup_oci
   # rm -f main.zip
   ```

3. Adequar as permissões do diretório e arquivos:

   ```console
   # chown -R root:root /etc/pg_backup_oci
   # chmod 750 /etc/pg_backup_oci
   # chmod 750 /etc/pg_backup_oci/pg_backup_oci.py
   ```

4. Criar o arquivo de configuração com base no modelo e adequar sua permissão:

   ```console
   # cp -a /etc/pg_backup_oci/{config.example.yml,config.yml}
   # chmod 400 /etc/pg_backup_oci/config.yml
   ```

5. Definir as configurações de `config.yml`:

   Ajustes os valores conforme o ambiente, definindo dados da empresa, uma descrição ao processo de backup, identificando banco de dados e/ou aplicação envolvidos, dados de conexão ao banco de dados, dados de conexão a API da Oracle OCI e dados do block volume que será feito backup, configurações de logs e de e-mail.

   > **Note**
   >
   > O valor do parâmetro `key_content` deve ser o conteúdo do arquivo de chave privada que foi feito download nos passos anteriores.

   Abaixo conteúdo de `config.yml.example`:

   ```yml
   company:
     # Nome da empresa
     name: EMPRESA-XXX
     # Departamento responsável pelo backup
     department: Infraestrutura de T.I.
     # Link para a página principal da empresa
     site: https://www.linkedin.com/in/cleberson-batista/

   backup:
     # Descrição geral do backup, como qual aplicativo ou sistema está sendo protegido
     description: Backup da base de dados do APP XXX.

   database:
     # Nome do banco de dados utilizado para conexão SQL
     name: postgres
     # Usuário de conexão
     user: backup_db
     # Senha do usuário de conexão
     pass: backup_db123
     # Endereço do servidor
     host: localhost
     # Porta do serviço
     port: 5432
     # Tempo limite de conexão inicial
     connect_timeout: 15
     # Habilitar keepalives
     # Valores válidos: [true|false]
     keepalives: true
     # Tempo de inatividade após o último keepalive antes de considerar a conexão morta
     keepalives_idle: 20
     # Intervalo entre os keepalive
     keepalives_interval: 5
     # Máximo número de tentativas de keepalive
     keepalives_count: 5
     # Opções adicionais para a conexão
     #   Notas:
     #     - Suprimir a saída desnecessária com client_min_messages
     #     - pg_backup_start e pg_backup_stop precisam ser executados na
     #       mesma sessão, portanto, deve-se desativar idle_session_timeout
     #       para evitar a interrupção da sessão.
     #     - Desativar statement_timeout para que os comando SQL não sejam interrompidos por tempo de execução
     options: -c client_min_messages=ERROR -c idle_session_timeout=0 -c statement_timeout=0

   oci:
     # Configurações para autenticação na Oracle Cloud Infrastructure (OCI)
     config:
       # OCID do usuário OCI
       user: ocid1.user.oc1..aaaaaaaa2no3......
       # Conteúdo da chave privada
       key_content: |
         -----BEGIN PRIVATE KEY-----
         MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDjOK850mKVVPwP
         r/pa8C4eqtKGU9srwEKv8MWfo7HeJsFXvnHkMvXJwJrXLbGqA+BjCLjlG6KavWkc
         kGBWiNaDg+lpRStKK+LEbPvzysM9dBDNCqr5XV9LZP7I+nJ4jynPB893HVctoNKX
         vLM750x+0hgDWcPPmmsurmk/P0vQY0C72hctIHmWNdwQ/NzS0UnjSIoaecP3BOqv
         ...
         ...
         dykq6/9d7dwH2X4G8XagYE1Xusp9brsCSm761boeBou6LkoPi8wwPAAzNV5ZseCk
         Uk5KNNVZm2dAedYS6pJXEofxWEiE+QgQMlygJ5qOSqLLsfXPXG4YZ5VaoVfmqZoz
         IS4dzW9oOjVWGwtxrjLDkLCK1g==
         -----END PRIVATE KEY-----
       # Fingerprint do par de chaves RSA
       fingerprint: 81:d7:24:cd:1d:05...
       # OCID da tenancy
       tenancy: ocid1.tenancy.oc1..aaaaaaaa356vm2j4st2vky...
       # Identificação da região de acesso
       region: sa-saopaulo-1
     # OCID do compartimento OCI onde o block volume se encontra
     compartiment_ocid: ocid1.compartment.oc1....
     # OCID do volume OCI que será realizado backup
     volume_ocid: ocid1.volume.oc1....
     # Tag definida para identificação e liberação de acesso ao backup do block volume
     defined_tags: { "backup": {"access": "BACKUP-XXX"} }
     # Número de dias de retenção dos os backups no OCI
     retention_days: 7

   logging:
     # Nível de log do script
     # Valores válidos: [DEBUG, INFO, WARNING, ERROR, CRITICAL]
     level: INFO
     # Capacidade de registros de logs para o buffer em memória
     memory_capacity: 1024

   mail:
     # Endereço de origem do e-mail
     from: <send_from@empresa.com.br>
     # Endereço(s) de destinatário(s) do e-mail
     # Mais de um valor, separar por vírgula
     to: <send_to@empresa.com.br>
     # Endereço do servidor SMTP
     host: <smtp.empresa.com.br>
     # Porta do servidor SMTP
     port: 587
     # Usuário para autenticação
     username: <email@empresa.com.br>
     # Senha para autenticação
     password: <senha>
     # Nome do arquivo de template de e-mail
     template: mail.template.j2
   ```

#### Agendamento de Backup

O script de backup deverá ser executado pelo menos uma vez por dia, mas pode ser ajustado de acordo com os requisitos de negócio.

```console
# cat << 'EOF' > /etc/cron.d/pg_backup_oci
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
#MAILTO=email@empresa.com.br
HOME=/

# Rotina de backup PostgreSQL via backup de block volume
00 00 * * * root /etc/pg_backup_oci/pg_backup_oci.py > /dev/null
EOF
```

A execução do script irá gerar um e-mail no formato:

![E-mail informativo](mail.png)

## Referência

- <https://www.postgresql.org/docs/15/continuous-archiving.html#BACKUP-BASE-BACKUP>
- <https://www.cybertec-postgresql.com/en/exclusive-backup-deprecated-what-now/>
- <https://github.com/cybertec-postgresql/safe-backup>
- <https://daemoniolabs.wordpress.com/2011/07/19/entendendo-coprocessos-coproc-do-bash/>
- <https://giovannireisnunes.wordpress.com/2017/11/10/coprocessos-em-bash-parte-2/>
- <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-BACKUP-TABLE>
- <https://infohub.delltechnologies.com/en-us/l/deploying-postgresql-on-dell-powerflex/use-case-2-database-backup-process/>
- <https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING>
- <https://www.postgresql.org/docs/current/runtime-config-client.html>
- <https://docs.oracle.com/en-us/iaas/tools/oci-cli/3.40.3/oci_cli_docs/cmdref/bv/backup/create.html>
- <https://stackoverflow.com/a/75146373>
- <https://docs.python.org/3.10/library/traceback.html#traceback.StackSummary>
- <https://docs.oracle.com/en-us/iaas/tools/python/2.126.3/waiters.html>
- <https://github.com/oracle/oci-python-sdk/blob/master/examples/wait_for_resource_in_state.py>
