<!-- [[RARO]]/apps/web-console/src/components/OutputPane.svelte -->
<script lang="ts">
  import { logs, updateLog, runtimeStore, type LogEntry } from '$lib/stores'
  import Typewriter from './sub/Typewriter.svelte'
  import SmartText from './sub/SmartText.svelte'
  import ApprovalCard from './sub/ApprovalCard.svelte'
  import ArtifactCard from './sub/ArtifactCard.svelte'
  import ToolExecutionCard from './sub/ToolExecutionCard.svelte'
  import { tick } from 'svelte';

  // Refs & Scroll Logic
  let scrollContainer = $state<HTMLDivElement | null>(null);
  let contentWrapper = $state<HTMLDivElement | null>(null);
  let isPinnedToBottom = $state(true);
  let isAutoScrolling = false;

  // === GROUPING LOGIC ===
  let groupedLogs = $derived.by(() => {
    const rawLogs = $logs;
    const groups: { id: string, role: string, items: LogEntry[] }[] = [];
    
    if (rawLogs.length === 0) return [];

    let currentGroup = {
      id: rawLogs[0].id,
      role: rawLogs[0].role,
      items: [rawLogs[0]]
    };

    for (let i = 1; i < rawLogs.length; i++) {
      const log = rawLogs[i];
      if (log.role === currentGroup.role) {
        currentGroup.items.push(log);
      } else {
        groups.push(currentGroup);
        currentGroup = {
          id: log.id,
          role: log.role,
          items: [log]
        };
      }
    }
    groups.push(currentGroup);
    return groups;
  });

  function handleScroll() {
    if (!scrollContainer) return;
    if (isAutoScrolling) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    const distanceFromBottom = scrollHeight - (scrollTop + clientHeight);
    isPinnedToBottom = distanceFromBottom < 50;
  }

  function scrollToBottom(behavior: ScrollBehavior = 'auto') {
    if (!scrollContainer) return;
    isAutoScrolling = true;
    try {
      scrollContainer.scrollTo({ top: scrollContainer.scrollHeight, behavior });
    } finally {
      requestAnimationFrame(() => { isAutoScrolling = false; });
    }
  }

  $effect(() => {
    if (!contentWrapper) return;
    const observer = new ResizeObserver(() => {
      if (isPinnedToBottom) scrollToBottom('auto'); 
    });
    observer.observe(contentWrapper);
    return () => observer.disconnect();
  });

  $effect(() => {
    const _l = $logs;
    tick().then(() => {
      if (isPinnedToBottom) {
        // Use 'auto' (instant) instead of 'smooth' for live logs to prevent
        // the viewport from lagging behind the Typewriter speed.
        scrollToBottom('auto');
      }
    });
  });

  function handleTypewriterComplete(id: string) {
    updateLog(id, { isAnimated: false });
  }

  // === UPDATED: Extraction Logic for Multiple Files ===
  function extractAllFilenames(msg: string): string[] {
      const files: string[] = [];

      // 1. Match RFS System Tags: [SYSTEM: Generated Image saved to 'filename.png']
      // Allow optional spaces \s* around colons and brackets
      const systemRegex = /\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*'([^']+)'\s*\]/gi;
      let match;
      while ((match = systemRegex.exec(msg)) !== null) {
          files.push(match[1]);
      }

      // 2. Match Markdown Images: ![alt](filename.png)
      // (Used as fallback or for agents explicitly outputting MD)
      // Updated regex to catch non-image extensions too in case agent formatted them as links
      const mdRegex = /!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg|json|csv|txt))\)/gi;
      while ((match = mdRegex.exec(msg)) !== null) {
          // Avoid duplicates if both formats exist for the same file
          if (!files.includes(match[1])) {
              files.push(match[1]);
          }
      }

      return files;
  }

  // [[FIXED]]: Removes BOTH Markdown images AND the System Tag text
  function stripSystemTags(msg: string): string {
      let cleaned = msg;

      // 1. Remove standard markdown images: ![alt](url)
      // Updated to match the broader regex in extraction
      cleaned = cleaned.replace(/!\[.*?\]\(([^)]+\.(?:png|jpg|jpeg|svg|json|csv|txt))\)/gi, '');

      // 2. Remove RFS System Tags (Relaxed Regex)
      cleaned = cleaned.replace(/\[\s*SYSTEM\s*:\s*Generated\s*(?:Image|File)\s*saved\s*to\s*'[^']+'\s*\]/gi, '');

      return cleaned.trim();
  }
</script>

