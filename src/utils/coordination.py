"""
Shared coordination utilities for process lifecycle management.
DRY implementation used by both MCP server and API server.
"""

import os
import time
import tempfile
from pathlib import Path
from typing import Dict, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from src.utils.atomic_file_ops import AtomicFileOperations, with_coordination_lock
except ImportError:
    # Fallback for import issues
    AtomicFileOperations = None
    with_coordination_lock = None


def get_coordination_file_path(port: str = None) -> Path:
    """
    Get coordination file path using same logic as MCP server.
    DRY implementation for consistent coordination file naming.
    """
    temp_dir = Path(tempfile.gettempdir())
    port = port or os.environ.get('SERVICE_PORT', '6969')
    return temp_dir / f"headless_pm_mcp_clients_{port}.json"


def register_api_process(port: str = None) -> bool:
    """
    Register API process PID in coordination system.
    DRY implementation matching MCP client registration logic.
    """
    if not AtomicFileOperations:
        return False  # Skip if atomic operations unavailable
        
    coordination_file = get_coordination_file_path(port)
    
    def add_api_pid(data: Dict) -> Dict:
        """Add API process PID to coordination data."""
        # Clean existing stale API PID
        existing_api_pid = data.get('api_pid')
        if existing_api_pid and HAS_PSUTIL and not psutil.pid_exists(existing_api_pid):
            data.pop('api_pid', None)
            
        # Register this API process
        data['api_pid'] = os.getpid()
        
        # Preserve existing clients
        data['clients'] = data.get('clients', {})
        
        return data
    
    try:
        AtomicFileOperations.atomic_json_update(
            coordination_file, add_api_pid, {}
        )
        return True
    except Exception:
        return False


def unregister_api_process(port: str = None) -> bool:
    """
    Unregister API process PID from coordination system.
    DRY implementation matching MCP client unregistration logic.
    """
    if not AtomicFileOperations:
        return False
        
    coordination_file = get_coordination_file_path(port)
    current_pid = os.getpid()
    
    def remove_api_pid(data: Dict) -> Dict:
        """Remove API process PID if it matches current process."""
        existing_api_pid = data.get('api_pid')
        if existing_api_pid == current_pid:
            data.pop('api_pid', None)
            
        # Preserve clients
        data['clients'] = data.get('clients', {})
        
        return data
    
    try:
        AtomicFileOperations.atomic_json_update(
            coordination_file, remove_api_pid, {}
        )
        return True
    except Exception:
        return False


def cleanup_coordination_file(port: str = None) -> bool:
    """
    Clean up coordination file if no processes remain.
    DRY implementation for consistent coordination cleanup.
    """
    if not AtomicFileOperations:
        return False
        
    coordination_file = get_coordination_file_path(port)
    
    def check_and_cleanup(data: Dict) -> Dict:
        """Remove file if no active processes remain."""
        api_pid = data.get('api_pid')
        clients = data.get('clients', {})
        
        # Check if API process still exists
        api_exists = api_pid and HAS_PSUTIL and psutil.pid_exists(api_pid)
        
        # Check if any MCP clients still exist  
        active_clients = {}
        for client_id, info in clients.items():
            try:
                pid = info.get('pid')
                if pid and HAS_PSUTIL and psutil.pid_exists(pid):
                    active_clients[client_id] = info
            except:
                pass
                
        # Update data with only active processes
        if api_exists:
            data['api_pid'] = api_pid
        else:
            data.pop('api_pid', None)
            
        data['clients'] = active_clients
        
        return data
    
    try:
        result = AtomicFileOperations.atomic_json_update(
            coordination_file, check_and_cleanup, {}
        )
        
        # Remove file if no active processes
        has_api = result.get('api_pid') is not None
        has_clients = len(result.get('clients', {})) > 0
        
        if not has_api and not has_clients:
            try:
                coordination_file.unlink()
                return True
            except:
                pass
                
        return True
    except Exception:
        return False