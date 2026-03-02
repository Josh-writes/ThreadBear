"""
API clients for different AI providers (proxy-safe, correct Gemini endpoints, stable streaming)
"""
from __future__ import annotations
import os
import json
import time
import requests
from typing import List, Dict, Iterator, Union
from error_classifier import LLMApiError

# Local (no proxy) -> use for llama.cpp localhost calls
_local_session = requests.Session()
_local_session.trust_env = False  # bypass proxies only for localhost

# Web (default env/proxy/CA) -> use for Groq/Google/Mistral/OpenRouter
_web_session = requests.Session()  # trust_env True by default



# -------- utilities --------
def estimate_tokens(text: str) -> int:
    return len(text) // 4


def _repair_json(s: str) -> str:
    """
    Multi-strategy JSON repair for tool call arguments from streaming.

    Strategies (tried in order):
    1. Direct parse — already valid JSON
    2. Brace completion — missing closing braces/brackets (original logic)
    3. Extract from wrapping — JSON embedded in markdown fences or text
    4. Fix common syntax issues — unquoted keys, trailing commas
    5. Fallback — return '{}'

    Returns valid JSON string or '{}' if all strategies fail.
    """
    s = s.strip()
    if not s:
        return '{}'

    # Strategy 1: Direct parse
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # Strategy 2: Brace/bracket completion (original logic)
    repaired = _try_brace_completion(s)
    if repaired:
        return repaired

    # Strategy 3: Extract JSON from wrapping text
    extracted = _try_extract_json(s)
    if extracted:
        return extracted

    # Strategy 4: Fix common syntax issues
    fixed = _try_fix_syntax(s)
    if fixed:
        return fixed

    # Fallback
    return '{}'


def _try_brace_completion(s: str) -> str | None:
    """Fix missing closing braces/brackets from streaming truncation."""
    candidate = s
    opens = candidate.count('{') - candidate.count('}')
    candidate += '}' * max(0, opens)
    opens = candidate.count('[') - candidate.count(']')
    candidate += ']' * max(0, opens)
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        return None


def _try_extract_json(s: str) -> str | None:
    """
    Extract JSON object from surrounding text.

    Handles:
    - Markdown code fences: ```json\n{...}\n```
    - JSON buried in explanatory text: "Here are the args: {...}"
    - Multiple JSON objects (takes the first valid one)
    """
    import re

    # Try markdown code fence extraction first
    fence_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', s, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Try brace completion on extracted content
            completed = _try_brace_completion(candidate)
            if completed:
                return completed

    # Try to find a JSON object anywhere in the string
    # Look for the first { and find its matching }
    start = s.find('{')
    if start == -1:
        return None

    # Try progressively from each } working backwards
    for end in range(len(s) - 1, start, -1):
        if s[end] == '}':
            candidate = s[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

    # Try brace completion on substring from first {
    candidate = s[start:]
    completed = _try_brace_completion(candidate)
    if completed:
        return completed

    return None


def _try_fix_syntax(s: str) -> str | None:
    """
    Fix common JSON syntax issues from weak models.

    Handles:
    - Trailing commas: {"a": 1,}
    - Unquoted keys: {path: "/tmp/foo"}
    - Single quotes: {'path': '/tmp/foo'}
    """
    import re

    candidate = s

    # Fix single quotes to double quotes (careful: don't break apostrophes in values)
    # Only do this if there are no double quotes at all (model used all single quotes)
    if "'" in candidate and '"' not in candidate:
        candidate = candidate.replace("'", '"')

    # Fix unquoted keys: {path: "value"} -> {"path": "value"}
    candidate = re.sub(
        r'(?<=[\{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r' "\1":',
        candidate
    )

    # Fix trailing commas before closing brace/bracket
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)

    # Try brace completion + parse
    completed = _try_brace_completion(candidate)
    if completed:
        return completed

    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        return None


# --- Groq ---
def fetch_groq_catalog(api_key: str) -> List[Dict]:
    """Fetch the model catalog from Groq's API (requires auth)."""
    try:
        resp = _web_session.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        catalog = []
        for m in data:
            if not m.get("active", True):
                continue
            catalog.append({
                "id": m.get("id", ""),
                "name": m.get("owned_by", m.get("id", "")),
                "context_length": m.get("context_window", 0),
                "prompt_price": None,
                "completion_price": None,
                "is_free": False,
            })
        return catalog
    except Exception as e:
        print(f"Failed to fetch Groq catalog: {e}")
        return []


