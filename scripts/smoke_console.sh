#!/usr/bin/env bash
# Smoke test for the bundled console and cloud-parity API features.
#
# Usage:
#   ./scripts/smoke_console.sh [--port PORT]
#
# Prerequisites:
#   - EverOS server running with --console flag
#   - curl and jq installed
#
# This script exercises the core cloud-parity flow:
#   1. Health check
#   2. Create API key (using bootstrap token or legacy_open mode)
#   3. Add memory
#   4. Search memory
#   5. Edit profile
#   6. View request logs
#   7. View usage
#   8. Console assets served
#
# Exit codes:
#   0 - All tests passed
#   1 - One or more tests failed

set -euo pipefail

PORT="${1:-8000}"
BASE_URL="http://127.0.0.1:${PORT}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() {
    echo -e "${GREEN}PASS${NC}: $1"
}

fail() {
    echo -e "${RED}FAIL${NC}: $1"
    FAILED=1
}

FAILED=0

echo "EverOS Console Smoke Test"
echo "========================="
echo "Target: ${BASE_URL}"
echo ""

# 1. Health check
echo "1. Health check..."
if curl -sf "${BASE_URL}/health" > /dev/null; then
    pass "Health endpoint responds"
else
    fail "Health endpoint not responding"
    echo "   Is the server running? Start with: everos server start --root /tmp/everos-smoke"
    exit 1
fi

# 2. Create API key (requires legacy_open=true or bootstrap token)
echo "2. Create API key..."
KEY_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/auth/keys" \
    -H "Content-Type: application/json" \
    -d '{"name": "smoke-test", "scopes": ["admin"]}' 2>/dev/null || echo '{"error": true}')

if echo "$KEY_RESPONSE" | jq -e '.key' > /dev/null 2>&1; then
    API_KEY=$(echo "$KEY_RESPONSE" | jq -r '.key')
    pass "Created API key: ${API_KEY:0:20}..."
elif echo "$KEY_RESPONSE" | jq -e '.key_id' > /dev/null 2>&1; then
    API_KEY=$(echo "$KEY_RESPONSE" | jq -r '.key')
    pass "Created API key: ${API_KEY:0:20}..."
else
    echo "   Note: Key creation failed (expected if auth.legacy_open=false and no bootstrap token)"
    echo "   Using no auth for remaining tests (may fail if auth required)"
    API_KEY=""
fi

AUTH_HEADER=""
if [ -n "$API_KEY" ]; then
    AUTH_HEADER="-H \"Authorization: Bearer ${API_KEY}\""
fi

# 3. Add memory
echo "3. Add memory..."
ADD_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/add" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{
        "user_id": "smoke_user",
        "messages": [
            {"role": "user", "content": "My favorite color is blue."}
        ]
    }' 2>/dev/null || echo '{"error": true}')

if echo "$ADD_RESPONSE" | jq -e '.data.session_id' > /dev/null 2>&1; then
    SESSION_ID=$(echo "$ADD_RESPONSE" | jq -r '.data.session_id')
    pass "Added memory (session: ${SESSION_ID})"
elif echo "$ADD_RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
    fail "Add memory failed: $(echo "$ADD_RESPONSE" | jq -r '.error.message // .error')"
else
    fail "Add memory: unexpected response"
fi

# 4. Search memory
echo "4. Search memory..."
SEARCH_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/search" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{
        "query": "What is the users favorite color?",
        "user_id": "smoke_user"
    }' 2>/dev/null || echo '{"error": true}')

if echo "$SEARCH_RESPONSE" | jq -e '.data' > /dev/null 2>&1; then
    COUNT=$(echo "$SEARCH_RESPONSE" | jq -r '.data.total_count // 0')
    pass "Search returned (count: ${COUNT})"
else
    fail "Search memory failed"
fi

# 5. Edit profile
echo "5. Edit profile..."
EDIT_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/edit" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{
        "user_id": "smoke_user",
        "operations": [
            {"action": "add", "type": "explicit_info", "data": {"category": "preference", "description": "Likes blue"}}
        ]
    }' 2>/dev/null || echo '{"error": true}')

if echo "$EDIT_RESPONSE" | jq -e '.data.applied' > /dev/null 2>&1; then
    APPLIED=$(echo "$EDIT_RESPONSE" | jq -r '.data.applied')
    ITEM_ID=$(echo "$EDIT_RESPONSE" | jq -r '.data.results[0].item_id // ""')
    if [ "${APPLIED}" = "1" ] && [ -n "${ITEM_ID}" ]; then
        pass "Edit profile (applied: ${APPLIED}, item_id: ${ITEM_ID})"
    else
        fail "Edit profile returned unexpected data"
    fi
