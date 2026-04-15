import { useEffect } from "react";

const BASE_TITLE = "Canadian Political Data";

/** Sets the document title while this component is mounted. Reverts to the
 *  base title on unmount so quick route changes don't leave stale titles. */
export function useDocumentTitle(title?: string | null) {
  useEffect(() => {
    const prev = document.title;
    document.title = title ? `${title} — ${BASE_TITLE}` : BASE_TITLE;
    return () => { document.title = prev; };
  }, [title]);
}
