import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const dataDir = fs.mkdtempSync(path.join('/tmp', 'eva-agentmail-'));
process.env.EVA_DATA_DIR = dataDir;

const { agentMailStatus, signUpAgentMail, verifyAgentMail } = await import('../src/integrations/agentmail.js');

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  };
}

test('AgentMail signup bewaart het geheim uitsluitend in de kluis en wacht exact op OTP', async () => {
  const seen = [];
  const apiKey = 'am_super_secret_test_key';
  const result = await signUpAgentMail({
    humanEmail: 'owner@example.nl',
    username: 'eva-test',
  }, {
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return response({ organization_id: 'org_1', inbox_id: 'eva-test@agentmail.to', api_key: apiKey });
    },
  });
  assert.equal(seen[0].url, 'https://api.agentmail.to/v0/agent/sign-up');
  assert.equal(result.waiting_for, 'human_otp');
  assert.equal(result.partial, true);
  assert.equal(JSON.stringify(result).includes(apiKey), false);
  const status = agentMailStatus();
  assert.equal(status.configured, true);
  assert.equal(status.account_verified, false);
  assert.equal(status.inbox_id, 'eva-test@agentmail.to');
  assert.equal(status.awaiting_otp, true);
});

test('AgentMail verificatie gebruikt de kluissleutel zonder haar te tonen', async () => {
  let authorization = '';
  const result = await verifyAgentMail('123456', {
    fetchImpl: async (url, options) => {
      assert.equal(url, 'https://api.agentmail.to/v0/agent/verify');
      authorization = options.headers.authorization;
      return response({ verified: true });
    },
  });
  assert.match(authorization, /^Bearer am_/);
  assert.equal(result.verified, true);
  assert.equal(JSON.stringify(result).includes(authorization), false);
  assert.equal(agentMailStatus().awaiting_otp, false);
});
