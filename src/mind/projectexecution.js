import path from 'node:path';
import { config } from '../config.js';
import {
  artifactReceiptChainMatches,
  verifiedCompleteReceipt,
} from '../util.js';

export function cleanUserText(value) {
  const text = String(value || '').trim();
  const lines = text.split('\n');
  if (lines.length < 2) return text;
  const lastSpeakerLine = [...lines].reverse().find((line) => /^[^:\n]{1,50}:\s+\S/.test(line));
  return lastSpeakerLine ? lastSpeakerLine.replace(/^[^:\n]{1,50}:\s+/, '').trim() : text;
}

export function actionData(tool) {
  try {
    return JSON.parse(String(tool?.output || '')).data || null;
  } catch {
    return null;
  }
}

/** Canonicaliseert alleen bewijs-/displaypaden. Dit is bewust geen fysieke
 * workspace-authoritycheck: die private grens in tools.js resolveert symlinks,
 * dwingt WRITABLE_ROOT af en heeft daarom een ander contract. */
export function canonicalArtifactDisplayPath(value, cwd = '') {
  let item = String(value || '').trim().replace(/\\/g, '/');
  if (!item) return '';
  const root = String(config.root || '').replace(/\\/g, '/').replace(/\/$/, '');
  const workspace = String(config.workspaceDir || '').replace(/\\/g, '/').replace(/\/$/, '');
  const asWorkspaceDisplayPath = (candidate) => {
    if (!workspace) return candidate;
    if (candidate === workspace) return 'data/workspace';
    return candidate.startsWith(`${workspace}/`)
      ? `data/workspace/${candidate.slice(workspace.length + 1)}`
      : candidate;
  };
  item = asWorkspaceDisplayPath(item);
  if (root && item.startsWith(`${root}/`)) item = item.slice(root.length + 1);
  if (item.startsWith('/')) return path.posix.normalize(item);
  if (item.startsWith('data/workspace/')) return path.posix.normalize(item);
  let base = String(cwd || '').trim().replace(/\\/g, '/');
  base = asWorkspaceDisplayPath(base);
  if (root && base.startsWith(`${root}/`)) base = base.slice(root.length + 1);
  if (!base) base = 'data/workspace';
  return path.posix.normalize(path.posix.join(base, item));
}

export function writtenArtifactPath(tool) {
  // Het gebonden uitvoeringsargument is canoniek; data.path is bewust een
  // mensvriendelijk displaypad en kan bij een afwijkende workspace-root relatief zijn.
  return canonicalArtifactDisplayPath(tool?.args?.pad || actionData(tool)?.path || '');
}

export function readArtifactPath(tool) {
  return canonicalArtifactDisplayPath(tool?.args?.pad || '');
}

export function executedArtifactPath(tool) {
  const args = Array.isArray(tool?.args?.argumenten) ? tool.args.argumenten : [];
  const candidate = args.find((argument) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(String(argument || '').trim()));
  return candidate ? canonicalArtifactDisplayPath(candidate, tool?.args?.werkmap || '') : '';
}

export function isTestArtifactPath(value = '') {
  const name = canonicalArtifactDisplayPath(value).split('/').pop() || '';
  return /(?:^|[._-])(?:tests?|proef|proeven)(?:[._-]|$)/i.test(name);
}

function preferredExecutableWriteIndexes(toolResults = []) {
  const indexes = toolResults
    .map((tool, index) => ({ tool, index }))
    .filter(({ tool }) => tool.name === 'schrijf_bestand' && verifiedCompleteReceipt(tool))
    .filter(({ tool }) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(tool)));
  const source = indexes.filter(({ tool }) => !isTestArtifactPath(writtenArtifactPath(tool)));
  return (source.length ? source : indexes).map(({ index }) => index).reverse();
}

export function successfulZeroExit(tool) {
  if (!verifiedCompleteReceipt(tool)) return false;
  return Number(actionData(tool)?.exit_code) === 0;
}

