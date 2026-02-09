Here are the fixes for both issues located in `apps/kernel-server/src/runtime.rs`.

### Issue 1: Protocol Violation (Logic Fix)
The kernel currently enforces a strict rule: if an agent's name starts with `analyze_`, it **must** use `execute_python`. However, your `analyze_swot_and_strategy` agent correctly synthesized data and saved it using `write_file` without needing Python calculation. We need to relax the validator to accept `write_file` as a valid "work action" for analysis agents.

### Issue 2: Duplicate Output (Prompt Fix)
The "Duplication" isn't a code bug in the display logic, but a behavior of the LLM. The logs show the LLM is outputting the full Markdown text in its "thought" stream *before* it calls the `write_file` tool with the same content.

To fix this, we need to inject a specific system instruction in `prepare_invocation_payload` telling the model to be concise when writing files.

---

### The Fix

Apply these changes to `apps/kernel-server/src/runtime.rs`.

#### 1. Update `execute_dynamic_dag` (Fixing the Protocol Violation)
Search for the `// 3. Protocol Validation Logic` section (around line 520 in your provided file) and update it to check for `write_file`.

```rust
// ... inside execute_dynamic_dag loop ...

                    // 2. Check Tool Evidence (Robust Check)
                    // We check the explicit list from Python first, fall back to text scan
                    let used_python = res.executed_tools.contains(&"execute_python".to_string())
                                      || text.contains("execute_python")
                                      || text.contains("Tool 'execute_python' Result");

                    let used_search = res.executed_tools.contains(&"web_search".to_string())
                                      || text.contains("web_search")
                                      || text.contains("Tool 'web_search' Result");
                    
                    // [[FIX 1 START: Detect File Writing]]
                    let used_write = res.executed_tools.contains(&"write_file".to_string())
                                      || text.contains("write_file")
                                      || text.contains("Tool 'write_file' Result");
                    // [[FIX 1 END]]

                    // 3. Protocol Validation Logic
                    let mut protocol_violation = None;

                    if !is_bypassed {
                        if agent_id.starts_with("research_") && !used_search {
                            protocol_violation = Some("Protocol Violation: 'research_' agent did not use web_search (Hallucination Risk).");
                        } else if (agent_id.starts_with("analyze_") || agent_id.starts_with("coder_")) {
                             // [[FIX 1 START: Allow write_file as a valid output action for analysts]]
                             if !used_python && !used_write {
                                 protocol_violation = Some("Protocol Violation: 'analyze_'/'coder_' agent did not use execute_python or write_file (Integrity Risk).");
                             }
                             // [[FIX 1 END]]
                        }
                    }
```

#### 2. Update `prepare_invocation_payload` (Fixing the Duplication)
Search for `prepare_invocation_payload` and find where `final_prompt` is constructed. We will inject a suppression instruction if the agent has the `write_file` tool.

```rust
// ... inside prepare_invocation_payload ...

        // ... (existing tool provisioning logic) ...

        tracing::info!("Final provisioned tools for {}: {:?}", agent_id, tools);

        // [[FIX 2 START: Anti-Duplication Prompt Injection]]
        // If the agent can write files, tell it NOT to output the content in the chat text first.
        let mut final_prompt = agent_config.prompt.clone();
        
        if tools.contains(&"write_file".to_string()) {
            final_prompt.push_str("\n\n[SYSTEM NOTICE]: You have access to 'write_file'. When generating a file, DO NOT output the file content in your text response. Simply state 'Writing [filename]...' and then execute the tool immediately. Duplicating content in text and tool arguments is prohibited.");
        }
        
        if !context_prompt_appendix.is_empty() {
            final_prompt.push_str(&context_prompt_appendix);
        }
        // [[FIX 2 END]]

        let graph_view = self.generate_graph_context(
            run_id,
            agent_id,
            agent_config.allow_delegation
        );

        Ok(InvocationPayload {
            // ... (rest of struct)
```

### Explanation of Fixes
1.  **Protocol Validator:** The logic `if (analyze) && !python` was too rigid. It now reads `if (analyze) && !python && !write`. This means if the analyst calculates something (`python`) OR produces a report (`write`), the kernel considers the job done correctly.
2.  **Anti-Duplication:** By detecting if the agent is equipped with `write_file` and appending a strong system directive, we force the LLM to skip the verbose "Here is the file content..." preamble and go straight to the JSON tool call. This cleans up the logs and saves tokens.