/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_API_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// MDX posts live under src/content/blog/*.mdx and are loaded eagerly
// via import.meta.glob. Each module exports:
//   - default: the React component rendering the post body
//   - frontmatter: the YAML header parsed by remark-mdx-frontmatter
//
// Keeping this typed at the module boundary means consumers can read
// `module.frontmatter.title` without `any` casts.
declare module "*.mdx" {
  import type { ComponentType } from "react";
  const MDXComponent: ComponentType<{ components?: Record<string, unknown> }>;
  export const frontmatter: {
    title: string;
    slug: string;
    date: string;
    excerpt: string;
    author?: string;
    tags?: string[];
    draft?: boolean;
  };
  export default MDXComponent;
}