function sameIssuedRequirement(tool, plan) {
  const requirementId = String(plan?.requirementId || '');
  return Boolean(requirementId)
    && String(tool?.semanticBinding?.requirementId || '') === requirementId;
}

export function executableWorkspaceArtifact(value = '') {
  // Alleen runtimes die de capabilityregistry zonder transpileerstap kan
  // uitvoeren krijgen een uitvoeringsbewijscontract.
  return /\.(?:cjs|js|mjs|py)$/i.test(canonicalArtifactDisplayPath(value));
}

function syntaxOnlyArtifactExecution(tool) {
  const args = Array.isArray(tool?.args?.argumenten) ? tool.args.argumenten.map(String) : [];
  const artifactIndex = args.findIndex((argument) => (
    /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(String(argument || '').trim())
  ));
  return (artifactIndex < 0 ? args : args.slice(0, artifactIndex))
    .some((argument) => ['--check', '--syntax-check', '-c', '-n'].includes(argument));
}

function materialWriteSha(tool) {
  const value = String(actionData(tool)?.material_sha256 || '');
  return /^[a-f0-9]{64}$/u.test(value) ? value : '';
}

/** Een identieke no-op-write is wel bewijs van actuele bytes, maar geen nieuwe
 * materialisatie die eerdere hashgebonden runs kunstmatig veroudert. */
function effectiveMaterialWrite(writes, predicate) {
  const candidates = writes.filter(({ tool }) => predicate(tool));
  const latest = candidates.at(-1) || null;
  if (!latest || latest.tool.changed === true) return latest;
  const latestSha = materialWriteSha(latest.tool);
  if (!latestSha) return latest;
  return candidates.findLast(({ tool }) => (
    tool.changed === true && materialWriteSha(tool) === latestSha
  )) || latest;
}

/** De host leidt de programmeerbewijsstaat uitsluitend uit gebonden receipts
 * af. Bestandsnamen of antwoordtekst bepalen geen authority of voltooiing. */
