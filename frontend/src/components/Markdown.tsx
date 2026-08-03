// Markdown レンダラ: parseMarkdown が返すブロック AST を React 要素として描画する。
// 依存ライブラリなし。回答本文の見た目（見出し・表・箇条書き・太字・コード等）を整える。
import { Fragment } from 'react';
import type { Block, Inline } from '../markdown/parseMarkdown';
import { parseMarkdown } from '../markdown/parseMarkdown';

function InlineNodes({ nodes }: { nodes: Inline[] }) {
  return (
    <>
      {nodes.map((node, idx) => {
        switch (node.type) {
          case 'bold':
            return <strong key={idx}>{node.value}</strong>;
          case 'code':
            return <code key={idx}>{node.value}</code>;
          case 'link':
            return (
              <a key={idx} href={node.href} target="_blank" rel="noopener noreferrer">
                {node.value}
              </a>
            );
          default:
            return <Fragment key={idx}>{node.value}</Fragment>;
        }
      })}
    </>
  );
}

function BlockNode({ block }: { block: Block }) {
  switch (block.type) {
    case 'heading': {
      const Tag = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      return (
        <Tag>
          <InlineNodes nodes={block.inline} />
        </Tag>
      );
    }
    case 'hr':
      return <hr />;
    case 'list': {
      const items = block.items.map((item, idx) => (
        <li key={idx}>
          <InlineNodes nodes={item} />
        </li>
      ));
      return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
    }
    case 'blockquote':
      return (
        <blockquote>
          {block.lines.map((line, idx) => (
            <p key={idx}>
              <InlineNodes nodes={line} />
            </p>
          ))}
        </blockquote>
      );
    case 'table':
      return (
        <div className="markdown-table-wrap">
          <table>
            <thead>
              <tr>
                {block.header.map((cell, idx) => (
                  <th key={idx}>
                    <InlineNodes nodes={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rIdx) => (
                <tr key={rIdx}>
                  {row.map((cell, cIdx) => (
                    <td key={cIdx}>
                      <InlineNodes nodes={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'paragraph':
      return (
        <p>
          {block.lines.map((line, idx) => (
            <Fragment key={idx}>
              {idx > 0 && <br />}
              <InlineNodes nodes={line} />
            </Fragment>
          ))}
        </p>
      );
    default:
      return null;
  }
}

export function Markdown({ source }: { source: string }) {
  const blocks = parseMarkdown(source);
  return (
    <div className="markdown-body">
      {blocks.map((block, idx) => (
        <BlockNode key={idx} block={block} />
      ))}
    </div>
  );
}