<div id="output-pane" bind:this={scrollContainer} onscroll={handleScroll}>
  <div class="log-wrapper" bind:this={contentWrapper}>
    
    {#each groupedLogs as group (group.id)}
      <div class="log-group">
        
        <!-- COLUMN 1: Agent Identity -->
        <div class="group-meta">
            <span class="group-role">{group.role}</span>
            <div class="timeline-line"></div>
        </div>

        <!-- COLUMN 2: Stack of Events -->
        <div class="group-body">
          {#each group.items as log (log.id)}
            <div class="log-item">
              
              <!-- Inline Metadata Header -->
              {#if log.metadata && log.metadata !== 'INFO'}
                <div class="item-meta-header">
                  <span class="meta-tag">{log.metadata}</span>
                  <span class="meta-time">{new Date(log.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}</span>
                </div>
              {/if}

              <!-- Content Renderer -->
              <div class="log-content">
                {#if log.category === 'TOOL_CALL' || log.category === 'TOOL_RESULT'}
                  <ToolExecutionCard
                    category={log.category}
                    message={log.message}
                    metadata={log.metadata || 'INFO'}
                    agentId={log.role}
                    isComplete={log.isComplete}
                    toolResult={log.toolResult}
                    toolStatus={log.toolStatus}
                  />
                
                {:else if log.metadata === 'INTERVENTION'}
                  <ApprovalCard
                    reason={log.message === 'SAFETY_PATTERN_TRIGGERED' ? "System Policy Violation" : log.message}
                    runId={$runtimeStore.runId || ''}
                  />
                
                {:else if log.isAnimated}
                  <Typewriter
                    text={log.message}
                    onComplete={() => handleTypewriterComplete(log.id)}
                  />
                
                {:else}
                  <!-- Static Text + Artifacts -->
                  {@const fileList = extractAllFilenames(log.message)}
                  
                  <!-- [[FIXED]]: Use the robust cleaning function -->
                  <SmartText text={stripSystemTags(log.message)} />
                  
                  {#if fileList.length > 0}
                     <!-- Single Card, Array of Files -->
                     <ArtifactCard 
                        filenames={fileList} 
                        runId={$runtimeStore.runId || ''} 
                     />
                  {/if}
                {/if}
              </div>
            </div>
          {/each}
        </div>

      </div>
    {/each}

  </div>
</div>

<style>
  :global(.error-block) { background: rgba(211, 47, 47, 0.05); border-left: 3px solid #d32f2f; color: var(--paper-ink); padding: 10px; margin-top: 8px; font-family: var(--font-code); font-size: 11px; white-space: pre-wrap; word-break: break-all; }
  :global(.log-content strong) { color: var(--paper-ink); font-weight: 700; }
  
  #output-pane { flex: 1; padding: 24px; overflow-y: auto; display: flex; flex-direction: column; scrollbar-gutter: stable; will-change: scroll-position; }
  
  .log-wrapper { display: flex; flex-direction: column; gap: 0; min-height: min-content; }

  /* GROUP CONTAINER */
  .log-group {
    display: grid; 
    grid-template-columns: 60px 1fr;
    gap: 16px;
    padding: 16px 0;
    border-top: 1px dashed var(--paper-line);
    animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  }

  @keyframes slideUp { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

  /* LEFT COLUMN */
  .group-meta {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    position: relative;
  }

  .group-role {
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.5px;
    color: var(--paper-ink);
    background: var(--paper-surface);
    padding: 2px 4px;
    border-radius: 2px;
    border: 1px solid var(--paper-line);
    text-transform: uppercase;
    z-index: 2;
  }

  .timeline-line {
    position: absolute;
    left: 50%; top: 20px; bottom: -20px;
    width: 1px;
    background: var(--paper-line);
    opacity: 0.2;
    z-index: 1;
    transform: translateX(-50%);
  }

  /* RIGHT COLUMN */
  .group-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .log-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .item-meta-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 2px;
  }

  .meta-tag { 
    font-family: var(--font-code); 
    font-size: 8px; 
    font-weight: 700;
    color: var(--paper-line); 
    background: var(--paper-surface); 
    padding: 1px 4px; 
    border-radius: 2px; 
    display: inline-block; 
    border: 1px solid transparent; 
  }
  
  :global(.mode-phosphor) .meta-tag { border-color: var(--paper-line); }

  .meta-time {
    font-family: var(--font-code);
    font-size: 8px;
    color: var(--paper-line);
    opacity: 0.5;
  }

  .log-content { 
    font-size: 13px; 
    line-height: 1.6; 
    color: var(--paper-ink); 
    opacity: 0.9; 
  }
</style>