export function iterativeArtifactStatus(toolResults = [], plan = null) {
  const targetPath = canonicalArtifactDisplayPath(plan?.args?.pad || '');
  const bound = toolResults.map((tool, index) => ({ tool, index }))
    .filter(({ tool }) => sameIssuedRequirement(tool, plan));
  const writes = bound.filter(({ tool }) => (
    tool.name === 'schrijf_bestand' && verifiedCompleteReceipt(tool)
  ));
  const sourceWrite = effectiveMaterialWrite(
    writes, (tool) => writtenArtifactPath(tool) === targetPath,
  );
  if (!sourceWrite) return { complete: false, next: 'write', targetPath };
  const executable = executableWorkspaceArtifact(targetPath);
  if (!executable) {
    const read = bound.findLast(({ tool, index }) => (
      index > sourceWrite.index && tool.name === 'lees_bestand'
      && verifiedCompleteReceipt(tool) && readArtifactPath(tool) === targetPath
    ));
    const complete = Boolean(read && artifactReceiptChainMatches({
      write: sourceWrite.tool, read: read.tool, path: targetPath,
      normalizePath: canonicalArtifactDisplayPath,
    }));
    return {
      complete, next: complete ? null : read ? 'write' : 'readback',
      targetPath, sourceWrite, read,
    };
  }
  const testWrite = effectiveMaterialWrite(writes, (tool) => {
    const artifact = writtenArtifactPath(tool);
    return artifact !== targetPath && executableWorkspaceArtifact(artifact)
      && isTestArtifactPath(artifact);
  });
  if (!testWrite) return { complete: false, next: 'write', targetPath, sourceWrite };
  const testPath = writtenArtifactPath(testWrite.tool);
  const testRun = bound.findLast(({ tool, index }) => (
    index > Math.max(sourceWrite.index, testWrite.index)
    && tool.name === 'voer_uit' && successfulZeroExit(tool)
    && executedArtifactPath(tool) === testPath
  ));
  if (!testRun) return {
    complete: false, next: 'run', runTarget: testPath, targetPath, sourceWrite, testWrite,
  };
  if (!artifactReceiptChainMatches({
    write: testWrite.tool, run: testRun.tool, path: testPath,
    normalizePath: canonicalArtifactDisplayPath,
  })) return { complete: false, next: 'write', targetPath, sourceWrite, testWrite, testRun };
  const sourceRun = bound.findLast(({ tool, index }) => (
    index > sourceWrite.index
    && tool.name === 'voer_uit' && successfulZeroExit(tool)
    && !syntaxOnlyArtifactExecution(tool)
    && executedArtifactPath(tool) === targetPath
  ));
  if (!sourceRun) return {
    complete: false, next: 'run', runTarget: targetPath,
    targetPath, sourceWrite, testWrite, testRun,
  };
  const finalRunIndex = Math.max(testRun.index, sourceRun.index);
  const sourceRead = bound.findLast(({ tool, index }) => (
    index > Math.max(sourceWrite.index, finalRunIndex)
    && tool.name === 'lees_bestand' && verifiedCompleteReceipt(tool)
    && readArtifactPath(tool) === targetPath
  ));
  const testRead = bound.findLast(({ tool, index }) => (
    index > Math.max(testWrite.index, finalRunIndex)
    && tool.name === 'lees_bestand' && verifiedCompleteReceipt(tool)
    && readArtifactPath(tool) === testPath
  ));
  const sourceHashesMatch = sourceRead && artifactReceiptChainMatches({
    write: sourceWrite.tool, run: sourceRun.tool, read: sourceRead.tool, path: targetPath,
    normalizePath: canonicalArtifactDisplayPath,
  });
  const testHashesMatch = testRead
    && artifactReceiptChainMatches({
      write: testWrite.tool, run: testRun.tool, read: testRead.tool, path: testPath,
      normalizePath: canonicalArtifactDisplayPath,
    })
    && artifactReceiptChainMatches({
      write: testWrite.tool, run: sourceRun.tool, read: testRead.tool, path: testPath,
      normalizePath: canonicalArtifactDisplayPath,
    });
  const complete = Boolean(sourceHashesMatch && testHashesMatch);
  const readTarget = !sourceRead ? targetPath : !testRead ? testPath : null;
  return {
    complete,
    next: complete ? null : readTarget ? 'readback' : 'write',
    readTarget,
    targetPath,
    testPath,
    sourceWrite,
    testWrite,
    testRun,
    sourceRun,
    read: sourceRead,
    sourceRead,
    testRead,
  };
}

export function iterativeArtifactEvidence(toolResults = [], plan = null) {
  const status = iterativeArtifactStatus(toolResults, plan);
  if (!status.complete) return '';
  const artifacts = [...new Set([status.targetPath, status.testPath].filter(Boolean))].join(' en ');
  return status.sourceRun
    ? `${artifacts} zijn geschreven, uitgevoerd met exitcode 0 en daarna exact teruggelezen.`
    : `${artifacts} is geschreven en daarna exact teruggelezen.`;
}

function lastExecutionReceiptIndex(receipts, predicate) {
  for (let index = receipts.length - 1; index >= 0; index -= 1) {
    if (predicate(receipts[index])) return index;
  }
  return -1;
}

function verifiedExecutionReceipt(receipt, alias) {
  return receipt?.alias === alias
    && verifiedCompleteReceipt(receipt.result);
}

/** Acceptatiestatus en herstelstrategie blijven gescheiden. Een mislukte
 * uitvoering wordt eerst geïnspecteerd en gerepareerd; blind herhalen kan zo
 * geen bewijs- of pogingbudget opsouperen. */
