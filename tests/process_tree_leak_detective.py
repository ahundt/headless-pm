"""
Process Tree Leak Detective - Superior approach using process tree tracking.
Tracks child processes spawned from the test process itself - no permission issues.
"""

import os
import psutil
import time
from typing import Dict, List, Set
from pathlib import Path


class ProcessTreeLeakDetective:
    """Detective using process tree tracking - avoids permission issues."""
    
    def __init__(self):
        self.test_pid = os.getpid()
        self.initial_children: Set[int] = set()
        self.spawned_processes: List[Dict] = []
        
    def capture_baseline(self):
        """Capture baseline of child processes at test start."""
        try:
            current_process = psutil.Process(self.test_pid)
            self.initial_children = {child.pid for child in current_process.children(recursive=True)}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.initial_children = set()
    
    def detect_leaks(self, test_name: str) -> Dict:
        """Detect and report process leaks using process tree tracking."""
        print(f"\n[TREE DETECTIVE] Investigating {test_name} (PID: {self.test_pid})")
        
        try:
            current_process = psutil.Process(self.test_pid)
            current_children = {child.pid for child in current_process.children(recursive=True)}
            leaked_pids = current_children - self.initial_children
            
            if not leaked_pids:
                print(f"[TREE DETECTIVE] ✅ {test_name}: No child process leaks detected")
                return {'leaks_found': 0, 'processes_killed': 0, 'leaked_processes': []}
            
            # Get detailed info about leaked processes
            leaked_processes = []
            for pid in leaked_pids:
                try:
                    proc = psutil.Process(pid)
                    leaked_processes.append({
                        'pid': pid,
                        'name': proc.name(),
                        'cmdline': ' '.join(proc.cmdline()),
                        'status': proc.status()
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    leaked_processes.append({
                        'pid': pid,
                        'name': 'unknown',
                        'cmdline': 'access denied',
                        'status': 'unknown'
                    })
            
            print(f"[TREE DETECTIVE] ❌ {test_name}: {len(leaked_processes)} child process leaks detected")
            for proc_info in leaked_processes:
                print(f"  PID {proc_info['pid']}: {proc_info['name']} - {proc_info['cmdline'][:80]}")
            
            # Attempt cleanup
            killed_count = 0
            for proc_info in leaked_processes:
                if self._terminate_process(proc_info['pid']):
                    killed_count += 1
            
            if killed_count > 0:
                time.sleep(1)  # Wait for cleanup
                print(f"[TREE DETECTIVE] SUMMARY {test_name}: Killed {killed_count}/{len(leaked_processes)} leaked child processes")
            
            return {
                'leaks_found': len(leaked_processes),
                'processes_killed': killed_count,
                'leaked_processes': leaked_processes
            }
            
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            print(f"[TREE DETECTIVE] ⚠️ {test_name}: Cannot access test process tree")
            return {'leaks_found': 0, 'processes_killed': 0, 'leaked_processes': []}
    
    def _terminate_process(self, pid: int) -> bool:
        """Terminate a specific process safely."""
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            proc.wait(timeout=3)
            return True
        except (psutil.TimeoutExpired, psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                proc.kill()
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False
        except Exception:
            return False


# Global instance for test coordination
_tree_detective = ProcessTreeLeakDetective()


def setup_process_tree_tracking():
    """Call at the beginning of test class to establish baseline."""
    _tree_detective.capture_baseline()
    print(f"[TREE DETECTIVE] Baseline captured for PID {_tree_detective.test_pid}")


def detect_and_cleanup_process_tree_leaks(test_name: str) -> Dict:
    """
    DRY function for process tree leak detection and cleanup.
    
    Usage in test teardown:
        def tearDown(self):
            detect_and_cleanup_process_tree_leaks("TestClassName.test_method_name")
    
    Args:
        test_name: Name of test for attribution
        
    Returns:
        Cleanup statistics dict
    """
    return _tree_detective.detect_leaks(test_name)


def check_for_orphaned_ports(test_ports: Set[int] = None) -> List[Dict]:
    """
    Check specific test ports for orphaned processes using lsof.
    More reliable than psutil on macOS and doesn't require permissions.
    """
    import subprocess
    
    test_ports = test_ports or {6969, 6968, 3001}  # Default HeadlessPM ports
    orphaned_ports = []
    
    for port in test_ports:
        try:
            # Use lsof to check port - works without special permissions
            result = subprocess.run(
                ['lsof', '-ti', f':{port}'],
                capture_output=True, text=True, check=False
            )
            
            if result.returncode == 0 and result.stdout.strip():
                # Port is in use, get process details
                pids = [int(pid.strip()) for pid in result.stdout.strip().split('\n') if pid.strip().isdigit()]
                for pid in pids:
                    try:
                        proc = psutil.Process(pid)
                        orphaned_ports.append({
                            'port': port,
                            'pid': pid,
                            'name': proc.name(),
                            'cmdline': ' '.join(proc.cmdline())
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        orphaned_ports.append({
                            'port': port,
                            'pid': pid,
                            'name': 'unknown',
                            'cmdline': 'access denied'
                        })
                        
        except (subprocess.SubprocessError, FileNotFoundError):
            # lsof not available or failed
            continue
    
    if orphaned_ports:
        print(f"[PORT DETECTIVE] Found {len(orphaned_ports)} orphaned ports:")
        for port_info in orphaned_ports:
            print(f"  Port {port_info['port']}: PID {port_info['pid']} - {port_info['cmdline'][:80]}")
    
    return orphaned_ports


def comprehensive_leak_detection(test_name: str, test_ports: Set[int] = None) -> Dict:
    """
    Comprehensive leak detection combining process tree and port checking.
    
    Usage:
        def tearDown(self):
            comprehensive_leak_detection("TestClass.test_method", {6969, 8080})
    """
    print(f"\n[COMPREHENSIVE DETECTIVE] Starting investigation for {test_name}")
    
    # Process tree leak detection
    tree_results = detect_and_cleanup_process_tree_leaks(test_name)
    
    # Port-based orphan detection
    orphaned_ports = check_for_orphaned_ports(test_ports)
    
    total_leaks = tree_results['leaks_found'] + len(orphaned_ports)
    
    if total_leaks == 0:
        print(f"[COMPREHENSIVE DETECTIVE] ✅ {test_name}: No leaks detected")
    else:
        print(f"[COMPREHENSIVE DETECTIVE] ❌ {test_name}: {total_leaks} total leaks detected")
        print(f"  - Child process leaks: {tree_results['leaks_found']}")
        print(f"  - Orphaned port processes: {len(orphaned_ports)}")
    
    return {
        'test_name': test_name,
        'total_leaks': total_leaks,
        'child_process_leaks': tree_results['leaks_found'],
        'orphaned_ports': len(orphaned_ports),
        'processes_killed': tree_results['processes_killed'],
        'leaked_processes': tree_results['leaked_processes'],
        'port_processes': orphaned_ports
    }