def call_groq(messages: List[Dict], config: Dict) -> str:
    try:
        from config_manager import ConfigManager
        cfg_manager = ConfigManager()
        
        api_key = os.getenv('GROQ_API_KEY') or config.get('groq_api_key')
        if not api_key or api_key == "your_groq_api_key_here":
            return "Error: Groq API key not found. Please set GROQ_API_KEY environment variable."
        # Build messages with optional system prompt
        api_messages = []
        system_prompt = config.get("groq_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)
        # Model + base URL
        model_name = (config.get("model") or config.get("groq_model") or "llama-3.3-70b-versatile")
        if not model_name:
            return "Error: missing model for Groq request."
        base_url = (config.get("groq_base_url") or "https://api.groq.com/openai/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("groq_temperature", 0.7),
            "stream": False
        }
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            # Some providers ignore top_k; harmless to pass if supported.
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "groq" == "groq" else req)
        response = _web_session.post(url, headers=headers, json=data, timeout=60)
        # Single-path fallback if Groq ever serves without '/openai'
        if response.status_code == 404 and "/openai/" in url:
            fallback_url = url.replace("/openai/", "/")
            response = _web_session.post(fallback_url, headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return f"Error: {response.status_code} {response.reason} - {response.text} (model={model_name}, url={url})"
    except Exception as e:
        return f"Groq API error: {str(e)}"

def call_groq_stream(messages: List[Dict], config: Dict,
                     tools: List[Dict] = None) -> Iterator[Union[str, Dict]]:
    """
    Enhanced to handle tool calls in streaming responses.
    Yields: str for content chunks, dict for tool calls {'type': 'tool_calls', 'tool_calls': [...]}
    Final yield (if usage available): dict {'type': 'usage', 'input_tokens': N, 'output_tokens': N}
    """
    try:
        api_key = os.getenv('GROQ_API_KEY') or config.get('groq_api_key')
        if not api_key or api_key == "your_groq_api_key_here":
            yield f"data: {json.dumps({'type':'error','content':'Groq API key not found. Please set GROQ_API_KEY environment variable.'})}\n\n"
            yield f"data: {json.dumps({'type':'complete'})}\n\n"
            return
        api_messages = []
        system_prompt = config.get("groq_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)
        model_name = (config.get("model") or config.get("groq_model") or "llama-3.3-70b-versatile")
        if not model_name:
            yield f"data: {json.dumps({'type':'error','content':'Missing model for Groq request.'})}\n\n"
            yield f"data: {json.dumps({'type':'complete'})}\n\n"
            return
        base_url = (config.get("groq_base_url") or "https://api.groq.com/openai/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("groq_temperature", 0.7),
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        # Add tools if provided
        if tools:
            data['tools'] = tools
            data['tool_choice'] = 'auto'
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "groq" == "groq" else req)
        response = _web_session.post(url, headers=headers, json=data, stream=True, timeout=60)
        # Single-path fallback if Groq ever serves without '/openai'
        if response.status_code == 404 and "/openai/" in url:
            fallback_url = url.replace("/openai/", "/")
            response = _web_session.post(fallback_url, headers=headers, json=data, stream=True, timeout=60)
        
        # Track accumulated tool calls across streaming deltas
        tool_calls_acc = {}  # index -> {id, function: {name, arguments}}
        usage_data = None  # Capture usage from final chunk

        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text, "groq")

        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8', errors='ignore')
            if line.startswith('data: '):
                payload = line[6:]
                if payload.strip() == '[DONE]':
                    break
                try:
                    chunk = json.loads(payload)
                    # Capture usage data from stream (arrives in final chunk)
                    if 'usage' in chunk and chunk['usage']:
                        u = chunk['usage']
                        usage_data = {
                            'type': 'usage',
                            'input_tokens': u.get('prompt_tokens', 0),
                            'output_tokens': u.get('completion_tokens', 0),
                        }
                    if 'choices' in chunk and chunk['choices']:
                        delta = chunk['choices'][0].get('delta', {})
                        # Content chunks
                        content = delta.get('content', '')
                        if content:
                            yield content
                        # Tool call deltas
                        if delta.get('tool_calls'):
                            for tc_delta in delta['tool_calls']:
                                idx = tc_delta.get('index', 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        'id': tc_delta.get('id', f'call_{idx}'),
                                        'type': 'function',
                                        'function': {'name': '', 'arguments': ''}
                                    }
                                if tc_delta.get('function', {}).get('name'):
                                    tool_calls_acc[idx]['function']['name'] = tc_delta['function']['name']
                                if tc_delta.get('function', {}).get('arguments'):
                                    tool_calls_acc[idx]['function']['arguments'] += tc_delta['function']['arguments']
                        # On finish_reason='tool_calls', yield accumulated tool calls
                        finish = chunk['choices'][0].get('finish_reason')
                        if finish == 'tool_calls' and tool_calls_acc:
                            # Auto-repair incomplete JSON arguments
                            for tc in tool_calls_acc.values():
                                tc['function']['arguments'] = _repair_json(tc['function']['arguments'])
                            yield {'type': 'tool_calls', 'tool_calls': list(tool_calls_acc.values())}
                            tool_calls_acc = {}
                except json.JSONDecodeError:
                    continue
        # Yield usage data at the end of the stream
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), "groq") from e


