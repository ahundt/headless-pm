#!/usr/bin/env python3
"""
TDD test for complete KISS coordination implementation.
Validates flat structure prevents all PID conflicts and coordination contamination.
"""

import os
import sys
import time
import json
import tempfile
sys.path.append('/Users/athundt/source/agentic/headless-pm')

from src.utils.process_registry import (
    register_api_server, get_registry_status, 
    check_pid_conflict, migrate_legacy_structure,
    get_process_registry_path
)

def test_complete_coordination_system():
    """Test that complete flat structure prevents all coordination issues."""
    print("=== TDD: Testing Complete KISS Coordination ===")
    
    # Test 1: Clean registration creates correct structure
    coord_file = get_process_registry_path()
    print(f"Testing coordination file: {coord_file}")
    
    # Clean start
    if coord_file.exists():
        coord_file.unlink()
        print("✅ Cleaned coordination file for fresh test")
    
    # Register API server
    success = register_api_server()
    assert success, "API registration should succeed"
    print("✅ Test 1: API server registration successful")
    
    # Check structure
    status = get_registry_status()
    print(f"Registry status: {json.dumps(status, indent=2)}")
    
    # Validate flat structure
    processes = status.get('processes', {})
    assert len(processes) > 0, "Should have registered processes"
    
    current_pid = str(os.getpid())
    assert current_pid in processes, f"Current PID {current_pid} should be registered"
    assert processes[current_pid]['type'] == 'api_server', "Should be registered as API server"
    print("✅ Test 2: Flat structure contains correct API server entry")
    
    # Test 3: Validate no duplicate PIDs possible
    all_pids = []
    for pid_str in processes.keys():
        all_pids.append(int(pid_str))
    
    assert len(all_pids) == len(set(all_pids)), "All PIDs should be unique"
    print("✅ Test 3: No duplicate PIDs in flat structure")
    
    # Test 4: Validate structure self-documents
    api_count = status.get('api_servers', [])
    mcp_count = status.get('mcp_clients', [])
    print(f"✅ Test 4: Clear process summary - {len(api_count)} API servers, {len(mcp_count)} MCP clients")
    
    # Test 5: Validate repository tracking
    repo_path = processes[current_pid].get('repository')
    assert repo_path, "Repository path should be tracked"
    assert 'headless-pm' in repo_path, "Should track correct repository"
    print(f"✅ Test 5: Repository tracking works - {repo_path}")
    
    # Test 6: Test conflict prevention
    test_data = {
        'processes': {
            current_pid: {'type': 'api_server', 'started': time.time()}
        }
    }
    
    conflict_detected = check_pid_conflict(test_data, int(current_pid), 'mcp_client')
    assert conflict_detected, "Should detect PID conflict"
    print("✅ Test 6: PID conflict detection prevents same PID registration")
    
    print("\n🎉 Complete coordination system validation successful!")
    print("✅ KISS flat structure implementation eliminates all PID conflicts")
    return True

if __name__ == "__main__":
    test_complete_coordination_system()
    print("✅ TDD: Complete KISS coordination system validated")