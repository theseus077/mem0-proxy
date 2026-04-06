# Memory Quality Control Implementation

## Overview

This implementation adds controlled memory ingestion to `mem0-proxy`, preventing:
- ❌ Speculation from being stored as facts
- ❌ Low-confidence data polluting the vector store
- ❌ Duplicate or redundant memories
- ❌ Temporary context being persisted permanently

## Architecture

```
┌─────────────┐
│   Client    │
│  (OpenCode) │
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│            mem0-proxy (FastAPI)                       │
│                                                       │
│  1. Receive chat request                              │
│  2. Search relevant memories (with quality filter)     │
│  3. Inject memory context into prompt                 │
│  4. Forward to upstream LLM                          │
│  5. Store response with custom instructions:         │
│     - Fact Extraction Prompt (filters speculation)    │
│     - Memory Update Prompt (avoids duplicates)         │
│                                                       │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
        ┌─────────────┐
        │   Qdrant    │
        │ (Vector DB) │
        │             │
        │ Only stores │
        │ HIGH QUALITY│
        │   FACTS     │
        └─────────────┘
```

## How It Works

### 1. Custom Fact Extraction Prompt

When mem0 extracts facts from conversations, it uses this prompt to decide what to store:

```python
# From prompts/fact_extraction_default.txt

PURPOSE:
Ensure only high-quality, verified facts are stored in memory

YES - STORE THESE TYPES:
✓ CONFIRMED USER PREFERENCES (explicitly stated)
✓ TECHNICAL CONFIGURATIONS (with specifics)
✓ ARCHITECTURE DECISIONS (with rationale)
✓ SPECIFIC FACTS (who/what/when/where)

NO - NEVER STORE THESE:
✗ SPECULATION ("I think", "maybe", "possibly")
✗ TEMPORARY CONTEXT ("right now", "this session")
✗ VAGUE STATEMENTS ("something", "somehow")
✗ DEBUG/ERROR OUTPUT
✗ CODE WITHOUT CONTEXT
✗ DUPLICATES

QUALITY REQUIREMENTS:
• Confidence Level: 80%+
• Specificity: concrete details required
• Uniqueness: no duplicates
• Relevance: useful for future interactions
```

### 2. Custom Memory Update Prompt

When new information might update existing memories:

```python
# From prompts/update_memory_default.txt

UPDATE if:
- New information contradicts stored fact
- More specific details available
- Facts have changed

ADD if:
- Completely new topic
- Complementary information

DELETE if:
- Information outdated
- Duplicate detected
- Contradicted by facts

PRESERVE:
- Audit trail (timestamps)
- Metadata
- Relationships between memories
```

## Integration in mem0_proxy.py

### 1. Configuration Loading

```python
# Environment variables
CUSTOM_FACT_PROMPT_PATH = os.getenv("MEM0_PROXY_FACT_EXTRACTION_PROMPT")
CUSTOM_UPDATE_PROMPT_PATH = os.getenv("MEM0_PROXY_UPDATE_MEMORY_PROMPT")

# Load from files
def load_custom_prompt(path: str) -> str | None:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        logger.warning("Could not load custom prompt from %s: %s", path, exc)
        return None
```

### 2. Memory Initialization with Custom Prompts

```python
def create_memory() -> Any | None:
    if Memory is None:
        logger.warning("mem0 is not installed. Memory features are disabled.")
        return None

    try:
        config = dict(CONFIG) if CONFIG else {}
        
        # Load custom fact extraction prompt
        fact_prompt = load_custom_prompt(CUSTOM_FACT_PROMPT_PATH)
        if fact_prompt:
            config["custom_fact_extraction_prompt"] = fact_prompt
        elif "custom_fact_extraction_prompt" not in config:
            # Use default quality filter
            config["custom_fact_extraction_prompt"] = get_default_quality_filter()
        
        # Load custom update prompt
        update_prompt = load_custom_prompt(CUSTOM_UPDATE_PROMPT_PATH)
        if update_prompt:
            config["custom_update_memory_prompt"] = update_prompt
        elif "custom_update_memory_prompt" not in config:
            # Use default update filter
            config["custom_update_memory_prompt"] = get_default_update_filter()
        
        instance = Memory.from_config(config) if config else Memory()
        logger.info("mem0 initialized successfully with custom instructions")
        return instance
    except Exception as exc:
        logger.exception("Failed to initialize mem0: %s", exc)
        return None
```