# --- Google (Gemini) ---
def fetch_google_catalog(api_key: str) -> List[Dict]:
    """Fetch the model catalog from Google's Gemini API (requires auth)."""
    # Known deprecated models to exclude
    DEPRECATED_PREFIXES = ("gemini-1.5", "gemini-pro", "gemini-ultra")
    try:
        resp = _web_session.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 1000},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("models", [])
        catalog = []
        for m in data:
            # Only include models that support generateContent (chat)
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            # name is like "models/gemini-1.5-pro-001" — strip prefix
            raw_name = m.get("name", "")
            model_id = raw_name.replace("models/", "") if raw_name.startswith("models/") else raw_name
            # Skip deprecated models
            if any(model_id.startswith(p) for p in DEPRECATED_PREFIXES):
                continue
            catalog.append({
                "id": model_id,
                "name": m.get("displayName", model_id),
                "context_length": m.get("inputTokenLimit", 0),
                "prompt_price": None,
                "completion_price": None,
                "is_free": False,
            })
        return catalog
    except Exception as e:
        print(f"Failed to fetch Google catalog: {e}")
        return []


def call_google_stream(messages: List[Dict], config: Dict, tools=None) -> Iterator[Union[str, Dict]]:
    """
    True streaming adapter for Google Gemini API using streamGenerateContent endpoint.
    Uses alt=sse to get Server-Sent Events format from Gemini.
    Yields plain text chunks (the flask layer wraps them in SSE for the frontend).
    Final yield (if usage available): dict {'type': 'usage', 'input_tokens': N, 'output_tokens': N}
    """
    try:
        api_key = os.getenv('GOOGLE_API_KEY') or config.get('google_api_key')
        if not api_key or api_key == "your_google_api_key_here":
            yield "Error: Google API key not found. Please set GOOGLE_API_KEY environment variable."
            return

        model_name = config.get("google_model", "gemini-2.0-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent"

        full_prompt = ""
        system_instruction = config.get("google_system_prompt", "")
        if system_instruction:
            full_prompt += f"System: {system_instruction}\n\n"
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            full_prompt += f"{role}: {msg['content']}\n\n"

        gen_cfg = {
            "temperature": config.get("google_temperature", 0.7),
            "topP": 0.8,
            "topK": 10
        }
        if "top_p" in config:
            gen_cfg["topP"] = config["top_p"]
        if "top_k" in config:
            gen_cfg["topK"] = config["top_k"]

        req = (config.get("max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            gen_cfg["maxOutputTokens"] = req

        payload = {
            "contents": [{"parts": [{"text": full_prompt.strip()}]}],
            "generationConfig": gen_cfg
        }
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key, "alt": "sse"}

        response = _web_session.post(url, headers=headers, json=payload, params=params, stream=True, timeout=60)

        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text or response.reason, "google")

        usage_data = None
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8', errors='ignore')
            if line.startswith('data: '):
                payload_str = line[6:].strip()
                if not payload_str or payload_str == '[DONE]':
                    continue
                try:
                    chunk_data = json.loads(payload_str)
                    # Capture usage metadata (arrives in final chunk)
                    if 'usageMetadata' in chunk_data:
                        um = chunk_data['usageMetadata']
                        usage_data = {
                            'type': 'usage',
                            'input_tokens': um.get('promptTokenCount', 0),
                            'output_tokens': um.get('candidatesTokenCount', 0),
                        }
                    if 'candidates' in chunk_data and chunk_data['candidates']:
                        candidate = chunk_data['candidates'][0]
                        if 'content' in candidate:
                            parts = candidate['content'].get('parts', [])
                            for part in parts:
                                if 'text' in part and part['text']:
                                    yield part['text']
                except json.JSONDecodeError:
                    continue
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), "google") from e

