import json
import os
from groq import Groq

# ============================
# GROQ CLIENT
# ============================

client = Groq(
    api_key="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"
)

# ============================
# MEMORY STORAGE
# ============================

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_FILE = os.path.join(ROOT_DIR, "memory.json")


def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}

    with open(MEMORY_FILE, "r") as f:
        return json.load(f)


def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=4)


memory = load_memory()


# ============================
# MEMORY FUNCTIONS
# ============================

def remember(key, value):
    """
    Add something to memory
    """
    memory[key] = value
    save_memory(memory)
    return f"Remembered: {key} = {value}"


def forget(key):
    """
    Remove memory
    """
    if key in memory:
        del memory[key]
        save_memory(memory)
        return f"Forgot {key}"
    return "Memory not found"


def update_memory(key, value):
    memory[key] = value
    save_memory(memory)
    return f"Updated {key}"


def search_memory():
    return json.dumps(memory, indent=2)


# ============================
# TOOL DEFINITIONS
# ============================

tools = [
    {
        "name": "remember",
        "description": "Save a user's preference, personal information, or anything the user wants remembered.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Memory name"
                },
                "value": {
                    "type": "string",
                    "description": "Information to store"
                }
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "forget",
        "description": "Delete something from memory when the user asks to forget it.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "update_memory",
        "description": "Change an existing memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string"
                },
                "value": {
                    "type": "string"
                }
            },
            "required": ["key", "value"]
        }
    },
    {
        "name": "search_memory",
        "description": "Retrieve stored memories.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
]


if __name__ == "__main__":
    messages = [
        {
            "role": "system",
            "content": """
You are a personal AI assistant.

You have access to memory.

Use memory tools when:

- User says remember/save/store something
- User gives a preference
- User asks what you remember
- User says forget something

Do not store temporary conversations.

Only store useful long term information.

Before answering questions about the user,
check memory first.

No need to ask the user before remembering.

Store even the smallest personalizations, preferences etc.
"""
        }
    ]

    while True:
        user = input("\nYou: ")
        if user.lower() == "exit":
            break

        messages.append({
            "role": "user",
            "content": user
        })

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=[{"type": "function", "function": tool} for tool in tools],
            tool_choice="auto"
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for call in msg.tool_calls:
                name = call.function.name
                args = json.loads(call.function.arguments)

                if name == "remember":
                    result = remember(args["key"], args["value"])
                elif name == "forget":
                    result = forget(args["key"])
                elif name == "update_memory":
                    result = update_memory(args["key"], args["value"])
                elif name == "search_memory":
                    result = search_memory()
                else:
                    result = "Unknown tool"

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result
                })

            final = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages
            )
            answer = final.choices[0].message.content
        else:
            answer = msg.content

        messages.append({
            "role": "assistant",
            "content": answer
        })

        print("\nAI:", answer)