### 3. Default Quality Filters

If no custom prompts are configured, sensible defaults are used:

```python
def get_default_quality_filter() -> str:
    return """Memory QA rules:

ONLY STORE CONFIRMED FACTS:
- User preferences (name, settings, preferences)
- Technical details (configurations, APIs, architectures)
- Project context (dependencies, patterns, decisions)
- Specific dates, versions, names, numbers

NEVER STORE:
- Speculation (might, maybe, possibly, I think, could be)
- Temporary context (current time, session info)
- Vague statements without details
- Debug output or error messages
- Code snippets without explanation
- Duplicates of existing memories

QUALITY REQUIREMENTS:
- Confidence level: HIGH (only store what you're certain about)
- Specific details required (who, what, when, where)
- One fact per memory
- No redundancy"""
```

## Configuration Methods

### Method 1: Separate Prompt Files (Recommended)

```bash
# 1. Create prompts directory
sudo mkdir -p /etc/mem0

# 2. Copy default prompts
sudo cp prompts/fact_extraction_default.txt /etc/mem0/fact_extraction.txt
sudo cp prompts/update_memory_default.txt /etc/mem0/update_memory.txt

# 3. Customize prompts (optional)
sudo nano /etc/mem0/fact_extraction.txt

# 4. Configure environment variables in systemd service
sudo nano /etc/systemd/system/mem0-api.service

# Uncomment these lines:
Environment=MEM0_PROXY_FACT_EXTRACTION_PROMPT=/etc/mem0/fact_extraction.txt
Environment=MEM0_PROXY_UPDATE_MEMORY_PROMPT=/etc/mem0/update_memory.txt

# 5. Restart service
sudo systemctl daemon-reload
sudo systemctl restart mem0-api
```

### Method 2: Config JSON

```json
{
  "llm": { ... },
  "embedder": { ... },
  "vector_store": { ... },
  "custom_fact_extraction_prompt": "Memory rules:\n\nSTORE ONLY:\n- Confirmed facts\n- Technical details\n\nNEVER STORE:\n- Speculation\n- Vague statements",
  "custom_update_memory_prompt": "Update rules:\n\nUPDATE if: More specific\nADD if: New topic"
}
```

### Method 3: Automatic Defaults

Just start the proxy - defaults are applied automatically:

```bash
python mem0_proxy.py

# Logs will show:
# "mem0 initialized successfully with custom instructions"
```

## Testing

### Run Quality Filter Tests

```bash
python scripts/test_memory_quality.py
```

Expected output:
```
================================================================================
Testing Memory Quality Filter
================================================================================

Scenario: Speculation - Should be REJECTED
Message: I think maybe the API might be slow...
  ✗ Filtered (as expected)

Scenario: Specific Technical Detail - Should be STORED
Message: The API handles 1000 requests per minute...
  ✓ Stored: API handles 1000 requests per minute

...

✓ Quality filter is working - more items rejected than stored
✓ Test PASSED
```

### Verify in Logs

```bash
journalctl -u mem0-api -f | grep "custom"

# Should see:
# "Loaded custom prompt from /etc/mem0/fact_extraction.txt (2456 chars)"
# "mem0 initialized successfully with custom instructions"
```

## Benefits

### 1. Quality Over Quantity

**Before:**
```
Memory Store:
- "I think the API might be slow" (speculation)
- "Something is wrong" (vague)
- "Error: timeout at line 245" (debug output)
- "API handles 1000 req/min" (confirmed fact)
Total: 100% stored, 75% noise
```

