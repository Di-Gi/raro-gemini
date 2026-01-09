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


def extract_code_block(text: str, block_type: str) -> Optional[ParsedBlock]:
    """
    Extract and parse a single code block of specified type from text.

    Args:
        text: The text to search for code blocks
        block_type: The type identifier (e.g., 'function', 'delegation')

    Returns:
        ParsedBlock if found and valid, None otherwise

    Example:
        ```json:function
        {
          "name": "web_search",
          "args": {"query": "test"}
        }
        ```

        extract_code_block(text, 'function') -> ParsedBlock(...)
    """
    # Pattern matches: ```json:TYPE \n { ... } \n ```
    # Uses re.DOTALL to match across newlines
    pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"

    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return None

    try:
        json_str = match.group(1)
        data = json.loads(json_str)
        return ParsedBlock(block_type=block_type, data=data, raw_json=json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in ```json:{block_type}``` block: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error parsing ```json:{block_type}``` block: {e}")
        return None


def extract_all_code_blocks(text: str, block_type: str) -> List[ParsedBlock]:
    """
    Extract and parse ALL code blocks of specified type from text.

    Args:
        text: The text to search for code blocks
        block_type: The type identifier (e.g., 'function', 'delegation')

    Returns:
        List of ParsedBlock objects (empty list if none found)

    Example:
        Multiple ```json:function blocks -> [ParsedBlock(...), ParsedBlock(...)]
    """
    # Pattern matches: ```json:TYPE \n { ... } \n ```
    pattern = rf"```json:{re.escape(block_type)}\s*(\{{[\s\S]*?\}})\s*```"

    matches = re.finditer(pattern, text, re.IGNORECASE)
    blocks = []

    for match in matches:
        try:
            json_str = match.group(1)
            data = json.loads(json_str)
            blocks.append(ParsedBlock(block_type=block_type, data=data, raw_json=json_str))
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in ```json:{block_type}``` block: {e}")
        except Exception as e:
            logger.error(f"Unexpected error parsing ```json:{block_type}``` block: {e}")

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

    Example Output:
        {
            "reason": "Need to fetch data",
            "new_nodes": [...]
        }
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

    Example Output:
        [
            ("web_search", {"query": "test"}),
            ("execute_python", {"code": "print('hello')"})
        ]
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
