#!/usr/bin/env python3
"""
Parser Debug Test - Minimal reproduction case
Can run locally or in container
"""

import sys
import os
from pathlib import Path

# Add agent-service src to path
script_dir = Path(__file__).parent.parent.parent
agent_src = script_dir / 'apps' / 'agent-service' / 'src'
sys.path.insert(0, str(agent_src))

print(f"Loading parsers from: {agent_src}")

try:
    from core.parsers import extract_all_code_blocks, parse_function_calls
    from core.config import logger
    import logging
    # Set logging to DEBUG to see parser output
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')
except ImportError as e:
    print(f"Import error: {e}")
    print(f"sys.path: {sys.path}")
    print("\nTry running from project root or inside agent-service container")
    sys.exit(1)

# ============================================================================
# TEST CASE 1: Minimal Function Call (Baseline)
# ============================================================================
test1_minimal = """```json:function
{
  "name": "web_search",
  "args": {
    "query": "test"
  }
}
```"""

print("=" * 80)
print("TEST 1: Minimal Function Call")
print("=" * 80)
print("INPUT:")
print(repr(test1_minimal))
print("\nRESULT:")
blocks = extract_all_code_blocks(test1_minimal, 'function')
print(f"Found {len(blocks)} blocks")
for i, block in enumerate(blocks):
    print(f"Block {i}: {block.data}")
print()

# ============================================================================
# TEST CASE 2: Write File (No Nested Markdown)
# ============================================================================
test2_simple_write = """```json:function
{
  "name": "write_file",
  "args": {
    "filename": "test.md",
    "content": "# Hello World\\nThis is a test."
  }
}
```"""

print("=" * 80)
print("TEST 2: Write File (Simple Content)")
print("=" * 80)
print("INPUT:")
print(repr(test2_simple_write))
print("\nRESULT:")
blocks = extract_all_code_blocks(test2_simple_write, 'function')
print(f"Found {len(blocks)} blocks")
for i, block in enumerate(blocks):
    print(f"Block {i}: {block.data}")
print()

# ============================================================================
# TEST CASE 3: Write File with Nested Markdown Code Block
# ============================================================================
test3_nested = """```json:function
{
  "name": "write_file",
  "args": {
    "filename": "api_guide.md",
    "content": "# API Guide\\n\\nExample request:\\n\\n```json\\n{\\n  \\"endpoint\\": \\"/api/users\\"\\n}\\n```\\n\\nDone."
  }
}
```"""

print("=" * 80)
print("TEST 3: Write File (Nested Markdown)")
print("=" * 80)
print("INPUT:")
print(repr(test3_nested))
print("\nRESULT:")
blocks = extract_all_code_blocks(test3_nested, 'function')
print(f"Found {len(blocks)} blocks")
for i, block in enumerate(blocks):
    print(f"Block {i}: {block.data}")
print()

# ============================================================================
# TEST CASE 4: Literal \n escape sequences (LLM output format)
# ============================================================================
test4_literal_escapes = r"""```json:function\n{\n  \"name\": \"web_search\",\n  \"args\": {\n    \"query\": \"test\"\n  }\n}\n```"""

print("=" * 80)
print("TEST 4: Literal \\n Escapes (Raw LLM Output)")
print("=" * 80)
print("INPUT:")
print(repr(test4_literal_escapes))
print("\nRESULT:")
blocks = extract_all_code_blocks(test4_literal_escapes, 'function')
print(f"Found {len(blocks)} blocks")
for i, block in enumerate(blocks):
    print(f"Block {i}: {block.data}")
print()

# ============================================================================
# TEST CASE 5: Full Integration (parse_function_calls)
# ============================================================================
test5_full = """I'll search for information.

```json:function
{
  "name": "web_search",
  "args": {
    "query": "quantum computing"
  }
}
```"""

print("=" * 80)
print("TEST 5: Full Integration (parse_function_calls)")
print("=" * 80)
print("INPUT:")
print(repr(test5_full))
print("\nRESULT:")
calls = parse_function_calls(test5_full)
print(f"Found {len(calls)} function calls")
for i, (name, args) in enumerate(calls):
    print(f"Call {i}: {name}({args})")
print()

# ============================================================================
# TEST CASE 6: Real-world failure case (from user's test.md)
# ============================================================================
test6_real = """```json:function
{
  "name": "write_file",
  "args": {
    "filename": "market_strategy.md",
    "content": "# Market Strategy\\n\\n## Summary\\n\\nKey points here.\\n\\n## Details\\n\\n```json\\n{\\n  \\"source\\": \\"data\\"\\n}\\n```\\n\\nConclusion."
  }
}
```"""

print("=" * 80)
print("TEST 6: Real-world Case (Market Strategy)")
print("=" * 80)
print("INPUT:")
print(repr(test6_real))
print("\nRESULT:")
blocks = extract_all_code_blocks(test6_real, 'function')
print(f"Found {len(blocks)} blocks")
for i, block in enumerate(blocks):
    print(f"Block {i}: {block.data.get('name', 'NO NAME')} - filename: {block.data.get('args', {}).get('filename', 'N/A')}")
print()

print("=" * 80)
print("DIAGNOSTIC SUMMARY")
print("=" * 80)
print("Check logs above for [PARSER] debug lines to diagnose failures")
print("Expected: All tests should find 1 block except test failures")
print("If 0 blocks found, check:")
print("  1. Marker detection (looking for correct ```json:function)")
print("  2. Brace balancing (finding correct closing })")
print("  3. JSON parsing (handling escapes correctly)")
