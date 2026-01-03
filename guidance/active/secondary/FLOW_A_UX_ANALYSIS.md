# Flow A: Architect - UX Workflow Analysis

## The Core Question

**When should the DAG be generated, verified, and executed?**

This affects:
- User trust and control
- Speed vs safety trade-off
- Learning and transparency
- Error recovery

---

## Workflow Options

### Option 1: Implicit Auto-Generate (Fastest)

```
┌─────────────────────────────────────────────────────────────┐
│  [Text Input: "Research quantum computing"]                 │
│  [ ↵ RUN ]  ← Single click                                  │
└─────────────────────────────────────────────────────────────┘
         ↓
    (Background)
    1. Generate DAG from text
    2. Overwrite store
    3. Execute immediately
         ↓
    User sees execution start
```

**Pros:**
- ✅ Fastest: One click from idea to execution
- ✅ Clean UX: No intermediate steps
- ✅ "Just works" magic feeling

**Cons:**
- ❌ No visibility into what was generated
- ❌ No chance to correct mistakes
- ❌ Hard to learn what the AI decided
- ❌ If AI generates wrong plan, execution starts anyway

**Best for:** Trusted AI, experienced users, low-stakes tasks

---

### Option 2: Auto-Generate + Review Before Execute (Balanced)

```
┌─────────────────────────────────────────────────────────────┐
│  [Text Input: "Research quantum computing"]                 │
│  [ GENERATE PLAN ]  ← First click                           │
└─────────────────────────────────────────────────────────────┘
         ↓
    Loading...
         ↓
┌─────────────────────────────────────────────────────────────┐
│  Generated Plan: 4 agents                                    │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  [ORCHESTRATOR] → [RESEARCHER] → [ANALYST] → [SYNTH]  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  You can:                                                    │
│  - Click nodes to edit prompts                              │
│  - Drag nodes to reposition                                 │
│  - Click edges to modify dependencies                       │
│                                                              │
│  [ ✓ LOOKS GOOD, EXECUTE ]  [ ✗ CANCEL ]  [ ↻ REGENERATE ] │
└─────────────────────────────────────────────────────────────┘
         ↓
    User clicks "EXECUTE"
         ↓
    Execution starts
```

**Pros:**
- ✅ User sees what was generated
- ✅ Can catch obvious errors
- ✅ Educational: Learn AI's planning
- ✅ Can edit before execution
- ✅ Builds trust through transparency

**Cons:**
- ⚠️ Two clicks instead of one
- ⚠️ Requires review step (adds friction)

**Best for:** New users, important tasks, learning mode

---

### Option 3: Toggle-Based Hybrid (Flexible)

```
┌─────────────────────────────────────────────────────────────┐
│  Mode: [ Manual Graph ] [ AI-Assisted 🤖 ] ← Toggle         │
│                                                              │
│  [Text Input: "Research quantum computing"]                 │
│  [ ↵ RUN ]                                                   │
└─────────────────────────────────────────────────────────────┘

If "Manual Graph" mode:
    → Uses existing graph + appends query to orchestrator
    → Current behavior (preserved)

If "AI-Assisted" mode:
    → Sub-choice: [Auto-Execute] or [Review First]
    → User preference saved
```

**Pros:**
- ✅ Preserves existing manual workflow
- ✅ Lets users choose their preference
- ✅ Can switch modes per task
- ✅ Advanced users can go fast, new users can go safe

**Cons:**
- ⚠️ More UI complexity
- ⚠️ Settings to manage

**Best for:** Production system with mixed user base

---

### Option 4: Two-Stage Process with Visual Diff (Most Control)

