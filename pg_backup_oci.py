#!/usr/bin/env python3
# coding: utf-8

import os
from datetime import datetime, timedelta, timezone
import re
import traceback
import logging
from logging.handlers import SysLogHandler, MemoryHandler
import smtplib
from email.mime.text import MIMEText
from jinja2 import Template
import yaml
import psycopg2
from psycopg2 import OperationalError
import oci
from oci.config import validate_config

# Versão do script
VERSION = "1.0"

class AppConfig:
    def __init__(self, yaml_file: str):
        self.start_exec = datetime.now()

        self.script_version = VERSION
        self.script_name = os.path.basename(os.path.abspath(__file__))
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.script_config = os.path.join(self.script_dir, yaml_file)

        self.pg_backup_start_location = None
        self.pg_backup_stop_location = None

        self.oci_compartiment_name = None
        self.oci_volume_display_name = None
        self.oci_volume_size_in_gbs = None
        self.oci_volume_backup_display_name = None
        self.oci_volume_backup_ocid = None
        self.oci_volume_backup_unique_size_in_gbs = None
        self.oci_volume_backup_display_name = "backup_" + self.start_exec.strftime("%Y%m%d%H%M%S")

        self.end_exec = None
        self.duration_exec = None

        self.backup_status = None
        self.logs = None

        with open(self.script_config, "r", encoding='utf-8') as f:
           yaml_config = yaml.safe_load(f)

        self.load_from_yaml(yaml_config)

        # Tratar parâmetros específicos após leitura do arquivo yaml

        # Converter bool em int
        self.database_keepalives = 1 if self.database_keepalives else 0

        # Converter mail_to em lista, se for string
        if isinstance(self.mail_to, str):
            self.mail_to = re.split(r'[,\s]+', self.mail_to.strip())

        # Definir o caminho absoluto do arquivo de template de e-mail
        self.mail_template = os.path.join(self.script_dir, self.mail_template)

        # Validar configuração OCI SDK
        validate_config(self.oci_config)

    def load_from_yaml(self, yaml_config: str, parent_key: str =''):
        """Inicializa as configurações a partir do yaml_config."""
        for key, value in yaml_config.items():
            # Ignorar as sub-chaves yaml de oci.config
            if isinstance(value, dict) and parent_key != "oci_" and key != "config":
                self.load_from_yaml(value, f"{parent_key}{key}_")
            else:
                setattr(self, f"{parent_key}{key}", value)

    def to_dict(self) -> dict:
        """Retorna a configuração como dicionário."""
        return self.__dict__

    def runtime(self):
        """Calcula e registra o tempo de execução."""
        self.end_exec = datetime.now()
        seconds = (self.end_exec - self.start_exec).seconds
        if seconds == 0:
            self.duration_exec = '0 seg.'
        else:
            days, remainder = divmod(seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.duration_exec = '{} {} {} {}'.format(
                "" if int(days) == 0 else str(int(days)) + ' dia(s)',
                "" if int(hours) == 0 else str(int(hours)) + ' hora(s)',
                "" if int(minutes) == 0 else str(int(minutes))  + ' min.',
                "" if int(seconds) == 0 else str(int(seconds))  + ' seg.'
            ).lstrip()

def setup_logger(config: AppConfig) -> logging.Logger:
    """Configura o logger para a aplicação."""
    logger = logging.getLogger(__name__)
    logger.setLevel(config.logging_level)

    default_formatter = logging.Formatter(
        fmt="%(asctime)s %(filename)s[%(process)d]: %(funcName)s:%(lineno)d [%(levelname)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    syslog_formatter = logging.Formatter(
        fmt="%(filename)s[%(process)d]: %(funcName)s:%(lineno)d [%(levelname)s]: %(message)s"
    )

    memory_handler = MemoryHandler(capacity=config.logging_memory_capacity)
    memory_handler.setFormatter(default_formatter)
    logger.addHandler(memory_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(default_formatter)
    logger.addHandler(stream_handler)

    syslog_handler = SysLogHandler(facility=SysLogHandler.LOG_DAEMON, address='/dev/log')
    syslog_handler.setFormatter(syslog_formatter)
    logger.addHandler(syslog_handler)

    return logger

def connect_db(config: AppConfig):
    """Conecta ao banco de dados PostgreSQL."""
    try:
        conn = psycopg2.connect(
            dbname=config.database_name,
            user=config.database_user,
            password=config.database_pass,
            host=config.database_host,
            port=config.database_port,
            connect_timeout=config.database_connect_timeout,
            options=config.database_options,
            keepalives=config.database_keepalives,
            keepalives_idle=config.database_keepalives_idle,
            keepalives_interval=config.database_keepalives_interval,
            keepalives_count=config.database_keepalives_count,
            application_name=config.script_name
        )
        return conn
    except OperationalError as err:
        err.args = (f"Erro ao conectar ao banco de dados:\n{'-'.join(err.args)}",)
        raise err

def execute_sql(conn, sql: str, params: tuple = None):
    """Executa uma consulta SQL no banco de dados PostgreSQL."""
    with conn.cursor() as cur:
        try:
            cur.execute(sql, params)
            return cur.fetchone()
        except Exception as err:
            err.args = (f"Falha na execução de Query:\n{'-'.join(err.args)}",)
            raise err

def pg_is_master(conn):
    """Verifica se o servidor PostgreSQL é o Master/Primário."""
    pg_is_in_recovery = execute_sql(conn, "SELECT pg_is_in_recovery();")
    if pg_is_in_recovery and pg_is_in_recovery[0]:
        raise Exception("Servidor está em modo Standby. As ações são exclusivas para host/nó Master/Primário")

def pg_backup_start(conn, volume_backup_display_name: str):
    """Inicia o backup do PostgreSQL."""
    pg_backup_start_location = execute_sql(
        conn,
        "SELECT pg_backup_start(label => %s, fast => true);",
        (volume_backup_display_name,)
    )
    return pg_backup_start_location[0]

def pg_backup_stop(conn):
    """Finaliza o backup do PostgreSQL."""
    return execute_sql(conn, "SELECT * FROM pg_backup_stop(wait_for_archive => true);")

def oci_get_compartiment(config: AppConfig):
    """Obtém o compartimento OCI."""
    client = oci.identity.IdentityClient(config.oci_config)
    response = client.get_compartment(compartment_id=config.oci_compartiment_ocid)
    data = response.data

    if data.lifecycle_state == 'ACTIVE':
        if data.is_accessible:
            config.oci_compartiment_name = data.name
        else:
            raise Exception(f"Compartimento está inacessível! Necessário revisar permissões/políticas de acesso. Compartimento OCID: {config.oci_compartiment_ocid}")
    else:
        raise Exception(f"Compartimento não localizado. Compartimento OCID: {config.oci_compartiment_ocid}")

def oci_get_volume(config: AppConfig):
    """Obtém o volume OCI."""
    client = oci.core.BlockstorageClient(config.oci_config)
    response = client.get_volume(volume_id=config.oci_volume_ocid)
    data = response.data

    if data.lifecycle_state == 'AVAILABLE':
        config.oci_volume_display_name = data.display_name
        config.oci_volume_size_in_gbs = data.size_in_gbs
    else:
        raise Exception(f"Block volume não localizado. Block volume OCID: {config.oci_volume_ocid}")

def oci_create_volume_backup(config: AppConfig):
    """Cria o backup do volume OCI."""
    def should_stop_waiting_volume_backup(evaluate_response):
        return evaluate_response.data.lifecycle_state in ['AVAILABLE', 'FAULTY']

    client = oci.core.BlockstorageClient(config.oci_config)
    response = client.create_volume_backup(
        create_volume_backup_details=oci.core.models.CreateVolumeBackupDetails(
            volume_id=config.oci_volume_ocid,
            display_name=config.oci_volume_backup_display_name,
            defined_tags=config.oci_defined_tags,
            freeform_tags={"script": f"{config.script_name} - Ver. {VERSION}"},
            type="INCREMENTAL"
        )
    )

    data = response.data

    wait_response = oci.wait_until(client, client.get_volume_backup(data.id), evaluate_response=should_stop_waiting_volume_backup)

    wait_data = wait_response.data

    if wait_data.lifecycle_state == 'AVAILABLE':
        return [data.id, wait_data.unique_size_in_gbs ]
    else:
        raise Exception(f"Falha ao criar backup do volume! Backup Display Name/OCID: {config.oci_volume_backup_display_name}/{data.id}")

def oci_delete_volume_backup(config: AppConfig, volume_backup_ocid: str) -> bool:
    """Deleta o backup do volume OCI."""
    client = oci.core.BlockstorageClient(config.oci_config)

    client.delete_volume_backup(
        volume_backup_id=volume_backup_ocid
    )

    wait_response = oci.wait_until(client, client.get_volume_backup(volume_backup_ocid), 'lifecycle_state', 'TERMINATED', succeed_on_not_found=True)
    volume_backup_lifecycle_state = wait_response.data.lifecycle_state

    if volume_backup_lifecycle_state == 'TERMINATED' or volume_backup_lifecycle_state == 'SUCCEEDED':
        return True
    else:
        raise Exception(f"Falha ao deletar backup do volume! Backup OCID: {volume_backup_ocid}")

def oci_retention_volume_backups(config: AppConfig, logger: logging.Logger):
    """Gerencia a retenção de backups do volume OCI."""
    client = oci.core.BlockstorageClient(config.oci_config)

    # Listar todos os backups do block volume no compartimento
    volume_backups = client.list_volume_backups(
        compartment_id=config.oci_compartiment_ocid,
        volume_id=config.oci_volume_ocid,
        sort_order="DESC",
        lifecycle_state="AVAILABLE"
    )

    show_header = True
    for volume_backup in volume_backups.data:
        # Calcula a data de expiração do backup
        expiration_date = volume_backup.time_created + timedelta(days=config.oci_retention_days)

        # Verifica se a data de expiração é anterior à data atual
        # NOTA: As datas retornadas pela OCI estão com timezone UTC
        if expiration_date < datetime.now(timezone.utc):
            if show_header:
                logger.info(f"Deletando block volume mantidos a mais de {config.oci_retention_days} dias")
                show_header = False

            if oci_delete_volume_backup(config, volume_backup.id):
                logger.info(f"Sucesso ao deletar backup do volume! Backup block volume OCID: {volume_backup.id}")

def extract_logs_from_memory_handler(logger: logging.Logger) -> str:
    """Extrai logs do MemoryHandler."""
    for handler in logger.handlers:
        if isinstance(handler, MemoryHandler):
            return '\n'.join([handler.format(record) for record in handler.buffer])
    return ""

def render_template(template_path: str, **kwargs) -> str:
    """Renderiza um template."""
    try:
        with open(template_path, 'r', encoding='utf-8') as file:
            template_content = file.read()

        template = Template(template_content)
        return template.render(**kwargs)
    except Exception as err:
        err.args = (f"Erro ao renderizar corpo do e-mail:\n{'-'.join(err.args)}",)
        raise err

def send_mail(config: AppConfig, logger: logging.Logger):
    """Envia um e-mail."""
    config.logs = extract_logs_from_memory_handler(logger)

    body = render_template(config.mail_template, **config.to_dict())

    msg = MIMEText(body, 'html', 'utf-8')
    msg['Subject'] = f"{'SUCESSO' if config.backup_status == 'success' else 'FALHA'}: {config.backup_description}"
    msg['From'] = config.mail_from
    msg['To'] = ','.join(config.mail_to)

    try:
        with smtplib.SMTP(config.mail_host, config.mail_port) as server:
            if config.mail_username and config.mail_password:
                server.login(config.mail_username, config.mail_password)
            server.sendmail(config.mail_from, config.mail_to, msg.as_string())
        logger.info("E-mail enviado com sucesso!")
    except Exception as err:
        err.args = (f"Erro ao enviar e-mail:\n{'-'.join(err.args)}",)
        raise err

def main():
    try:
        config = AppConfig("config.yml")
        logger = setup_logger(config)
        logger.info("Iniciando processamento do backup")
        logger.info("Checando existência, acessibilidade e detalhes do compartimento")
        oci_get_compartiment(config)
        logger.info("Checando existência e detalhes do block volume")
        oci_get_volume(config)

        with connect_db(config) as conn:
            logger.info("Verifica se o servidor PostgreSQL é o Principal/Master")
            pg_is_master(conn)
            logger.info("Iniciando backup online")
            config.pg_backup_start_location = pg_backup_start(conn, config.oci_volume_backup_display_name)
            logger.info(f"Backup - Localização(LSN) inicial: {config.pg_backup_start_location}")
            logger.info("Criando backup do block volume")
            volume_backup = oci_create_volume_backup(config)
            config.oci_volume_backup_ocid = volume_backup[0]
            config.oci_volume_backup_unique_size_in_gbs = volume_backup[1]
            logger.info(f"Backup do block volume criado com sucesso! Backup Display Name/OCID: {config.oci_volume_backup_display_name}/{config.oci_volume_backup_ocid}")

            # Deletar backups antigos
            oci_retention_volume_backups(config, logger)

            logger.info("Finalizando backup online")
            config.pg_backup_stop_location = pg_backup_stop(conn)
            logger.info(f"Backup - Localização(LSN) final: {config.pg_backup_stop_location[0]}")
            logger.info(f"Backup - Arquivo de rótulo de backup:\n{config.pg_backup_stop_location[1]}")

        config.backup_status = "success"
        logger.info("Backup concluído com sucesso")

    except Exception as err:
        logger.error(f"{err}\nTraceback (most recent call last):\n{''.join(traceback.format_tb(err.__traceback__))}")
        config.backup_status = "failure"

    finally:
        # Registrar o fim da execução
        config.runtime()

        # Enviar e-mail informativo
        send_mail(config, logger)

##############################################################################
# Main script
##############################################################################

if __name__ == "__main__":
    main()
