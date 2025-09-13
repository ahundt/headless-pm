#!/usr/bin/env python3
"""
TDD test for new flat structure registration.
Test that register_api_server() creates correct flat structure.
"""

import os
import sys
import time
sys.path.append('/Users/athundt/source/agentic/headless-pm')

from src.utils.process_registry import register_api_server, get_registry_status

def test_new_flat_registration():
    """Test that new registration creates flat structure."""
    print("=== TDD: Testing New Flat Registration ===")
    
    # Test registration
    success = register_api_server()
    print(f"Registration result: {'✅ Success' if success else '❌ Failed'}")
    
    if success:
        # Check structure
        status = get_registry_status()
        print(f"Registry status: {status}")
        
        # Verify flat structure exists
        if 'processes' in str(status):
            print("✅ New flat processes structure created")
        else:
            print("❌ Flat structure not found")
            
        # Check for PID conflicts
        current_pid = os.getpid()
        api_pid = status.get('api_pid')
        if api_pid == current_pid:
            print(f"✅ API PID {current_pid} registered correctly")
        else:
            print(f"❌ API PID mismatch: expected {current_pid}, got {api_pid}")
    
    print("✅ TDD: New registration structure test complete")
    return success

if __name__ == "__main__":
    test_new_flat_registration()