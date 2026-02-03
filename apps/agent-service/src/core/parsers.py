# [[RARO]]/apps/agent-service/src/core/parsers.py
# Purpose: Unified Parser Module for Markdown Code Block Extraction
# Architecture: Core Layer - Shared parsing utilities
# Dependencies: re, json, typing, codecs

import re
import json
import codecs
from typing import Optional, List, Dict, Any, Tuple
from core.config import logger


class ParsedBlock:
    """
    Represents a parsed code block with its type and data.
    """
    def __init__(self, block_type: str, data: Dict[str, Any], raw_json: str):
        self.block_type = block_type
        self.data = data
        self.raw_json = raw_json

    def __repr__(self):
        return f"ParsedBlock(type={self.block_type}, keys={list(self.data.keys())})"


def _repair_json_string(json_str: str) -> str:
    """
    Attempts to repair common JSON errors made by LLMs when embedding Python code.
    
    Specifically fixes invalid escape sequences (e.g., '\d', '\s' in regex) by 
    doubling backslashes that are not part of valid JSON control characters.
    
    Args:
        json_str: The raw JSON string extracted from the LLM output.
        
    Returns:
        The sanitized string with double backslashes where appropriate.
    """
    # Regex logic: Find a backslash that is NOT followed by a valid JSON escape char.
    # Valid JSON escapes are: " \ / b f n r t u
    # Pylance Fix: We want to match the literal characters b, f, n, r, t, u, ", \, /
    # We do NOT use \b (backspace) inside the character class.
    pattern = r'\\(?![/u"\\bfnrt])'
    
    # Replace single backslash with double backslash
    return re.sub(pattern, r'\\\\', json_str)


def _parse_with_repair(json_str: str, block_type: str) -> Optional[Dict[str, Any]]:
    """
    Helper to parse JSON with multiple fallback/repair mechanisms.
    
    Strategies:
    1. Standard Parse (strict=False): Handles control chars like newlines.
    2. Regex Repair: Fixes invalid backslashes (e.g. in regex strings).
    3. Unicode Unescape: Fixes over-escaped JSON (e.g. \"key\": \"val\", \n).
    4. Hybrid (Unescape + Repair): Fixes over-escaped JSON that ALSO has invalid escapes.
    """
    # --- Strategy 1: Standard Parse ---
    try:
        # strict=False allows control characters (like real newlines) inside strings
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError as e1:
        # Log basic failure only if debug is on to reduce noise, as we have fallbacks
        logger.debug(f"JSON Strategy 1 failed for {block_type}: {e1}")

    # --- Strategy 2: Regex Repair (Invalid Escapes) ---
    try:
        repaired_json = _repair_json_string(json_str)
        # Check if repair actually changed anything before trying
        if repaired_json != json_str:
            logger.warning(f"Attempting regex repair for ```json:{block_type}```...")
            data = json.loads(repaired_json, strict=False)
            logger.info(f"JSON regex repair successful for ```json:{block_type}```.")
            return data
    except json.JSONDecodeError:
        pass

    # --- Strategy 3 & 4: Unicode Unescape variants ---
    # Handles cases like: {\n \"name\": \"value\" \n}
    try:
        # Decode python string literals (turns literal \n into newline, \" into ")
        unescaped_json = codecs.decode(json_str, 'unicode_escape')
        
        if unescaped_json != json_str:
            # Sub-strategy 3a: Direct Load after unescape
            try:
                data = json.loads(unescaped_json, strict=False)
                logger.info(f"JSON unescape successful for ```json:{block_type}```.")
                return data
            except json.JSONDecodeError:
                # Sub-strategy 4: Unescape THEN Regex Repair
                # (Handles cases like \"pattern\": \"\\d+\" -> "pattern": "\d+" -> "pattern": "\\d+")
                repaired_unescaped = _repair_json_string(unescaped_json)
                data = json.loads(repaired_unescaped, strict=False)
                logger.info(f"JSON Hybrid (Unescape+Repair) successful for ```json:{block_type}```.")
                return data

    except Exception as e3:
        logger.debug(f"JSON Advanced Strategies failed: {e3}")

    # --- Final Failure ---
    logger.error(f"Failed to parse ```json:{block_type}``` block after all strategies.")
    logger.debug(f"Failed JSON content (first 200 chars): {json_str[:200]}...") 
    return None


