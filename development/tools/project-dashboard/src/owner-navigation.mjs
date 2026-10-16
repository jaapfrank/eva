// Product navigation never reads the development workboard.
export const ownerSections = Object.freeze([
  { id: 'overview', label: 'Overzicht', icon: '◉', description: 'Eva en de ingangen voor dagelijks gebruik.' },
  { id: 'conversation', label: 'Gesprek', icon: '☏', description: 'Praat met dezelfde Eva via de bestaande privégesprekroute.' },
  { id: 'memory', label: 'Personen & geheugen', icon: '◇', description: 'Bekijk je eigen bronnen en geef een correctie door.' },
  { id: 'work', label: 'Opdrachten & resultaten', icon: '▤', description: 'Volg werk, bekijk een resultaat of stop een opdracht.' },
  { id: 'initiative', label: 'Initiatief & leren', icon: '✦', description: 'Zie wat Eva zelf oppakt en haar laatste afweging.' },
  { id: 'connections', label: 'Verbindingen', icon: '↗', description: 'Actuele controles van de ingestelde onderdelen.' },
]);
export function ownerSection(hash) {
  const selected = new URLSearchParams(hash.split('?')[1] || '').get('section');
  return ownerSections.some(item => item.id === selected) ? selected : 'overview';
}
export function ownerSectionHref(id) {
  return `#/?section=${ownerSections.some(item => item.id === id) ? id : 'overview'}`;
}