def call_google(messages: List[Dict], config: Dict) -> str:
    try:
        api_key = os.getenv('GOOGLE_API_KEY') or config.get('google_api_key')
        if not api_key or api_key == "your_google_api_key_here":
            return "Error: Google API key not found. Please set GOOGLE_API_KEY environment variable."
        model_name = config.get("google_model", "gemini-2.0-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

        full_prompt = ""
        system_instruction = config.get("google_system_prompt", "")
        if system_instruction:
            full_prompt += f"System: {system_instruction}\n\n"
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            full_prompt += f"{role}: {msg['content']}\n\n"

        gen_cfg = {
            "temperature": config.get("google_temperature", 0.7),
            "topP": 0.8,
            "topK": 10
        }
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            gen_cfg["topP"] = config["top_p"]
        if "top_k" in config:
            # Some providers ignore top_k; harmless to pass if supported.
            gen_cfg["topK"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            gen_cfg["maxOutputTokens"] = min(req, 4096 if "google" == "groq" else req)

        payload = {
            "contents": [{"parts": [{"text": full_prompt.strip()}]}],
            "generationConfig": gen_cfg
        }
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key}

        response = _web_session.post(url, headers=headers, json=payload, params=params, timeout=60)
        if response.status_code == 200:
            result = response.json()
            if "candidates" in result and result["candidates"]:
                return result["candidates"][0]["content"]["parts"][0]["text"]
            return "Error: No response from Google Gemini API"
        else:
            return f"Error: {response.status_code} {response.reason} - {response.text}"
    except Exception as e:
        return f"Google API error: {str(e)}"


# --- Mistral ---
def fetch_mistral_catalog(api_key: str) -> List[Dict]:
    """Fetch the model catalog from Mistral's API (requires auth)."""
    try:
        resp = _web_session.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        catalog = []
        for m in data:
            if m.get("archived", False):
                continue
            # Only include models that support chat
            caps = m.get("capabilities", {})
            if not caps.get("completion_chat", True):
                continue
            catalog.append({
                "id": m.get("id", ""),
                "name": m.get("name") or m.get("id", ""),
                "context_length": m.get("max_context_length", 0),
                "prompt_price": None,
                "completion_price": None,
                "is_free": False,
            })
        return catalog
    except Exception as e:
        print(f"Failed to fetch Mistral catalog: {e}")
        return []


def call_mistral(messages: List[Dict], config: Dict) -> str:
    try:
        from config_manager import ConfigManager
        cfg_manager = ConfigManager()
        
        api_key = os.getenv('MISTRAL_API_KEY') or config.get('mistral_api_key')
        if not api_key or api_key == "your_mistral_api_key_here":
            return "Error: Mistral API key not found. Please set MISTRAL_API_KEY environment variable."
        api_messages = []
        system_prompt = config.get("mistral_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        model_name = config.get("mistral_model", "mistral-medium-latest")
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("mistral_temperature", 0.7)
        }
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            # Some providers ignore top_k; harmless to pass if supported.
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "mistral" == "groq" else req)

        response = _web_session.post("https://api.mistral.ai/v1/chat/completions",
                                 headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        else:
            return f"Error: {response.status_code} {response.reason} - {response.text}"
    except Exception as e:
        return f"Mistral API error: {str(e)}"


def call_mistral_stream(messages: List[Dict], config: Dict, tools=None) -> Iterator[Union[str, Dict]]:
    try:
        from config_manager import ConfigManager
        cfg_manager = ConfigManager()
        
        api_key = os.getenv('MISTRAL_API_KEY') or config.get('mistral_api_key')
        if not api_key or api_key == "your_mistral_api_key_here":
            yield f"data: {json.dumps({'type':'error','content':'Mistral API key not found. Please set MISTRAL_API_KEY environment variable.'})}\n\n"
            yield f"data: {json.dumps({'type':'complete'})}\n\n"
            return
        api_messages = []
        system_prompt = config.get("mistral_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        model_name = config.get("mistral_model", "mistral-medium-latest")
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("mistral_temperature", 0.7),
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        # Add tools if provided
        if tools:
            data['tools'] = tools
            data['tool_choice'] = 'auto'
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "mistral" == "groq" else req)

        response = _web_session.post("https://api.mistral.ai/v1/chat/completions",
                                 headers=headers, json=data, stream=True, timeout=60)
        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text, "mistral")

        tool_calls_acc = {}  # index -> {id, function: {name, arguments}}
        usage_data = None
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    line = line[6:]
                    if line.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(line)
                        if 'usage' in chunk and chunk['usage']:
                            u = chunk['usage']
                            usage_data = {
                                'type': 'usage',
                                'input_tokens': u.get('prompt_tokens', 0),
                                'output_tokens': u.get('completion_tokens', 0),
                            }
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                            # Tool call deltas
                            if delta.get('tool_calls'):
                                for tc_delta in delta['tool_calls']:
                                    idx = tc_delta.get('index', 0)
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {
                                            'id': tc_delta.get('id', f'call_{idx}'),
                                            'type': 'function',
                                            'function': {'name': '', 'arguments': ''}
                                        }
                                    if tc_delta.get('function', {}).get('name'):
                                        tool_calls_acc[idx]['function']['name'] = tc_delta['function']['name']
                                    if tc_delta.get('function', {}).get('arguments'):
                                        tool_calls_acc[idx]['function']['arguments'] += tc_delta['function']['arguments']
                            # On finish_reason='tool_calls', yield accumulated tool calls
                            finish = chunk['choices'][0].get('finish_reason')
                            if finish == 'tool_calls' and tool_calls_acc:
                                for tc in tool_calls_acc.values():
                                    tc['function']['arguments'] = _repair_json(tc['function']['arguments'])
                                yield {'type': 'tool_calls', 'tool_calls': list(tool_calls_acc.values())}
                                tool_calls_acc = {}
                    except json.JSONDecodeError:
                        continue
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), "mistral") from e