def extract_code_block(text: str, block_type: str) -> Optional[ParsedBlock]:
    """Helper for single block extraction"""
    blocks = extract_all_code_blocks(text, block_type)
    return blocks[0] if blocks else None


def _find_balanced_json_end(text: str, start_idx: int) -> int:
    """
    Fast balanced brace parser to find the end of a JSON object.
    Tracks {/} depth while respecting string escaping.
    """
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string

        if not in_string:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return i + 1  # Return index after closing brace

    return -1  # Unbalanced


def extract_all_code_blocks(text: str, block_type: str) -> List[ParsedBlock]:
    """
    Extract and parse ALL code blocks with nested markdown support.
    """
    blocks = []

    logger.debug(f"[PARSER] Searching for block_type='{block_type}' in text of length {len(text)}")

    # --- PRE-PROCESSING: Handle literal \n escape sequences ---
    # Global 're' is used here, safe from UnboundLocalError
    if '\\n' in text and '```json:' in text:
        def fix_fence_newlines(match):
            full_match = match.group(0)
            json_start = full_match.find('{')
            if json_start == -1: return full_match
            header = full_match[:json_start].replace('\\n', '\n')
            body = full_match[json_start:]
            return header + body

        fence_pattern = r'```json:[a-zA-Z0-9_-]+[^\{]*'
        text = re.sub(fence_pattern, fix_fence_newlines, text)
        logger.debug("[PARSER] Converted literal \\n in fence markers")

    # --- 1. STRICT PASS (Priority) ---
    start_marker_regex = re.compile(rf"```json:\s*{re.escape(block_type)}", re.IGNORECASE)
    start_indices = [m.start() for m in start_marker_regex.finditer(text)]

    for start_idx in start_indices:
        json_start = text.find('{', start_idx)
        if json_start == -1: continue

        json_end = _find_balanced_json_end(text, json_start)
        if json_end == -1:
            logger.warning(f"Unbalanced braces in {block_type} block at index {start_idx}")
            continue

        json_str = text[json_start:json_end].strip()
        data = _parse_with_repair(json_str, block_type)
        
        if data:
            blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))
            logger.debug(f"[PARSER] Successfully parsed block at index {start_idx}")
        else:
            logger.warning(f"[PARSER] Valid brace structure but invalid JSON for {block_type} at index {start_idx}")

    if blocks:
        return blocks

    # --- 2. LOOSE PASS (Recovery) ---
    if block_type == 'function':
        logger.debug(f"[PARSER] Strict pass found 0 blocks, trying loose pass...")
        loose_pattern = r"```json\s*(\{[\s\S]*?\})\s*```"
        loose_matches = re.finditer(loose_pattern, text, re.IGNORECASE)

        for match in loose_matches:
            json_str = match.group(1)
            data = _parse_with_repair(json_str, block_type)
            if data and isinstance(data, dict) and "name" in data and "args" in data:
                logger.info(f"Recovered valid tool call from loose JSON block: {data['name']}")
                blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))

    return blocks


# ============================================================================
# Specialized Parsers
# ============================================================================

def parse_delegation_request(text: str) -> Optional[Dict[str, Any]]:
    block = extract_code_block(text, 'delegation')
    return block.data if block else None

def parse_function_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Extracts function calls and guarantees strict return typing.
    Filters out any blocks where 'name' is missing or not a string.
    """
    blocks = extract_all_code_blocks(text, 'function')
    results: List[Tuple[str, Dict[str, Any]]] = []
    
    for b in blocks:
        name = b.data.get('name')
        args = b.data.get('args', {})
        
        # Pylance Fix: Explicit check ensures 'name' is strictly 'str'
        if isinstance(name, str):
            results.append((name, args))
            
    return results

def has_delegation_request(text: str) -> bool:
    return bool(extract_code_block(text, 'delegation'))

def has_function_calls(text: str) -> bool:
    return bool(extract_all_code_blocks(text, 'function'))