#!/bin/bash
# Setup script for mem0-proxy with memory quality control

set -e

echo "=========================================="
echo "mem0-proxy Quality Control Setup"
echo "=========================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Create prompts directory
echo -e "${YELLOW}Creating prompts directory...${NC}"
mkdir -p /etc/mem0
echo -e "${GREEN}✓ Created /etc/mem0${NC}"

# Copy prompt files
echo -e "${YELLOW}Installing custom prompt files...${NC}"
if [ -f "prompts/fact_extraction_default.txt" ]; then
    cp prompts/fact_extraction_default.txt /etc/mem0/fact_extraction.txt
    echo -e "${GREEN}✓ Installed fact_extraction.txt${NC}"
else
    echo -e "${RED}✗ fact_extraction_default.txt not found${NC}"
    exit 1
fi

if [ -f "prompts/update_memory_default.txt" ]; then
    cp prompts/update_memory_default.txt /etc/mem0/update_memory.txt
    echo -e "${GREEN}✓ Installed update_memory.txt${NC}"
else
    echo -e "${RED}✗ update_memory_default.txt not found${NC}"
    exit 1
fi

# Set permissions
chmod 644 /etc/mem0/*.txt
echo -e "${GREEN}✓ Set permissions on prompt files${NC}"

# Update systemd service
echo -e "${YELLOW}Updating systemd service...${NC}"
if [ -f "mem0-api.service" ]; then
    # Check if Environment lines are already uncommented
    if grep -q "^Environment=MEM0_PROXY_FACT_EXTRACTION_PROMPT" mem0-api.service; then
        echo -e "${GREEN}✓ Service already configured for custom prompts${NC}"
    else
        # Uncomment the prompt environment variables
        sed -i 's|# Environment=MEM0_PROXY_FACT_EXTRACTION_PROMPT|Environment=MEM0_PROXY_FACT_EXTRACTION_PROMPT|g' mem0-api.service
        sed -i 's|# Environment=MEM0_PROXY_UPDATE_MEMORY_PROMPT|Environment=MEM0_PROXY_UPDATE_MEMORY_PROMPT|g' mem0-api.service
        echo -e "${GREEN}✓ Updated service to use custom prompts${NC}"
    fi
    
    cp mem0-api.service /etc/systemd/system/
    systemctl daemon-reload
    echo -e "${GREEN}✓ Installed systemd service${NC}"
else
    echo -e "${RED}✗ mem0-api.service not found${NC}"
    exit 1
fi

# Check if mem0 config exists
MEM0_CONFIG="/root/.mem0/config.json"
if [ ! -f "$MEM0_CONFIG" ]; then
    echo -e "${YELLOW}Creating mem0 config directory...${NC}"
    mkdir -p /root/.mem0
    echo -e "${GREEN}✓ Created /root/.mem0${NC}"
    
    if [ -f "config.example.json" ]; then
        echo -e "${YELLOW}Creating config from example...${NC}"
        cp config.example.json "$MEM0_CONFIG"
        echo -e "${GREEN}✓ Created $MEM0_CONFIG${NC}"
        echo -e "${YELLOW}NOTE: Edit $MEM0_CONFIG with your Ollama host URL${NC}"
    else
        echo -e "${YELLOW}Creating minimal config...${NC}"
        cat > "$MEM0_CONFIG" << 'EOF'
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
EOF
        echo -e "${GREEN}✓ Created $MEM0_CONFIG${NC}"
        echo -e "${YELLOW}NOTE: Edit $MEM0_CONFIG with your Ollama host URL${NC}"
    fi
fi

# Restart service if running
echo -e "${YELLOW}Restarting mem0-api service...${NC}"
if systemctl is-active --quiet mem0-api; then
    systemctl restart mem0-api
    echo -e "${GREEN}✓ Service restarted${NC}"
else
    echo -e "${YELLOW}Service not running, will start after installation${NC}"
fi

# Copy main script
echo -e "${YELLOW}Installing mem0_proxy.py...${NC}"
if [ -f "mem0_proxy.py" ]; then
    cp mem0_proxy.py /opt/mem0_proxy.py
    chmod 755 /opt/mem0_proxy.py
    echo -e "${GREEN}✓ Installed mem0_proxy.py${NC}"
else
    echo -e "${RED}✗ mem0_proxy.py not found${NC}"
    exit 1
fi

# Enable service
echo -e "${YELLOW}Enabling mem0-api service...${NC}"
systemctl enable mem0-api
echo -e "${GREEN}✓ Service enabled${NC}"

# Start service
echo -e "${YELLOW}Starting mem0-api service...${NC}"
systemctl start mem0-api
echo -e "${GREEN}✓ Service started${NC}"

# Show status
echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Configuration files:"
echo "  - Prompt files: /etc/mem0/*.txt"
echo "  - Config: $MEM0_CONFIG"
echo "  - Service: /etc/systemd/system/mem0-api.service"
echo ""
echo "View logs: journalctl -u mem0-api -f"
echo "Check status: systemctl status mem0-api"
echo ""
echo -e "${GREEN}✓ Quality filtering enabled with default prompts${NC}"
echo -e "${YELLOW}To customize, edit /etc/mem0/fact_extraction.txt${NC}"
echo ""