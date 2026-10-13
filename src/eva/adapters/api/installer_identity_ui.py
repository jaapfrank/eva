"""Browser behavior for the loopback-only installer identity workflow."""

INSTALLER_IDENTITY_SCRIPT = r"""
const writable = window.__evaIdentityWritable;
const resume = window.__evaIdentityResume;
const result = document.getElementById('soul-result');
const soul = document.getElementById('soul');
const previewButton = document.getElementById('soul-preview');
const stageButton = document.getElementById('soul-stage');
const issueButton = document.getElementById('soul-issue');
const activateButton = document.getElementById('soul-activate');
const rollbackButton = document.getElementById('soul-rollback');
const newProposalButton = document.getElementById('soul-new-proposal');
let state = {};

async function post(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('geweigerd');
  return response.json();
}

function lockEditor() {
  soul.disabled = true;
  previewButton.disabled = true;
}

if (writable && resume && resume.phase !== 'empty') {
  state = {
    base: resume.base_pack_sha256,
    draft: resume.draft_sha256,
    candidate: resume.candidate_sha256,
    active: resume.candidate_sha256,
  };
  if (resume.phase === 'staged') {
    lockEditor();
    issueButton.disabled = false;
    result.textContent = 'Voorstel teruggevonden. Je kunt nu ondertekenen.';
  } else if (resume.phase === 'issued') {
    lockEditor();
    activateButton.disabled = false;
    result.textContent = 'Ondertekend voorstel teruggevonden. Je kunt nu activeren.';
  } else if (resume.phase === 'active') {
    lockEditor();
    rollbackButton.disabled = false;
    newProposalButton.hidden = false;
    result.textContent = 'De nieuwe identiteit is actief. Je kunt de direct vorige identiteit terugzetten.';
  } else if (resume.phase === 'rolled_back') {
    state = {};
    result.textContent = 'De direct vorige identiteit is teruggezet. Je kunt een nieuw voorstel maken.';
  }
}

previewButton.addEventListener('click', async () => {
  try {
    result.textContent = 'Voorstel controleren…';
    const preview = await post('/internal/v1/identity/soul-import/diff', {
      draft_id: 'installer-preview',
      soul_markdown: soul.value,
    });
    state = {base: preview.base_pack_sha256};
    result.textContent = preview.changes.map(change =>
      `${change.field}:\n  was: ${JSON.stringify(change.before)}\n  wordt: ${JSON.stringify(change.after)}`
    ).join('\n\n');
    if (writable) stageButton.disabled = false;
  } catch {
    result.textContent = 'Dit voorstel is niet geldig. Eva heeft niets bewaard of gewijzigd.';
  }
});

if (writable) {
  stageButton.addEventListener('click', async () => {
    try {
      const staged = await post('/internal/v1/identity/soul-import/stage', {
        draft_id: 'installer-preview',
        soul_markdown: soul.value,
        expected_base_sha256: state.base,
      });
      state = {...state, draft: staged.draft_sha256};
      result.textContent = 'Voorstel veilig bewaard. Het is nog niet ondertekend of actief.';
      issueButton.disabled = false;
      stageButton.disabled = true;
      lockEditor();
    } catch {
      result.textContent = 'Bewaren is geweigerd; de identiteit is niet gewijzigd.';
    }
  });

  issueButton.addEventListener('click', async () => {
    try {
      const issued = await post('/internal/v1/identity/staged/issue', {
        draft_sha256: state.draft,
        expected_base_sha256: state.base,
        confirmation: 'ONDERTEKENEN',
      });
      state = {...state, candidate: issued.candidate_sha256};
      result.textContent = 'Voorstel ondertekend. Het is nog niet actief.';
      activateButton.disabled = false;
      issueButton.disabled = true;
    } catch {
      result.textContent = 'Ondertekenen is geweigerd; de identiteit is niet gewijzigd.';
    }
  });

  activateButton.addEventListener('click', async () => {
    if (!confirm('Wil je deze ondertekende identiteit nu echt activeren?')) return;
    try {
      const activated = await post('/internal/v1/identity/candidate/activate', {
        candidate_sha256: state.candidate,
        expected_current_sha256: state.base,
        confirmation: 'ACTIVEREN',
      });
      state = {...state, active: activated.current_sha256};
      result.textContent = 'De nieuwe identiteit is actief. Je kunt haar hier direct weer terugzetten.';
      rollbackButton.disabled = false;
      newProposalButton.hidden = false;
      activateButton.disabled = true;
    } catch {
      result.textContent = 'Activeren is geweigerd; de huidige identiteit bleef behouden.';
    }
  });

  rollbackButton.addEventListener('click', async () => {
    if (!confirm('Wil je de direct vorige identiteit terugzetten?')) return;
    try {
      await post('/internal/v1/identity/rollback', {
        expected_current_sha256: state.active,
        confirmation: 'TERUGZETTEN',
      });
      result.textContent = 'De direct vorige identiteit is teruggezet.';
      rollbackButton.disabled = true;
      newProposalButton.hidden = true;
    } catch {
      result.textContent = 'Terugzetten is geweigerd; Eva doet geen stille gok.';
    }
  });

  newProposalButton.addEventListener('click', () => {
    state = {};
    rollbackButton.disabled = true;
    newProposalButton.hidden = true;
    soul.disabled = false;
    previewButton.disabled = false;
    result.textContent = 'Je kunt nu een nieuw voorstel voorvertonen.';
  });
}
"""

__all__ = ["INSTALLER_IDENTITY_SCRIPT"]
