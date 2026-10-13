let dependencies = null;

function configured(name) {
  const value = dependencies?.[name];
  if (typeof value !== 'function') {
    throw new Error(`isolated web test dependency ${name} is not configured`);
  }
  return value;
}

export function configureIsolatedWebTestDependencies(value) {
  if (dependencies || !value || typeof value !== 'object') {
    throw new Error('isolated web test dependencies may be configured exactly once');
  }
  for (const name of [
    'handleMessage', 'recordDelivery', 'verifyHttpResult',
    'replayHttpResult', 'verifyDelivery',
  ]) {
    if (typeof value[name] !== 'function') {
      throw new Error(`isolated web test dependency ${name} is not configured`);
    }
  }
  dependencies = Object.freeze({ ...value });
}

export function handleMessageDetailed(input) {
  return configured('handleMessage')(input);
}

export function recordDeliveredReply(result, receipt) {
  return configured('recordDelivery')(result, receipt);
}

export function verifiedPreparedUnderstandingTurnForResult(result) {
  return configured('verifyHttpResult')(result);
}

export function preparedUnderstandingIngressReplay(input) {
  return configured('replayHttpResult')(input);
}

export function deliveredReplyRecorded(result) {
  return configured('verifyDelivery')(result);
}
