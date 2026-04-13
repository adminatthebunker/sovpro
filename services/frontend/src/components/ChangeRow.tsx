import { useState } from "react";
import type { ChangeItem } from "../types";

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export function ChangeRow({ change }: { change: ChangeItem }) {
  const [showVerify, setShowVerify] = useState(false);
  const host = hostnameOf(change.website_url);
  const ipForVerify = change.change_type.includes("ip") || change.change_type.includes("country") || change.change_type.includes("city")
    ? change.new_value
    : null;
  return (
    <li className={`changes__row changes__row--${change.severity}`}>
      <div className="changes__time">{new Date(change.detected_at).toLocaleString()}</div>
      <div className="changes__summary">
        <strong>{change.owner_name}</strong>
        <a href={change.website_url} target="_blank" rel="noopener">{change.website_url}</a>
        <span className={`changes__type changes__type--${change.change_type}`}>{change.change_type.replace(/_/g, " ")}</span>
        <p>{change.summary}</p>
        {(change.old_value || change.new_value) && (
          <div className="changes__diff">
            <del>{change.old_value ?? "—"}</del>
            <span> → </span>
            <ins>{change.new_value ?? "—"}</ins>
          </div>
        )}
        <button className="changes__verify-btn" onClick={() => setShowVerify(s => !s)}>
          {showVerify ? "Hide verification" : "Verify yourself ↗"}
        </button>
        {showVerify && (
          <div className="changes__verify-panel">
            <p>Run any of these to confirm independently:</p>
            <pre>{`dig +short ${host}
whois $(dig +short ${host} | head -1)`}</pre>
            <ul>
              <li><a href={`https://hackertarget.com/ip-tools/?q=${encodeURIComponent(host)}`} target="_blank" rel="noopener">HackerTarget DNS lookup</a></li>
              <li><a href={`https://viewdns.info/iplocation/?ip=${encodeURIComponent(ipForVerify || host)}`} target="_blank" rel="noopener">ViewDNS IP geolocation</a></li>
              {ipForVerify && (
                <li><a href={`https://ipinfo.io/${ipForVerify}`} target="_blank" rel="noopener">ipinfo.io for {ipForVerify}</a></li>
              )}
              <li><a href={`https://www.shodan.io/search?query=hostname:${encodeURIComponent(host)}`} target="_blank" rel="noopener">Shodan search</a></li>
            </ul>
          </div>
        )}
      </div>
    </li>
  );
}
