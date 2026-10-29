import type {Processor} from 'unified';
import type {Extension} from 'mdast-util-from-markdown';

/** Record fence closure from the parser's own tokens, including nested fences. */
export function remarkCodeFences(this: Processor) {
  const extension: Extension = {
    enter: {
      codeFencedFence() {
        const node = this.stack.find(value => value.type === 'code');
        if (node?.type !== 'code') return;
        const properties = ((node.data ??= {}).hProperties ??= {});
        properties.dataFenceClosed = properties.dataFenceClosed === false;
      },
    },
  };
  this.data('fromMarkdownExtensions', [...(this.data('fromMarkdownExtensions') ?? []), extension]);
}

/** Text content of a highlighted HAST code node, without its markup. */
export function codeTextContent(node: unknown): string {
  if (!node || typeof node !== 'object') return '';
  const value = node as {type?: string; value?: string; children?: unknown[]};
  if (value.type === 'text') return value.value ?? '';
  return value.children?.map(codeTextContent).join('') ?? '';
}
