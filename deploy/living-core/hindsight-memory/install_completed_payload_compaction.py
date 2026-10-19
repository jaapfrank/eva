"""Install the version-bound native memory addendum without restarting services."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

NATIVE_INSTALL = r'''import asyncio,hashlib,json,os,re,sys
from pathlib import Path
import hindsight_api
import asyncpg
from hindsight_api.config import _get_raw_config
async def run():
 data=json.load(sys.stdin)
 actual_version=hindsight_api.__version__
 if actual_version!="0.9.1":raise RuntimeError("Native version changed; review payload/retry contracts before installation")
 schema=_get_raw_config().database_schema
 if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",schema):raise RuntimeError("Native schema identifier is invalid")
 sql=data["sql"]
 if hashlib.sha256(sql.encode()).hexdigest()!=data["sql_sha256"]:raise RuntimeError("SQL input hash differs")
 connection=await asyncpg.connect(Path(os.environ["HINDSIGHT_API_DATABASE_URL_FILE"]).read_text().strip())
 try:
  async with connection.transaction():
   columns=await connection.fetch("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=$1 AND table_name='async_operations'",schema)
   expected={"operation_id":"uuid","bank_id":"text","operation_type":"text","status":"text","task_payload":"jsonb","result_metadata":"jsonb"}
   observed={x["column_name"]:x["data_type"] for x in columns}
   if any(observed.get(k)!=v for k,v in expected.items()):raise RuntimeError("Native operation schema differs")
   await connection.execute(sql.replace("__SCHEMA__",schema))
   trigger=await connection.fetchrow("SELECT t.tgenabled::text AS enabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1 AND c.relname='async_operations' AND t.tgname='eva_compact_closed_memory_family'",schema)
   if trigger is None or trigger["enabled"]!="O":raise RuntimeError("Native payload trigger is not enabled")
  print(json.dumps({"status":"installed","contract":"EvaCompletedMemoryPayloadCompaction/v1","nativeVersion":actual_version,"sqlSha256":data["sql_sha256"],"triggerEnabled":True,"backlogSweep":False,"modelCalls":0,"servicesRestarted":0}))
 finally:await connection.close()
asyncio.run(run())
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="Existing native Hindsight container")
    args = parser.parse_args()
    sql = Path(__file__).with_name("completed_payload_compaction.sql").read_text()
    data = {"sql": sql, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()}
    subprocess.run(["docker", "exec", "-i", args.container, "python", "-c", NATIVE_INSTALL],
                   input=json.dumps(data).encode(), check=True)


if __name__ == "__main__":
    main()
