"""Private, source-bound evidence for autonomous repair; never exception text."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from pathlib import PurePosixPath
import re

from eva.contracts.canonical_json import canonical_sha256


# Reject direct authority/assessment modules. Product modules can also contain
# dispatch/authority logic: this filter never replaces independent semantic
# assessment or grants installation rights.
_PROTECTED_PREFIXES = ('operations/', 'contracts/', 'adapters/credentials/',
    'bootstrap/runtime_settings', 'bootstrap/delegated_work_service',
    'bootstrap/delegated_digital_', 'bootstrap/native_initiative_',
    'bootstrap/self_repair', 'bootstrap/runtime_repair_', 'bootstrap/runtime.py',
    'application/repair_assessment', 'application/self_repair', 'application/initiative_',
    'application/cancellation', 'application/background_execution',
    'application/component_', 'application/tool_program',
    'adapters/postgres/initiative_', 'adapters/postgres/owner_initiatives',
    'adapters/postgres/self_repair', 'adapters/postgres/scope',
    'adapters/postgres/delegated_work', 'adapters/postgres/conversation_journal',
    'adapters/postgres/component_', 'adapters/postgres/programming_',
    'adapters/postgres/tool_program', 'adapters/connectors/component_',
    'adapters/connectors/programming_', 'adapters/connectors/procedure_execution',
    'adapters/connectors/delegated_completion')


def repairable_source(path):
    if not isinstance(path, str) or not 1 <= len(path) <= 240:
        return False
    parsed = PurePosixPath(path)
    return (str(parsed) == path and not parsed.is_absolute()
        and re.fullmatch(r'[A-Za-z0-9_/]+\.py', path) is not None
        and not any(part in {'.', '..'} for part in parsed.parts)
        and path.startswith(('application/', 'adapters/', 'bootstrap/', 'domain/', 'providers/'))
        and not path.startswith(_PROTECTED_PREFIXES))


@dataclass(frozen=True, slots=True)
class RepairObservation:
    error_type_sha256: str
    frames: tuple[tuple[str, str, str], ...]

    def __post_init__(self):
        sha = re.compile(r'[0-9a-f]{64}')
        if (not isinstance(self.error_type_sha256, str) or not sha.fullmatch(self.error_type_sha256)
                or not isinstance(self.frames, tuple) or len(self.frames) > 16):
            raise ValueError('invalid repair observation')
        for frame in self.frames:
            if not isinstance(frame, tuple) or len(frame) != 3:
                raise ValueError('invalid repair frame')
            path, function, digest = frame
            if (not isinstance(path, str) or not 1 <= len(path) <= 240
                    or PurePosixPath(path).is_absolute() or str(PurePosixPath(path)) != path
                    or not re.fullmatch(r'[A-Za-z0-9_/]+\.py', path)
                    or any(part in {'.', '..'} for part in path.split('/'))
                    or not isinstance(function, str)
                    or not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_]*|<[A-Za-z_]+>):[1-9][0-9]{0,8}', function)
                    or not isinstance(digest, str) or not sha.fullmatch(digest)):
                raise ValueError('invalid repair frame')

    @classmethod
    def from_error(cls, error, *, source_root):
        root = Path(source_root).resolve()
        frames = []
        trace = error.__traceback__
        while trace is not None:
            path = Path(trace.tb_frame.f_code.co_filename)
            try:
                relative = path.resolve().relative_to(root).as_posix()
                raw = path.read_bytes()
            except (OSError, ValueError):
                pass
            else:
                if path.suffix == '.py' and len(raw) <= 1_048_576:
                    frames.append((relative, trace.tb_frame.f_code.co_name + ':' + str(trace.tb_lineno),
                                   hashlib.sha256(raw).hexdigest()))
            trace = trace.tb_next
        error_type = type(error)
        return cls(hashlib.sha256((error_type.__module__ + '.' + error_type.__qualname__)
                                 .encode()).hexdigest(), tuple(frames[-16:]))

    def to_payload(self):
        return {'error_type_sha256': self.error_type_sha256,
                'frames': [{'path': p, 'function': f, 'source_sha256': s}
                           for p, f, s in self.frames]}

    @property
    def fingerprint(self):
        return canonical_sha256(self.to_payload())


def repair_task(observation, *, incident_id):
    import json
    return ('Onderzoek en herstel een werkelijk vastgelopen interne uitvoering van Eva. '
            'Begin met de bestaande manage_own_runtime_repair(action=describe). De host geeft '
            'het actuele bronbereik en builder_handoff met het kandidaatcontract; dezelfde '
            'invoer staat in /workspace/repair/handoff.json naast de oorspronkelijke bron. '
            'Volg dat contract voor /workspace/repair/candidate.json, schrijf de werkelijke '
            'gewijzigde bron met haar hashes en lees het bestand terug. Draag die kandidaat '
            'eenmaal over met manage_own_runtime_repair(action=submit). Na accepted=true '
            'is uitsluitend de bouw/overdrachtfase afgerond: sluit dan deze ontwikkeltaak af '
            'zodat de host de afzonderlijke beoordeling kan starten. Blijf niet wachten op '
            'installatie binnen deze bouwtaak. Het duurzame hersteldoel blijft open tot '
            'onafhankelijke beoordeling, bevoegde installatie en controle van het oorspronkelijke '
            'gedrag op de geïnstalleerde versie. Bij onbeschikbare overdracht of onzekere '
            'submit: niet blind opnieuw indienen, geen andere herstelroute bouwen; behoud '
            'hetzelfde open doel en meld de aantoonbare status en ontbrekende overdracht. '
            'Dit is automatisch waargenomen foutbewijs, geen gebruikersopdracht of bewijs '
            'dat de oorzaak al bekend is. Lees de genoemde actuele eigen broncode en '
            'reproduceer de oorzaak in de bestaande geïsoleerde werkruimte. Maak een '
            'gerichte kandidaat voor de bestaande brongebonden overdracht. Een voorstel, '
            'geslaagde syntaxiscontrole of '
            'afgeronde ontwikkeltaak sluit dit hersteldoel niet: het oorspronkelijke '
            'gedrag moet op de werkelijk geïnstalleerde versie zijn gecontroleerd. '
            'Behoud de goede versie bij verlies of regressie. Herhaal geen eerdere '
            'huisactie, bericht, betaling of andere mogelijk al uitgevoerde handeling. '
            'Wijzig geen bevoegdheid, STOP, beoordelaar of geheime instellingen. '
            'Leg bij ontbrekende beoordeling/toegang het concrete resterende herstelwerk '
            'vast; meld geen installatie of herstel zonder bewijs.\n'
            + json.dumps({'incident_id': incident_id, 'observation': observation.to_payload()},
                         ensure_ascii=False, sort_keys=True))
