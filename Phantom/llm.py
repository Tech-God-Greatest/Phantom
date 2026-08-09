"""
llm.py — LLM orchestration for Phantom AI.

Architecture
────────────
  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
  │  llm.py      │────▶│ tools/       │────▶│ memory_tools.py      │
  │  (this file) │     │ registry.py  │     │ web_tools.py         │
  └──────────────┘     │              │     │ your_new_tool.py ... │
                       └──────────────┘     └──────────────────────┘

To add a new tool: edit tools/registry.py only — nothing here changes.

Public functions
────────────────
  execute_llm_query(query, chat_id, check_cancelled)            -> str
  execute_qwen_query(query, chat_id, check_cancelled)           -> str
  execute_vision_query(query, image_path, chat_id, check_cancelled) -> str
  unload_ollama_model(model_name)                               -> None
"""

import os
import re
import json
import time
import base64
import requests
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

from storage import storage
from tools import ALL_SCHEMAS, dispatch, EXECUTOR_MAP  # ← single import covers all tools
from tools.memory_tools import search_memory  # helper: inject memory into context
from utils.logger import log_info, log_success, log_warning, log_error


# ── Model / API configuration ─────────────────────────────────────────────────

GROQ_API_KEY      = os.environ.get("GROQ_API_KEY",      "KEY")
GROQ_MODEL        = os.environ.get("GROQ_MODEL",        "qwen/qwen3.6-27b")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")
OLLAMA_MODEL      = os.environ.get("OLLAMA_MODEL",      "qwen3.5:9b")
OLLAMA_BASE_URL   = os.environ.get("OLLAMA_BASE_URL",   "http://localhost:11434/v1")

# Groq client (primary)
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=6.0
)

# Ollama client (local fallback)
ollama_client = OpenAI(
    api_key="ollama",
    base_url=OLLAMA_BASE_URL
)


# ── System prompts ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Phantom, an intelligent, helpful, and concise voice assistant created by Lakshit Agarwal.
You act as a personal assistant to your boss (the user).

CRITICAL RESPONSE RULES:
- Output ONLY your final conversational response or standard function calls.
- Never output reasoning steps, thinking processes, or internal analysis.
- Keep responses short, direct, natural, and conversational.
- ALWAYS use standard OpenAI function calling for tools. Never output raw JSON text in your response body.
- Whenever current info, news, or websites are needed, call web_search automatically.
- Check stored memory before answering questions about the user.
- Store preferences and long-term facts automatically using memory tools.

TOOL USAGE DIRECTIVES:
- MOUSE & CURSOR TOOLS: Use `mouse_action` (or mouse tools) whenever asked to perform mouse clicks (left click/LMB, right click/RMB, middle click/MMB, double click), scroll (up, down, left, right), move the mouse cursor (to center, screen presets, specific X,Y coordinates, or relative directions like up/down/left/right), or query mouse position.
- HARDWARE & TELEMETRY TOOLS: Use `get_system_overview`, `get_top_resource_apps`, `get_process_list`, `get_cpu_status`, `get_cpu_specs`, `get_gpu_status`, `get_display_info`, `get_ram_status`, `get_ram_hardware_specs`, `get_disk_status`, `get_storage_hardware_info`, `get_motherboard_info`, `get_network_info`, `get_battery_status`, `get_temperatures`, `get_fan_speeds`, `get_power_draw` for hardware monitoring, specs, temperatures, fan speeds, power draw, and Task Manager app resource ranking.
- OS CONTROL & WINDOW TOOLS: Use OS control & window tools (`focus_window`, `minimize_window`, `maximize_window`, `restore_window`, `resize_window`, `list_open_windows`, `lock_pc`, `sleep_pc`, `restart_pc`, `shutdown_pc`, `logoff_user`, `open_task_manager`, `kill_process`, `set_volume`, `mute_audio`, `change_brightness`, `empty_recycle_bin`) whenever asked to focus, minimize, maximize, restore, resize, or list open windows, lock/sleep/restart/shutdown/logoff PC, open task manager, kill processes, adjust master volume, mute/unmute audio, change screen brightness, or empty the recycle bin.

