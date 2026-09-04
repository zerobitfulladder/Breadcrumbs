import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import "./Markdown.css";

interface Props {
  children: string;
  /**
   * Called for a `paper:` link. The assistant suggests papers by writing
   * `[title](paper:10.1234/xyz)`, which renders here as a button.
   */
  onPaperLink?: (identifier: string, label: string) => void;
}

/** A DOI written in prose, e.g. 10.1038/323533a0. */
const DOI_RE = /\b(10\.\d{4,9}\/[^\s,;)\]}"'<>]+)/g;
const URL_RE = /^(https?:\/\/|www\.)\S+$/i;

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

/**
 * Turns bare DOIs into links.
 *
 * GFM already autolinks anything starting http or www, but a DOI on its own is
 * just text — and this assistant produces them constantly, since a DOI is how
 * the whole library is keyed.
 */
function remarkDoiLinks() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node.children) return;
      // Inside a link or any code, a DOI is already addressed or is meant
      // literally; either way it must be left alone.
      if (node.type === "link" || node.type === "inlineCode" || node.type === "code") return;

      const out: MdNode[] = [];
      for (const child of node.children) {
        if (child.type !== "text" || !child.value) {
          walk(child);
          out.push(child);
          continue;
        }

        DOI_RE.lastIndex = 0;
        let last = 0;
        let match: RegExpExecArray | null;
        let replaced = false;
        while ((match = DOI_RE.exec(child.value)) !== null) {
          replaced = true;
          if (match.index > last) {
            out.push({ type: "text", value: child.value.slice(last, match.index) });
          }
          const doi = match[1].replace(/[.,;]$/, "");
          out.push({
            type: "link",
            url: `https://doi.org/${doi}`,
            children: [{ type: "text", value: doi }],
          });
          last = match.index + doi.length;
        }
        if (!replaced) {
          out.push(child);
        } else if (last < child.value.length) {
          out.push({ type: "text", value: child.value.slice(last) });
        }
      }
      node.children = out;
    };
    walk(tree);
  };
}

/**
 * Renders assistant replies: GitHub-flavoured Markdown, TeX maths, and links
 * that open away from the app.
 *
 * Models write Markdown whether or not you ask them to, so rendering it is not
 * a nicety — unrendered, every reply arrives full of asterisks and pipes.
 */
export default function Markdown({ children, onPaperLink }: Props) {
  return (
    <div className="md">
      <ReactMarkdown
        // react-markdown strips URL schemes it does not recognise, which turned
        // every `paper:` link into an empty href — so clicking one navigated to
        // the page itself instead of opening the preview. Let ours through and
        // sanitise everything else as normal.
        urlTransform={(url) =>
          url.startsWith("paper:") ? url : defaultUrlTransform(url)
        }
        remarkPlugins={[remarkGfm, remarkMath, remarkDoiLinks]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={{
          a: ({ href, children: text }) => {
            // A paper the assistant is suggesting: rendered as a button that
            // opens the preview, so several can sit inline in one answer
            // rather than each one interrupting with a modal.
            if (href?.startsWith("paper:")) {
              const identifier = href.slice(6);
              const label = typeof text === "string" ? text : identifier;
              return (
                <button
                  type="button"
                  className="md-paper"
                  onClick={() => onPaperLink?.(identifier, label)}
                  title={`Preview ${identifier}`}
                >
                  <span className="md-paper-icon" aria-hidden>
                    +
                  </span>
                  {text}
                </button>
              );
            }
            // Anything else can point anywhere; open it away from the app.
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {text}
              </a>
            );
          },
          // Models habitually wrap URLs in backticks, which stops GFM
          // autolinking them. A lone URL in code is still a destination.
          code: ({ children: content, className }) => {
            const text = String(content ?? "");
            const isBlock = Boolean(className);
            if (!isBlock && URL_RE.test(text.trim())) {
              const url = text.trim();
              return (
                <a
                  href={url.startsWith("www.") ? `https://${url}` : url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  <code>{text}</code>
                </a>
              );
            }
            return <code className={className}>{content}</code>;
          },
          // Wide tables and long code must scroll inside their own box rather
          // than stretching the panel.
          table: ({ children: rows }) => (
            <div className="md-scroll">
              <table>{rows}</table>
            </div>
          ),
          pre: ({ children: code }) => (
            <div className="md-scroll">
              <pre>{code}</pre>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