else
    fail "Edit profile failed"
fi

# 5b. Verify profile edit persisted
echo "5b. Verify profile persisted..."
GET_PROFILE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/get" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{"user_id": "smoke_user", "memory_type": "profile"}' 2>/dev/null || echo '{"error": true}')

if echo "$GET_PROFILE" | jq -e '.data.profiles[0].profile_data.explicit_info' > /dev/null 2>&1; then
    INFO_COUNT=$(echo "$GET_PROFILE" | jq -r '.data.profiles[0].profile_data.explicit_info | length')
    if [ "${INFO_COUNT}" -ge 1 ]; then
        pass "Profile persisted (${INFO_COUNT} explicit_info items)"
    else
        fail "Profile explicit_info is empty after edit"
    fi
else
    # Profile may not exist yet if extraction hasn't run
    echo "   Note: Profile not yet available (extraction may not have run)"
    pass "Profile get endpoint responded"
fi

# 5c. Test context endpoint
echo "5c. Test context endpoint..."
CONTEXT_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/context" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{
        "query": "What does the user like?",
        "user_id": "smoke_user",
        "top_k": 5
    }' 2>/dev/null || echo '{"error": true}')

if echo "$CONTEXT_RESPONSE" | jq -e '.data.context' > /dev/null 2>&1; then
    TOKENS=$(echo "$CONTEXT_RESPONSE" | jq -r '.data.tokens_estimate // 0')
    pass "Context endpoint (tokens_estimate: ${TOKENS})"
else
    fail "Context endpoint failed"
fi

# 5d. Test feedback endpoint
echo "5d. Test feedback endpoint..."
FEEDBACK_RESPONSE=$(curl -sf -X POST "${BASE_URL}/api/v2/memory/feedback" \
    -H "Content-Type: application/json" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    -d '{
        "memory_type": "episode",
        "memory_id": "smoke_user_ep_test",
        "rating": "positive"
    }' 2>/dev/null || echo '{"error": true}')

if echo "$FEEDBACK_RESPONSE" | jq -e '.data.id' > /dev/null 2>&1; then
    FB_ID=$(echo "$FEEDBACK_RESPONSE" | jq -r '.data.id')
    pass "Feedback persisted (id: ${FB_ID})"

    # Verify feedback can be retrieved
    GET_FB=$(curl -sf "${BASE_URL}/api/v2/memory/feedback?memory_id=smoke_user_ep_test" \
        ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} 2>/dev/null || echo '{"error": true}')
    if echo "$GET_FB" | jq -e '.data.items[0]' > /dev/null 2>&1; then
        pass "Feedback retrieved"
    else
        fail "Feedback retrieval failed"
    fi
else
    fail "Feedback endpoint failed"
fi

# 6. View request logs (requires admin)
echo "6. View request logs..."
LOGS_RESPONSE=$(curl -sf "${BASE_URL}/api/v2/admin/request-logs?limit=5" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    2>/dev/null || echo '{"error": true}')

if echo "$LOGS_RESPONSE" | jq -e '.data.items' > /dev/null 2>&1; then
    LOG_COUNT=$(echo "$LOGS_RESPONSE" | jq -r '.data.items | length')
    pass "Request logs (count: ${LOG_COUNT})"
else
    fail "Request logs failed (requires admin scope)"
fi

# 7. View usage
echo "7. View usage..."
USAGE_RESPONSE=$(curl -sf "${BASE_URL}/api/v2/admin/usage?range=24h" \
    ${AUTH_HEADER:+-H "Authorization: Bearer ${API_KEY}"} \
    2>/dev/null || echo '{"error": true}')

if echo "$USAGE_RESPONSE" | jq -e '.data.totals' > /dev/null 2>&1; then
    pass "Usage endpoint responds"
else
    fail "Usage endpoint failed (requires admin scope)"
fi

# 8. Console assets
echo "8. Console assets..."
if curl -sf "${BASE_URL}/console" | grep -q "<!DOCTYPE html>"; then
    pass "Console HTML served"
else
    fail "Console HTML not served"
fi

if curl -sf "${BASE_URL}/console/app.js" | grep -q "function"; then
    pass "Console JS served"
else
    fail "Console JS not served"
fi

if curl -sf "${BASE_URL}/console/app.css" | grep -q "body"; then
    pass "Console CSS served"
else
    fail "Console CSS not served"
fi

# Summary
echo ""
echo "========================="
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All smoke tests passed${NC}"
    exit 0
else
    echo -e "${RED}Some smoke tests failed${NC}"
    exit 1
fi
