"""Private, source-bound evidence for autonomous repair; never exception text."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from pathlib import PurePosixPath
import re

from eva.contracts.canonical_json import canonical_sha256


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
            'Dit is automatisch waargenomen foutbewijs, geen gebruikersopdracht of bewijs '
            'dat de oorzaak al bekend is. Lees de genoemde actuele eigen broncode en '
            'reproduceer de oorzaak in de bestaande geïsoleerde werkruimte. Maak een '
            'gerichte kandidaat en gebruik de onafhankelijke beoordeling en beheerde '
            'toepassing waar beschikbaar. Een voorstel, geslaagde syntaxiscontrole of '
            'afgeronde ontwikkeltaak sluit dit hersteldoel niet: het oorspronkelijke '
            'gedrag moet op de werkelijk geïnstalleerde versie zijn gecontroleerd. '
            'Behoud de goede versie bij verlies of regressie. Herhaal geen eerdere '
            'huisactie, bericht, betaling of andere mogelijk al uitgevoerde handeling. '
            'Wijzig geen bevoegdheid, STOP, beoordelaar of geheime instellingen. '
            'Leg bij ontbrekende beoordeling/toegang het concrete resterende herstelwerk '
            'vast; meld geen installatie of herstel zonder bewijs.\n'
            + json.dumps({'incident_id': incident_id, 'observation': observation.to_payload()},
                         ensure_ascii=False, sort_keys=True))
