"""Owner-session protected, bounded restore intake."""

import base64
import binascii
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool

from eva.application.installer_restore import InstallerRestorePort, InactiveRecoveryMaterialRetained
from eva.adapters.api.installer_request_json import read_small_json_request

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
INSTALLER_RESTORE_SECTION = """<section><h2>Reservekopie herstellen</h2><p>Alleen naar de vooraf ingestelde lege doelinstallatie. Bestaande gegevens worden niet overschreven. Aanmeldingen worden niet uit de reservekopie overgenomen.</p><input id="restore-file" type="file"><input id="restore-password" type="password" autocomplete="off" placeholder="Wachtwoord reservekopie"><button id="restore-check" type="button">Controleer reservekopie</button><button id="restore-execute" type="button" disabled>Herstel gecontroleerde reservekopie</button><pre id="restore-result" role="status"></pre></section>"""
INSTALLER_RESTORE_SCRIPT = r"""
(() => {
 const file=document.getElementById('restore-file'), password=document.getElementById('restore-password');
 const check=document.getElementById('restore-check'), execute=document.getElementById('restore-execute'), result=document.getElementById('restore-result');
 let confirmation=null;
 function invalidate(){confirmation=null;execute.disabled=true;}
 file.addEventListener('change',invalidate); password.addEventListener('input',invalidate);
 async function post(path, body){
  const response=await fetch('/internal/v1/onboarding/restore/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
  const value=await response.json(); if(!response.ok){const error=new Error(value.detail || 'Herstel niet beschikbaar');error.recoveryState=value.recovery_state;throw error;} return value;
 }
 check.onclick=async()=>{
  invalidate(); check.disabled=true;
  try {
   const selected=file.files[0]; if(!selected || selected.size>67108864) throw new Error('Kies een reservekopie van maximaal 64 MiB.');
   const bytes=new Uint8Array(await selected.arrayBuffer()); let binary='';
   for(let i=0;i<bytes.length;i+=32768) binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
   const passphrase=password.value; password.value='';
   const value=await post('prepare',{archive: btoa(binary),passphrase});
   confirmation=value.confirmation; execute.disabled=false;
   result.textContent='Gecontroleerd: '+value.summary.records+' gegevens. Nog niets hersteld. '+(value.summary.notice || '')+' Opnieuw aanmelden: '+(value.summary.reauthorization_required.join(', ') || 'geen verbindingen in deze reservekopie')+'.';
  } catch(error){result.textContent=error.message;} finally{check.disabled=false;}
 };
 execute.onclick=async()=>{
  const token=confirmation; invalidate();check.disabled=true;
  try{
   const value=await post('execute',{confirmation:token});
   const state=value.target_activation==='closed' ? 'Eva is nog niet gebruiksklaar en blijft uit totdat de overige herstelstappen zijn afgerond. ' : '';
   result.textContent=value.inserted_records+' gegevens teruggezet. '+state+(value.notice || '')+' Opnieuw aanmelden: '+(value.reauthorization_required.join(', ') || 'geen verbindingen in deze reservekopie')+'.';
  }
  catch(error){result.textContent=error.message+(error.recoveryState==='materialized_inactive' ? '' : ' Controleer opnieuw voordat je nogmaals herstelt.');}
  finally{check.disabled=false;}
 };
})();
"""


def register_installer_restore_routes(
    app: FastAPI,
    *,
    workflow: InstallerRestorePort | None,
    require_write: Callable[[Request], None],
    security_headers: dict[str, str]
) -> None:
    async def invoke(request: Request, *, prepare: bool) -> JSONResponse:
        require_write(request)
        if workflow is None:
            raise HTTPException(409, "Herstel is niet ingesteld voor deze installatie.")
        raw = await read_small_json_request(
            request, MAX_ARCHIVE_BYTES * 4 // 3 + 8192 if prepare else 1024, "restore"
        )
        try:
            if not isinstance(raw, dict):
                raise ValueError()
            if prepare:
                if (
                    set(raw) != {"archive", "passphrase"}
                    or not isinstance(raw["archive"], str)
                    or not isinstance(raw["passphrase"], str)
                    or len(raw["passphrase"]) > 4096
                ):
                    raise ValueError()
                archive = base64.b64decode(raw["archive"], validate=True)
                if not 0 < len(archive) <= MAX_ARCHIVE_BYTES:
                    raise ValueError()
                result = await run_in_threadpool(
                    workflow.prepare, archive, raw["passphrase"]
                )
            else:
                if (
                    set(raw) != {"confirmation"}
                    or not isinstance(raw["confirmation"], str)
                    or len(raw["confirmation"]) > 128
                ):
                    raise ValueError()
                result = await run_in_threadpool(workflow.execute, raw["confirmation"])
        except InactiveRecoveryMaterialRetained as error:
            return JSONResponse({
                "detail": ("Herstel is niet bevestigd. De inactieve bestandskopie is bewaard; Eva blijft uit. "
                    + ("Documentproviders kunnen al gestart zijn in alleen-lezen-modus. Laat de bewaarde doelen en providers controleren; start niet opnieuw op dezelfde doelen."
                       if error.document_connection_attempted else
                       "Documentkoppelingen kunnen gedeeltelijk zijn voorbereid; geheugen en gateway kunnen al zijn geïmporteerd. Laat de bewaarde hersteldoelen controleren; start niet opnieuw op dezelfde doelen."
                       if error.document_preparation_attempted else
                       "Geheugen of gateway kan al gedeeltelijk zijn geïmporteerd. Laat de hersteldoelen controleren; start niet opnieuw op dezelfde doelen."
                       if error.external_import_attempted else
                       "Controle van de bewaarde kopie is nodig voordat je opnieuw kunt herstellen.")),
                "recovery_state": "materialized_inactive",
            }, status_code=409, headers=security_headers)
        except (ValueError, binascii.Error):
            raise HTTPException(
                409,
                "Reservekopie of herstelvoorwaarden ongeldig. Controleer wachtwoord, lege doelinstallatie en herstelbevoegdheid.",
            ) from None
        return JSONResponse(result, headers=security_headers)

    @app.post("/internal/v1/onboarding/restore/prepare")
    async def prepare(request: Request) -> JSONResponse:
        return await invoke(request, prepare=True)

    @app.post("/internal/v1/onboarding/restore/execute")
    async def execute(request: Request) -> JSONResponse:
        return await invoke(request, prepare=False)