# --- llama.cpp (remote server) ---
def _llamacpp_base_from(config: Dict) -> str:
    """Get llama.cpp server base URL from config."""
    base = config.get("llamacpp_url", "http://127.0.0.1:8080")
    return base.rstrip("/")

def _llamacpp_sanitize_messages(api_messages: List[Dict]) -> List[Dict]:
    """
    Sanitize messages for llama.cpp chat templates that require strict
    user/assistant alternation. Folds system messages into adjacent user
    messages and merges consecutive same-role messages.
    """
    if not api_messages:
        return api_messages

    # Step 0: Separate tool-related messages (pass through unchanged)
    # assistant messages with tool_calls and role='tool' messages are part of
    # the tool loop and must not be mangled by sanitization.
    tool_messages = []
    regular_messages = []
    for msg in api_messages:
        if msg.get("role") == "tool" or msg.get("tool_calls"):
            tool_messages.append(msg)
        else:
            regular_messages.append(msg)

    # If we have tool messages, pass everything through unchanged —
    # the model needs the exact tool call/response structure
    if tool_messages:
        return api_messages

    # Step 1: Separate leading system messages from the rest
    system_parts = []
    rest = []
    for msg in regular_messages:
        if msg["role"] == "system" and not rest:
            system_parts.append(msg["content"])
        else:
            rest.append(msg)

    # Step 2: Fold non-leading system messages into adjacent user messages
    merged = []
    for msg in rest:
        if msg["role"] == "system":
            # Attach to the previous user message, or buffer for the next one
            if merged and merged[-1]["role"] == "user":
                merged[-1]["content"] += "\n\n" + msg["content"]
            else:
                # Buffer as user context (will merge with next user msg in step 3)
                merged.append({"role": "user", "content": msg["content"]})
        else:
            merged.append({"role": msg["role"], "content": msg["content"]})

    # Step 3: Prepend collected system prompt to the first user message
    if system_parts:
        system_block = "\n\n".join(system_parts)
        first_user = next((m for m in merged if m["role"] == "user"), None)
        if first_user:
            first_user["content"] = system_block + "\n\n" + first_user["content"]
        else:
            # No user message at all; inject as one
            merged.insert(0, {"role": "user", "content": system_block})

    # Step 4: Merge consecutive same-role messages
    collapsed = []
    for msg in merged:
        if collapsed and collapsed[-1]["role"] == msg["role"]:
            collapsed[-1]["content"] += "\n\n" + msg["content"]
        else:
            collapsed.append({"role": msg["role"], "content": msg["content"]})

    # Step 5: Ensure conversation starts with user (some templates require it)
    if collapsed and collapsed[0]["role"] == "assistant":
        collapsed.insert(0, {"role": "user", "content": "(continuing conversation)"})

    return collapsed

