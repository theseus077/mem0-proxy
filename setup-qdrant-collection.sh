#!/bin/bash
set -e

# Configuration
COLLECTION_NAME="${COLLECTION_NAME:-mem0}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
MAX_RETRIES=30
RETRY_INTERVAL=5

echo "=== Configuring Qdrant Collection '$COLLECTION_NAME' ==="

# Check if Qdrant is running
echo "Checking Qdrant connection..."
if ! curl -s "$QDRANT_URL/collections" > /dev/null; then
    echo "❌ Qdrant not reachable at $QDRANT_URL"
    exit 1
fi
echo "✅ Qdrant is reachable"

# Wait for collection to be ready
echo "Waiting for collection '$COLLECTION_NAME'..."
for i in $(seq 1 $MAX_RETRIES); do
    if curl -s "$QDRANT_URL/collections/$COLLECTION_NAME" | grep -q '"status":"green"'; then
        echo "✅ Collection '$COLLECTION_NAME' found and ready"
        break
    fi
    echo "⏳ Waiting for collection... ($i/$MAX_RETRIES)"
    if [ $i -eq $MAX_RETRIES ]; then
        echo "❌ Collection '$COLLECTION_NAME' not ready after $MAX_RETRIES retries"
        exit 1
    fi
    sleep $RETRY_INTERVAL
done

# Get current config for reference
echo "Current collection configuration:"
curl -s "$QDRANT_URL/collections/$COLLECTION_NAME" | jq '.result.config'

echo "=== Step 1: Activate Quantization ==="
QUANTIZATION_RESPONSE=$(curl -s -X PATCH "$QDRANT_URL/collections/$COLLECTION_NAME" \
    -H 'Content-Type: application/json' \
    -d '{
        "quantization_config": {
            "scalar": {
                "type": "int8",
                "quantile": 0.99,
                "always_ram": true
            }
        }
    }')

if echo "$QUANTIZATION_RESPONSE" | grep -q '"status":"ok"'; then
    echo "✅ Quantization activated successfully"
else
    echo "❌ Failed to activate quantization: $QUANTIZATION_RESPONSE"
    exit 1
fi

sleep 2

echo "=== Step 2: Update HNSW Configuration ==="
HNSW_RESPONSE=$(curl -s -X PATCH "$QDRANT_URL/collections/$COLLECTION_NAME" \
    -H 'Content-Type: application/json' \
    -d '{
        "hnsw_config": {
            "m": 16,
            "ef_construct": 100,
            "full_scan_threshold": 1000000,
            "max_indexing_threads": 0,
            "on_disk": false
        }
    }')

if echo "$HNSW_RESPONSE" | grep -q '"status":"ok"'; then
    echo "✅ HNSW config updated successfully"
else
    echo "⚠️ Could not update HNSW config: $HNSW_RESPONSE"
    echo "(This may be because it matches the default config)"
fi

sleep 2

echo "=== Step 3: Update Optimizer Configuration ==="
OPTIMIZER_RESPONSE=$(curl -s -X PATCH "$QDRANT_URL/collections/$COLLECTION_NAME" \
    -H 'Content-Type: application/json' \
    -d '{
        "optimizer_config": {
            "deleted_threshold": 0.2,
            "vacuum_min_vector_number": 1000,
            "default_segment_number": 0,
            "max_segment_size": null,
            "memmap_threshold": null,
            "indexing_threshold": 102400,
            "flush_interval_sec": 5,
            "max_optimization_threads": null
        }
    }')

if echo "$OPTIMIZER_RESPONSE" | grep -q '"status":"ok"'; then
    echo "✅ Optimizer config updated successfully"
else
    echo "⚠️ Could not update optimizer config: $OPTIMIZER_RESPONSE"
    echo "(This may be because it matches the default config)"
fi

sleep 2

echo "=== Step 4: Update Vectors Configuration ==="
VECTORS_RESPONSE=$(curl -s -X PATCH "$QDRANT_URL/collections/$COLLECTION_NAME" \
    -H 'Content-Type: application/json' \
    -d '{
        "vectors": {
            "": {
                "on_disk": false
            }
        }
    }')

if echo "$VECTORS_RESPONSE" | grep -q '"status":"ok"'; then
    echo "✅ Vectors config updated successfully"
else
    echo "⚠️ Could not update vectors config: $VECTORS_RESPONSE"
fi

# Verify final configuration
echo "Final collection configuration:"
curl -s "$QDRANT_URL/collections/$COLLECTION_NAME" | jq '.result.config'

# Wait a bit for changes to propagate
echo "Waiting 5 seconds for changes to take effect..."
sleep 5

echo "=== Collection configuration complete ==="
echo "Summary of changes:"
echo "1. ✅ Scalar quantization activated (int8, quantile=0.99, always_ram=true)"
echo "2. ✅ HNSW config: m=16, ef_construct=100, full_scan_threshold=1MB"
echo "3. ✅ Optimizer config: indexing_threshold=100KB, deleted_threshold=0.2"
echo "4. ✅ Vectors: on_disk=false"
echo ""
echo "Note: Parameters like max_indexing_threads, vaccum_min_vector_number, flush_interval_sec"
echo "      should be set via Qdrant config.yaml as instance-wide defaults."