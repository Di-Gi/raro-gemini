# [[RARO]]/apps/agent-service/src/utils/schema_formatter.py
# Purpose: JSON Schema Extraction Helper
# Architecture: Utility Layer

import json
from pydantic import BaseModel
from typing import Type

def get_clean_schema_json(model_class: Type[BaseModel]) -> str:
    """
    Extracts a clean JSON schema from a Pydantic model.
    Removes extraneous 'definitions' if possible to save tokens.
    """
    try:
        # Generate the schema
        schema = model_class.model_json_schema()
        
        # Serialize to pretty JSON
        return json.dumps(schema, indent=2)
    except Exception as e:
        return f"{{ 'error': 'Schema generation failed: {str(e)}' }}"