def get_llamacpp_context_size(config_or_url=None) -> int:
    """Query llama.cpp /slots endpoint for n_ctx. Returns 0 on failure."""
    if isinstance(config_or_url, dict):
        base = config_or_url.get("llamacpp_url", "http://127.0.0.1:8080")
    elif isinstance(config_or_url, str) and config_or_url.strip():
        base = config_or_url.strip()
    else:
        base = "http://127.0.0.1:8080"
    try:
        resp = _local_session.get(f"{base.rstrip('/')}/slots", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return int(data[0].get("n_ctx", 0))
        if isinstance(data, dict):
            return int(data.get("n_ctx", 0))
    except Exception:
        pass
    return 0

def get_available_llamacpp_models(config_or_url=None) -> List[str]:
    """
    Query a llama.cpp server for available models.
    Returns a list of model names. Returns [] on failure.
    """
    if isinstance(config_or_url, dict):
        base = config_or_url.get("llamacpp_url", "http://127.0.0.1:8080")
    elif isinstance(config_or_url, str) and config_or_url.strip():
        base = config_or_url.strip()
    else:
        base = "http://127.0.0.1:8080"

    url = f"{base.rstrip('/')}/v1/models"
    try:
        resp = _local_session.get(url, timeout=6)
        resp.raise_for_status()
        data = resp.json() or {}
        models = data.get("data", [])
        return [m.get("id", "") for m in models if m.get("id")]
    except Exception:
        return []


def _resolve_llamacpp_model(config: Dict) -> str:
    """Query the llama.cpp server's /v1/models and return whatever model is loaded.

    The user loads models manually on the server, so we just ask the server
    what it has rather than relying on a configured name.
    """
    try:
        base = _llamacpp_base_from(config)
        resp = _local_session.get(f"{base}/v1/models", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            ids = [m.get("id", "") for m in models if m.get("id")]
            if ids:
                return ids[0]
    except Exception:
        pass
    # Fallback if server is unreachable
    return config.get("llamacpp_model", "model")


def call_llamacpp(messages: List[Dict], config: Dict) -> str:
    """
    Call llama.cpp server API (OpenAI-compatible) synchronously.
    """
    try:
        base = _llamacpp_base_from(config)
        url = f"{base}/v1/chat/completions"

        # Build messages with optional system prompt
        api_messages = []
        system_prompt = config.get("llamacpp_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        # Sanitize for strict role-alternation chat templates
        api_messages = _llamacpp_sanitize_messages(api_messages)

        model_name = _resolve_llamacpp_model(config)

        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("llamacpp_temperature", 0.7),
            "stream": False
        }

        # Optional sampling params
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            data["top_k"] = config["top_k"]

        # Max tokens
        req = (config.get("max_tokens")
               or config.get("llamacpp_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = req

        headers = {"Content-Type": "application/json"}
        response = _local_session.post(url, headers=headers, json=data, timeout=300)

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return f"Error: {response.status_code} {response.reason} - {response.text}"
    except Exception as e:
        return f"llama.cpp API error: {str(e)}"

def call_llamacpp_stream(messages: List[Dict], config: Dict, tools=None) -> Iterator[Union[str, Dict]]:
    """
    Call llama.cpp server API with streaming response.
    Yields message chunks progressively.
    """
    try:
        base = _llamacpp_base_from(config)
        url = f"{base}/v1/chat/completions"

        # Build messages with optional system prompt
        api_messages = []
        system_prompt = config.get("llamacpp_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        # Sanitize for strict role-alternation chat templates
        api_messages = _llamacpp_sanitize_messages(api_messages)

        model_name = _resolve_llamacpp_model(config)

        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("llamacpp_temperature", 0.7),
            "stream": True
        }

        # Add tools if provided
        if tools:
            data['tools'] = tools
            data['tool_choice'] = 'auto'

        # Optional sampling params
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            data["top_k"] = config["top_k"]

        # Max tokens
        req = (config.get("max_tokens")
               or config.get("llamacpp_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = req

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }

        # Use longer timeout for inference (3090 can take time on large contexts)
        response = _local_session.post(url, headers=headers, json=data, stream=True, timeout=300)

        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text, "llamacpp")

        tool_calls_acc = {}  # index -> {id, function: {name, arguments}}
        usage_data = None
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8', errors='ignore')
            if line.startswith('data: '):
                payload = line[6:]
                if payload.strip() == '[DONE]':
                    break
                try:
                    chunk = json.loads(payload)
                    if 'usage' in chunk and chunk['usage']:
                        u = chunk['usage']
                        usage_data = {
                            'type': 'usage',
                            'input_tokens': u.get('prompt_tokens', 0),
                            'output_tokens': u.get('completion_tokens', 0),
                        }
                    if 'choices' in chunk and chunk['choices']:
                        delta = chunk['choices'][0].get('delta', {})
                        content = delta.get('content', '')
                        if content:
                            yield content
                        # Tool call deltas
                        if delta.get('tool_calls'):
                            for tc_delta in delta['tool_calls']:
                                idx = tc_delta.get('index', 0)
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        'id': tc_delta.get('id', f'call_{idx}'),
                                        'type': 'function',
                                        'function': {'name': '', 'arguments': ''}
                                    }
                                if tc_delta.get('function', {}).get('name'):
                                    tool_calls_acc[idx]['function']['name'] = tc_delta['function']['name']
                                if tc_delta.get('function', {}).get('arguments'):
                                    tool_calls_acc[idx]['function']['arguments'] += tc_delta['function']['arguments']
                        # On finish_reason='tool_calls', yield accumulated tool calls
                        finish = chunk['choices'][0].get('finish_reason')
                        if finish == 'tool_calls' and tool_calls_acc:
                            for tc in tool_calls_acc.values():
                                tc['function']['arguments'] = _repair_json(tc['function']['arguments'])
                            yield {'type': 'tool_calls', 'tool_calls': list(tool_calls_acc.values())}
                            tool_calls_acc = {}
                except json.JSONDecodeError:
                    continue
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), "llamacpp") from e


# --- OpenRouter ---
def fetch_openrouter_catalog() -> List[Dict]:
    """Fetch the full model catalog from OpenRouter (no auth needed)."""
    try:
        resp = _web_session.get("https://openrouter.ai/api/v1/models", timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        catalog = []
        for m in data:
            pricing = m.get("pricing", {})
            prompt_price = str(pricing.get("prompt", "0"))
            completion_price = str(pricing.get("completion", "0"))
            try:
                p_val = float(prompt_price)
            except (ValueError, TypeError):
                p_val = 0
            try:
                c_val = float(completion_price)
            except (ValueError, TypeError):
                c_val = 0
            catalog.append({
                "id": m.get("id", ""),
                "name": m.get("name", m.get("id", "")),
                "context_length": m.get("context_length", 0),
                "prompt_price": prompt_price,
                "completion_price": completion_price,
                "is_free": p_val == 0 and c_val == 0,
            })
        return catalog
    except Exception as e:
        print(f"Failed to fetch OpenRouter catalog: {e}")
        return []


def call_openrouter(messages: List[Dict], config: Dict) -> str:
    try:
        from config_manager import ConfigManager
        cfg_manager = ConfigManager()
        
        api_key = os.getenv('OPENROUTER_API_KEY') or config.get('openrouter_api_key')
        if not api_key or api_key == "your_openrouter_api_key_here":
            return "Error: OpenRouter API key not found. Please set OPENROUTER_API_KEY environment variable."
        api_messages = []
        system_prompt = config.get("openrouter_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8080",
            "X-Title": "AI Chat App"
        }
        model_name = config.get("openrouter_model", "meta-llama/llama-3.2-3b-instruct:free")
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("openrouter_temperature", 0.7)
        }
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            # Some providers ignore top_k; harmless to pass if supported.
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "openrouter" == "groq" else req)

        response = _web_session.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        else:
            return f"Error: {response.status_code} {response.reason} - {response.text}"
    except Exception as e:
        return f"OpenRouter API error: {str(e)}"