```
Stage 1: PLANNING
┌─────────────────────────────────────────────────────────────┐
│  📝 Planning Mode                                            │
│  [Text Input: "Research quantum computing"]                 │
│  [ GENERATE PLAN ]                                           │
└─────────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────────┐
│  Generated Plan Preview:                                     │
│                                                              │
│  Current Graph (4 nodes)  →  New Plan (5 nodes)            │
│  ┌─────────────────┐          ┌─────────────────┐          │
│  │ n1 → n2 → n4   │          │ research_agent  │          │
│  │   ↘  n3 ↗      │    VS    │    ↓            │          │
│  └─────────────────┘          │ data_collector  │          │
│                                │    ↓            │          │
│                                │ analyzer        │          │
│                                │    ↓            │          │
│                                │ fact_checker    │          │
│                                │    ↓            │          │
│                                │ synthesizer     │          │
│                                └─────────────────┘          │
│                                                              │
│  [ ACCEPT & SWITCH TO EXECUTION MODE ]                      │
│  [ KEEP CURRENT GRAPH ]                                     │
│  [ REGENERATE WITH DIFFERENT APPROACH ]                     │
└─────────────────────────────────────────────────────────────┘
         ↓
Stage 2: EXECUTION
┌─────────────────────────────────────────────────────────────┐
│  ▶️ Execution Mode                                           │
│  [Graph now shows 5 nodes from AI plan]                     │
│  [ ▶ EXECUTE ]                                               │
└─────────────────────────────────────────────────────────────┘
```

**Pros:**
- ✅ Clear separation: Plan vs Execute
- ✅ Shows before/after diff
- ✅ Can keep old graph if preferred
- ✅ Explicit mode switching
- ✅ Feels professional/powerful

**Cons:**
- ⚠️ Most clicks
- ⚠️ Most UI complexity

**Best for:** Complex workflows, professional tools, high-stakes tasks

---

## Recommended Workflow

### **Proposal: Progressive Disclosure with Smart Defaults**

Combine the best of Options 2 and 3:

```
Default Behavior (First Time User):
┌─────────────────────────────────────────────────────────────┐
│  💬 Directive:                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Research quantum computing applications                │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [ 🎨 GENERATE PLAN ]  [ ▶ USE CURRENT GRAPH ]             │
└─────────────────────────────────────────────────────────────┘
```

**After clicking "GENERATE PLAN":**

```
┌─────────────────────────────────────────────────────────────┐
│  ✨ AI Generated Plan (4 agents, 3 seconds)                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │         [Graph visualization here]                     │ │
│  │  research_agent → data_collector → analyzer → synth   │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  ℹ️  This plan will:                                        │
│  1. Search academic papers for quantum computing            │
│  2. Extract key findings and applications                   │
│  3. Analyze trends and breakthroughs                        │
│  4. Synthesize comprehensive report                         │
│                                                              │
│  [ ✓ EXECUTE THIS PLAN ]                                    │
│                                                              │
│  Advanced:                                                   │
│  [ ✏️ Edit Graph ]  [ ↻ Regenerate ]  [ ✗ Cancel ]         │
│                                                              │
│  ☐ Always execute generated plans immediately (skip review) │
└─────────────────────────────────────────────────────────────┘
```

**After execution starts:**

```
┌─────────────────────────────────────────────────────────────┐
│  🏃 Executing: Research quantum computing applications      │
│  [Progress visualization with logs...]                      │
└─────────────────────────────────────────────────────────────┘
```

---

## Why This Workflow?

### 1. **Safe Default** (Review First)
- New users see what AI generated
- Builds trust through transparency
- Educational value

### 2. **Progressive Enhancement** (Opt-in Speed)
- Checkbox: "Always execute immediately"
- Advanced users can skip review
- Preference remembered

### 3. **Escape Hatches**
- Cancel if plan looks wrong
- Regenerate with different approach
- Edit manually before execution

### 4. **Clear Actions**
- "GENERATE PLAN" = I want AI help
- "USE CURRENT GRAPH" = I built it manually
- No ambiguity

---

## Implementation States

### State 1: IDLE (Default)
```
User sees:
- Text input for directive
- Current graph (can be empty or pre-built)
- Two clear buttons
```

### State 2: GENERATING
```
User sees:
- Loading indicator
- "Consulting AI architect..."
- Cancel button (optional)
```

### State 3: PLAN_READY
```
User sees:
- Generated graph visualization
- Summary of what plan will do
- Execute button (primary action)
- Edit/Regenerate/Cancel (secondary actions)
```

### State 4: EXECUTING
```
User sees:
- Live execution progress
- Logs streaming
- Current behavior (already implemented)
```

