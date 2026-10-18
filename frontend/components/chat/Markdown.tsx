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
          <code key={i} className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-zinc-800">
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
            className="underline underline-offset-2 hover:text-zinc-500"
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
    <div className="my-2 overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-800">
      <div className="flex items-center justify-between bg-zinc-50 px-3 py-1 text-xs text-zinc-500 dark:bg-zinc-900">
        <span>{lang || "text"}</span>
        <button
          onClick={copy}
          disabled={!closed && text === ""}
          aria-label="Copy code"
          className="cursor-pointer rounded px-1.5 py-0.5 hover:bg-zinc-200 dark:hover:bg-zinc-800"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto bg-zinc-100 p-3 font-mono text-xs leading-relaxed text-zinc-800 dark:bg-zinc-950 dark:text-zinc-200">
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
        <blockquote key={key} className="my-2 border-l-2 border-zinc-300 pl-3 text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
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
                    className="border border-zinc-200 bg-zinc-50 px-2 py-1 font-semibold dark:border-zinc-800 dark:bg-zinc-900"
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
                      className="border border-zinc-200 px-2 py-1 dark:border-zinc-800"
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
      return <hr key={key} className="my-3 border-zinc-200 dark:border-zinc-800" />;
  }
}

// An assistant answer as formatted text. It never renders raw HTML: the text is
// parsed to plain data first (see lib/markdown.ts), so it is safe on half-written
// answers while they stream.
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return <div className="text-zinc-800 dark:text-zinc-200">{parseMarkdown(text).map(renderBlock)}</div>;
});
