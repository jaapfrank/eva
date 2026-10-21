"""Public vulnerability identifiers must not be misread as telephone numbers."""
import pytest
from eva.adapters.model.research_tools import validate_public_projection
from eva.application.conversation import ConversationConflict


def projection(text):
    return dict(question=text, title='Public vulnerability timeline',
                options=['PHP public security advisories'], public_review='public_unlinked')


@pytest.mark.parametrize('identifier', ['CVE-2026-109618', 'CVE-2026-109619',
    'CVE-1999-0067', 'CVE-2016-7654321', 'CVE-2026-1234567890123456789'])
def test_complete_public_identifier_and_its_advisory_url_are_admitted(identifier):
    assert validate_public_projection(projection(f'When was {identifier} first reported?'))['host_checks'] == 'passed'
    assert validate_public_projection(projection(f'https://nvd.nist.gov/vuln/detail/{identifier}'))['host_checks'] == 'passed'


@pytest.mark.parametrize('private', ['mijn server', 'api_key=not-public', '10.0.0.2',
    'private@example.com', '+31 6 1234 5678', '06-12345678', '/home/private/data',
    'https://router.local/', 'https://example.com/?token=x', '21:30', '2026-10-21',
    'CVE-2026-12345678901234567890', 'prefixCVE-2026-109618', 'CVE-2026-109618-0612345678'])
def test_cve_does_not_hide_private_or_malformed_input(private):
    with pytest.raises(ConversationConflict):
        validate_public_projection(projection('CVE-2026-109618 ' + private))
