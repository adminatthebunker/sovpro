import type { ComponentType } from "react";

// Frontmatter is whatever we've declared in src/vite-env.d.ts;
// mirrored here so consumers importing from `types/blog` don't need
// to reach into the ambient module declaration.
export interface BlogPostFrontmatter {
  title: string;
  slug: string;
  date: string; // ISO-8601; YYYY-MM-DD is fine.
  excerpt: string;
  author?: string;
  tags?: string[];
  draft?: boolean;
}

export interface BlogPost extends BlogPostFrontmatter {
  // The default export of the MDX module — a React component that
  // renders the post body. Kept optional on the list view so that
  // BlogListPage doesn't need to hold full bodies in memory, though
  // `import.meta.glob({ eager: true })` means it's essentially always
  // present today.
  body: ComponentType<{ components?: Record<string, unknown> }>;
  // Pre-formatted for lists; `undefined` for posts without a date.
  formattedDate?: string;
}