export function iterativeExecutionObligation(context, current) {
  if (!context?.recovery) return { lane: 'proof', type: current.next };
  const receipts = Array.isArray(context.receipts) ? context.receipts : [];
  const uncertainWriteIndex = lastExecutionReceiptIndex(receipts, (receipt) => (
    /_(?:write|repair)$/u.test(String(receipt?.alias || ''))
    && receipt.result?.effect_uncertain === true
  ));
  if (uncertainWriteIndex >= 0) {
    const afterUncertainWrite = receipts.slice(uncertainWriteIndex + 1);
    const inspected = afterUncertainWrite.some((receipt) => (
      verifiedExecutionReceipt(receipt, 'execution_recovery_inspect')
    ));
    if (!inspected) {
      const attemptedInspection = afterUncertainWrite.some((receipt) => (
        receipt?.alias === 'execution_recovery_inspect'
      ));
      return { lane: 'recovery', type: attemptedInspection ? 'repair' : 'inspect' };
    }
  }
  const failedReadIndex = lastExecutionReceiptIndex(receipts, (receipt) => (
    /_(?:readback|inspect)$/u.test(String(receipt?.alias || ''))
    && (receipt.result?.ok === false || receipt.result?.verified === false)
  ));
  if (failedReadIndex >= 0) {
    const failedRead = receipts[failedReadIndex];
    const repaired = receipts.slice(failedReadIndex + 1).some((receipt) => (
      verifiedExecutionReceipt(receipt, 'execution_recovery_repair')
    ));
    if (!repaired) {
      return {
        lane: 'recovery',
        type: failedRead.alias === 'execution_proof_readback' ? 'inspect' : 'repair',
      };
    }
  }
  const failedRunIndex = lastExecutionReceiptIndex(receipts, (receipt) => (
    /_(?:run|retest)$/u.test(String(receipt?.alias || ''))
    && (receipt.result?.ok === false || receipt.result?.verified === false)
  ));
  if (failedRunIndex >= 0) {
    const afterFailure = receipts.slice(failedRunIndex + 1);
    const repaired = afterFailure.some((receipt) => (
      verifiedExecutionReceipt(receipt, 'execution_recovery_repair')
    ));
    if (!repaired) {
      const inspected = afterFailure.some((receipt) => (
        verifiedExecutionReceipt(receipt, 'execution_recovery_inspect')
      ));
      return { lane: 'recovery', type: inspected ? 'repair' : 'inspect' };
    }
  }
  return {
    lane: 'recovery',
    type: ({
      write: 'repair', run: 'retest', readback: 'inspect', inspect: 'inspect',
    })[current.next] || current.next,
  };
}

export function programmingAcceptanceStatus(toolResults = [], userText = '') {
  const text = cleanUserText(userText);
  const needsTests = /\b(?:gerichte\s+)?tests?\b/i.test(text);
  const needsCli = /\b(?:echte|werkelijke)\s+(?:cli|command[ -]?line)(?:-?run|uitvoer)\b|\bcontroleer\w*.{0,60}\bcli(?:-?run|uitvoer)\b/i.test(text);
  const executions = toolResults.filter((tool) => tool.name === 'voer_uit' && successfulZeroExit(tool));
  const isTest = (tool) => {
    const file = executedArtifactPath(tool).split('/').pop() || '';
    const output = `${actionData(tool)?.stdout || ''}\n${actionData(tool)?.stderr || ''}`;
    return isTestArtifactPath(file)
      || /\b(?:Ran\s+\d+\s+tests?|tests?\s+(?:passed|geslaagd)|pytest|unittest)\b/i.test(output);
  };
  const testExecution = [...executions].reverse().find(isTest) || null;
  const cliExecution = [...executions].reverse().find((tool) => {
    if (isTest(tool)) return false;
    const args = Array.isArray(tool.args?.argumenten) ? tool.args.argumenten.map(String) : [];
    const output = `${actionData(tool)?.stdout || ''}\n${actionData(tool)?.stderr || ''}`;
    if (/\b(?:CLI[- ]CONTROLE:\s*OK|(?:afzonderlijke\s+)?CLI[- ]run\s+geslaagd|echte\s+CLI[- ]run\s+geslaagd)\b/i.test(output)) {
      return true;
    }
    const sourceIndex = args.findIndex((arg) => /\.(?:py|m?js|cjs)$/i.test(arg));
    if (sourceIndex < 0 || args.includes('--help') || args.includes('-h')) return false;
    return args.slice(sourceIndex + 1).some((arg) => !arg.startsWith('-'));
  }) || null;
  return {
    needsTests,
    needsCli,
    testExecution,
    cliExecution,
    complete: (!needsTests || !!testExecution) && (!needsCli || !!cliExecution),
  };
}

