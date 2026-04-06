#!/usr/bin/env python3
"""Test that custom instructions are properly filtering memories."""

import asyncio
import json
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    from mem0 import Memory
except ImportError:
    print("Error: mem0 is not installed. Install with: pip install mem0ai")
    sys.exit(1)

# Import default prompts from proxy
from mem0_proxy import get_default_quality_filter, get_default_update_filter

TEST_MESSAGES = [
    # Should be REJECTED (speculation)
    {
        "scenario": "Speculation - Should be REJECTED",
        "messages": [
            {"role": "user", "content": "I think maybe the API might be slow"},
        ]
    },
    
    # Should be STORED (confirmed fact with specifics)
    {
        "scenario": "Specific Technical Detail - Should be STORED",
        "messages": [
            {"role": "user", "content": "The API handles 1000 requests per minute"},
        ]
    },
    
    # Should be REJECTED (vague)
    {
        "scenario": "Vague Statement - Should be REJECTED",
        "messages": [
            {"role": "user", "content": "Something is wrong with the database"},
        ]
    },
    
    # Should be STORED (specific technical detail)
    {
        "scenario": "Specific Configuration - Should be STORED",
        "messages": [
            {"role": "user", "content": "PostgreSQL database is running on port 5432"},
        ]
    },
    
    # Should be REJECTED (duplicate - similar to previous)
    {
        "scenario": "Potential Duplicate - Should check for existing",
        "messages": [
            {"role": "user", "content": "API rate limit is 1000 requests per minute"},
        ]
    },
    
    # Should be STORED (preference)
    {
        "scenario": "User Preference - Should be STORED",
        "messages": [
            {"role": "user", "content": "I prefer Python 3.11 for production projects"},
        ]
    },
    
    # Should be REJECTED (speculation with some details)
    {
        "scenario": "Mixed Speculation - Should be REJECTED",
        "messages": [
            {"role": "user", "content": "I think FastAPI 0.100 may have issues with Pydantic v2"},
        ]
    },
    
    # Should be STORED (confirmed architecture decision)
    {
        "scenario": "Architecture Decision - Should be STORED",
        "messages": [
            {"role": "user", "content": "We chose Redis for caching to reduce database load"},
        ]
    },
]


async def test_quality_filter():
    """Test the memory quality filter with various scenarios."""
    
    print("=" * 80)
    print("Testing Memory Quality Filter")
    print("=" * 80)
    print()
    
    # Create memory instance with default prompts
    config = {
        "custom_fact_extraction_prompt": get_default_quality_filter(),
        "custom_update_memory_prompt": get_default_update_filter(),
    }
    
    # Try to load actual config if available
    config_path = os.getenv("MEM0_CONFIG_PATH", "/root/.mem0/config.json")
    if os.path.exists(config_path):
        print(f"Loading config from {config_path}")
        try:
            with open(config_path, "r") as f:
                base_config = json.load(f)
                base_config.update(config)
                config = base_config
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
    else:
        print("Using default configuration (no config file found)")
    
    try:
        memory = Memory.from_config(config)
        print("✓ Memory initialized successfully\n")
    except Exception as e:
        print(f"✗ Failed to initialize memory: {e}")
        return
    
    # Test user ID
    user_id = "test_quality_filter"
    
    # Clear existing test memories
    print("Clearing existing test memories...")
    try:
        all_memories = memory.get_all(user_id=user_id)
        for m in all_memories.get("results", []):
            try:
                memory.delete(m["id"])
            except Exception:
                pass
        print("✓ Cleared existing memories\n")
    except Exception as e:
        print(f"Warning: Could not clear memories: {e}\n")
    
    # Run test scenarios
    print("Running test scenarios:")
    print("-" * 80)
    
    stored_count = 0
    rejected_count = 0
    
    for test_case in TEST_MESSAGES:
        scenario = test_case["scenario"]
        messages = test_case["messages"]
        
        print(f"\nScenario: {scenario}")
        print(f"Message: {messages[0]['content'][:70]}...")
        
        try:
            result = memory.add(messages, user_id=user_id)
            results = result.get("results", [])
            
            if results:
                memory_text = results[0].get("memory", "N/A")
                print(f"  ✓ Stored: {memory_text[:80]}...")
                stored_count += 1
            else:
                print(f"  ✗ Filtered (as expected)")
                rejected_count += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            rejected_count += 1
    
    print("\n" + "=" * 80)
    print("Final stored memories:")
    print("-" * 80)
    
    try:
        final = memory.get_all(user_id=user_id)
        for m in final.get("results", []):
            print(f"  • {m['memory']}")
    except Exception as e:
        print(f"Error retrieving memories: {e}")
    
    print("\n" + "=" * 80)
    print(f"Test Results:")
    print(f"  Stored: {stored_count}")
    print(f"  Rejected: {rejected_count}")
    print(f"  Total: {len(TEST_MESSAGES)}")
    
    # Expected: More rejections than stored (quality filter working)
    print("\n" + "=" * 80)
    if rejected_count > stored_count:
        print("✓ Quality filter is working - more items rejected than stored")
        print("✓ Test PASSED")
    else:
        print("✗ Quality filter may not be working properly")
        print("✗ Test FAILED - consider reviewing custom prompts")
    
    # Cleanup
    print("\nCleaning up test memories...")
    try:
        all_memories = memory.get_all(user_id=user_id)
        for m in all_memories.get("results", []):
            try:
                memory.delete(m["id"])
            except Exception:
                pass
        print("✓ Cleanup complete")
    except Exception as e:
        print(f"Warning: Could not cleanup: {e}")


if __name__ == "__main__":
    asyncio.run(test_quality_filter())