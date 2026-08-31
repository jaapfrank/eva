import { registerHooks } from 'node:module';

const telegramModuleUrl = new URL('../src/integrations/telegram.js', import.meta.url).href;
const testComposition = String.raw`
export const __telegramMediaTest = Object.freeze({
  describeRecentPhotoReference,
  inheritedPhotoForShortFollowup,
  isShortPhotoFollowup,
  localPhotoReply,
  telegramReplyChainPhoto,
  telegramPhotoReferenceRole,
});
export const __telegramTransportTest = Object.freeze({
  TelegramDeliveryError,
  archiveTelegramEnvelope,
  authenticatedTelegramMessageContext,
  async prepareAuthenticatedTelegramTurnForTest({
    ctx, sender, text, agentText = text, extras = {}, replyTo = null, replyEvidence = null,
    onContext = null,
  }) {
    if (!ctx?.message || !ctx?.chat || !ctx?.from || typeof text !== 'string') {
      throw new TypeError('authentieke Telegram-testbeurt mist context of tekst');
    }
    ctx.message.text = text;
    if (!ctx.message.date) ctx.message.date = 1_786_000_000;
    const archived = archiveTelegramEnvelope(ctx);
    if (!archived) throw new Error('authentieke Telegram-testbeurt kon niet archiveren');
    const claim = claimTelegramMessage(ctx.chat.id, ctx.message.message_id);
    bindClaimedTelegramProcessingLease(ctx, claim);
    try {
      const authority = beginTelegramUnderstanding(ctx, sender);
      const messageContext = authenticatedTelegramMessageContext(ctx, extras, {
        authority, requester: sender, originalText: agentText,
      }, {
        userText: text, replyTo, replyEvidence,
      });
      if (typeof onContext === 'function') onContext(messageContext);
      const { handleMessageDetailed } = await import('../mind/agent.js');
      return await handleMessageDetailed({
        channel: 'telegram', sender, text: agentText, replyTo, replyEvidence, messageContext,
      });
    } finally {
      telegramProcessingLeases.delete(ctx);
    }
  },
  currentPreparedTelegramUnderstandingSource,
  recoverFailedTelegramInterpretationHandoffs,
  deliverTelegramTurn,
  drainPreparedTelegramOutbox,
  failTelegramMessageAndRecover,
  resolveTelegramTargets,
  directTurnIdentity,
  directTelegramReplyText,
  publishDirectTelegramReply,
  resumePreparedTelegramTurn,
  settleTelegramTextTurn,
  sendTelegramMedia,
  sendTelegramText,
  issueTelegramDeliveryAuthority,
  verifiedTelegramDeliveryAuthority,
  persistPreparedTelegramReply,
  preparedTelegramResult,
  setBotForTest(value) {
    bot = value;
    if (value) registerMessenger(undefined, reconcileSupersededToFamily);
  },
  telegramTextIntent,
  telegramMediaIntent,
  telegramTextSendOptions,
});
`;

// Deze synchrone loadercompositie bestaat uitsluitend in testprocessen die haar
// expliciet importeren. De production module en haar namespace bevatten geen
// ambient NODE_TEST_CONTEXT-factory of mutable testtransport.
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== telegramModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    const configuredSource = source.replace(
      'const TELEGRAM_SAFE_TEXT_LIMIT = 3800;',
      `const TELEGRAM_SAFE_TEXT_LIMIT = Number.parseInt(
        process.env.EVA_TEST_TELEGRAM_SAFE_TEXT_LIMIT || '3800', 10);`,
    );
    return { ...loaded, source: `${configuredSource}\n${testComposition}` };
  },
});
