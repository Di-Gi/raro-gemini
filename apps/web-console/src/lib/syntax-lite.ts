// [[RARO]]/apps/web-console/src/lib/syntax-lite.ts

export function highlight(code: string, lang: string): string {
    // 1. Sanitize HTML entities first to prevent injection/layout breaking
    let html = code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  
    // Storage for protected tokens (Strings & Comments)
    const tokens: string[] = [];
    const pushToken = (str: string) => {
        tokens.push(str);
        return `%%%TOKEN_${tokens.length - 1}%%%`;
    };
  
    // 2. Extract Strings (Single/Double/Backtick) -> Placeholders
    // We do this first so keywords/comments inside strings are ignored
    html = html.replace(/(['"`])(.*?)\1/g, (match) => {
        return pushToken(`<span class="token-str">${match}</span>`);
    });
  
    // 3. Extract Comments -> Placeholders
    // Note: JS/TS style // and Python/Bash style #
    html = html.replace(/(\/\/.*$)|(#.*$)/gm, (match) => {
        return pushToken(`<span class="token-comment">${match}</span>`);
    });
  
    // 4. Highlight Keywords (Safe to do now, strings/comments are hidden)
    const keywords = /\b(import|export|from|const|let|var|function|return|if|else|for|while|class|interface|type|async|await|def|print|impl|struct|fn|pub)\b/g;
    html = html.replace(keywords, '<span class="token-kw">$1</span>');
  
    // 5. Highlight Numbers
    html = html.replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
  
    // 6. Highlight Booleans
    html = html.replace(/\b(true|false|null|None)\b/g, '<span class="token-bool">$1</span>');
  
    // 7. Restore Placeholders
    // We cycle until no placeholders remain (just in case)
    tokens.forEach((token, i) => {
        html = html.replace(`%%%TOKEN_${i}%%%`, token);
    });
  
    return html;
}