def call_openrouter_stream(messages: List[Dict], config: Dict, tools=None) -> Iterator[Union[str, Dict]]:
    try:
        from config_manager import ConfigManager
        cfg_manager = ConfigManager()
        
        api_key = os.getenv('OPENROUTER_API_KEY') or config.get('openrouter_api_key')
        if not api_key or api_key == "your_openrouter_api_key_here":
            yield f"data: {json.dumps({'type':'error','content':'OpenRouter API key not found. Please set OPENROUTER_API_KEY environment variable.'})}\n\n"
            yield f"data: {json.dumps({'type':'complete'})}\n\n"
            return
        api_messages = []
        system_prompt = config.get("openrouter_system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8080",
            "X-Title": "AI Chat App"
        }
        model_name = config.get("openrouter_model", "meta-llama/llama-3.2-3b-instruct:free")
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get("openrouter_temperature", 0.7),
            "stream": True
        }
        # Add tools if provided
        if tools:
            data['tools'] = tools
            data['tool_choice'] = 'auto'
        # Optional sampling params (per-model/per-provider)
        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            # Some providers ignore top_k; harmless to pass if supported.
            data["top_k"] = config["top_k"]

        # Respect requested max_tokens if supplied
        req = (config.get("max_tokens")
               or config.get("openrouter_max_tokens")
               or config.get("groq_max_tokens")
               or config.get("mistral_max_tokens")
               or config.get("google_max_tokens"))
        if isinstance(req, int) and req > 0:
            data["max_tokens"] = min(req, 4096 if "openrouter" == "groq" else req)

        response = _web_session.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers=headers, json=data, stream=True, timeout=(15, 120))
        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text, "openrouter")

        tool_calls_acc = {}  # index -> {id, function: {name, arguments}}
        usage_data = None
        for line in response.iter_lines(decode_unicode=False):
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    line = line[6:]
                    if line.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(line)
                        if 'usage' in chunk and chunk['usage']:
                            u = chunk['usage']
                            usage_data = {
                                'type': 'usage',
                                'input_tokens': u.get('prompt_tokens', 0),
                                'output_tokens': u.get('completion_tokens', 0),
                            }
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                            # Tool call deltas
                            if delta.get('tool_calls'):
                                for tc_delta in delta['tool_calls']:
                                    idx = tc_delta.get('index', 0)
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {
                                            'id': tc_delta.get('id', f'call_{idx}'),
                                            'type': 'function',
                                            'function': {'name': '', 'arguments': ''}
                                        }
                                    if tc_delta.get('function', {}).get('name'):
                                        tool_calls_acc[idx]['function']['name'] = tc_delta['function']['name']
                                    if tc_delta.get('function', {}).get('arguments'):
                                        tool_calls_acc[idx]['function']['arguments'] += tc_delta['function']['arguments']
                            # On finish_reason='tool_calls', yield accumulated tool calls
                            finish = chunk['choices'][0].get('finish_reason')
                            if finish == 'tool_calls' and tool_calls_acc:
                                for tc in tool_calls_acc.values():
                                    tc['function']['arguments'] = _repair_json(tc['function']['arguments'])
                                yield {'type': 'tool_calls', 'tool_calls': list(tool_calls_acc.values())}
                                tool_calls_acc = {}
                    except json.JSONDecodeError:
                        continue
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), "openrouter") from e


