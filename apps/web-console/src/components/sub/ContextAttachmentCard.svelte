<!-- [[RARO]]/apps/web-console/src/components/sub/ContextAttachmentCard.svelte -->
<!-- Purpose: Special renderer for the [AUTOMATED CONTEXT ATTACHMENT] block -->
<script lang="ts">
  import { slide } from 'svelte/transition';

  let { rawData }: { rawData: string } = $props();

  let isExpanded = $state(false);

  // Parse the raw text dump into structured data
  let parsedContent = $derived.by(() => {
    const sections: Array<{ type: string, items: any[] }> = [];

    // Regex to split multiple tool outputs if present
    // Format: "--- tool_name results ---\n{json}"
    const pattern = /--- (.*?) results ---\n([\s\S]*?)(?=\n---|$)/g;

    let match;
    while ((match = pattern.exec(rawData)) !== null) {
        const toolName = match[1];
        const jsonStr = match[2];

        try {
            const data = JSON.parse(jsonStr);

            // Special handling for Web Search results (Tavily format)
            if (toolName === 'web_search' && data.result) {
                // Tavily often returns 'result' as a stringified JSON string
                let results = [];
                if (typeof data.result === 'string') {
                    try { results = JSON.parse(data.result); } catch (e) { results = [{ content: data.result }]; }
                } else if (Array.isArray(data.result)) {
                    results = data.result;
                }

                sections.push({
                    type: 'WEB_SEARCH',
                    items: results.map((r: any) => ({
                        title: extractDomain(r.url),
                        url: r.url,
                        content: r.content || r.body
                    }))
                });
            }
            // Fallback for generic tools (read_file, etc)
            else {
                sections.push({
                    type: toolName.toUpperCase(),
                    items: [{ content: typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2) }]
                });
            }
        } catch (e) {
            console.warn("Failed to parse context attachment", e);
        }
    }
    return sections;
  });

  function extractDomain(url: string): string {
      if (!url) return 'Unknown Source';
      try {
          const hostname = new URL(url).hostname;
          return hostname.replace('www.', '');
      } catch (e) { return 'External Link'; }
  }

  function toggle() { isExpanded = !isExpanded; }

  function copyRaw() {
      navigator.clipboard.writeText(rawData);
  }
</script>

{#if parsedContent.length > 0}
    <div class="ctx-card">
        <!-- HEADER -->
        <div class="ctx-header-wrapper">
            <button class="ctx-header" onclick={toggle}>
                <div class="header-left">
                    <span class="icon">📎</span>
                    <span class="label">AUTOMATED_CONTEXT</span>
                    <span class="count-badge">{parsedContent.length} SOURCE{parsedContent.length > 1 ? 'S' : ''}</span>
                </div>
                <div class="chevron {isExpanded ? 'up' : 'down'}">▼</div>
            </button>
            <button class="copy-btn" onclick={copyRaw} title="Copy raw data">📋</button>
        </div>

        <!-- BODY -->
        {#if isExpanded}
            <div class="ctx-body" transition:slide={{ duration: 200 }}>
                {#each parsedContent as section}
                    <div class="section-group">
                        <div class="section-title">{section.type} DATA</div>

                        {#if section.type === 'WEB_SEARCH'}
                            <div class="search-grid">
                                {#each section.items as item}
                                    <a href={item.url} target="_blank" rel="noopener noreferrer" class="search-result">
                                        <div class="res-domain">{item.title}</div>
                                        <div class="res-snippet">{item.content.slice(0, 150)}...</div>
                                    </a>
                                {/each}
                            </div>
                        {:else}
                            <!-- Generic Pre Block for other tools -->
                            {#each section.items as item}
                                <div class="generic-block">
                                    <pre>{item.content.slice(0, 300)}{item.content.length > 300 ? '...' : ''}</pre>
                                </div>
                            {/each}
                        {/if}
                    </div>
                {/each}
            </div>
        {/if}
    </div>
{:else if rawData}
    <!-- Fallback for unparseable data -->
    <div class="ctx-card">
        <div class="ctx-header-wrapper">
            <div class="ctx-header static">
                <div class="header-left">
                    <span class="icon">⚠️</span>
                    <span class="label">RAW_CONTEXT</span>
                </div>
            </div>
            <button class="copy-btn" onclick={copyRaw} title="Copy raw data">📋</button>
        </div>
        <div class="ctx-body">
            <pre class="raw-dump">{rawData.slice(0, 500)}{rawData.length > 500 ? '\n...(truncated)' : ''}</pre>
        </div>
    </div>
{/if}

<style>
    .ctx-card {
        margin-top: 12px;
        background: var(--paper-surface);
        border: 1px dashed var(--paper-line);
        border-radius: 2px;
        font-family: var(--font-code);
        overflow: hidden;
    }

    .ctx-header-wrapper {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 12px;
        gap: 8px;
    }

    .ctx-header {
        flex: 1;
        display: flex; justify-content: space-between; align-items: center;
        padding: 0;
        background: transparent; border: none; cursor: pointer;
        transition: background 0.2s;
    }
    .ctx-header:hover { background: color-mix(in srgb, var(--paper-ink), transparent 95%); }
    .ctx-header.static { cursor: default; }
    .ctx-header.static:hover { background: transparent; }

    .header-left { display: flex; align-items: center; gap: 8px; }

    .icon { font-size: 12px; }
    .label { font-size: 10px; font-weight: 700; color: var(--paper-line); letter-spacing: 0.5px; }
    .count-badge { font-size: 9px; background: var(--paper-line); color: var(--paper-bg); padding: 1px 4px; border-radius: 2px; }

    .copy-btn {
        font-size: 10px;
        background: transparent;
        border: 1px solid transparent;
        cursor: pointer;
        opacity: 0.5;
        transition: opacity 0.2s, border-color 0.2s;
        padding: 2px 4px;
        border-radius: 2px;
        flex-shrink: 0;
    }
    .copy-btn:hover {
        opacity: 1;
        border-color: var(--paper-line);
    }

    .chevron { font-size: 8px; color: var(--paper-line); transition: transform 0.2s; }
    .chevron.up { transform: rotate(180deg); }

    .ctx-body {
        border-top: 1px dashed var(--paper-line);
        padding: 12px;
        background: var(--paper-bg);
    }

    .section-group { margin-bottom: 12px; }
    .section-group:last-child { margin-bottom: 0; }

    .section-title {
        font-size: 9px; font-weight: 700; color: var(--paper-ink);
        margin-bottom: 8px; opacity: 0.7;
    }

    /* WEB SEARCH STYLES */
    .search-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }

    .search-result {
        display: block; text-decoration: none;
        background: var(--paper-surface);
        border: 1px solid var(--paper-line);
        padding: 8px; border-radius: 2px;
        transition: border-color 0.2s;
    }
    .search-result:hover { border-color: var(--arctic-cyan); }

    .res-domain { font-size: 10px; font-weight: 700; color: var(--arctic-cyan); margin-bottom: 4px; }
    .res-snippet { font-size: 9px; color: var(--paper-ink); line-height: 1.4; opacity: 0.8; }

    /* GENERIC STYLES */
    .generic-block pre {
        margin: 0; font-size: 10px; color: var(--paper-line);
        white-space: pre-wrap; word-break: break-all;
    }

    /* RAW DUMP FALLBACK */
    .raw-dump {
        font-size: 9px;
        color: var(--paper-line);
        opacity: 0.7;
        margin: 0;
        white-space: pre-wrap;
        word-break: break-all;
    }
</style>
