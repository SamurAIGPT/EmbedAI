"use client";

import { Fragment, memo, useState, type ReactNode } from "react";
import { parseMarkdown, type Block, type Inline } from "@/lib/markdown";

function renderInline(nodes: Inline[]): ReactNode {
  return nodes.map((node, i) => {
    switch (node.type) {
      case "text":
        return <Fragment key={i}>{node.text}</Fragment>;
      case "code":
        return (
          <code key={i} className="rounded bg-code px-1 py-0.5 font-mono text-[0.85em]">
            {node.text}
          </code>
        );
      case "strong":
        return <strong key={i}>{renderInline(node.children)}</strong>;
      case "em":
        return <em key={i}>{renderInline(node.children)}</em>;
      case "del":
        return <del key={i}>{renderInline(node.children)}</del>;
      case "link":
        return (
          <a
            key={i}
            href={node.href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="text-accent underline underline-offset-2 hover:opacity-80"
          >
            {renderInline(node.children)}
          </a>
        );
    }
  });
}

function CodeBlock({ lang, text, closed }: { lang: string; text: string; closed: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: nothing to do
    }
  };
  return (
    <div className="my-3 overflow-hidden rounded-field border border-line">
      <div className="flex items-center justify-between bg-inset px-3 py-1 text-xs text-muted">
        <span>{lang || "text"}</span>
        <button
          onClick={copy}
          disabled={!closed && text === ""}
          aria-label="Copy code"
          className="cursor-pointer rounded px-1.5 py-0.5 hover:bg-hover touch:px-3 touch:py-2"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto bg-code p-3 font-mono text-xs leading-relaxed text-ink">
        <code>{text}</code>
      </pre>
    </div>
  );
}

const HEADING_SIZE = ["", "text-xl", "text-lg", "text-base", "text-sm", "text-sm", "text-sm"];

function renderBlock(block: Block, key: number): ReactNode {
  switch (block.type) {
    case "paragraph":
      return (
        <p key={key} className="my-2 whitespace-pre-wrap break-words first:mt-0 last:mb-0">
          {renderInline(block.children)}
        </p>
      );
    case "heading":
      return (
        <p key={key} role="heading" aria-level={block.level} className={`mt-4 mb-1 font-semibold ${HEADING_SIZE[block.level]}`}>
          {renderInline(block.children)}
        </p>
      );
    case "code":
      return <CodeBlock key={key} lang={block.lang} text={block.text} closed={block.closed} />;
    case "quote":
      return (
        <blockquote key={key} className="my-2 border-l-2 border-line pl-3 text-muted">
          {block.blocks.map(renderBlock)}
        </blockquote>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          key={key}
          start={block.ordered ? block.start : undefined}
          className={`my-2 space-y-1 pl-5 ${block.ordered ? "list-decimal" : "list-disc"}`}
        >
          {block.items.map((item, i) => (
            <li key={i} className="break-words">
              {renderInline(item)}
            </li>
          ))}
        </Tag>
      );
    }
    case "table":
      return (
        <div key={key} className="my-2 overflow-x-auto">
          <table className="min-w-full border-collapse text-xs">
            <thead>
              <tr>
                {block.header.map((cell, i) => (
                  <th
                    key={i}
                    style={{ textAlign: (block.align[i] ?? undefined) as "left" | "right" | "center" | undefined }}
                    className="border border-line bg-inset px-2 py-1 font-semibold"
                  >
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, i) => (
                    <td
                      key={i}
                      style={{ textAlign: (block.align[i] ?? undefined) as "left" | "right" | "center" | undefined }}
                      className="border border-line px-2 py-1"
                    >
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "rule":
      return <hr key={key} className="my-3 border-line" />;
  }
}

// An assistant answer as formatted text. It never renders raw HTML: the text is
// parsed to plain data first (see lib/markdown.ts), so it is safe on half-written
// answers while they stream.
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return <div className="text-ink">{parseMarkdown(text).map(renderBlock)}</div>;
});
