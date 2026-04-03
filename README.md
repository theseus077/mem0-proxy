# mem0-inject-proxy

> **OpenAI-compatible mem0 injection proxy for AI memory persistence**

An OpenAI-compatible API proxy that injects relevant mem0 context into prompts and stores conversation memories asynchronously.

## Features

- **OpenAI-Compatible API** - Drop-in replacement for OpenAI chat completions
- **Automatic Memory Injection** - Retrieves relevant memories from Qdrant vector store
- **Async Memory Storage** - Extracts and stores memories after responses
- **Streaming Support** - Full SSE streaming support for chat completions
- **Configurable Upstream** - Works with any OpenAI-compatible backend (Ollama, etc.)
- **Optional API Key Auth** - Protect your proxy with API key authentication

## Architecture

```
┌─────────────┐     ┌───────────────────┐     ┌─────────────┐
│   Client    │────▶│   mem0-proxy     │────▶│   Ollama    │
│  (OpenCode) │     │   (FastAPI)       │     │   (LLM)     │
└─────────────┘     └─────────┬─────────┘     └─────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │    Qdrant       │
                    │  (Vector DB)    │
                    └─────────────────┘
```

## Requirements

- Python 3.11+
- Qdrant (Vector Database)
- mem0ai
- Upstream LLM (Ollama, OpenAI, etc.)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/theseus077/mem0-proxy.git
cd mem0-proxy
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure mem0

Create `/root/.mem0/config.json`:

```json
{
  "llm": {
    "provider": "openai",
    "config": {
      "model": "glm-5:cloud",
      "openai_base_url": "http://YOUR_OLLAMA_HOST:11434/v1",
      "api_key": "not-needed"
    }
  },
  "embedder": {
    "provider": "openai",
    "config": {
      "model": "nomic-embed-text",
      "openai_base_url": "http://YOUR_OLLAMA_HOST:11434/v1",
      "api_key": "not-needed",
      "embedding_dims": 768
    }
  },
  "vector_store": {
    "provider": "qdrant",
    "config": {
      "host": "localhost",
      "port": 6333,
      "embedding_model_dims": 768
    }
  }
}
```

### 4. Install Systemd Service

```bash
sudo cp mem0-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mem0-api
sudo systemctl start mem0-api
```

## Usage

### Start Server

```bash
# Direct
python mem0_proxy.py

# Or with custom options
MEM0_PROXY_HOST=0.0.0.0 MEM0_PROXY_PORT=8765 python mem0_proxy.py
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check |
| `/v1/health` | GET | Health check (alternative) |

### Authentication

The proxy supports optional API key authentication via headers:

| Header | Description |
|--------|-------------|
| `x-api-key` | Direct API key |
| `Authorization` | `Bearer <api_key>` |

Set `MEM0_PROXY_API_KEY` to enable authentication.

### User Identification

User ID is extracted in this priority order:

1. `x-mem0-user-id` header
2. `x-user-id` header
3. `user_id` in payload
4. `user` in payload
5. `metadata.user_id`
6. `metadata.user`
7. `metadata.session_user`
8. Default: `default`

### Payload Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `messages` | array | **Required.** Chat messages |
| `model` | string | Model name (uses default if not set) |
| `stream` | boolean | Enable streaming (default: false) |
| `user_id` | string | User identifier for memory |
| `memory_limit` | integer | Max memories to inject (1-20, default: 5) |
| `metadata.session_user` | string | Alternative user identifier |

### Example Request

```bash
curl -X POST http://localhost:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5:cloud",
    "messages": [
      {"role": "user", "content": "My name is Andreas."}
    ],
    "user_id": "default",
    "stream": true
  }'
