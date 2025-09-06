"""
Concrete resource leak detection to identify test interaction contamination.
Captures specific leaked resources preventing MCP server API startup.
"""

import psutil
import os
import socket
from pathlib import Path
from typing import Dict, List, Set, Tuple


def capture_system_state() -> Dict:
    """Capture concrete system state for leak detection."""
    state = {
        'processes': [],
        'ports': [],
        'files': [],
        'timestamp': os.times().elapsed
    }
    
    # Capture processes containing headless-pm, mcp, or uvicorn
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
            name = proc.info['name'] or ''
            
            if any(keyword in cmdline.lower() or keyword in name.lower() for keyword in [
                'headless-pm', 'mcp', 'uvicorn', 'src.main', 'src.mcp', 'python3.*src'
            ]):
                state['processes'].append({
                    'pid': proc.info['pid'],
                    'name': name,
                    'cmdline': cmdline[:100]  # Truncate for readability
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # Capture bound ports in test range
    for conn in psutil.net_connections():
        try:
            if conn.laddr and 6000 <= conn.laddr.port <= 10000:
                state['ports'].append({
                    'port': conn.laddr.port,
                    'status': conn.status,
                    'pid': conn.pid
                })
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    
    # Capture coordination files
    temp_dir = Path("/tmp")
    for pattern in ["headless_pm_*.json", "*.lock", "*mcp*.json"]:
        for file_path in temp_dir.glob(pattern):
            try:
                stat = file_path.stat()
                state['files'].append({
                    'path': str(file_path),
                    'size': stat.st_size,
                    'mtime': stat.st_mtime
                })
            except (OSError, FileNotFoundError):
                pass
    
    return state


def compare_states(before: Dict, after: Dict) -> Dict:
    """Compare system states to identify resource leaks."""
    leaks = {
        'new_processes': [],
        'new_ports': [],
        'new_files': []
    }
    
    # Find new processes
    before_pids = {p['pid'] for p in before['processes']}
    for proc in after['processes']:
        if proc['pid'] not in before_pids:
            leaks['new_processes'].append(proc)
    
    # Find new ports  
    before_ports = {p['port'] for p in before['ports']}
    for port in after['ports']:
        if port['port'] not in before_ports:
            leaks['new_ports'].append(port)
    
    # Find new files
    before_files = {f['path'] for f in before['files']}
    for file_info in after['files']:
        if file_info['path'] not in before_files:
            leaks['new_files'].append(file_info)
    
    return leaks


def audit_test_contamination(test_name: str, before_state: Dict, after_state: Dict) -> str:
    """Generate concrete audit report of test contamination."""
    leaks = compare_states(before_state, after_state)
    
    report = [f"\n=== RESOURCE LEAK AUDIT: {test_name} ==="]
    
    total_leaks = len(leaks['new_processes']) + len(leaks['new_ports']) + len(leaks['new_files'])
    
    if total_leaks == 0:
        report.append("✅ NO RESOURCE LEAKS DETECTED")
        return '\n'.join(report)
    
    report.append(f"❌ {total_leaks} RESOURCE LEAKS DETECTED")
    
    if leaks['new_processes']:
        report.append(f"\nLEAKED PROCESSES ({len(leaks['new_processes'])}):")
        for proc in leaks['new_processes']:
            report.append(f"  PID {proc['pid']}: {proc['name']} - {proc['cmdline']}")
    
    if leaks['new_ports']:
        report.append(f"\nLEAKED PORTS ({len(leaks['new_ports'])}):")
        for port in leaks['new_ports']:
            report.append(f"  Port {port['port']}: {port['status']} (PID {port['pid']})")
    
    if leaks['new_files']:
        report.append(f"\nLEAKED FILES ({len(leaks['new_files'])}):")
        for file_info in leaks['new_files']:
            report.append(f"  {file_info['path']}: {file_info['size']} bytes")
    
    report.append("=== END AUDIT ===")
    return '\n'.join(report)


def log_mcp_server_failure_context(server_manager) -> str:
    """Log specific context when MCP server fails to start API."""
    report = [f"\n=== MCP SERVER FAILURE CONTEXT (Port {server_manager.port}) ==="]
    
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
    
    # Check coordination files
    coordination_files = list(Path("/tmp").glob("headless_pm_*.json"))
    if coordination_files:
        report.append(f"❌ Found {len(coordination_files)} coordination files:")
        for file_path in coordination_files:
            report.append(f"  {file_path}")
    else:
        report.append("✅ No coordination files found")
    
    report.append("=== END CONTEXT ===")
    return '\n'.join(report)