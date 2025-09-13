#!/usr/bin/env python3
"""
TDD test for PID conflict detection implementation.
Test-driven development for KISS coordination fix.
"""

import os
import sys
import time
sys.path.append('/Users/athundt/source/agentic/headless-pm')

from src.utils.process_registry import check_pid_conflict

def test_pid_conflict_detection():
    """Test that PID conflict detection works correctly."""
    print("=== TDD: Testing PID Conflict Detection ===")
    
    # Test 1: No conflicts in empty data
    empty_data = {}
    assert check_pid_conflict(empty_data, 12345, 'api_server') == False
    print("✅ Test 1: Empty data allows registration")
    
    # Test 2: No conflicts with different PIDs
    data_different_pids = {
        'processes': {
            '12345': {'type': 'api_server', 'started': time.time()}
        }
    }
    assert check_pid_conflict(data_different_pids, 12346, 'mcp_client') == False
    print("✅ Test 2: Different PIDs allowed")
    
    # Test 3: Conflict detected - same PID different type  
    data_same_pid = {
        'processes': {
            '12345': {'type': 'api_server', 'started': time.time()}
        }
    }
    assert check_pid_conflict(data_same_pid, 12345, 'mcp_client') == True
    print("✅ Test 3: Same PID conflict detected")
    
    # Test 4: Same PID same type is OK (idempotent)
    assert check_pid_conflict(data_same_pid, 12345, 'api_server') == False  
    print("✅ Test 4: Same PID same type allowed (idempotent)")
    
    # Test 5: Legacy api_pid conflict detection
    legacy_data = {'api_pid': 12345}
    assert check_pid_conflict(legacy_data, 12345, 'mcp_client') == True
    print("✅ Test 5: Legacy api_pid conflict detected")
    
    # Test 6: Legacy clients conflict detection
    legacy_clients = {
        'clients': {
            'mcp_12345_123': {'pid': 12345, 'timestamp': time.time()}
        }
    }
    assert check_pid_conflict(legacy_clients, 12345, 'api_server') == True
    print("✅ Test 6: Legacy clients conflict detected")
    
    print("\n🎉 All PID conflict detection tests passed!")
    return True

if __name__ == "__main__":
    test_pid_conflict_detection()
    print("✅ TDD: PID conflict detection is working correctly")