import { useState } from "react";

interface Item {
  q: string;
  a: React.ReactNode;
}

const ITEMS: Item[] = [
  {
    q: "What does this site actually do?",
    a: (
      <>
        <p>For every Canadian Member of Parliament, Alberta MLA, and Edmonton/Calgary city councillor we could find a personal or campaign website for, we asked one question: <strong>where in the world is that website's data physically stored?</strong></p>
        <p>The map plots each politician's constituency on the Canadian side, and a pin where their server lives — often in the United States. The lines connecting them visualize where citizens' interactions with their representative actually flow.</p>
      </>
    ),
  },
  {
    q: "How are sites scanned?",
    a: (
      <>
        <p>For each website we run a five-step pipeline:</p>
        <ol>
          <li><strong>DNS</strong> — resolve A records, follow CNAME chains, capture nameservers and MX records.</li>
          <li><strong>GeoIP</strong> — look up the primary IP in MaxMind's GeoLite2 City + ASN databases for country, city, and the network that owns it.</li>
          <li><strong>TLS</strong> — open the HTTPS connection and parse the certificate (issuer, expiry, subject).</li>
          <li><strong>HTTP</strong> — request the page, follow redirects, capture the <code>Server</code> and <code>X-Powered-By</code> headers.</li>
          <li><strong>Classify</strong> — apply pattern matching to identify the hosting provider, any CDN in front, and the CMS, then assign a sovereignty tier.</li>
        </ol>
      </>
    ),
  },
  {
    q: "What do the sovereignty tiers mean?",
    a: (
      <table className="faq__tiers">
        <thead>
          <tr><th>Tier</th><th>Label</th><th>Criteria</th></tr>
        </thead>
        <tbody>
          <tr><td>🍁 1</td><td>Canadian Sovereign</td><td>Canadian-owned hosting + Canadian datacenter</td></tr>
          <tr><td>🇨🇦 2</td><td>Canadian Soil</td><td>Foreign provider but server in Canada (e.g. AWS ca-central-1)</td></tr>
          <tr><td>🌐 3</td><td>CDN-Fronted</td><td>Behind a global CDN like Cloudflare; origin opaque</td></tr>
          <tr><td>🇺🇸 4</td><td>US-Hosted</td><td>Server in the United States, US provider</td></tr>
          <tr><td>🌍 5</td><td>Other Foreign</td><td>Hosted somewhere outside Canada and the US</td></tr>
          <tr><td>❓ 6</td><td>Unknown</td><td>Scan failed or inconclusive</td></tr>
        </tbody>
      </table>
    ),
  },
  {
    q: "Why are some ridings empty on the map?",
    a: (
      <>
        <p>An empty riding means we couldn't find a personal or campaign website for the politician. Reasons include:</p>
        <ul>
          <li>They only have a presence on social media, not a self-hosted site</li>
          <li>They're newly elected and haven't set one up yet</li>
          <li>Their official party caucus page (e.g. liberal.ca/MP-name) is the only digital presence</li>
        </ul>
        <p>Toggle the "Ridings without a website" overlay on the map to see them outlined in gray.</p>
      </>
    ),
  },
  {
    q: "Why doesn't ourcommons.ca show up on the map?",
    a: (
      <>
        <p>The official parliamentary site (<code>www.ourcommons.ca</code>) is shared by all 343 MPs — it's institutional infrastructure, not a personal political choice. Same for <code>www.assembly.ab.ca</code> and the city council pages. We scan and track them, but exclude them from the headline numbers and the map so the personal/campaign sites — where representatives <em>actually choose</em> their hosting — aren't drowned out.</p>
        <p>For the record: ourcommons.ca runs on Microsoft Azure in Toronto.</p>
      </>
    ),
  },
  {
    q: "How can I verify any of this myself?",
    a: (
      <>
        <p>Every claim on this site is checkable from your terminal in seconds:</p>
        <pre className="faq__pre">{`# Where does a site's IP live?
dig +short example.com

# What's the ASN / hosting org?
whois $(dig +short example.com | head -1)

# Or use a third party (no install required):
https://hackertarget.com/ip-tools/
https://viewdns.info/iplocation/?ip=...
https://ipinfo.io/<ip>`}</pre>
        <p>The <em>Changes</em> tab gives you the exact commands for any specific site we've scanned.</p>
      </>
    ),
  },
  {
    q: "Where does the data come from?",
    a: (
      <>
        <p>Three sources:</p>
        <ul>
          <li><a href="https://represent.opennorth.ca/" target="_blank" rel="noopener">Open North's Represent API</a> — politicians, ridings, constituency boundaries (Open Government Licence — Canada)</li>
          <li><a href="https://api.openparliament.ca/" target="_blank" rel="noopener">Open Parliament</a> — used to find each MP's official ourcommons.ca page</li>
          <li>Web search + manual curation — for the personal/campaign URL itself, plus the referendum organization list</li>
        </ul>
        <p>IP geolocation uses MaxMind's GeoLite2 databases. All scans are publicly observable — we don't see anything you couldn't see yourself.</p>
      </>
    ),
  },
  {
    q: "Why does this matter?",
    a: (
      <>
        <p>When a Canadian's interaction with their elected representative travels through a US-based server or CDN, that interaction is potentially subject to US law — including subpoenas under the CLOUD Act — even if the data never leaves what looks like a Canadian website.</p>
        <p>And in the specific case of the October 19, 2026 Alberta independence referendum: <strong>both sides</strong> of the sovereignty debate host their digital infrastructure on American servers. The "Referendum" tab makes that visible.</p>
      </>
    ),
  },
  {
    q: "Is this open source?",
    a: (
      <>
        <p>Yes. The whole stack is MIT-licensed: <a href="https://github.com/adminatthebunker/sovpro" target="_blank" rel="noopener">github.com/adminatthebunker/sovpro</a>. Pull requests, dataset corrections, and additional municipal scrapers welcome.</p>
      </>
    ),
  },
];

export function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section className="faq">
      <h2>Frequently asked questions</h2>
      <ul className="faq__list">
        {ITEMS.map((item, i) => (
          <li key={i} className={`faq__item ${open === i ? "is-open" : ""}`}>
            <button
              className="faq__q"
              onClick={() => setOpen(open === i ? null : i)}
              aria-expanded={open === i}
            >
              <span>{item.q}</span>
              <span className="faq__caret">{open === i ? "−" : "+"}</span>
            </button>
            {open === i && <div className="faq__a">{item.a}</div>}
          </li>
        ))}
      </ul>
    </section>
  );
}
