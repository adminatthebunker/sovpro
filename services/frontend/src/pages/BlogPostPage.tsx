import { Link, useParams } from "react-router-dom";
import { useBlogPost } from "../hooks/useBlogPosts";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import "../styles/blog.css";

export default function BlogPostPage() {
  const { slug } = useParams<{ slug: string }>();
  const post = useBlogPost(slug);
  useDocumentTitle(post ? post.title : "Post not found");

  if (!post) {
    return (
      <section className="blog">
        <div className="blog__empty">
          <h2>Post not found</h2>
          <p>We couldn&rsquo;t find a post at <code>/{slug}</code>.</p>
          <p><Link to="/blog">← Back to all posts</Link></p>
        </div>
      </section>
    );
  }

  const PostBody = post.body;

  return (
    <article className="post">
      <header className="post__header">
        <p className="post__back"><Link to="/blog">← All posts</Link></p>
        <h1 className="post__title">{post.title}</h1>
        <p className="post__meta">
          {post.formattedDate && <time dateTime={post.date}>{post.formattedDate}</time>}
          {post.author && <span> · <span className="post__author">{post.author}</span></span>}
          {post.draft && <span className="post__draft-flag"> · Draft</span>}
        </p>
        {post.tags && post.tags.length > 0 && (
          <ul className="post__tags" aria-label="Tags">
            {post.tags.map((t) => (
              <li key={t} className="post__tag">{t}</li>
            ))}
          </ul>
        )}
      </header>

      <div className="post__body">
        <PostBody />
      </div>

      <footer className="post__footer">
        <Link to="/blog" className="post__back-link">← Back to all posts</Link>
      </footer>
    </article>
  );
}
