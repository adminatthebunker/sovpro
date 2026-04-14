import React from "react";
import ReactDOM from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles/global.css";
import App from "./App";
import PoliticianDetail from "./pages/PoliticianDetail";

/** Extract the politician ID from `/politician/:id` (with optional trailing
 *  slash or suffix). Returns null when the path isn't a politician detail. */
function matchPoliticianId(pathname: string): string | null {
  const m = pathname.match(/^\/politician\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function Root() {
  const politicianId = matchPoliticianId(window.location.pathname);
  if (politicianId) {
    return <PoliticianDetail politicianId={politicianId} />;
  }
  return <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
