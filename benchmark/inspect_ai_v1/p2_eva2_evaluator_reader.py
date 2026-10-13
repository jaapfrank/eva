"""Exact evaluator read interface; no model, action, task or authority admission."""
from pathlib import Path
import sqlalchemy as sa
import hashlib,json
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from cp001_native import read_eva2_memory_settlement,read_eva2_committed_reply

def open_readers(core_dsn_file,memory_dsn_file,body):
    scope=external_timeline_scope(body['tenant_id'],body['installation_id'],'scope')
    options='-crole=eva_runtime -ceva.tenant_id='+str(scope.tenant_id)+' -ceva.installation_id='+str(scope.installation_id)
    return [sa.create_engine(Path(core_dsn_file).read_text().strip().replace('postgresql://','postgresql+psycopg://'),connect_args={'options':options}),sa.create_engine(Path(memory_dsn_file).read_text().strip().replace('postgresql://','postgresql+psycopg://'))]

def read_native(engines,body,timeout_seconds):
    return {'memory':read_eva2_memory_settlement(*engines,body,timeout_seconds=timeout_seconds),'reply':read_eva2_committed_reply(engines[0],body,timeout_seconds=timeout_seconds)}

def close_readers(engines):
    for engine in engines:engine.dispose()

def verify_connections(engines,body):
    scope=external_timeline_scope(body['tenant_id'],body['installation_id'],'scope')
    with engines[0].connect() as c:
        c.execute(sa.text('SET TRANSACTION READ ONLY'))
        row=c.execute(sa.text("SELECT current_user,current_setting('eva.tenant_id'),current_setting('eva.installation_id'),current_setting('transaction_read_only')")).one()
        if tuple(row)!=('eva_runtime',str(scope.tenant_id),str(scope.installation_id),'on'):raise ValueError('READER_ROLE_SCOPE_READONLY_MISMATCH')
    with engines[1].connect() as c:
        c.execute(sa.text('SET TRANSACTION READ ONLY'))
        if c.execute(sa.text("SELECT current_setting('transaction_read_only')")).scalar_one()!='on':raise ValueError('MEMORY_READER_NOT_READONLY')
    return {'ordinary_role_scope_verified':True,'read_only_verified':True,'memory_engine_unchanged':True}

def baseline_metadata(engines,body,before):
    verified=verify_connections(engines,body)
    if before['reply'] is not None or before['memory']['settled'] is not False:raise ValueError('BASELINE_NOT_EMPTY')
    import cp001_native
    return {'contract':'CanonicalReaderBeforeInput/v1','wrapper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'canonical_sha256':hashlib.sha256(Path(cp001_native.__file__).read_bytes()).hexdigest(),'body_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'static_envelope_sha256':hashlib.sha256(json.dumps({k:v for k,v in body.items() if k!='occurred_at'},sort_keys=True,separators=(',',':')).encode()).hexdigest(),'reply_absent':True,'memory_settled':False,**verified}
