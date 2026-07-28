"use client";

import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

type TeacherMarkdownProps = {
  children: string;
};

function SafeLink({ href, children, ...props }: ComponentPropsWithoutRef<"a">) {
  const external = Boolean(
    href?.startsWith("http://") || href?.startsWith("https://"),
  );
  return (
    <a
      {...props}
      href={href}
      rel={external ? "noopener noreferrer" : undefined}
      target={external ? "_blank" : undefined}
    >
      {children}
      {external ? (
        <span className="sr-only"> (se abre en otra pestaña)</span>
      ) : null}
    </a>
  );
}

export function TeacherMarkdown({ children }: TeacherMarkdownProps) {
  return (
    <div className="teacher-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={defaultUrlTransform}
        components={{
          a: SafeLink,
          table: ({ children: tableChildren }) => (
            <div className="teacher-table-scroll" tabIndex={0}>
              <table>{tableChildren}</table>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
