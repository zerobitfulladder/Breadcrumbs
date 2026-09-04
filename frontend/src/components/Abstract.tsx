import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

/**
 * A paper's abstract, with its mathematics typeset.
 *
 * Publishers ship abstracts with LaTeX left in — "distortion $\propto
 * K^{-2/d}$" arrives exactly like that — and shown as plain text it reads as
 * line noise. Here the notation is the content, so it is rendered rather than
 * stripped the way a title is.
 *
 * Only the maths plugin is enabled. An abstract is prose, not Markdown: with
 * the full syntax on, a stray asterisk or underscore in ordinary text would
 * silently turn into emphasis.
 */
export default function Abstract({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        disallowedElements={["h1", "h2", "h3", "h4", "h5", "h6", "img", "a"]}
        unwrapDisallowed
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
