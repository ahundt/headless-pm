"""
Atomic file operations utility for safe concurrent file updates.
Uses standard library only - no external dependencies.
"""
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar('T')


class AtomicFileOperations:
    """Provides atomic file operations using tempfile + rename pattern."""
    
    @staticmethod
    def atomic_json_update(file_path: Path, update_func: Callable[[Dict], Dict], 
                          default_data: Optional[Dict] = None) -> Dict:
        """
        Atomically update a JSON file using tempfile + rename.
        
        Args:
            file_path: Path to the JSON file
            update_func: Function that takes current data and returns new data
            default_data: Default data if file doesn't exist
            
        Returns:
            The updated data that was written
            
        Example:
            def add_client(data):
                data.setdefault('clients', []).append({'id': 'new_client'})
                return data
                
            result = AtomicFileOperations.atomic_json_update(
                Path('/tmp/coordination.json'), add_client, {'clients': []}
            )
        """
        file_path = Path(file_path)
        
        # Read current data safely
        current_data = AtomicFileOperations._read_json_safe(file_path, default_data or {})
        
        # Apply update function
        updated_data = update_func(current_data.copy())
        
        # Write atomically using tempfile + rename
        return AtomicFileOperations._write_json_atomic(file_path, updated_data)
    
    @staticmethod
    def _read_json_safe(file_path: Path, default: Dict) -> Dict:
        """Safely read JSON file with fallback to default."""
        try:
            if file_path.exists():
                with open(file_path, 'r') as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return default
    
    @staticmethod
    def _write_json_atomic(file_path: Path, data: Dict) -> Dict:
        """Atomically write JSON data using tempfile + rename."""
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create temporary file in same directory for atomic rename
        with tempfile.NamedTemporaryFile(
            mode='w', 
            delete=False,
            dir=file_path.parent,
            suffix='.tmp',
            prefix=f"{file_path.name}."
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())  # Ensure data is written to disk
            tmp_path = tmp.name
        
        # Atomic rename - this is the key operation that prevents races
        try:
            os.rename(tmp_path, file_path)
            return data
        except OSError:
            # Clean up temp file if rename failed
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


class ProcessCoordinationLock:
    """
    Simple coordination lock for process lifecycle management.
    Uses atomic file operations to prevent race conditions.
    """
    
    def __init__(self, lock_name: str, base_dir: Optional[Path] = None):
        """
        Initialize coordination lock.
        
        Args:
            lock_name: Unique name for this lock (e.g., 'api_exit_6969')
            base_dir: Directory for lock files (default: /tmp)
        """
        self.base_dir = Path(base_dir or "/tmp")
        self.lock_file = self.base_dir / f"{lock_name}.lock"
        self.acquired = False
    
    def acquire(self, timeout: int = 10, client_id: str = None) -> bool:
        """
        Acquire exclusive lock with timeout.
        
        Args:
            timeout: Maximum seconds to wait for lock
            client_id: Identifier for debugging
            
        Returns:
            True if lock acquired, False if timeout
        """
        client_info = f"{client_id}:{os.getpid()}:{int(time.time())}" if client_id else f"{os.getpid()}:{int(time.time())}"
        
        for _ in range(timeout * 10):  # 0.1 second intervals
            try:
                # Atomic create with exclusive flag - this prevents races
                with open(self.lock_file, 'x') as f:  # 'x' = exclusive creation
                    f.write(client_info)
                    f.flush()
                    os.fsync(f.fileno())
                    
                self.acquired = True
                return True
                
            except FileExistsError:
                # Lock exists, check if it's stale
                if self._is_stale_lock():
                    self._cleanup_stale_lock()
                    continue  # Try again
                    
                time.sleep(0.1)
                continue
                
        return False
    
    def release(self):
        """Release the lock."""
        if self.acquired:
            try:
                os.unlink(self.lock_file)
                self.acquired = False
            except FileNotFoundError:
                pass  # Already released
    
    def _is_stale_lock(self, max_age: int = 60) -> bool:
        """Check if existing lock file is stale (older than max_age seconds)."""
        try:
            stat = self.lock_file.stat()
            return time.time() - stat.st_mtime > max_age
        except OSError:
            return True  # If we can't stat it, consider it stale
    
    def _cleanup_stale_lock(self):
        """Remove stale lock file."""
        try:
            os.unlink(self.lock_file)
        except OSError:
            pass
    
    def __enter__(self):
        """Context manager entry."""
        return self.acquire()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()


# Convenience function for common use case
def with_coordination_lock(lock_name: str, operation: Callable[[], T], 
                          timeout: int = 10, client_id: str = None) -> Optional[T]:
    """
    Execute operation with coordination lock protection.
    
    Args:
        lock_name: Unique lock identifier
        operation: Function to execute while holding lock
        timeout: Lock acquisition timeout
        client_id: Client identifier for debugging
        
    Returns:
        Result of operation, or None if lock acquisition failed
        
    Example:
        def exit_cleanup():
            # Remove client from coordination file
            # Check if API should be terminated
            return True
            
        success = with_coordination_lock('api_exit_6969', exit_cleanup, client_id='mcp_123')
    """
    lock = ProcessCoordinationLock(lock_name)
    
    if lock.acquire(timeout, client_id):
        try:
            return operation()
        finally:
            lock.release()
    else:
        return None  # Lock acquisition failed