```

### OpenCode Integration

Add to `~/.config/opencode/config.json`:

```json
{
  "providers": {
    "ollamiga": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "mem0-proxy",
      "options": {
        "baseURL": "http://YOUR_PROXY_HOST:8765/v1",
        "apiKey": "DOESNOTMATTER"
      },
      "models": {
        "glm-5:cloud": {"name": "glm-5:cloud"}
      }
    }
  }
}
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEM0_PROXY_HOST` | `0.0.0.0` | Bind host |
| `MEM0_PROXY_PORT` | `8765` | Bind port |
| `MEM0_CONFIG_PATH` | `/root/.mem0/config.json` | mem0 config path |
| `MEM0_PROXY_LOG_LEVEL` | `INFO` | Log level |
| `MEM0_PROXY_TIMEOUT_SECONDS` | `180` | Upstream timeout |
| `MEM0_PROXY_CONNECT_TIMEOUT_SECONDS` | `15` | Upstream connect timeout |
| `MEM0_PROXY_MEMORY_LIMIT` | `5` | Max memories to inject |
| `MEM0_PROXY_MAX_MEMORY_CONTEXT_CHARS` | `12000` | Max memory context length |
| `MEM0_PROXY_MAX_MEMORY_QUERY_CHARS` | `4000` | Max memory query length |
| `MEM0_PROXY_API_KEY` | (none) | Optional API key for proxy |
| `MEM0_PROXY_UPSTREAM_URL` | (from config) | Upstream API URL |
| `MEM0_PROXY_UPSTREAM_API_KEY` | (from config) | Upstream API key |
| `MEM0_PROXY_DEFAULT_MODEL` | (from config) | Default model name |

## Qdrant Optimization

This proxy includes optimized Qdrant configuration for better performance and memory efficiency.

### Features

- **INT8 Scalar Quantization** - Reduces memory usage by ~75%
- **Optimized HNSW Parameters** - Faster vector search
- **Instance-wide Defaults** - Consistent configuration for all collections
- **Automated Setup** - Collection optimization script included

### Configuration Files

| File | Purpose |
|------|---------|
| `qdrant-config.yaml` | Instance-wide Qdrant defaults |
| `qdrant.service` | Systemd service with config path |
| `setup-qdrant-collection.sh` | Collection optimization script |

### Quick Setup

1. **Install Qdrant config:**
   ```bash
   sudo mkdir -p /etc/qdrant
   sudo cp qdrant-config.yaml /etc/qdrant/config.yaml
   sudo chown qdrant:qdrant /etc/qdrant/config.yaml
   ```

2. **Update systemd service:**
   ```bash
   sudo cp qdrant.service /etc/systemd/system/qdrant.service
   sudo systemctl daemon-reload
   sudo systemctl restart qdrant
   ```

3. **Optimize collection:**
   ```bash
   chmod +x setup-qdrant-collection.sh
   ./setup-qdrant-collection.sh
   ```

### Performance Improvements

| Setting | Default | Optimized | Benefit |
|---------|---------|-----------|---------|
| Quantization | None | INT8 Scalar | 75% memory reduction |
| `full_scan_threshold` | 10 KB | 1 MB | Fewer unnecessary index scans |
| `indexing_threshold` | 20 KB | 100 KB | Better segment management |

### Verification

```bash
# Check Qdrant config is loaded
systemctl status qdrant
# Should show: --config-path /etc/qdrant/config.yaml

# Verify collection config
curl http://localhost:6333/collections/mem0 | jq '.result.config.quantization_config'
# Expected: { "scalar": { "type": "int8", ... } }
```

For detailed optimization documentation, see `docs/qdrant-optimization.md`.

## Services

### Dependencies

- **qdrant.service** - Qdrant vector database (must run first)
- **mem0-api.service** - This proxy service

### Management

```bash
# Check status
systemctl status mem0-api
systemctl status qdrant

# View logs
journalctl -u mem0-api -f
journalctl -u qdrant -f

# Restart
systemctl restart mem0-api
systemctl restart qdrant
```

### Health Check Response

```json
{
  "status": "healthy",
  "memory_enabled": true,
  "upstream_base_url": "http://127.0.0.1:11434/v1",
  "default_model": "glm-5:cloud",
  "timestamp": 1743589200
}
```

## Project Structure

```
mem0-inject-proxy/
├── mem0_proxy.py          # Main FastAPI application
├── mem0-api.service      # Systemd service unit
├── requirements.txt      # Python dependencies
├── README.md              # This file
├── comparison_analysis.md # Architecture analysis
├── mem0_canvas.md        # Canvas documentation
└── Mem0 - Workflow.canvas # Workflow visualization
```

## Related

- [mem0](https://github.com/mem0ai/mem0) - Memory layer for AI applications
- [Qdrant](https://qdrant.tech/) - Vector database
- [Ollama](https://ollama.ai/) - Local LLM runtime

## License

MIT

## Author

Adam Friese

---

*Last updated: 2026-04-02*
