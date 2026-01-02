<!-- // [[RARO]]/apps/web-console/src/components/sub/SmartText.svelte
// Purpose: Parses markdown-like text to separate Code Blocks from standard text.
// Architecture: Logic/View Controller
-->

<script lang="ts">
  import CodeBlock from './CodeBlock.svelte';

  let { text }: { text: string } = $props();

  // Simple parser to split by ``` block ```
  // Returns array of objects: { type: 'text' | 'code', content: string, lang?: string }
  function parseContent(input: string) {
    const regex = /```(\w+)?\n([\s\S]*?)```/g;
    const parts = [];
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(input)) !== null) {
      // 1. Push preceding text
      if (match.index > lastIndex) {
        parts.push({
          type: 'text',
          content: input.slice(lastIndex, match.index)
        });
      }

      // 2. Push Code Block
      parts.push({
        type: 'code',
        lang: match[1] || 'text',
        content: match[2]
      });

      lastIndex = regex.lastIndex;
    }

    // 3. Push remaining text
    if (lastIndex < input.length) {
      parts.push({
        type: 'text',
        content: input.slice(lastIndex)
      });
    }

    return parts;
  }

  let blocks = $derived(parseContent(text));
</script>

<div class="smart-text-wrapper">
  {#each blocks as block}
    {#if block.type === 'code'}
      <CodeBlock code={block.content} language={block.lang || 'text'} />
    {:else}
      <!-- 
         We use basic whitespace preservation for text blocks.
         Using @html allows bold/italics from the LLM if needed (e.g. **bold**)
         For true markdown text support, we would use a library, 
         but for now we trust the LLM's spacing or basic formatting.
      -->
      <span class="text-segment">{@html block.content}</span>
    {/if}
  {/each}
</div>

<style>
  .smart-text-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
  }

  .text-segment {
    white-space: pre-wrap; /* Preserve newlines from text parts */
  }
</style>