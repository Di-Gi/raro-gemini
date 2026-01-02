// [[RARO]]/apps/web-console/src/lib/syntax-lite.ts

export function highlight(code: string, lang: string): string {
    // Basic sanitization
    let html = code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  
    // 1. Strings (Single and Double quotes)
    // We utilize a specific class to style them via CSS variables
    html = html.replace(/(['"`])(.*?)\1/g, '<span class="token-str">$1$2$1</span>');
  
    // 2. Comments (Simple single line)
    html = html.replace(/(\/\/.*$)/gm, '<span class="token-comment">$1</span>');
    html = html.replace(/(#.*$)/gm, '<span class="token-comment">$1</span>'); // Python style
  
    // 3. Keywords (Generic set for JS/TS/Rust/Python)
    const keywords = /\b(import|export|from|const|let|var|function|return|if|else|for|while|class|interface|type|async|await|def|print|impl|struct|fn|pub)\b/g;
    html = html.replace(keywords, '<span class="token-kw">$1</span>');
  
    // 4. Numbers
    html = html.replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
  
    // 5. Booleans
    html = html.replace(/\b(true|false|null|None)\b/g, '<span class="token-bool">$1</span>');
  
    return html;
  }