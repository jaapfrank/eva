"""Separate repair assessment inputs on the existing memory/model protocols."""
import json
import re

from eva.application.conversation import ConversationConflict
from eva.application.memory import PresenceMemory
from eva.contracts.memory import MemoryRecall
from eva.contracts.canonical_json import canonical_sha256


class RepairAssessmentMemory:
    """No candidate/personal-memory recall in an independently scoped assessment.

    This is an empty projection for one authorized work stage, not another
    memory store. The ordinary memory implementation remains unchanged.
    """
    def __init__(self, source, authorize):
        self.source, self.authorize = source, authorize

    def recall_for_presence(self, query):
        source = self.source
        self.authorize(source)
        if any(getattr(query, key) != getattr(source, key) for key in (
                'tenant_id', 'installation_id', 'principal_id', 'conversation_id', 'project_id', 'audience')):
            raise ConversationConflict('repair assessment memory left its original scope')
        return PresenceMemory(recall=MemoryRecall(
            schema_version=1, contract='MemoryRecall/v1', recall_id=query.recall_id,
            query_purpose=query.query_purpose, tenant_id=query.tenant_id,
            principal_id=query.principal_id, conversation_id=query.conversation_id,
            project_id=query.project_id, requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256=canonical_sha256([]), selected=[], excluded_reasons={},
            projection_version='repair-assessment-isolation-v1', scope_sha256=canonical_sha256(query.scope),
            created_at=query.requested_valid_at), confirmed_facts=())

    recall_for_presence_fast = recall_for_presence


def repair_assessment_task(role, *, target_id, sources, observation, candidate_sha256=None,
                           comparison_sha256=None):
    if (role not in {'original_behavior_oracle', 'independent_review'}
            or not isinstance(target_id, str) or not re.fullmatch(r'[0-9a-f]{64}', target_id)
            or not isinstance(sources, dict) or not 1 <= len(sources) <= 8):
        raise ValueError('invalid independent repair work scope')
    payload = {'role': role, 'target_id': target_id, 'sources': sources, 'observation': observation}
    common = ('Je voert een afzonderlijke beoordeling voor Eva uit, via haar bestaande programmeerroute. '
        'Dit is geen gebruikersgesprek. Lees de werkelijk geïnstalleerde genoemde bron en controleer haar hashes. '
        'Je werkruimte is geïsoleerd; verander geen lopende installatie, bevoegdheden of beoordelingsregels. '
        'Geen persoonlijke herinneringen of resultaten van andere taken ophalen. Gebruik alleen de hier '
        'gebonden oorspronkelijke bron en invoer. Begin met manage_own_runtime_repair(action=describe) '
        'om je eigen actuele fase-invoer en volledige oorspronkelijke bron onder '
        '/workspace/repair/source/eva klaar te zetten; je mag geen kandidaat indienen. Bewaar je resultaat in /workspace/repair en lees het terug. '
        'Een geslaagde ontwikkeltest bewijst geen herstel. Als de fout niet veilig te reproduceren of te '
        'beoordelen is: bewaar blocked.json met de concrete ontbrekende bron/controle; verzin geen testbewijs. ')
    if role == 'original_behavior_oracle':
        if candidate_sha256 is not None or comparison_sha256 is not None:
            raise ValueError('original behavior author must not receive a candidate or comparison')
        instructions = ('Maak onafhankelijke gedragsgevallen vanuit de oorspronkelijke bron en werkelijk '
            'waargenomen fout. Je krijgt geen reparatiekandidaat en beoordeelt haar nu ook niet. '
            'Schrijf /workspace/repair/oracle.json als EvaIndependentRuntimeRepairCases/v1 met target_id, '
            'original_case_id en cases (2 tot 16). Elk geval heeft id, driver_source en expected. '
            'driver_source is Python met def run(): die de echte productcode aanroept en een JSON-waarde '
            'teruggeeft; drivercode bevat geen verwachte uitkomst, testverdict of kunstmatig opgewekte fout. '
            'expected is apart {"kind":"value","value":...} of {"kind":"error","error_type_sha256":...}. '
            'Het oorspronkelijke geval moet op de oude bron de waargenomen foutklasse én een genoemde '
            'functie/regel werkelijk raken. De verwachte uitkomst beschrijft correct gedrag en behoudt '
            'die fout niet. Voeg onafhankelijke normale/uitzonderingsgevallen toe die bestaand gedrag '
            'beschermen. Geen netwerk, huisactie, geheimen, echte privégegevens of externe effecten. '
            'Een driver is hoogstens 8192 UTF-8-bytes en een verwachte uitkomst hoogstens 16 KiB. '
            'Je hoeft de onafhankelijke vergelijking niet zelf te starten; de host houdt de verwachte '
            'antwoorden buiten kandidaatprocessen. Rond af met de werkelijk bewaarde vindplaats. ')
    else:
        for value in (candidate_sha256, comparison_sha256):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
                raise ValueError('independent review requires exact candidate/comparison hashes')
        payload.update(candidate_sha256=candidate_sha256, comparison_sha256=comparison_sha256)
        instructions = ('Lees de hostgebonden invoer onder /workspace/repair/review-input: '
            'target.json, candidate.json, oracle.json en comparison.json. Controleer de genoemde hashes '
            'en beoordeel de werkelijke wijziging onafhankelijk. Controleer of de oorspronkelijke fout '
            'betekenisvol is gereproduceerd, het gewenste gedrag klopt, controles niet worden omzeild en '
            'bestaande werking, privacy, rechten, STOP en gegevens intact blijven. Een padfilter of groene '
            'tests zijn geen zelfstandige toestemming. Let op code die testprocessen, imports, verwachte '
            'antwoorden, stdout of uitkomsten vervalst. Schrijf /workspace/repair/review.json met contract '
            'EvaIndependentRuntimeRepairReview/v1, target_id, candidate_sha256, comparison_sha256, '
            'decision (accept of reject), behavioral_reason en findings. Accepteer alleen bewezen herstel; '
            'onvoldoende betekenisvol bewijs is reject met concrete reden. Je keurt je eigen kandidaat '
            'niet goed en installeert niets. ')
    task = common + instructions + '\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(task.encode()) > 60000:
        raise ValueError('independent repair task exceeds current work contract')
    return task
