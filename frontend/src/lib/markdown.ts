/**
 * markdown 渲染：markdown-it 解析 + shiki 做代码块高亮。
 *
 * shiki 用 core API（createHighlighterCore + 显式 import 语言/主题）而不是
 * createHighlighter，因为后者会把所有 300+ 语言 loader 都打进 bundle
 * (dist 会膨胀到 10MB+)。core API 只打进我们真正列出来的语言，把 227KB
 * 保持在合理量级。
 *
 * shiki 加载完成前代码块会先渲染纯文本（无高亮），就绪后组件 watch 一个
 * ready tick 触发重渲染。绝大多数场景（几十毫秒内 shiki 就绪）用户不会
 * 察觉这个空窗。
 */

import MarkdownIt from 'markdown-it'
import {
  createHighlighterCore,
  createOnigurumaEngine,
  type HighlighterCore,
} from 'shiki/core'

let highlighterPromise: Promise<HighlighterCore> | null = null

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighterCore({
      themes: [import('@shikijs/themes/github-dark')],
      langs: [
        import('@shikijs/langs/bash'),
        import('@shikijs/langs/shellscript'),
        import('@shikijs/langs/python'),
        import('@shikijs/langs/typescript'),
        import('@shikijs/langs/javascript'),
        import('@shikijs/langs/json'),
        import('@shikijs/langs/yaml'),
        import('@shikijs/langs/html'),
        import('@shikijs/langs/css'),
        import('@shikijs/langs/vue'),
        import('@shikijs/langs/sql'),
        import('@shikijs/langs/markdown'),
        import('@shikijs/langs/diff'),
      ],
      engine: createOnigurumaEngine(import('shiki/wasm')),
    })
  }
  return highlighterPromise
}

// 预热 shiki：应用启动就 fire-and-forget 初始化
void getHighlighter()

// 语言别名映射：markdown 里 ```shell 也走 shellscript 高亮
const LANG_ALIAS: Record<string, string> = {
  shell: 'shellscript',
  sh: 'shellscript',
  ts: 'typescript',
  js: 'javascript',
  py: 'python',
  md: 'markdown',
}

const SUPPORTED_LANGS = new Set([
  'bash',
  'shellscript',
  'python',
  'typescript',
  'javascript',
  'json',
  'yaml',
  'html',
  'css',
  'vue',
  'sql',
  'markdown',
  'diff',
])

let readyHighlighter: HighlighterCore | null = null

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight: (code, lang) => {
    const hl = readyHighlighter
    if (!hl) return ''
    const resolved = LANG_ALIAS[lang] ?? lang
    if (!SUPPORTED_LANGS.has(resolved)) return ''
    try {
      return hl.codeToHtml(code, {
        lang: resolved,
        theme: 'github-dark',
      })
    } catch {
      return ''
    }
  },
})

void getHighlighter().then((hl) => {
  readyHighlighter = hl
})

export function renderMarkdown(text: string): string {
  return md.render(text)
}

export function whenHighlighterReady(): Promise<void> {
  return getHighlighter().then(() => undefined)
}
