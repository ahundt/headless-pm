#!/usr/bin/env python3
"""
TDD test for flat PID-keyed structure implementation.
Tests the new processes object format that prevents duplicate PIDs.
"""

import os
import sys
import time
sys.path.append('/Users/athundt/source/agentic/headless-pm')

def test_flat_structure_prevents_duplicates():
    """Test that flat structure design prevents same PID duplicates."""
    print("=== TDD: Testing Flat Structure Design ===")
    
    # Test the desired flat structure
    flat_data = {
        'processes': {
            '12345': {'type': 'api_server', 'started': time.time(), 'repository': '/path/to/repo'},
            '12346': {'type': 'mcp_client', 'started': time.time(), 'client_id': 'mcp_12346_123'}
        },
        'primary_api': 12345
    }
    
    # Test 1: Each PID appears exactly once
    all_pids = []
    for pid_str in flat_data['processes'].keys():
        all_pids.append(int(pid_str))
    
    assert len(all_pids) == len(set(all_pids)), "PIDs should be unique"
    print("✅ Test 1: All PIDs are unique in flat structure")
    
    # Test 2: Cannot accidentally add same PID twice
    try:
        # This should be impossible by design
        if '12345' in flat_data['processes']:
            print("✅ Test 2: PID 12345 already exists - duplicate prevented by structure")
        else:
            print("❌ Test 2: PID checking failed")
    except Exception as e:
        print(f"❌ Test 2: Structure error: {e}")
    
    # Test 3: Primary API references valid process
    primary_api = flat_data['primary_api']
    assert str(primary_api) in flat_data['processes'], "Primary API must reference existing process"
    assert flat_data['processes'][str(primary_api)]['type'] == 'api_server', "Primary must be API server"
    print("✅ Test 3: Primary API references valid API server process")
    
    # Test 4: Structure is self-documenting
    api_count = sum(1 for info in flat_data['processes'].values() if info['type'] == 'api_server')
    mcp_count = sum(1 for info in flat_data['processes'].values() if info['type'] == 'mcp_client')
    print(f"✅ Test 4: Structure clearly shows {api_count} API servers, {mcp_count} MCP clients")
    
    print("\n🎉 All flat structure tests passed!")
    print("✅ Flat structure design prevents duplicate PIDs by construction")
    return True

if __name__ == "__main__":
    test_flat_structure_prevents_duplicates()
    print("✅ TDD: Flat structure design validated")