export function completedArtifact(toolResults = [], { requireExecution = true, path: requiredPath = '' } = {}) {
  const preferredIndexes = requireExecution && !requiredPath
    ? preferredExecutableWriteIndexes(toolResults)
    : [...toolResults.keys()].reverse();
  for (const writeIndex of preferredIndexes) {
    const write = toolResults[writeIndex];
    if (write.name !== 'schrijf_bestand' || !verifiedCompleteReceipt(write)) continue;
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) continue;
    if (requiredPath && artifactPath !== canonicalArtifactDisplayPath(requiredPath)) continue;
    let executeIndex = writeIndex;
    if (requireExecution) {
      executeIndex = toolResults.findIndex((tool, index) => (
        index > writeIndex
        && tool.name === 'voer_uit' && successfulZeroExit(tool)
        && executedArtifactPath(tool) === artifactPath
      ));
      if (executeIndex < 0) continue;
    }
    const readIndex = toolResults.findIndex((tool, index) => (
      index > executeIndex
      && tool.name === 'lees_bestand' && verifiedCompleteReceipt(tool)
      && readArtifactPath(tool) === artifactPath
    ));
    if (readIndex < 0) continue;
    if (!artifactReceiptChainMatches({
      write,
      ...(requireExecution ? { run: toolResults[executeIndex] } : {}),
      read: toolResults[readIndex], path: artifactPath,
      normalizePath: canonicalArtifactDisplayPath,
    })) continue;
    return {
      write,
      execute: requireExecution ? toolResults[executeIndex] : null,
      read: toolResults[readIndex],
      path: artifactPath,
    };
  }
  return null;
}

export function pendingExecutableArtifactPath(toolResults = []) {
  for (const index of preferredExecutableWriteIndexes(toolResults)) {
    const tool = toolResults[index];
    if (tool.name !== 'schrijf_bestand' || !verifiedCompleteReceipt(tool)) continue;
    const artifactPath = writtenArtifactPath(tool);
    if (!/\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath)) continue;
    if (!completedArtifact(toolResults, { requireExecution: true, path: artifactPath })) return artifactPath;
  }
  return '';
}

export function buildMissionComplete(toolResults = [], requireExecution = true) {
  return !!completedArtifact(toolResults, { requireExecution })
    && (requireExecution || !pendingExecutableArtifactPath(toolResults));
}

function separateProgrammingFixtureRequired(userText = '') {
  return /\b(?:apart|afzonderlijk|los)\w*.{0,40}\b(?:voorbeeld|invoer|fixture)[-_ ]?bestand\b|\b(?:voorbeeld|invoer|fixture)[-_ ]?bestand\b.{0,40}\b(?:apart|afzonderlijk|los)\w*/i
    .test(cleanUserText(userText));
}

