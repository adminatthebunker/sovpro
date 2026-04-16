import { Link } from "react-router-dom";
import { useBlogPosts } from "../hooks/useBlogPosts";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import "../styles/blog.css";

export default function BlogListPage() {
  useDocumentTitle("Blog");
  const posts = useBlogPosts();

  return (
    <section className="blog">
      <header className="blog__header">
        <h2 className="blog__title">Blog</h2>
        <p className="blog__subtitle">
          Work-as-we-go updates on Canadian political data — what we&rsquo;re ingesting, what&rsquo;s blocked,
          and what the numbers say.
        </p>
      </header>

      {posts.length === 0 ? (
        <p className="blog__empty">No posts yet. Check back soon.</p>
      ) : (
        <ol className="blog__list" aria-label="Blog posts">
          {posts.map((post) => (
            <li key={post.slug} className="blog__item">
              <Link to={`/blog/${post.slug}`} className="blog__card">
                <div className="blog__card-meta">
                  {post.formattedDate && <time dateTime={post.date}>{post.formattedDate}</time>}
                  {post.draft && <span className="blog__draft-flag">Draft</span>}
                </div>
                <h3 className="blog__card-title">{post.title}</h3>
                <p className="blog__card-excerpt">{post.excerpt}</p>
                {post.tags && post.tags.length > 0 && (
                  <ul className="blog__card-tags" aria-label="Tags">
                    {post.tags.map((t) => (
                      <li key={t} className="blog__tag">{t}</li>
                    ))}
                  </ul>
                )}
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
