"""
Superior Consolidated Test Diagnostics Tool - DRY Design

Integrates best elements from all existing systems:
- DeterministicPortManager (reliability_framework.py) for consistent port allocation
- Process tree tracking (superior approach for child process detection)
- MCP server failure diagnostics (valuable context for API startup failures)  
- Robust process cleanup patterns (terminate-wait-kill)
- Backwards compatible with real system defaults (6969, 6968, 3001)

Easy to use correctly, hard to use incorrectly.
This is the single authoritative diagnostic tool for the test suite.
"""

import hashlib
import os
import psutil
import socket
import subprocess
import time
from typing import Dict, List, Set, Optional, Any
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


def log_mcp_server_failure_context(server_manager, test_name: str = "Unknown") -> str:
    """
    Log comprehensive MCP server failure context for debugging.
    Integrated from resource_leak_detector.py - this function was uniquely valuable.
    """
    report = [f"\n=== MCP SERVER FAILURE CONTEXT: {test_name} (Port {server_manager.port}) ==="]
    
    # Check if port is actually free
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', server_manager.port))
        sock.close()
        
        if result == 0:
            report.append(f"❌ Port {server_manager.port} is OCCUPIED (not free as expected)")
        else:
            report.append(f"✅ Port {server_manager.port} is free")
    except Exception as e:
        report.append(f"⚠️ Port check failed: {e}")
    
    # Check for running API processes
    api_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
            if any(keyword in cmdline for keyword in ['uvicorn', 'src.main', f':{server_manager.port}']):
                api_processes.append(f"PID {proc.info['pid']}: {cmdline[:80]}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    if api_processes:
        report.append(f"❌ Found {len(api_processes)} potentially conflicting API processes:")
        for proc_info in api_processes:
            report.append(f"  {proc_info}")
    else:
        report.append("✅ No conflicting API processes found")
    
    # Check MCP coordination files
    try:
        mcp_files = list(Path("/tmp").glob("*mcp_coordination*"))
        if mcp_files:
            report.append(f"⚠️ Found {len(mcp_files)} MCP coordination files:")
            for f in mcp_files[:5]:  # Limit to first 5
                report.append(f"  {f.name}")
        else:
            report.append("✅ No MCP coordination files in /tmp")
    except Exception as e:
        report.append(f"⚠️ File check failed: {e}")
    
    failure_context = "\n".join(report)
    print(failure_context)
    return failure_context


def robust_process_cleanup(process_list: List[subprocess.Popen], test_name: str, timeout: int = 5) -> Dict[str, Any]:
    """
    Implement robust terminate-wait-kill pattern for process cleanup.
    This prevents the source-level leaks identified in the analysis.
    """
    cleanup_report = {
        "test_name": test_name,
        "processes_cleaned": 0,
        "processes_killed": 0,
        "cleanup_failures": []
    }
    
    for proc in process_list:
        if proc and proc.poll() is None:
            try:
                print(f"[ROBUST CLEANUP] Terminating process {proc.pid} for {test_name}...")
                proc.terminate()
                
                try:
                    # Wait up to timeout seconds for graceful exit
                    proc.wait(timeout=timeout)
                    print(f"[ROBUST CLEANUP] ✅ Process {proc.pid} terminated gracefully")
                    cleanup_report["processes_cleaned"] += 1
                except subprocess.TimeoutExpired:
                    # Force kill if graceful termination fails
                    print(f"[ROBUST CLEANUP] ⚠️ Process {proc.pid} did not exit in time, force killing...")
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                        print(f"[ROBUST CLEANUP] ✅ Process {proc.pid} force-killed successfully")
                        cleanup_report["processes_killed"] += 1
                    except subprocess.TimeoutExpired:
                        print(f"[ROBUST CLEANUP] ❌ Process {proc.pid} could not be killed")
                        cleanup_report["cleanup_failures"].append({
                            "pid": proc.pid,
                            "error": "Could not be killed even with force"
                        })
                        
            except Exception as e:
                print(f"[ROBUST CLEANUP] ❌ Failed to cleanup process {proc.pid}: {e}")
                cleanup_report["cleanup_failures"].append({
                    "pid": proc.pid,
                    "error": str(e)
                })
    
    print(f"[ROBUST CLEANUP] {test_name}: {cleanup_report['processes_cleaned']} graceful, {cleanup_report['processes_killed']} force-killed, {len(cleanup_report['cleanup_failures'])} failures")
    return cleanup_report


def per_test_file_port_detection(test_file_path: str, test_ports: Set[int] = None) -> Dict[str, Any]:
    """
    Enhanced leak detection for end-of-test-file validation.
    Reports exact test file and specific ports still orphaned.
    
    Usage in test file teardown_class:
        @classmethod 
        def teardown_class(cls):
            per_test_file_port_detection(__file__, {6969, 6968, 3001})
    
    Args:
        test_file_path: __file__ of the test file for exact attribution
        test_ports: Set of ports this test file uses
        
    Returns:
        Dict with test file name, orphaned ports, and specific process details
    """
    test_file_name = os.path.basename(test_file_path)
    test_ports = test_ports or {6969, 6968, 3001}  # Default HeadlessPM ports
    
    print(f"\n[FILE PORT DETECTIVE] Checking {test_file_name} for orphaned ports: {test_ports}")
    
    orphaned_details = {
        "test_file": test_file_name,
        "test_file_path": test_file_path,
        "checked_ports": list(test_ports),
        "orphaned_ports": [],
        "clean_ports": [],
        "total_orphans": 0
    }
    
    for port in test_ports:
        try:
            # Check if port is occupied using socket test
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            
            if result == 0:
                # Port is occupied - get process details
                try:
                    result = subprocess.run(
                        ['lsof', '-ti', f':{port}'],
                        capture_output=True, text=True, check=False
                    )
                    
                    if result.returncode == 0 and result.stdout.strip():
                        pids = [int(pid.strip()) for pid in result.stdout.strip().split('\n') if pid.strip().isdigit()]
                        for pid in pids:
                            try:
                                proc = psutil.Process(pid)
                                orphaned_details["orphaned_ports"].append({
                                    "port": port,
                                    "pid": pid,
                                    "name": proc.name(),
                                    "cmdline": ' '.join(proc.cmdline()[:5]),  # First 5 args
                                    "create_time": proc.create_time(),
                                    "status": proc.status()
                                })
                                orphaned_details["total_orphans"] += 1
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                orphaned_details["orphaned_ports"].append({
                                    "port": port,
                                    "pid": pid,
                                    "name": "unknown",
                                    "cmdline": "access denied"
                                })
                                orphaned_details["total_orphans"] += 1
                except (subprocess.SubprocessError, FileNotFoundError):
                    # lsof not available - fallback to socket check only
                    orphaned_details["orphaned_ports"].append({
                        "port": port,
                        "pid": "unknown", 
                        "name": "unknown",
                        "cmdline": "lsof not available - port occupied"
                    })
                    orphaned_details["total_orphans"] += 1
            else:
                # Port is free
                orphaned_details["clean_ports"].append(port)
                
        except Exception as e:
            print(f"[FILE PORT DETECTIVE] Error checking port {port}: {e}")
    
    # Report results
    if orphaned_details["total_orphans"] > 0:
        print(f"[FILE PORT DETECTIVE] ❌ {test_file_name}: {orphaned_details['total_orphans']} orphaned ports detected")
        for port_info in orphaned_details["orphaned_ports"]:
            print(f"  Port {port_info['port']}: PID {port_info['pid']} - {port_info['name']} ({port_info['cmdline']})")
        print(f"[FILE PORT DETECTIVE] Clean ports: {orphaned_details['clean_ports']}")
    else:
        print(f"[FILE PORT DETECTIVE] ✅ {test_file_name}: All test ports clean ({orphaned_details['clean_ports']})")
    
    return orphaned_details