# ThreadBear

A local-first, multi-provider LLM chat application built with Flask and vanilla JavaScript. ThreadBear lets you talk to models from Groq, Google Gemini, Mistral, OpenRouter, and local llama.cpp servers — all from a single interface. No cloud accounts required for the app itself; just bring your own API keys.

## Features

- **Multi-provider support** — Switch between Groq, Google Gemini, Mistral, OpenRouter, and llama.cpp (local) from the same UI. Add custom OpenAI-compatible endpoints for any other provider.
- **Streaming responses** — Real-time token streaming via Server-Sent Events.
- **Chat management** — Create, rename, delete, and organize chats into folders. Full chat history persisted as JSON files.
- **Branching conversations** — Branch off any message to explore alternate paths without losing context.
- **System prompts** — Ship with sensible defaults; create and manage your own custom prompts.
- **Document context** — Upload PDFs, DOCX, TXT, Markdown, EPUB, PPTX, Excel, CSV, and code files. Attach them as context for any conversation.
- **Toolbox** — A script workspace where the LLM can write and you can run Python/shell scripts. Includes a toolbelt system for assigning scripts to specific chats with granular permissions.
- **Tool system** — Built-in tools (file read/write, shell commands, web requests, web search) that models can call during conversations. Per-provider toggle with safety controls.
- **Message compaction** — Automatically summarize long conversations to stay within context limits while preserving key information.
- **Cost tracking** — Token usage and estimated cost tracking per message and per conversation.
- **Light/Dark theme** — System-aware theme with manual override.
- **Local-first, privacy-first** — Everything runs on your machine. No telemetry, no cloud storage. Your chats and documents stay on disk.

## Requirements

- **Python 3.10+**
- A modern web browser
- At least one API key (Groq, Google, Mistral, or OpenRouter) — or a running llama.cpp server for fully local operation

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/josh-writes/ThreadBear.git
   cd ThreadBear
   ```

2. **Create a virtual environment** (recommended)

   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate

   # macOS / Linux
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up API keys**

   Create a `.env` file in the project root:

   ```env
   GROQ_API_KEY=your_groq_key_here
   GOOGLE_API_KEY=your_google_key_here
   MISTRAL_API_KEY=your_mistral_key_here
   OPENROUTER_API_KEY=your_openrouter_key_here
   ```

   You only need keys for the providers you plan to use. All keys are optional — you can also configure them through the Settings panel in the UI, where they are saved to a local `config.json` (gitignored).

   For **llama.cpp**, no API key is needed — just point ThreadBear to your server URL in Settings.

5. **Install optional dependencies** (if needed)

   ```bash
   # For .env file support (recommended)
   pip install python-dotenv

   # For standalone desktop window instead of browser tab
   pip install pywebview

   # For EPUB reading
   pip install ebooklib

   # For Excel reading
   pip install openpyxl
   ```

## Usage

**Start the application:**

```bash
python flask_chat_app.py
```

ThreadBear will start on `http://127.0.0.1:5000` and automatically open your browser. If port 5000 is busy, it tries 5001–5003.

**On Windows**, you can also double-click `ThreadBear.bat`.

**Quick start:**

1. Open Settings (gear icon) and select a provider
2. Choose or search for a model
3. Start chatting

### Providers

| Provider | API Key Env Var | Free Tier | Notes |
|----------|----------------|-----------|-------|
| [Groq](https://console.groq.com/) | `GROQ_API_KEY` | Yes | Fast inference, Llama/Mixtral models |
| [Google Gemini](https://aistudio.google.com/) | `GOOGLE_API_KEY` | Yes | Gemini models, large context windows |
| [Mistral](https://console.mistral.ai/) | `MISTRAL_API_KEY` | Yes | Mistral/Mixtral models |
| [OpenRouter](https://openrouter.ai/) | `OPENROUTER_API_KEY` | Some models | Access to 100+ models from many providers |
| llama.cpp | — | Local | Run models locally, no API key needed |

You can also add **custom OpenAI-compatible endpoints** (e.g., NVIDIA NIM, Together AI, local vLLM) through the Settings panel.

### Toolbox

The toolbox is a script workspace at `toolbox/`. The LLM can create scripts there, and you can manage them from the Toolbox panel in the sidebar:

- **Default scripts** ship in `default_toolbox/` (read-only, included with the app)
- **Your scripts** are saved to `toolbox/` (gitignored, private to you)
- Right-click any script to copy, open in editor, assign to a chat, or delete
- Assigned scripts appear in the chat's **toolbelt** with configurable permissions (network, file I/O, etc.)

### System Prompts

- **Default prompts** ship in `prompts/default_prompts.jsonl`
- **Custom prompts** you create are saved to `prompts/custom_prompts.jsonl` (gitignored)
- Manage prompts from the system prompt dropdown in the chat interface

### Document Context

Upload files via the context panel (paperclip icon). Supported formats:

- PDF, DOCX, PPTX, EPUB, Excel (.xlsx)
- Plain text, Markdown, CSV
- Source code files

Documents are chunked and attached as context to your messages. Manage active documents per conversation.

## Project Structure

```
ThreadBear/
├── flask_chat_app.py       # Main application (all routes, SSE streaming)
├── chat_manager.py         # Chat CRUD, JSON persistence, branching
├── api_clients.py          # Multi-provider LLM API calls + streaming
├── config_manager.py       # Per-provider settings, API key management
├── context_documents.py    # Document ingestion + context building
├── document_db.py          # SQLite document metadata
├── cost_tracker.py         # Token usage and cost tracking
├── message_compaction.py   # Conversation summarization
├── branch_db.py            # Branch/conversation graph database
├── folder_manager.py       # Chat folder organization
├── static/
│   └── chat.js             # Entire frontend (vanilla JS)
├── templates/
│   └── chat.html           # HTML + CSS
├── tools/                  # LLM tool system
│   ├── registry.py         # Tool registration
│   ├── core_tools.py       # File, shell, web tools
│   ├── safety.py           # Command/path safety checks
│   └── script_sandbox.py   # Sandboxed script execution
├── readers/                # Document format readers
│   ├── registry.py         # Reader registration
│   ├── pdf_reader.py       # PDF extraction
│   ├── docx_reader.py      # Word documents
│   └── ...                 # CSV, EPUB, Excel, code, etc.
├── default_toolbox/        # Example scripts (shipped with app)
├── toolbox/                # Your scripts (gitignored)
├── prompts/
│   ├── default_prompts.jsonl   # Shipped system prompts
│   └── custom_prompts.jsonl    # Your prompts (gitignored)
├── chats/                  # Chat history JSON files (gitignored)
├── documents/              # Uploaded documents (gitignored)
├── requirements.txt
├── ThreadBear.bat          # Windows launcher
└── .env                    # API keys (gitignored, you create this)
```

## Configuration

All configuration is stored in `config.json` (auto-created, gitignored). You can edit settings through the UI or modify the file directly. Key settings:

- **Provider selection and model** — per-provider model, temperature, max tokens
- **API keys** — stored in `.env` (preferred) or `config.json`
- **Tool system** — enable/disable per provider, safety settings, blocked commands
- **Document limits** — max upload size, PDF page limit, context token budget
- **Custom endpoints** — add any OpenAI-compatible API

## License

This project is licensed under the [MIT License](LICENSE).
