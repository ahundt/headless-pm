"""
Process Leak Detective - DRY function for systematic leak detection and attribution.
Call this at the end of every test to detect and eliminate process leaks.
"""

import psutil
import os
import signal
import time
from typing import List, Dict, Set


class ProcessLeakDetective:
    """Detective for finding and eliminating process leaks with attribution."""
    
    @staticmethod
    def cleanup_and_report(test_name: str, expected_ports: Set[int] = None) -> Dict:
        """
        DRY function to detect, report, and clean up process leaks.
        
        Args:
            test_name: Name of the test for leak attribution
            expected_ports: Ports this test should have cleaned up
            
        Returns:
            Dict with cleanup statistics and leak attribution
        """
        expected_ports = expected_ports or set()
        
        print(f"\n[LEAK DETECTIVE] Investigating {test_name}")
        
        # Capture initial state
        initial_processes = ProcessLeakDetective._find_test_processes()
        initial_ports = ProcessLeakDetective._find_test_ports()
        
        if not initial_processes and not initial_ports:
            print(f"[LEAK DETECTIVE] ✅ {test_name}: No leaks detected")
            return {'leaks_found': 0, 'processes_killed': 0, 'ports_freed': 0}
        
        # Report findings
        leak_report = {
            'test_name': test_name,
            'leaks_found': len(initial_processes) + len(initial_ports),
            'processes_killed': 0,
            'ports_freed': 0,
            'leaked_processes': [],
            'leaked_ports': []
        }
        
        print(f"[LEAK DETECTIVE] ❌ {test_name}: {leak_report['leaks_found']} leaks detected")
        
        # Report process leaks
        if initial_processes:
            print(f"[LEAK DETECTIVE] Process leaks from {test_name}:")
            for proc_info in initial_processes:
                print(f"  PID {proc_info['pid']}: {proc_info['cmdline'][:80]}")
                leak_report['leaked_processes'].append(proc_info)
        
        # Report port leaks  
        if initial_ports:
            print(f"[LEAK DETECTIVE] Port leaks from {test_name}:")
            for port_info in initial_ports:
                print(f"  Port {port_info['port']}: {port_info['status']} (PID {port_info['pid']})")
                leak_report['leaked_ports'].append(port_info)
        
        # Clean up leaked processes
        for proc_info in initial_processes:
            if ProcessLeakDetective._terminate_process(proc_info['pid']):
                leak_report['processes_killed'] += 1
        
        # Wait for port cleanup
        if leak_report['processes_killed'] > 0:
            time.sleep(2)
            
        # Verify cleanup
        remaining_processes = ProcessLeakDetective._find_test_processes()
        remaining_ports = ProcessLeakDetective._find_test_ports()
        
        leak_report['ports_freed'] = len(initial_ports) - len(remaining_ports)
        
        if remaining_processes or remaining_ports:
            print(f"[LEAK DETECTIVE] ⚠️ {test_name}: {len(remaining_processes)} processes and {len(remaining_ports)} ports still leaked after cleanup")
        else:
            print(f"[LEAK DETECTIVE] ✅ {test_name}: All leaks cleaned up")
        
        # Final summary
        print(f"[LEAK DETECTIVE] SUMMARY {test_name}: Killed {leak_report['processes_killed']} processes, freed {leak_report['ports_freed']} ports")
        
        return leak_report
    
    @staticmethod
    def _find_test_processes() -> List[Dict]:
        """Find processes related to our tests."""
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                name = proc.info['name'] or ''
                
                # Look for test-related processes (but not our own test runner)
                if any(keyword in cmdline.lower() or keyword in name.lower() for keyword in [
                    'uvicorn', 'src.main', 'src.mcp.server', 'headless-pm'
                ]) and proc.info['pid'] != os.getpid():
                    # Exclude pytest processes themselves
                    if 'pytest' not in cmdline:
                        processes.append({
                            'pid': proc.info['pid'],
                            'name': name,
                            'cmdline': cmdline
                        })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return processes
    
    @staticmethod
    def _find_test_ports() -> List[Dict]:
        """Find ports bound by test processes in test range."""
        ports = []
        try:
            for conn in psutil.net_connections():
                try:
                    # Focus on test port ranges
                    if (conn.laddr and 
                        (6000 <= conn.laddr.port <= 10000 or 
                         conn.laddr.port == 6969)):  # Include default port
                        ports.append({
                            'port': conn.laddr.port,
                            'status': conn.status,
                            'pid': conn.pid
                        })
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
        except (psutil.AccessDenied, PermissionError):
            print("[LEAK DETECTIVE] ⚠️ Permission denied for port scanning - skipping port detection")
        return ports
    
    @staticmethod
    def _terminate_process(pid: int) -> bool:
        """Terminate a specific process safely."""
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            proc.wait(timeout=3)
            return True
        except (psutil.TimeoutExpired, psutil.NoSuchProcess, psutil.AccessDenied):
            try:
                proc = psutil.Process(pid)  # Reinitialize proc for kill attempt
                proc.kill()
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False
        except Exception:
            return False


def detect_and_cleanup_leaks(test_name: str, expected_ports: Set[int] = None) -> Dict:
    """
    DRY function to call at the end of every test that starts processes.
    
    Usage:
        def test_my_api_test(self):
            # Test code that starts API servers
            pass
            
        def tearDown(self):
            detect_and_cleanup_leaks("test_my_api_test", {8080})
    
    Args:
        test_name: Name of test for attribution
        expected_ports: Ports this test was supposed to clean up
        
    Returns:
        Cleanup statistics dict
    """
    return ProcessLeakDetective.cleanup_and_report(test_name, expected_ports)


# Global leak tracking for cross-test analysis
GLOBAL_LEAK_TRACKER = {
    'total_leaks': 0,
    'leaks_by_test': {},
    'most_leaky_tests': []
}

def track_leak_statistics(cleanup_result: Dict):
    """Track leak statistics globally for identifying worst offenders."""
    test_name = cleanup_result.get('test_name')
    leaks_found = cleanup_result.get('leaks_found', 0)
    
    if leaks_found > 0:
        GLOBAL_LEAK_TRACKER['total_leaks'] += leaks_found
        GLOBAL_LEAK_TRACKER['leaks_by_test'][test_name] = leaks_found
        
        # Update most leaky tests ranking
        sorted_tests = sorted(
            GLOBAL_LEAK_TRACKER['leaks_by_test'].items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        GLOBAL_LEAK_TRACKER['most_leaky_tests'] = sorted_tests[:5]


def print_global_leak_summary():
    """Print summary of all test leaks for final analysis."""
    print(f"\n=== GLOBAL LEAK SUMMARY ===")
    print(f"Total leaks detected: {GLOBAL_LEAK_TRACKER['total_leaks']}")
    
    if GLOBAL_LEAK_TRACKER['most_leaky_tests']:
        print(f"Most leaky tests:")
        for test_name, leak_count in GLOBAL_LEAK_TRACKER['most_leaky_tests']:
            print(f"  {test_name}: {leak_count} leaks")
    else:
        print("✅ No leaks detected across all tests")