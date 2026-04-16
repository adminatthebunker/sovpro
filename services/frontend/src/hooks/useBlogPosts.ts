import { useMemo } from "react";
import type { BlogPost, BlogPostFrontmatter } from "../types/blog";

// Eager glob — posts are small MDX + metadata and Vite chunks each one.
// Keeping it eager means BlogListPage renders synchronously, no loading
// state required. If post count ever grows past ~50 large posts we'll
// switch to { eager: false } + Suspense.
type MdxModule = {
  default: BlogPost["body"];
  frontmatter: BlogPostFrontmatter;
};

const modules = import.meta.glob<MdxModule>(
  "../content/blog/*.mdx",
  { eager: true }
);

const dateFormatter = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "long",
  day: "numeric",
});

function formatDate(iso: string): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return undefined;
  return dateFormatter.format(d);
}

function buildPosts(includeDrafts: boolean): BlogPost[] {
  const posts: BlogPost[] = [];
  for (const mod of Object.values(modules)) {
    const fm = mod.frontmatter;
    if (!fm) continue; // .mdx without frontmatter: skip
    if (fm.draft && !includeDrafts) continue;
    posts.push({
      ...fm,
      body: mod.default,
      formattedDate: formatDate(fm.date),
    });
  }
  // Newest first. String compare works because we force ISO dates.
  posts.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return posts;
}

/**
 * Return the list of blog posts, newest first. Drafts are hidden in
 * production; set `VITE_SHOW_DRAFTS=1` (or hit the route from dev mode)
 * to include them.
 */
export function useBlogPosts(): BlogPost[] {
  return useMemo(() => {
    const includeDrafts = import.meta.env.DEV || import.meta.env.VITE_SHOW_DRAFTS === "1";
    return buildPosts(includeDrafts);
  }, []);
}

/** Return a single post by slug, or `null` if not found. */
export function useBlogPost(slug: string | undefined): BlogPost | null {
  const posts = useBlogPosts();
  return useMemo(() => {
    if (!slug) return null;
    return posts.find((p) => p.slug === slug) ?? null;
  }, [posts, slug]);
}
