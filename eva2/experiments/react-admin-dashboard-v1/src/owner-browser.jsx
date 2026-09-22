import React from 'react';
import './owner-browser.css';

const PERSONAL_CHROME = 'http://127.0.0.1:44718';

export default function OwnerBrowser() {
  const localDashboard = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

  return <section className="owner-browser" aria-labelledby="browser-heading">
    <div className="browser-heading"><h2 id="browser-heading">Persoonlijke Chrome</h2></div>
    <p>Deel zelf één bestaand Chrome-tabblad met Eva. In de browserbediening kun je het tabblad
      delen, overnemen, teruggeven en de verbinding intrekken.</p>
    {localDashboard ? <>
      <p>Open dit op de computer waarop Eva’s persoonlijke Chrome draait.
        De knop opent de lokale bediening op deze computer in een nieuw tabblad.</p>
      <a className="browser-control-link" href={PERSONAL_CHROME} target="_blank" rel="noopener noreferrer">
        Open Chrome-bediening
      </a>
    </> : <p>Open het dashboard op de computer waarop Eva’s persoonlijke Chrome draait,
      via het lokale dashboardadres. Op een telefoon, via Home Assistant of op een andere
      computer verwijst een lokale browserlink naar dat apparaat, niet naar Eva’s computer.</p>}
    <p>De actuele verbinding en bediening staan op die pagina. Kies bij delen of teruggeven
      zelf een tabblad in de Playwright-extensie. Na herstel of een onderbreking moet je
      opnieuw expliciet een tabblad kiezen.</p>
    <p>Met ‘Neem over’ stopt Eva’s browseractie. Wacht op de bevestiging voordat je zelf
      verdergaat. Eerdere klikken worden niet teruggedraaid. Als de bedieningspagina
      niet bereikbaar is, kun je de tabdeling intrekken met ‘Disconnect’ in de extensie.</p>
  </section>;
}