# ======== Generic OpenAI-compatible endpoint ========

def fetch_openai_compat_catalog(base_url: str, api_key: str) -> List[Dict]:
    """Fetch model catalog from any OpenAI-compatible /v1/models endpoint."""
    try:
        url = base_url.rstrip("/") + "/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = _web_session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        catalog = []
        for m in data:
            catalog.append({
                "id": m.get("id", ""),
                "name": m.get("id", ""),
                "context_length": m.get("context_length", 0) or m.get("max_model_len", 0),
                "prompt_price": "0",
                "completion_price": "0",
                "is_free": True,
            })
        return catalog
    except Exception as e:
        print(f"Failed to fetch OpenAI-compat catalog from {base_url}: {e}")
        return []


def call_openai_compat_stream(messages: List[Dict], config: Dict, tools=None) -> Iterator[Union[str, Dict]]:
    """Stream from any OpenAI-compatible chat/completions endpoint.

    Expects config to contain:
        _endpoint_base_url  — e.g. "https://integrate.api.nvidia.com/v1"
        _endpoint_api_key   — Bearer token
        _endpoint_provider  — provider id (for error messages and config key lookups)
    """
    try:
        base_url = config.get("_endpoint_base_url", "").rstrip("/")
        api_key = config.get("_endpoint_api_key", "")
        provider = config.get("_endpoint_provider", "custom")

        if not base_url:
            raise LLMApiError(0, "No base_url configured for custom endpoint", provider)

        api_messages = []
        system_prompt = config.get(f"{provider}_system_prompt") or config.get("system_prompt", "")
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        model_name = config.get(f"{provider}_model") or config.get("model", "")
        data = {
            "model": model_name,
            "messages": api_messages,
            "temperature": config.get(f"{provider}_temperature", config.get("temperature", 0.7)),
            "stream": True,
        }

        if tools:
            data['tools'] = tools
            data['tool_choice'] = 'auto'

        if "top_p" in config:
            data["top_p"] = config["top_p"]
        if "top_k" in config:
            data["top_k"] = config["top_k"]

        req_max = (config.get("max_tokens")
                   or config.get(f"{provider}_max_tokens"))
        if isinstance(req_max, int) and req_max > 0:
            data["max_tokens"] = req_max

        url = base_url + "/chat/completions"
        response = _web_session.post(url, headers=headers, json=data,
                                     stream=True, timeout=(15, 120))
        if response.status_code != 200:
            raise LLMApiError(response.status_code, response.text, provider)

        tool_calls_acc = {}
        usage_data = None
        for line in response.iter_lines(decode_unicode=False):
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    line = line[6:]
                    if line.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(line)
                        if 'usage' in chunk and chunk['usage']:
                            u = chunk['usage']
                            usage_data = {
                                'type': 'usage',
                                'input_tokens': u.get('prompt_tokens', 0),
                                'output_tokens': u.get('completion_tokens', 0),
                            }
                        if 'choices' in chunk and chunk['choices']:
                            delta = chunk['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                            if delta.get('tool_calls'):
                                for tc_delta in delta['tool_calls']:
                                    idx = tc_delta.get('index', 0)
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = {
                                            'id': tc_delta.get('id', f'call_{idx}'),
                                            'type': 'function',
                                            'function': {'name': '', 'arguments': ''}
                                        }
                                    if tc_delta.get('function', {}).get('name'):
                                        tool_calls_acc[idx]['function']['name'] = tc_delta['function']['name']
                                    if tc_delta.get('function', {}).get('arguments'):
                                        tool_calls_acc[idx]['function']['arguments'] += tc_delta['function']['arguments']
                            finish = chunk['choices'][0].get('finish_reason')
                            if finish == 'tool_calls' and tool_calls_acc:
                                for tc in tool_calls_acc.values():
                                    tc['function']['arguments'] = _repair_json(tc['function']['arguments'])
                                yield {'type': 'tool_calls', 'tool_calls': list(tool_calls_acc.values())}
                                tool_calls_acc = {}
                    except json.JSONDecodeError:
                        continue
        if usage_data:
            yield usage_data
    except LLMApiError:
        raise
    except Exception as e:
        raise LLMApiError(0, str(e), config.get("_endpoint_provider", "custom")) from e

