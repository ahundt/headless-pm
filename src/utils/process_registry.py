"""
Process registry for HeadlessPM multi-process lifecycle management.
Tracks API server and MCP client PIDs to prevent process conflicts and enable proper cleanup.

Concrete purpose: Register/unregister process PIDs in shared registry file for coordination.
Easy to use correctly: Simple register/unregister functions with automatic PID handling.
Hard to use incorrectly: Atomic operations prevent corruption, automatic stale cleanup.
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from src.utils.atomic_file_ops import AtomicFileOperations
except ImportError:
    AtomicFileOperations = None


def get_process_registry_path(service_port: str = None) -> Path:
    """
    Get process registry file path for HeadlessPM processes.
    Uses SERVICE_PORT to create port-specific registry files.
    
    Args:
        service_port: Port number for this HeadlessPM instance
        
    Returns:
        Path to process registry file for this port
    """
    temp_dir = Path(tempfile.gettempdir())
    port = service_port or os.environ.get('SERVICE_PORT', '6969')
    return temp_dir / f"headless_pm_processes_{port}.json"


def register_api_server() -> bool:
    """
    Register this API server process in the registry.
    Easy to use correctly: Call once at API startup.
    Hard to use incorrectly: Atomic operations prevent corruption.
    
    Returns:
        True if registration succeeded, False otherwise
    """
    if not AtomicFileOperations:
        return False  # Skip if atomic operations unavailable
        
    registry_file = get_process_registry_path()
    current_pid = os.getpid()
    
    def register_api_pid(data: Dict) -> Dict:
        """Register API server PID in process registry."""
        # Clean existing stale API PID
        existing_api_pid = data.get('api_server_pid')
        if existing_api_pid and HAS_PSUTIL and not psutil.pid_exists(existing_api_pid):
            data.pop('api_server_pid', None)
            
        # Register this API server
        data['api_server_pid'] = current_pid
        
        # Preserve MCP clients
        data.setdefault('mcp_clients', {})
        
        return data
    
    try:
        AtomicFileOperations.atomic_json_update(registry_file, register_api_pid, {})
        return True
    except Exception:
        return False


def unregister_api_server() -> bool:
    """
    Unregister this API server process from the registry.
    Easy to use correctly: Call once at API shutdown.
    Hard to use incorrectly: Only removes current process PID.
    
    Returns:
        True if unregistration succeeded, False otherwise
    """
    if not AtomicFileOperations:
        return False
        
    registry_file = get_process_registry_path()
    current_pid = os.getpid()
    
    def unregister_api_pid(data: Dict) -> Dict:
        """Remove API server PID from registry if it matches current process."""
        existing_api_pid = data.get('api_server_pid')
        if existing_api_pid == current_pid:
            data.pop('api_server_pid', None)
            
        # Preserve MCP clients
        data.setdefault('mcp_clients', {})
        
        return data
    
    try:
        AtomicFileOperations.atomic_json_update(registry_file, unregister_api_pid, {})
        return True
    except Exception:
        return False


def cleanup_process_registry() -> bool:
    """
    Clean up process registry if no active processes remain.
    Automatically removes stale PIDs and empty registry files.
    
    Returns:
        True if cleanup completed successfully
    """
    if not AtomicFileOperations:
        return False
        
    registry_file = get_process_registry_path()
    
    def cleanup_stale_processes(data: Dict) -> Dict:
        """Remove stale process entries and clean up empty registry."""
        # Check API server
        api_pid = data.get('api_server_pid')
        api_active = api_pid and HAS_PSUTIL and psutil.pid_exists(api_pid)
        
        # Check MCP clients
        clients = data.get('mcp_clients', {})
        active_clients = {}
        for client_id, info in clients.items():
            try:
                pid = info.get('pid')
                if pid and HAS_PSUTIL and psutil.pid_exists(pid):
                    active_clients[client_id] = info
            except:
                pass
                
        # Update registry with only active processes
        cleaned_data = {}
        if api_active:
            cleaned_data['api_server_pid'] = api_pid
        if active_clients:
            cleaned_data['mcp_clients'] = active_clients
            
        return cleaned_data
    
    try:
        result = AtomicFileOperations.atomic_json_update(
            registry_file, cleanup_stale_processes, {}
        )
        
        # Remove registry file if no active processes
        if not result.get('api_server_pid') and not result.get('mcp_clients'):
            try:
                registry_file.unlink()
            except:
                pass
                
        return True
    except Exception:
        return False


def get_registry_status() -> Dict:
    """
    Get current process registry status for debugging.
    Shows active API server and MCP client PIDs.
    
    Returns:
        Dict with registry status information
    """
    registry_file = get_process_registry_path()
    
    try:
        if registry_file.exists():
            import json
            with open(registry_file, 'r') as f:
                data = json.load(f)
                
            return {
                'registry_file': str(registry_file),
                'api_server_pid': data.get('api_server_pid'),
                'mcp_client_count': len(data.get('mcp_clients', {})),
                'mcp_clients': data.get('mcp_clients', {})
            }
        else:
            return {
                'registry_file': str(registry_file),
                'status': 'No registry file exists'
            }
    except Exception as e:
        return {
            'registry_file': str(registry_file),
            'error': str(e)
        }