---

## Alternative: Inline Preview

**For power users who want even faster workflow:**

```
┌─────────────────────────────────────────────────────────────┐
│  💬 Directive:                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Research quantum computing█                            │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  💡 AI Suggestion (as you type):                            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Suggested plan: 4 agents (researcher → collector...)  │ │
│  │  [Preview] [Use this ✓]                                │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [ GENERATE PLAN ]  [ USE CURRENT GRAPH ]                   │
└─────────────────────────────────────────────────────────────┘
```

**Debounced suggestion as user types (like GitHub Copilot)**

---

## Edge Cases to Handle

### Case 1: Empty/No Graph
- If no existing graph, "USE CURRENT GRAPH" is disabled
- "GENERATE PLAN" is the primary CTA

### Case 2: Generation Fails
```
┌─────────────────────────────────────────────────────────────┐
│  ❌ Plan Generation Failed                                   │
│  The AI couldn't generate a valid plan:                     │
│  "Query too vague. Please specify the research domain."     │
│                                                              │
│  [ ↻ TRY AGAIN ]  [ USE CURRENT GRAPH ]                     │
└─────────────────────────────────────────────────────────────┘
```

### Case 3: User Edits Generated Plan
- Mark graph as "Modified from AI plan"
- Allow saving as template
- Can regenerate to revert

### Case 4: Plan Already Generated
- If directive changes, show "Plan outdated, regenerate?"
- Visual indicator if graph doesn't match current input

---

## Technical Implementation Notes

### Store Management

```typescript
// Add new state to stores.ts

export const planningState = writable<{
  mode: 'idle' | 'generating' | 'plan_ready' | 'executing';
  generatedPlan: WorkflowConfig | null;
  originalQuery: string | null;
  lastGenerated: Date | null;
}>({
  mode: 'idle',
  generatedPlan: null,
  originalQuery: null,
  lastGenerated: null
});
```

### User Preferences

```typescript
// Add to localStorage or user settings

export const userPreferences = writable<{
  autoExecuteGeneratedPlans: boolean;
  showPlanSummary: boolean;
  rememberLastMode: boolean;
}>({
  autoExecuteGeneratedPlans: false, // Safe default
  showPlanSummary: true,
  rememberLastMode: false
});
```

---

## Recommended Implementation Order

### Phase 1: Core Flow (Minimal)
```
1. Add "GENERATE PLAN" button
2. Call /plan endpoint
3. Show loading state
4. Display generated graph
5. Add "EXECUTE" button
6. Wire to existing execution logic
```

### Phase 2: Review UI
```
7. Add plan summary/description
8. Add "Cancel" and "Regenerate" buttons
9. Visual graph diff (optional)
10. Edit capabilities
```

### Phase 3: Polish
```
11. Add user preference toggle
12. Remember last mode
13. Inline suggestions (optional)
14. Save plan templates
```

---

## My Recommendation

**Start with: Option 2 (Auto-Generate + Review)**

**Why:**
1. **Builds Trust:** Users see what AI decided
2. **Educational:** Learn from AI's planning
3. **Safe:** Catch errors before execution
4. **Simple:** Only 2 states to implement
5. **Upgradeable:** Easy to add auto-execute toggle later

**Implementation:**
```
Phase 1 (Day 1): Basic flow
- Button to generate
- Show result
- Button to execute

Phase 2 (Day 2): Add review features
- Summary of plan
- Regenerate option
- Cancel option

Phase 3 (Later): Add preferences
- Auto-execute toggle
- Save templates
- Inline suggestions
```

**Start minimal, iterate based on user feedback.**

---

## Questions to Decide

Before implementing, clarify:

1. **Primary Use Case:**
   - Research tasks (exploratory)
   - Repeated workflows (production)
   - Learning/experimentation

2. **User Trust Level:**
   - High trust → Can skip review
   - Low trust → Always review
   - Mixed → Need toggle

3. **Error Tolerance:**
   - High stakes → Must review
   - Low stakes → Can auto-execute

4. **Iteration Needs:**
   - Users refine plans → Need editing
   - One-shot tasks → Just execute
