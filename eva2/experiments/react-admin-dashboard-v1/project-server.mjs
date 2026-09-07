import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const boardFile = fileURLToPath(new URL('../../design/product/execution-workboard-v1.json', import.meta.url));

// One read-only projection of the same file used by the development workboard.
// No credentials, chat content, filesystem browsing or task mutation endpoint.
export function projectServer() {
  const configure = (server) => {
    server.middlewares.use('/project-state', async (request, response) => {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'application/json; charset=utf-8');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      if (request.method !== 'GET') {
        response.writeHead(405, { Allow: 'GET' }); response.end('{}'); return;
      }
      if (request.headers.origin && request.headers.origin !== `http://${request.headers.host}`) {
        response.writeHead(403); response.end('{}'); return;
      }
      try {
        const raw = await readFile(boardFile);
        const board = JSON.parse(raw);
        const metadata = await stat(boardFile);
        const project = board.audit.projectDashboard;
        if (!project || !Array.isArray(project.capabilities)) throw new Error('project state missing');
        const primary = board.executionTasks.find((task) => task.id === board.activeTaskId);
        if (!primary) throw new Error('active task missing');
        let runtime = { state: 'unknown', checkedAt: new Date().toISOString() };
        try {
          if (!Number.isInteger(project.runtimePort) || project.runtimePort < 1024 || project.runtimePort > 65535) throw new Error('invalid runtime port');
          const check = await fetch(`http://127.0.0.1:${project.runtimePort}/readyz`, { signal: AbortSignal.timeout(1500) });
          const value = await check.json();
          runtime.state = check.ok && value.ready === true ? 'ready' : 'not_ready';
        } catch { /* Unreachable never becomes a cached green result. */ }
        const card = (task) => ({ id: task.id, title: task.title, outcome: task.outcome,
          status: task.status, doneWhen: task.doneWhen, area: task.area });
        response.end(JSON.stringify({
          id: 'project', contract: 'EvaProjectOverview/v1',
          revision: createHash('sha256').update(raw).digest('hex'),
          updatedAt: metadata.mtime.toISOString(), readAt: new Date().toISOString(),
          project, runtime, active: card(primary),
          queued: board.executionTasks.filter((task) => task.status === 'queued').map(card),
          milestones: board.milestones.map(card),
          completed: board.executionTasks.filter((task) => task.status === 'done').map(card),
        }));
      } catch {
        response.writeHead(503); response.end(JSON.stringify({ error: 'Projectbron niet beschikbaar' }));
      }
    });
  };
  return { name: 'eva-project-source', configureServer: configure, configurePreviewServer: configure };
}
