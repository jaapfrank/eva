import os,json,urllib.request
import sqlalchemy as sa
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
e=sa.create_engine(sqlalchemy_postgres_dsn(read_runtime_secret_file(os.environ['EVA_DATABASE_DSN_FILE'],setting_name='EVA_DATABASE_DSN_FILE')))
with e.connect() as c:
 locks=c.scalar(sa.text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())"))
 active=c.scalar(sa.text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='active'"))
if not (locks==0 and active==0):
 raise RuntimeError('Quiescence or readiness condition failed')
ready=json.load(urllib.request.urlopen('http://127.0.0.1:8080/readyz',timeout=20))
if not (ready['ready']):
 raise RuntimeError('Quiescence or readiness condition failed')
print(json.dumps({'advisory_locks':locks,'other_active_queries':active,'readiness':ready}));e.dispose()
