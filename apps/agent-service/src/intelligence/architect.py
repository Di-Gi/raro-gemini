# [[RARO]]/apps/agent-service/src/intelligence/architect.py
# Purpose: Core Planning Engine (Flow A & C)
# Architecture: Intelligence Layer responsible for high-level reasoning and planning.
# Dependencies: google-genai, domain.protocol

# [[RARO]]/apps/agent-service/src/intelligence/architect.py
import json
import logging
from typing import Optional
from google import genai
# Import types to use for configuration
from google.genai import types 
from pydantic import ValidationError
from domain.protocol import WorkflowManifest, PatternDefinition, AgentRole
from intelligence.prompts import render_architect_prompt, render_safety_compiler_prompt
from core.config import logger, settings

class ArchitectEngine:
    """
    Engine that uses LLM reasoning to generate structured workflow plans 
    and safety patterns. Uses strict low-temperature settings for JSON consistency.
    """
    def __init__(self, client: genai.Client):
        self.client = client
        self.model = settings.MODEL_REASONING
        
        self.generation_config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json"
        )
    async def generate_plan(self, user_query: str) -> WorkflowManifest:
        """
        Translates a natural language user query into a WorkflowManifest (DAG).
        """
        if not self.client:
            raise ValueError("Gemini client is not initialized")

        prompt = render_architect_prompt(user_query)
        
        try:
            logger.info(f"Generating workflow plan for query: {user_query[:50]}...")
            
            # Pylance will now recognize self.generation_config as a valid type
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.generation_config
            )
            
            raw_text = response.text or "{}"
            data = json.loads(raw_text)

            # === SECURE IDENTITY COERCION ===
            valid_prefixes = ["research_", "analyze_", "coder_", "writer_", "master_"]

            if "agents" in data:
                for agent in data["agents"]:
                    aid = agent.get("id", "").lower()
                    prompt_text = agent.get("prompt", "").lower()

                    # 1. Enforcement: If no valid prefix, detect intent and force one
                    if not any(aid.startswith(p) for p in valid_prefixes):
                        original_id = aid
                        if any(k in prompt_text or k in aid for k in ["search", "find", "web", "lookup"]):
                            agent["id"] = f"research_{aid}"
                        elif any(k in prompt_text or k in aid for k in ["code", "script", "file", "save", "python"]):
                            agent["id"] = f"coder_{aid}"
                        elif any(k in prompt_text or k in aid for k in ["plot", "calc", "math", "viz", "analyze"]):
                            agent["id"] = f"analyze_{aid}"
                        else:
                            agent["id"] = f"analyze_{aid}" # Secure default

                        logger.warning(f"ID COERCION: '{original_id}' -> '{agent['id']}'")

                    # 2. Role Coercion (Ensure Enum safety)
                    valid_roles = [role.value for role in AgentRole]
                    if agent.get("role") not in valid_roles:
                        agent["role"] = AgentRole.WORKER.value

            manifest = WorkflowManifest(**data)
            return manifest

        except json.JSONDecodeError as e:
            logger.error(f"Architect produced invalid JSON: {e}")
            raise ValueError(f"Failed to parse architect response as JSON: {str(e)}")
        except ValidationError as e:
            logger.error(f"Architect plan failed schema validation: {e}")
            raise e
        except Exception as e:
            logger.error(f"Unexpected architect failure: {e}", exc_info=True)
            raise

    async def compile_pattern(self, policy_rule: str) -> PatternDefinition:
        """
        Translates a safety policy rule into a machine-readable PatternDefinition.
        """
        prompt = render_safety_compiler_prompt(policy_rule)
        
        try:
            logger.info(f"Compiling safety pattern: {policy_rule[:50]}...")
            
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self.generation_config
            )
            
            raw_text = response.text or "{}"
            data = json.loads(raw_text)
            
            pattern = PatternDefinition(**data)
            return pattern
                      
        except Exception as e:
            logger.error(f"Pattern compilation failure: {e}")
            raise

# Integration: Utilized by /plan and /compile-pattern endpoints in main.py.
# Notes: Uses temperature 0.1 to ensure output strictly follows the Pydantic schema.