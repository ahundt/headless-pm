"""
Process registry for HeadlessPM multi-process lifecycle management.
Tracks API server and MCP client PIDs to prevent process conflicts and enable proper cleanup.

Concrete purpose: Register/unregister process PIDs in shared registry file for coordination.
Easy to use correctly: Simple register/unregister functions with automatic PID handling.
Hard to use incorrectly: Atomic operations prevent corruption, automatic stale cleanup.
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
    AtomicFileOperations = None
    with_coordination_lock = None


def check_pid_conflict(data: Dict, pid: int, process_type: str) -> bool:
    """
    Check if PID already registered as different type.
    Prevents same PID from registering as both API server and MCP client.
    
    Args:
        data: Coordination data dictionary
        pid: Process ID to check
        process_type: Type trying to register ('api_server' or 'mcp_client')
        
    Returns:
        True if conflict detected, False if safe to register
    """
    # Check existing processes object (new flat structure)
    for existing_pid_str, info in data.get('processes', {}).items():
        existing_pid = int(existing_pid_str)
        if existing_pid == pid and info.get('type') != process_type:
            return True  # Conflict: PID already registered as different type
    
    # Check legacy api_pid field during migration period
    legacy_api_pid = data.get('api_pid')
    if legacy_api_pid == pid and process_type != 'api_server':
        return True  # Conflict: PID is API but trying to register as client
        
    # Check legacy clients during migration period
    for client_info in data.get('clients', {}).values():
        if client_info.get('pid') == pid and process_type != 'mcp_client':
            return True  # Conflict: PID is client but trying to register as API
    
    return False  # No conflict detected


def get_process_registry_path(service_port: str = None) -> Path:
    """
    Get existing MCP coordination file path - integrate with existing system.
    Uses SAME file as MCP server coordination to prevent duplicate systems.
    
    Args:
        service_port: Port number for this HeadlessPM instance
        
    Returns:
        Path to existing MCP coordination file (integrates with api_pid field)
    """
    temp_dir = Path(tempfile.gettempdir())
    port = service_port or os.environ.get('SERVICE_PORT', '6969')
    return temp_dir / f"headless_pm_mcp_clients_{port}.json"  # Use EXISTING MCP coordination file


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
        """Register API server in flat PID-keyed structure."""
        
        # Check for PID conflicts using new validation
        if check_pid_conflict(data, current_pid, 'api_server'):
            raise ValueError(f"PID {current_pid} already registered as different type")
        
        # Initialize new flat structure if needed
        if 'processes' not in data:
            data['processes'] = {}
            
        # Register in new flat structure (PID as key prevents duplicates)
        data['processes'][str(current_pid)] = {
            'type': 'api_server',
            'started': time.time(),
            'repository': os.getcwd(),
            'last_heartbeat': time.time()
        }
        
        # Set primary API for coordination
        data['primary_api'] = current_pid
        
        # Preserve legacy fields during migration (backward compatibility)
        data.setdefault('clients', {})
        
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
        existing_api_pid = data.get('api_pid')
        if existing_api_pid == current_pid:
            data.pop('api_pid', None)
            
        # Preserve MCP clients
        data.setdefault('clients', {})
        
        return data
    
    def coordinated_api_unregister():
        """Perform API unregistration with coordination lock like MCP server."""
        AtomicFileOperations.atomic_json_update(registry_file, unregister_api_pid, {})
        return True
    
    try:
        # Use coordination lock matching MCP server pattern (line 817-820 in mcp/server.py)
        if with_coordination_lock:
            return with_coordination_lock(
                f"api_shutdown_{port}",
                coordinated_api_unregister,
                timeout=10,
                description="API server unregistration"
            )
        else:
            # Fallback without locks if unavailable
            AtomicFileOperations.atomic_json_update(registry_file, unregister_api_pid, {})
            return True
    except Exception:
        return False


def migrate_legacy_structure(data: Dict) -> Dict:
    """
    Migrate old coordination format to new flat structure.
    Handles backward compatibility and resolves same-PID conflicts.
    
    Args:
        data: Legacy coordination data with api_pid + clients structure
        
    Returns:
        Migrated data with flat processes structure
    """
    if 'processes' in data and not data.get('api_pid') and not data.get('clients'):
        return data  # Already fully migrated
        
    migrated_data = {'processes': data.get('processes', {})}
    
    # Migrate legacy api_pid (takes priority in conflicts)
    legacy_api_pid = data.get('api_pid')
    if legacy_api_pid and HAS_PSUTIL and psutil.pid_exists(legacy_api_pid):
        migrated_data['processes'][str(legacy_api_pid)] = {
            'type': 'api_server',
            'started': time.time(),
            'repository': os.getcwd(),
            'last_heartbeat': time.time()
        }
        migrated_data['primary_api'] = legacy_api_pid
    
    # Migrate legacy clients (skip conflicts with API)
    for client_id, client_info in data.get('clients', {}).items():
        pid = client_info.get('pid')
        if (pid and 
            HAS_PSUTIL and psutil.pid_exists(pid) and 
            str(pid) not in migrated_data['processes']):  # No conflict
            migrated_data['processes'][str(pid)] = {
                'type': 'mcp_client',
                'started': client_info.get('timestamp', time.time()),
                'client_id': client_id,
                'last_heartbeat': time.time()
            }
    
    # Preserve other fields
    for key, value in data.items():
        if key not in ['api_pid', 'clients', 'processes', 'primary_api']:
            migrated_data[key] = value
    
    return migrated_data


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
        api_pid = data.get('api_pid')
        api_active = api_pid and HAS_PSUTIL and psutil.pid_exists(api_pid)
        
        # Check MCP clients
        clients = data.get('clients', {})
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
            cleaned_data['api_pid'] = api_pid
        if active_clients:
            cleaned_data['clients'] = active_clients
            
        return cleaned_data
    
    try:
        result = AtomicFileOperations.atomic_json_update(
            registry_file, cleanup_stale_processes, {}
        )
        
        # Remove registry file if no active processes
        if not result.get('api_pid') and not result.get('clients'):
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
                'api_pid': data.get('api_pid'),
                'mcp_client_count': len(data.get('clients', {})),
                'clients': data.get('clients', {})
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