INSTRUCTIONS FOR EFFICIENT & MULTI-STEP TOOL USAGE:
- Carefully analyze the user's entire request before responding.
- Identify every task that requires tool execution.
- MULTI-STEP / SEQUENTIAL TASKS:
  - If a task requires multiple steps sequentially (e.g. searching for a video/song on YouTube and opening it, or searching then fetching a web page):
    1. FIRST step: Issue tool call `web_search` (e.g., query="[song name] youtube").
    2. SECOND step: When search results return a YouTube URL (e.g. https://www.youtube.com/watch?v=...), immediately issue tool call `open_website(url="[youtube_url]")`.
    3. THIRD step: Provide a short confirmation (e.g., "Playing [song name] on YouTube.").
  - DO NOT STOP after `web_search` if the user asked you to play, open, or perform an action!
- PARALLEL TOOL CALLS:
  - If multiple independent tool actions are requested (e.g. "open spotify and open chrome"), call both tools simultaneously in a single turn.
- A tool may be called multiple times across iterations if the user's request requires repeated execution of the same action.
- Execute independent tool calls in parallel whenever supported. Only perform tool calls sequentially when one depends on the output of another.
- Never ignore or omit a valid portion of the user's request simply because another tool has already been called.
"""

QWEN_SYSTEM_PROMPT = "NOTHINK: Do not output reasoning or <think> tags. Output ONLY your final response or function calls directly.\n\n" + SYSTEM_PROMPT


# ── Internal helpers ──────────────────────────────────────────────────────────

def clean_llm_response(reply: str) -> str:
    """Strip internal thinking blocks, ReAct JSON thoughts, and citation artifacts from LLM output."""
    if not reply:
        return ""

    # 1. Remove <think>...</think> blocks (including unclosed <think>...)
    reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL | re.IGNORECASE)
    reply = re.sub(r'<think>.*$', '', reply, flags=re.DOTALL | re.IGNORECASE)
    reply = re.sub(r'</?think>', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'^(?:Here\'s a thinking process:|Here is a thinking process:)\s*', '', reply, flags=re.IGNORECASE)

    # 2. Check if reply is wrapped in markdown code blocks e.g. ```json ... ```
    cleaned_codeblock = reply.strip()
    if cleaned_codeblock.startswith("```"):
        cleaned_codeblock = re.sub(r'^```(?:json)?\s*', '', cleaned_codeblock, flags=re.IGNORECASE)
        cleaned_codeblock = re.sub(r'\s*```$', '', cleaned_codeblock).strip()

    # 3. Handle JSON formatted responses containing thoughts or pseudo tool calls
    if cleaned_codeblock.startswith("{") and cleaned_codeblock.endswith("}"):
        try:
            data = json.loads(cleaned_codeblock)
            if isinstance(data, dict):
                # If there's an explicit response/answer field, use it directly
                for key in ["response", "answer", "output", "text", "message", "reply"]:
                    if key in data and isinstance(data[key], str) and data[key].strip():
                        return data[key].strip()

                # Remove internal thought fields
                for thought_key in ["thought", "thinking", "reasoning", "rationale", "internal_thought"]:
                    data.pop(thought_key, None)

                # If only pseudo tool call fields remain, return empty string so execution/retry loop handles it
                remaining_keys = set(data.keys())
                if remaining_keys.issubset({"action", "actioninput", "action_input", "input", "tool", "args", "parameters"}):
                    return ""

                values = [str(v).strip() for k, v in data.items() if v]
                if values:
                    return " ".join(values)
        except Exception:
            pass

    # 4. Regex fallback to strip `"thought": "..."` or `Thought: ...` text patterns
    reply = re.sub(r'"thought"\s*:\s*"(?:[^"\\]|\\.)*"[,\s]*', '', reply, flags=re.DOTALL | re.IGNORECASE)
    reply = re.sub(r'"thinking"\s*:\s*"(?:[^"\\]|\\.)*"[,\s]*', '', reply, flags=re.DOTALL | re.IGNORECASE)
    reply = re.sub(r'Thought:\s*.*?(?=(Action:|Response:|$))', '', reply, flags=re.DOTALL | re.IGNORECASE)

    # Clean up empty JSON wrappers left behind
    reply = re.sub(r'\{\s*\}', '', reply)

    # 5. Remove citation / annotation symbols
    reply = re.sub(r'【[^】]*】', '', reply)
    reply = re.sub(r'†[^ \n]+', '', reply)

    return reply.strip()


def _build_messages(system_prompt: str, chat_id: int, query: str = "", limit: int = 4) -> list:
    """Build lightweight message context: system prompt + long-term memory + immediate recent turns (last 2 exchanges)."""
    messages = [{"role": "system", "content": system_prompt}]

    current_mem = search_memory()
    if current_mem and current_mem != "{}":
        messages.append({"role": "system", "content": f"User Stored Memory: {current_mem}"})

    # Only include immediate recent turns (last 2 exchanges / 4 messages) for follow-up resolution
    history = storage.get_recent_messages(chat_id, limit=limit)
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})

    # If the current user message isn't saved in database history yet, append it explicitly
    if query and (not history or history[-1]["role"] != "user" or history[-1]["content"] != query):
        messages.append({"role": "user", "content": query})

    return messages


def _run_tool_calls(tool_calls, messages: list, on_tool_call=None) -> list:
    """
    Execute tool calls requested by the model in parallel when multiple exist,
    append results to messages using unified registry dispatcher.
    """
    tool_results = []

    def _exec_single(tc):
        if hasattr(tc, "function"):
            name = tc.function.name
            raw_args = tc.function.arguments or "{}"
            tc_id = getattr(tc, "id", f"tc_{time.time()}")
        elif isinstance(tc, dict):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            tc_id = tc.get("id", f"tc_{time.time()}")
        else:
            return None

        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except Exception:
                args = {}
        else:
            args = raw_args or {}

        if on_tool_call:
            on_tool_call(name, args)

        result = dispatch(name, args)
        return (tc_id, name, result)

    if len(tool_calls) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(tool_calls))) as executor:
            executed_tuples = list(executor.map(_exec_single, tool_calls))
    else:
        executed_tuples = [_exec_single(tool_calls[0])]

    for item in executed_tuples:
        if not item:
            continue
        tc_id, name, result = item
        tool_results.append(str(result))
        messages.append({
            "tool_call_id": tc_id,
            "role":         "tool",
            "name":         name,
            "content":      str(result),
        })

    return tool_results


# ── Primary LLM path: Groq ────────────────────────────────────────────────────

def _create_groq_completion(messages: list):
    """Helper to create Groq completion with hidden reasoning tokens."""
    try:
        return client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=ALL_SCHEMAS,
            tool_choice="auto",
            temperature=0.6,
            timeout=10.0,
            extra_body={"reasoning_format": "hidden"}
        )
    except Exception as e:
        log_warning("Groq", f"extra_body parameter error: {e}. Retrying standard completion...")
        return client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=ALL_SCHEMAS,
            tool_choice="auto",
            temperature=0.6,
            timeout=10.0
        )


def execute_llm_query(query: str, chat_id: int, check_cancelled=None, on_tool_call=None, ai_mode: str = "Multi") -> str:
    """Primary handler: Groq API with all registered tools.
    Supports mode selection: 'Multi' (Groq main, Local fallback), 'Groq' (Groq only), 'Local AI' (Local only)."""
    if ai_mode == "Local AI":
        return execute_qwen_query(query, chat_id, check_cancelled=check_cancelled, on_tool_call=on_tool_call)

    # Ensure current user message is persisted in database history
    recent = storage.get_recent_messages(chat_id, limit=1)
    if query and (not recent or recent[-1]["role"] != "user" or recent[-1]["content"] != query):
        storage.save_message(chat_id, "user", query)

    try:
        messages     = _build_messages(SYSTEM_PROMPT, chat_id, query=query, limit=50)
        reply        = ""
        tool_results = []

        # Up to 4 tool-call round-trips before giving up
        for loop_idx in range(4):
            response = _create_groq_completion(messages)

            if check_cancelled and check_cancelled():
                return ""

            response_message = response.choices[0].message
            tool_calls       = getattr(response_message, "tool_calls", None)

            if tool_calls:
                messages.append(response_message)
                results = _run_tool_calls(tool_calls, messages, on_tool_call=on_tool_call)
                tool_results.extend(results)

                if check_cancelled and check_cancelled():
                    return ""
                continue  # let model react to tool results
            else:
                raw_content = (response_message.content or "").strip()

                # Intercept pseudo tool calls formatted as JSON in output body
                if raw_content:
                    clean_content = raw_content
                    if clean_content.startswith("```"):
                        clean_content = re.sub(r'^```(?:json)?\s*', '', clean_content, flags=re.IGNORECASE)
                        clean_content = re.sub(r'\s*```$', '', clean_content).strip()

                    if clean_content.startswith("{") and clean_content.endswith("}"):
                        try:
                            data = json.loads(clean_content)
                            if isinstance(data, dict) and "action" in data:
                                raw_action = str(data.get("action", "")).strip()
                                action_name = "web_search" if raw_action == "websearch" else raw_action
                                raw_input = data.get("actioninput", data.get("action_input", data.get("query", data.get("input", ""))))

                                if isinstance(raw_input, str):
                                    args = {"query": raw_input} if action_name in ["web_search", "fetch_web_page"] else {"app_name": raw_input}
                                elif isinstance(raw_input, dict):
                                    args = raw_input
                                else:
                                    args = {}

                                if action_name in EXECUTOR_MAP:
                                    if on_tool_call:
                                        on_tool_call(action_name, args)
                                    result = dispatch(action_name, args)
                                    tool_results.append(str(result))

                                    messages.append({"role": "assistant", "content": raw_content})
                                    messages.append({
                                        "tool_call_id": f"pseudo_{loop_idx}_{len(messages)}",
                                        "role": "tool",
                                        "name": action_name,
                                        "content": str(result),
                                    })
                                    if check_cancelled and check_cancelled():
                                        return ""
                                    continue
                        except Exception as e:
                            log_error("Groq", f"Pseudo-Tool Parser Error: {e}")

                cleaned = clean_llm_response(raw_content)
                if cleaned:
                    reply = cleaned
                elif tool_results:
                    valid_res = [str(r).strip() for r in tool_results if str(r).strip()]
                    reply = "; ".join(valid_res) if valid_res else "Done."
                else:
                    reply = raw_content
                break

        reply = clean_llm_response(reply)
        if not reply and tool_results:
            valid_res = [str(r).strip() for r in tool_results if str(r).strip()]
            reply = "; ".join(valid_res) if valid_res else "Done."

        storage.save_message(chat_id, "assistant", reply)
        return reply

    except Exception as e:
        if ai_mode == "Groq":
            error_msg = f"[Groq API Error]: {e}"
            log_error("Groq", error_msg)
            storage.save_message(chat_id, "assistant", error_msg)
            return error_msg
        log_warning("Groq", f"API Error: {e}. Falling back to local Qwen model ({OLLAMA_MODEL})...")
        return execute_qwen_query(query, chat_id, check_cancelled=check_cancelled, on_tool_call=on_tool_call)


# ── Fallback LLM path: local Ollama ──────────────────────────────────────────

def execute_qwen_query(query: str, chat_id: int, check_cancelled=None, on_tool_call=None) -> str:
    """Fallback / Local handler: local Qwen/Ollama model with native high-speed tool calling and VRAM persistence."""
    # Ensure current user message is persisted in database history
    recent = storage.get_recent_messages(chat_id, limit=1)
    if query and (not recent or recent[-1]["role"] != "user" or recent[-1]["content"] != query):
        storage.save_message(chat_id, "user", query)

    messages = _build_messages(QWEN_SYSTEM_PROMPT, chat_id, query=query, limit=50)
    reply = ""
    tool_results = []
    api_url = OLLAMA_BASE_URL.replace("/v1", "/api/chat")

    for loop_idx in range(4):
        if check_cancelled and check_cancelled():
            return ""

        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": ALL_SCHEMAS,
            "think": False,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "num_ctx": 4096,
                "temperature": 0.6,
                "num_predict": 512
            }
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=35)
            if resp.status_code != 200:
                log_error("Ollama", f"HTTP {resp.status_code}: {resp.text}")
                payload.pop("tools", None)
                resp = requests.post(api_url, json=payload, timeout=35)

            resp_data = resp.json()
            response_message = resp_data.get("message", {})
        except Exception as ex:
            error_msg = f"[Local AI Error]: {ex}"
            log_error("Ollama", error_msg)
            storage.save_message(chat_id, "assistant", error_msg)
            return error_msg

        if check_cancelled and check_cancelled():
            return ""

        tool_calls = response_message.get("tool_calls", None)

        if tool_calls:
            messages.append(response_message)
            results = _run_tool_calls(tool_calls, messages, on_tool_call=on_tool_call)
            tool_results.extend(results)

            if check_cancelled and check_cancelled():
                return ""
            continue  # let model digest tool results and execute follow-up tool calls
        else:
            raw_content = (response_message.get("content", "") or "").strip()

            # Intercept pseudo tool calls formatted as JSON in output body
            if raw_content:
                clean_content = raw_content
                if clean_content.startswith("```"):
                    clean_content = re.sub(r'^```(?:json)?\s*', '', clean_content, flags=re.IGNORECASE)
                    clean_content = re.sub(r'\s*```$', '', clean_content).strip()

                if clean_content.startswith("{") and clean_content.endswith("}"):
                    try:
                        data = json.loads(clean_content)
                        if isinstance(data, dict) and ("action" in data or "tool" in data or "name" in data):
                            raw_action = str(data.get("action", data.get("tool", data.get("name", "")))).strip()
                            action_name = "web_search" if raw_action in ["websearch", "search"] else raw_action
                            raw_input = data.get("actioninput", data.get("action_input", data.get("query", data.get("input", data.get("args", data.get("url", ""))))))

                            if isinstance(raw_input, str):
                                if action_name in ["web_search", "fetch_web_page"]:
                                    args = {"query": raw_input} if action_name == "web_search" else {"url": raw_input}
                                elif action_name in ["open_app", "close_app"]:
                                    args = {"name": raw_input}
                                elif action_name == "open_website":
                                    args = {"url": raw_input}
                                elif action_name in ["create_folder", "delete_folder", "list_directory"]:
                                    args = {"directory_path": raw_input} if action_name == "list_directory" else {"folder_path": raw_input}
                                elif action_name in ["create_file", "delete_file", "read_file_content", "edit_file"]:
                                    args = {"file_path": raw_input}
                                else:
                                    args = {"input_value": raw_input}
                            elif isinstance(raw_input, dict):
                                args = raw_input
                            else:
                                args = {}

                            if action_name in EXECUTOR_MAP:
                                if on_tool_call:
                                    on_tool_call(action_name, args)
                                result = dispatch(action_name, args)
                                tool_results.append(str(result))

                                messages.append({"role": "assistant", "content": raw_content})
                                messages.append({
                                    "tool_call_id": f"qwen_pseudo_{loop_idx}_{len(messages)}",
                                    "role": "tool",
                                    "name": action_name,
                                    "content": str(result),
                                })
                                if check_cancelled and check_cancelled():
                                    return ""
                                continue
                    except Exception as e:
                        log_error("Ollama", f"Pseudo-Tool Parser Error: {e}")

                cleaned = clean_llm_response(raw_content)
                if cleaned:
                    reply = cleaned
                elif tool_results:
                    valid_res = [str(r).strip() for r in tool_results if str(r).strip()]
                    reply = "; ".join(valid_res) if valid_res else "Done."
                else:
                    reply = raw_content
                break

        reply = clean_llm_response(reply)
        if not reply and tool_results:
            valid_res = [str(r).strip() for r in tool_results if str(r).strip()]
            reply = "; ".join(valid_res) if valid_res else "Done."

    storage.save_message(chat_id, "assistant", reply)
    return reply


# ── Ollama VRAM management ────────────────────────────────────────────────────

def unload_ollama_model(model_name: str = OLLAMA_MODEL):
    """Send an explicit unload request to Ollama to free GPU VRAM when application exits."""
    try:
        url = OLLAMA_BASE_URL.replace("/v1", "/api/generate")
        requests.post(url, json={"model": model_name, "keep_alive": 0}, timeout=3.0)
        log_info("Ollama", f"Unloaded model '{model_name}' from VRAM.")
    except Exception as e:
        log_error("Ollama", f"Failed to unload '{model_name}': {e}")


# ── Vision path ───────────────────────────────────────────────────────────────

def execute_vision_query(query: str, image_path: str, chat_id: int, check_cancelled=None, ai_mode: str = "Multi") -> str:
    """Vision handler: supports mode selection: 'Multi', 'Groq', 'Local AI'."""
    if not query or not query.strip():
        query = "Analyze this image snippet and explain what you see in detail."

    # Encode image to base64
    try:
        with open(image_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        error_msg = f"[Error reading image file]: {e}"
        storage.save_message(chat_id, "assistant", error_msg)
        return error_msg

    image_data_url = f"data:image/png;base64,{b64_image}"

    if ai_mode != "Local AI":
        # 1. Groq Vision API
        try:
            log_info("Vision", f"Attempting Groq Vision API ({GROQ_VISION_MODEL})...")
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text",      "text": query},
                        {"type": "image_url", "image_url": {"url": image_data_url}}
                    ]
                }
            ]

            response = client.chat.completions.create(
                model=GROQ_VISION_MODEL,
                messages=messages,
                max_tokens=1024,
                timeout=12.0
            )

            if check_cancelled and check_cancelled():
                return ""

            reply = clean_llm_response(response.choices[0].message.content or "")
            storage.save_message(chat_id, "assistant", reply)
            log_success("Vision", "Groq Vision API response received successfully.")
            return reply

        except Exception as e:
            if ai_mode == "Groq":
                error_msg = f"[Groq Vision API Error]: {e}"
                log_error("Vision", error_msg)
                storage.save_message(chat_id, "assistant", error_msg)
                return error_msg
            log_warning("Vision", f"Groq Vision API Error: {e}. Falling back to local Ollama ({OLLAMA_MODEL})...")

    # 2. Local Ollama fallback / Local AI direct mode
    try:
        ollama_messages = [
            {"role": "system", "content": QWEN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text",      "text": query},
                    {"type": "image_url", "image_url": {"url": image_data_url}}
                ]
            }
        ]

        response = ollama_client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=ollama_messages,
            extra_body={
                "think": False,
                "keep_alive": -1,  # Keep model pinned in GPU VRAM
                "options": {"num_ctx": 4096, "temperature": 0.6}
            }
        )

        if check_cancelled and check_cancelled():
            return ""

        reply = clean_llm_response(response.choices[0].message.content or "")
        storage.save_message(chat_id, "assistant", reply)
        log_success("Vision", f"Local Ollama ({OLLAMA_MODEL}) completed.")
        return reply

    except Exception as e:
        error_msg = f"[Local Vision Error]: {e}"
        log_error("Vision", error_msg)
        storage.save_message(chat_id, "assistant", error_msg)
        return error_msg