export function phasedProgrammingBuildStatus(toolResults = [], userText = '') {
  const acceptance = programmingAcceptanceStatus(toolResults, userText);
  const applicable = acceptance.needsTests || acceptance.needsCli;
  const sourceWriteIndex = preferredExecutableWriteIndexes(toolResults)[0] ?? -1;
  const sourcePath = sourceWriteIndex >= 0
    ? writtenArtifactPath(toolResults[sourceWriteIndex])
    : '';
  const testWriteIndex = toolResults.findLastIndex((tool, index) => (
    index > sourceWriteIndex
    && tool.name === 'schrijf_bestand' && verifiedCompleteReceipt(tool)
    && isTestArtifactPath(writtenArtifactPath(tool))
  ));
  const testExecutionIndex = toolResults.findLastIndex((tool, index) => (
    index > Math.max(sourceWriteIndex, testWriteIndex)
    && tool.name === 'voer_uit' && successfulZeroExit(tool)
    && isTestArtifactPath(executedArtifactPath(tool))
  ));
  const needsFixture = acceptance.needsCli && separateProgrammingFixtureRequired(userText);
  const fixtureAfterIndex = Math.max(sourceWriteIndex, acceptance.needsTests ? testExecutionIndex : -1);
  const fixtureWriteIndex = toolResults.findLastIndex((tool, index) => (
    index > fixtureAfterIndex
    && tool.name === 'schrijf_bestand' && verifiedCompleteReceipt(tool)
    && /\.(?:json|csv|txt|ya?ml)$/i.test(writtenArtifactPath(tool))
  ));
  const cliAfterIndex = Math.max(
    sourceWriteIndex,
    acceptance.needsTests ? testExecutionIndex : -1,
    needsFixture ? fixtureWriteIndex : -1,
  );
  const cliExecutionIndex = toolResults.findLastIndex((tool, index) => {
    if (index <= cliAfterIndex || tool.name !== 'voer_uit' || !successfulZeroExit(tool)) return false;
    const candidate = programmingAcceptanceStatus([tool], 'controleer een echte CLI-run');
    return !!candidate.cliExecution;
  });
  const finalExecutionIndex = Math.max(testExecutionIndex, cliExecutionIndex);
  // Een finale readback bewijst wat er ná de uitvoeringen werkelijk is blijven
  // staan. Bij een apart proefbestand geldt diezelfde grens voor beide files.
  const sourceReadIndex = toolResults.findLastIndex((tool, index) => (
    index > Math.max(sourceWriteIndex, finalExecutionIndex)
    && tool.name === 'lees_bestand' && verifiedCompleteReceipt(tool)
    && readArtifactPath(tool) === sourcePath
  ));
  const testPath = testWriteIndex >= 0 ? writtenArtifactPath(toolResults[testWriteIndex]) : '';
  const testReadIndex = toolResults.findLastIndex((tool, index) => (
    index > Math.max(testWriteIndex, finalExecutionIndex)
    && tool.name === 'lees_bestand' && verifiedCompleteReceipt(tool)
    && readArtifactPath(tool) === testPath
  ));
  return {
    applicable,
    sourcePath,
    sourceWriteIndex,
    testWriteIndex,
    testExecutionIndex,
    needsFixture,
    fixtureWriteIndex,
    cliExecutionIndex,
    sourceReadIndex,
    testReadIndex,
    complete: applicable
      && sourceWriteIndex >= 0
      && (!acceptance.needsTests || (testWriteIndex >= 0 && testExecutionIndex >= 0))
      && (!needsFixture || fixtureWriteIndex >= 0)
      && (!acceptance.needsCli || cliExecutionIndex >= 0)
      && sourceReadIndex >= 0
      && (!acceptance.needsTests || testReadIndex >= 0),
  };
}

/** @param {{
 * result: any,
 * turnId: any,
 * understandingSha256: any,
 * requirementId: any,
 * observedConsumptions?: readonly any[],
 * consumptions?: readonly any[],
 * }} input */
export function issuedExecutionTraceMetadata({
  result, turnId, understandingSha256, requirementId,
  observedConsumptions = [], consumptions = [],
}) {
  return {
    internalOnly: true,
    turnId,
    understandingSha256,
    requirementId,
    status: result.status,
    reason: result.reason || null,
    rounds: result.rounds,
    aliases: result.receipts.map((receipt) => receipt.alias),
    results: result.receipts.map((receipt) => ({
      ok: receipt.result?.ok === true,
      verified: receipt.result?.verified === true,
      failure_class: receipt.result?.failure_class || null,
    })),
    trace: result.trace.slice(-12).map((event) => ({
      type: event.type,
      round: event.round,
      alias: event.alias || null,
      reason: event.reason || null,
      modelRoute: event.fallbackModel ? `${event.model}->${event.fallbackModel}` : undefined,
      obligations: Array.isArray(event.obligations) ? event.obligations : undefined,
      count: Number.isFinite(Number(event.count)) ? Number(event.count) : undefined,
    })),
    observedConsumedRequirementIds: observedConsumptions.map((item) => item.requirementId),
    consumedRequirementIds: consumptions.map((item) => item.requirementId),
  };
}
