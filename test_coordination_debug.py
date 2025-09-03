#!/usr/bin/env python3
"""Debug script for multi-client MCP coordination issue."""

import subprocess
import asyncio
import time
import httpx
import os
import json
from pathlib import Path

async def check_coordination_file():
    """Check the coordination file state."""
    coord_file = Path.home() / '.headless-pm' / 'mcp_coordination.json'
    if coord_file.exists():
        with open(coord_file) as f:
            data = json.load(f)
        print(f"Coordination file: {json.dumps(data, indent=2)}")
    else:
        print("Coordination file does not exist")

async def test_multi_client_coordination():
    print('=== Testing Multi-Client Coordination ===')
    
    # Clean up any existing coordination file
    coord_file = Path.home() / '.headless-pm' / 'mcp_coordination.json'
    if coord_file.exists():
        coord_file.unlink()
        print("Cleaned up existing coordination file")
    
    # Start first MCP client
    print('\n1. Starting first MCP client...')
    env1 = {'SERVICE_PORT': '9876', **os.environ}
    proc1 = subprocess.Popen(['python', '-m', 'src.mcp'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env1, text=True)
    
    await asyncio.sleep(4)  # Wait for startup
    
    # Check coordination file after first client
    print("\nAfter starting first client:")
    await check_coordination_file()
    
    # Check API is running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get('http://localhost:9876/health', timeout=5)
            print(f'✅ API started by first client: {response.status_code}')
    except Exception as e:
        print(f'❌ API not responding: {e}')
        # Print first client output
        stdout, _ = proc1.communicate(timeout=1)
        print("First client output:", stdout)
        return
    
    # Start second client
    print('\n2. Starting second MCP client...')
    env2 = {'SERVICE_PORT': '9876', **os.environ}
    proc2 = subprocess.Popen(['python', '-m', 'src.mcp'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env2, text=True)
    
    await asyncio.sleep(3)
    
    # Check coordination file after second client
    print("\nAfter starting second client:")
    await check_coordination_file()
    
    # Check API still running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get('http://localhost:9876/health', timeout=5)
            print(f'✅ API running with both clients: {response.status_code}')
    except Exception as e:
        print(f'❌ API not responding with both clients: {e}')
    
    # Terminate first client
    print('\n3. Terminating first client...')
    proc1.terminate()
    proc1.wait()
    
    await asyncio.sleep(2)
    
    # Check coordination file after first client exit
    print("\nAfter terminating first client:")
    await check_coordination_file()
    
    # Check if API still running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get('http://localhost:9876/health', timeout=5)
            print(f'✅ API still running after first client exit: {response.status_code}')
    except Exception as e:
        print(f'❌ API stopped when first client exited: {e}')
    
    # Cleanup
    print('\n4. Terminating second client...')
    proc2.terminate()
    proc2.wait()
    
    await asyncio.sleep(1)
    
    # Check coordination file after all clients exit
    print("\nAfter terminating all clients:")
    await check_coordination_file()
    
    # Final API check
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get('http://localhost:9876/health', timeout=5)
            print(f'⚠️ API still running after all clients exited: {response.status_code}')
    except Exception as e:
        print(f'✅ API correctly stopped after all clients exited')

if __name__ == '__main__':
    asyncio.run(test_multi_client_coordination())