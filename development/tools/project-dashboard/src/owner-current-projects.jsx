import React from 'react';
import { currentProjects } from './owner-current-projects.mjs';

const short = (value, maximum = 280) => typeof value === 'string'
  ? value.length > maximum ? value.slice(0, maximum) + '…' : value : '';
const date = value => typeof value === 'string' && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('nl-NL') : 'Niet vastgelegd';

export default function OwnerCurrentProjects({ page, openProject }) {
  const summary = currentProjects(page.cards);
  return <section aria-label="Open eigen projecten">
    <h3>Waar Eva mee verder wil</h3>
    <p>Deze eigen projecten staan nog open. Dat betekent niet dat Eva ze op dit moment uitvoert.</p>
    {summary.cards.length > 0 && <p>{summary.cards.length} van {summary.openCount} open projecten in dit overzicht.</p>}
    {summary.unknownCount > 0 && <p role="status">Niet alle projectstatussen zijn leesbaar. Daaruit kan geen activiteit of stilte worden afgeleid.</p>}
    {summary.openCount === 0 && summary.unknownCount === 0 && <p>In dit opgehaalde overzicht staan geen open eigen projecten.</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 }}>{summary.cards.map(card => <article className="card" key={card.intent_id}
      aria-label={'Open project: ' + (card.title || 'Titel niet beschikbaar')}
      style={{ borderTop: `5px solid ${card.status === 'attention' ? '#b1842f' : '#397362'}`, minWidth: 0, overflowWrap: 'anywhere' }}>
      <strong>{card.status === 'attention' ? 'Aandacht nodig' : 'Open project'}</strong>
      <h4>{card.title || 'Titel niet beschikbaar'}</h4>
      <p>{short(card.motivation, 200)}</p>
      <p><strong>Volgende verkenning:</strong> {short(card.question) || 'Niet vastgelegd.'}</p>
      <p><strong>Laatste wijziging:</strong> {date(card.updated_at)}</p>
      {card.has_artifact === true && <div style={{ borderLeft: '3px solid #397362', paddingLeft: 12 }}>
        <strong>Opgeslagen bijdrage</strong>
        <p>{short(card.result_preview) || 'Er is een bijdrage opgeslagen; open het project om die te bekijken.'}</p>
        <small>Opslag bewijst nog geen aflevering of nut.</small>
      </div>}
      <button onClick={() => openProject(card.intent_id)}>Project bekijken</button>
    </article>)}</div>
    {summary.openCount > 2 && <p>De overige open projecten staan bij de volledige historie hieronder.</p>}
    {page.truncated && <p>Dit overzicht is begrensd; oudere projecten kunnen ontbreken.</p>}
  </section>;
}