**After:**
```
Memory Store:
- "API handles 1000 req/min" (confirmed fact)
Total: 25% stored, 100% quality
```

### 2. Better Retrieval

Cleaner memories mean:
- ✅ Fewer false positives in search results
- ✅ More relevant context injection
- ✅ Better AI responses

### 3. Storage Efficiency

- ✅ Less vector store bloat
- ✅ Faster searches
- ✅ Lower memory usage

## Use Case Examples

### Medical Assistant

```bash
cat > /etc/mem0/fact_extraction.txt << 'EOF'
Medical memory rules:

STORE ONLY:
✓ Confirmed diagnoses (with doctor name and date)
✓ Verified allergies (with reaction type)
✓ Current medications (with dosage and frequency)
✓ Test results (with values and dates)

NEVER STORE:
✗ Speculation (might, maybe, possibly)
✗ Unverified symptoms
✗ PII (SSN, insurance numbers, addresses)
✗ Debug information

CONFIDENCE: Require 80%+ confidence
SPECIFICITY: Must include who/what/when
EOF

sudo systemctl restart mem0-api
```

### Code Assistant

```bash
cat > /etc/mem0/fact_extraction.txt << 'EOF'
Code assistant memory rules:

STORE ONLY:
✓ User preferences (editor, language, frameworks)
✓ Project configurations (with versions)
✓ Architecture decisions (with rationale)
✓ Code patterns and conventions

NEVER STORE:
✗ Temporary errors or warnings
✗ Debug output
✗ Speculative suggestions
✗ Code snippets without context
✗ Session-specific information

CONFIDENCE: 80%+
SPECIFICITY: Include versions, file names, or exact paths
EOF

sudo systemctl restart mem0-api
```

## Monitoring

### Check Quality Metrics

```bash
# Count stored memories
curl -s http://localhost:8765/v1/health | jq

# Check logs for filtering
journalctl -u mem0-api --since "1 hour ago" | grep -i "filter"
```

### Validation Checklist

- [ ] Custom prompts loaded (check logs)
- [ ] Quality filter test passes
- [ ] No speculation stored
- [ ] No duplicates created
- [ ] Retrieval returns relevant results

## Troubleshooting

### Issue: Memories not being stored

**Check:**
```bash
# Logs will show if prompts are filtering too strictly
journalctl -u mem0-api -f

# Test with basic messages
python scripts/test_memory_quality.py
```

**Fix:**
Adjust prompt in `/etc/mem0/fact_extraction.txt` to be less restrictive.

### Issue: Speculation still being stored

**Check:**
```bash
# Verify prompts are loaded
cat /etc/mem0/fact_extraction.txt

# Check service is using them
systemctl show mem0-api --property=Environment
```

**Fix:**
```bash
# Restart service to reload prompts
sudo systemctl restart mem0-api

# Verify in logs
journalctl -u mem0-api -f | grep "custom"
```

### Issue: Duplicates appearing

**Check:**
The update prompt (`/etc/mem0/update_memory.txt`) handles deduplication.

**Fix:**
Make sure `custom_update_memory_prompt` is configured and includes deduplication rules.

## Files

```
mem0-inject-proxy/
├── mem0_proxy.py                      # Main proxy (updated with quality control)
├── prompts/
│   ├── fact_extraction_default.txt   # Default fact filter
│   └── update_memory_default.txt     # Default update filter
├── test_memory_quality.py            # Quality filter test script
├── config.example.json               # Example config with prompts
├── mem0-api.service                  # Systemd service (updated)
└── setup-quality-filter.sh           # Automated setup script
```

## References

- [Mem0 Custom Instructions Documentation](https://docs.mem0.ai/platform/features/custom-instructions)
- [Controlling Memory Ingestion Cookbook](https://docs.mem0.ai/cookbooks/essentials/controlling-memory-ingestion)
- [Mem0 Python SDK](https://deepwiki.com/hippoley/mem0/5.1-python-sdk)