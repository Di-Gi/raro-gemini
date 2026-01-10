# [[RARO]]/apps/agent-service/src/core/parsers.py
# Purpose: Unified Parser Module for Markdown Code Block Extraction
# Architecture: Core Layer - Shared parsing utilities
# Dependencies: re, json, typing

import re
import json
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
    # If we find a backslash followed by anything else (like 'd' in '\d'), double it.
    pattern = r'\\(?![/u"\\bfnrt])'
    
    # Replace single backslash with double backslash
    return re.sub(pattern, r'\\\\', json_str)


def _parse_with_repair(json_str: str, block_type: str) -> Optional[Dict[str, Any]]:
    """
    Helper to parse JSON with a fallback repair mechanism.
    
    1. Tries standard json.loads().
    2. If that fails, applies _repair_json_string() and tries again.
    3. Logs warnings/errors appropriately.
    """
    try:
        # Attempt 1: Standard Parse
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # Attempt 2: Auto-Repair (Fix regex backslashes)
            logger.warning(f"Initial JSON parse failed for ```json:{block_type}```, attempting regex repair...")
            
            repaired_json = _repair_json_string(json_str)
            data = json.loads(repaired_json)
            
            logger.info(f"JSON repair successful for ```json:{block_type}```.")
            return data
        except json.JSONDecodeError as e:
            # Attempt 3: Final Failure
            logger.error(f"Failed to parse ```json:{block_type}``` block even after repair: {e}")
            logger.debug(f"Failed JSON content: {json_str[:200]}...") # Log partial content for debug
            return None


def extract_code_block(text: str, block_type: str) -> Optional[ParsedBlock]:
    """
    Extract and parse a single code block of specified type from text.

    Args:
        text: The text to search for code blocks
        block_type: The type identifier (e.g., 'function', 'delegation')

    Returns:
        ParsedBlock if found and valid, None otherwise
    """
    # Pattern matches: ```json:TYPE \n { ... } \n ```
    # [\s\S]*? matches any character including newlines, non-greedy
    pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"

    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return None

    json_str = match.group(1)
    
    # Use robust parsing helper
    data = _parse_with_repair(json_str, block_type)

    if data:
        return ParsedBlock(block_type=block_type, data=data, raw_json=json_str)
    
    return None


def extract_all_code_blocks(text: str, block_type: str) -> List[ParsedBlock]:
    """
    Extract and parse ALL code blocks of specified type from text.

    Args:
        text: The text to search for code blocks
        block_type: The type identifier (e.g., 'function', 'delegation')

    Returns:
        List of ParsedBlock objects (empty list if none found)
    """
    pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"

    matches = re.finditer(pattern, text, re.IGNORECASE)
    blocks = []

    for match in matches:
        json_str = match.group(1)
        
        # Use robust parsing helper
        data = _parse_with_repair(json_str, block_type)
        
        if data:
            blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))
        else:
            # Error already logged in _parse_with_repair
            pass

    return blocks


# ============================================================================
# Specialized Parsers for Delegation and Function Calls
# ============================================================================

def parse_delegation_request(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse a delegation request from agent output.

    Searches for ```json:delegation blocks and extracts the structured data.

    Args:
        text: The agent's output text

    Returns:
        Dictionary with delegation data if found, None otherwise
    """
    block = extract_code_block(text, 'delegation')
    return block.data if block else None


def parse_function_calls(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Parse function call requests from agent output.

    Searches for ```json:function blocks and extracts tool invocations.

    Args:
        text: The agent's output text

    Returns:
        List of tuples: [(tool_name, args), ...]
    """
    blocks = extract_all_code_blocks(text, 'function')

    function_calls = []
    for block in blocks:
        tool_name = block.data.get('name')
        tool_args = block.data.get('args', {})

        if not tool_name:
            logger.warning(f"Function block missing 'name' field: {block.raw_json[:100]}")
            continue

        function_calls.append((tool_name, tool_args))

    return function_calls


def has_delegation_request(text: str) -> bool:
    """
    Quick check if text contains a delegation request.

    Args:
        text: The text to check

    Returns:
        True if delegation block is present, False otherwise
    """
    pattern = r"```json:delegation\s*\{[\s\S]*?\}\s*```"
    return bool(re.search(pattern, text, re.IGNORECASE))


def has_function_calls(text: str) -> bool:
    """
    Quick check if text contains function call requests.

    Args:
        text: The text to check

    Returns:
        True if function block is present, False otherwise
    """
    pattern = r"```json:function\s*\{[\s\S]*?\}\s*```"
    return bool(re.search(pattern, text, re.IGNORECASE))