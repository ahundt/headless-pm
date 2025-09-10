"""
Comprehensive Reliability Framework for Intermittent Test Resolution
Implements tenured CS faculty best practices for distributed systems testing
"""

import hashlib
import time
import psutil
import asyncio
import subprocess
import socket
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


class ProcessState(Enum):
    """Formal state machine for process lifecycle management."""
    INIT = "INIT"
    STARTING = "STARTING" 
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass
class ProcessInfo:
    """Comprehensive process tracking information."""
    pid: int
    port: int
    state: ProcessState
    start_time: float
    command: List[str]
    expected_lifecycle: float = 30.0  # Expected max lifecycle seconds
    
    
@dataclass 
class ResourceTracker:
    """Track all test resources for guaranteed cleanup."""
    processes: Dict[int, ProcessInfo] = field(default_factory=dict)
    ports: Set[int] = field(default_factory=set) 
    files: Set[Path] = field(default_factory=set)
    cleanup_verified: bool = False
    
    def register_process(self, proc: subprocess.Popen, port: int, command: List[str]) -> ProcessInfo:
        """Register process with complete tracking information."""
        info = ProcessInfo(
            pid=proc.pid,
            port=port, 
            state=ProcessState.STARTING,
            start_time=time.time(),
            command=command
        )
        self.processes[proc.pid] = info
        self.ports.add(port)
        return info
    
    async def verify_cleanup(self) -> bool:
        """Comprehensive resource cleanup verification."""
        # Check all processes terminated
        for pid, info in self.processes.items():
            if psutil.pid_exists(pid):
                logger.error(f"Process {pid} still exists after cleanup")
                return False
                
        # Check all ports released
        for port in self.ports:
            if await self._is_port_in_use(port):
                logger.error(f"Port {port} still in use after cleanup") 
                return False
                
        # Check all files cleaned up
        for file_path in self.files:
            if file_path.exists():
                logger.error(f"File {file_path} still exists after cleanup")
                return False
                
        self.cleanup_verified = True
        return True
        
    async def _is_port_in_use(self, port: int) -> bool:
        """Check if port is currently bound."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0
        except Exception:
            return False


class DeterministicPortManager:
    """Cryptographically deterministic port allocation for test isolation."""
    
    @staticmethod
    def allocate_port(test_identifier: str, base_port: int = 10000, 
                     port_range: int = 50000, legacy_compatible: bool = False) -> int:
        """
        Allocate deterministic port using cryptographic hash or legacy hash for compatibility.
        
        Args:
            test_identifier: Unique test identifier (class::method)
            base_port: Starting port number
            port_range: Range of available ports
            legacy_compatible: Use legacy hash() for backwards compatibility with existing tests
            
        Returns:
            Deterministic port number for this test
        """
        if legacy_compatible:
            # Use original hash() function for backwards compatibility with existing test logic
            hash_value = abs(hash(test_identifier)) % port_range
        else:
            # Use cryptographic hash for better distribution
            hash_value = int(hashlib.sha256(test_identifier.encode()).hexdigest()[:8], 16) % port_range
            
        port = base_port + hash_value
        
        # Avoid system reserved ports
        if port < 1024:
            port += 1024
        if port > 65535:
            port = 65535 - (port - 65535)
            
        return port
    
    @staticmethod
    def verify_port_determinism(test_identifier: str, expected_port: int) -> bool:
        """Verify port allocation is deterministic."""
        actual_port = DeterministicPortManager.allocate_port(test_identifier)
        return actual_port == expected_port


class ProcessLifecycleManager:
    """Formal state machine for reliable process lifecycle management."""
    
    def __init__(self, resource_tracker: ResourceTracker):
        self.tracker = resource_tracker
        self.state_transitions = {
            (ProcessState.INIT, ProcessState.STARTING): self._transition_start,
            (ProcessState.STARTING, ProcessState.RUNNING): self._transition_confirm_ready,
            (ProcessState.RUNNING, ProcessState.STOPPING): self._transition_stop,
            (ProcessState.STOPPING, ProcessState.STOPPED): self._transition_wait_exit,
            (ProcessState.STARTING, ProcessState.FAILED): self._transition_failed,
            (ProcessState.RUNNING, ProcessState.FAILED): self._transition_failed
        }
    
    async def start_process(self, command: List[str], port: int, 
                          timeout: float = 30.0) -> Optional[ProcessInfo]:
        """Start process with formal state machine and timeout protection."""
        try:
            # State: INIT -> STARTING
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                text=True
            )
            
            info = self.tracker.register_process(proc, port, command)
            await self._transition_state(info, ProcessState.STARTING)
            
            # State: STARTING -> RUNNING (with timeout)
            if await self._wait_for_ready(info, timeout):
                await self._transition_state(info, ProcessState.RUNNING)
                return info
            else:
                await self._transition_state(info, ProcessState.FAILED)
                return None
                
        except Exception as e:
            logger.error(f"Process start failed: {e}")
            return None
    
    async def stop_process(self, info: ProcessInfo, timeout: float = 10.0) -> bool:
        """Stop process with graceful degradation and timeout."""
        if info.state not in [ProcessState.RUNNING, ProcessState.STARTING]:
            return True
            
        try:
            # State: RUNNING -> STOPPING
            await self._transition_state(info, ProcessState.STOPPING)
            
            proc = psutil.Process(info.pid)
            
            # Graceful termination attempt
            proc.terminate()
            
            # Wait for graceful exit
            try:
                proc.wait(timeout=timeout)
                await self._transition_state(info, ProcessState.STOPPED)
                return True
            except psutil.TimeoutExpired:
                # Force kill if graceful failed
                proc.kill()
                proc.wait(timeout=5)
                await self._transition_state(info, ProcessState.STOPPED) 
                return True
                
        except psutil.NoSuchProcess:
            await self._transition_state(info, ProcessState.STOPPED)
            return True
        except Exception as e:
            logger.error(f"Process stop failed: {e}")
            await self._transition_state(info, ProcessState.FAILED)
            return False
    
    async def _wait_for_ready(self, info: ProcessInfo, timeout: float) -> bool:
        """Wait for process to become ready with exponential backoff."""
        start_time = time.time()
        backoff = 0.1
        max_backoff = 2.0
        
        while time.time() - start_time < timeout:
            # Check if process died
            if not psutil.pid_exists(info.pid):
                return False
                
            # Check if port is bound
            if await self._check_port_ready(info.port):
                return True
                
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)  # Exponential backoff
            
        return False
    
    async def _check_port_ready(self, port: int) -> bool:
        """Check if process is listening on port."""
        try:
            # Try to connect to health endpoint
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"http://localhost:{port}/health")
                return response.status_code == 200
        except Exception:
            return False
    
    async def _transition_state(self, info: ProcessInfo, new_state: ProcessState):
        """Execute formal state transition with logging."""
        old_state = info.state
        transition = (old_state, new_state)
        
        if transition in self.state_transitions:
            info.state = new_state
            logger.debug(f"Process {info.pid}: {old_state} -> {new_state}")
        else:
            logger.error(f"Invalid transition: {old_state} -> {new_state}")
            info.state = ProcessState.FAILED
    
    async def _transition_start(self, info: ProcessInfo):
        """Handle start transition logic."""
        pass
        
    async def _transition_confirm_ready(self, info: ProcessInfo):
        """Handle ready confirmation transition."""
        pass
        
    async def _transition_stop(self, info: ProcessInfo):
        """Handle stop initiation transition.""" 
        pass
        
    async def _transition_wait_exit(self, info: ProcessInfo):
        """Handle exit waiting transition."""
        pass
        
    async def _transition_failed(self, info: ProcessInfo):
        """Handle failure transition."""
        logger.error(f"Process {info.pid} transitioned to FAILED state")


class StatisticalTestValidator:
    """Rigorous statistical validation for test reliability."""
    
    def __init__(self, min_sample_size: int = 100):
        self.min_sample_size = min_sample_size
        self.results: List[Tuple[bool, str]] = []
    
    async def run_statistical_validation(self, test_func, iterations: int = None) -> Dict:
        """Run test multiple times for statistical significance."""
        iterations = iterations or self.min_sample_size
        
        successes = 0
        failures = 0
        failure_patterns = {}
        
        for i in range(iterations):
            try:
                await test_func()
                successes += 1
                self.results.append((True, ""))
            except Exception as e:
                failures += 1
                error_type = type(e).__name__
                failure_patterns[error_type] = failure_patterns.get(error_type, 0) + 1
                self.results.append((False, str(e)))
                
        return {
            "success_rate": successes / iterations,
            "total_runs": iterations,
            "successes": successes, 
            "failures": failures,
            "failure_patterns": failure_patterns,
            "is_statistically_significant": iterations >= self.min_sample_size,
            "confidence_interval": self._calculate_confidence_interval(successes, iterations)
        }
    
    def _calculate_confidence_interval(self, successes: int, total: int, 
                                     confidence: float = 0.95) -> Tuple[float, float]:
        """Calculate confidence interval for success rate."""
        import math
        p = successes / total
        z = 1.96  # 95% confidence
        margin = z * math.sqrt(p * (1-p) / total)
        return (max(0, p - margin), min(1, p + margin))


class ReliabilityTestFramework:
    """Master framework combining all reliability components."""
    
    def __init__(self, test_identifier: str):
        self.test_id = test_identifier
        self.port = DeterministicPortManager.allocate_port(test_identifier)
        self.resource_tracker = ResourceTracker()
        self.process_manager = ProcessLifecycleManager(self.resource_tracker)
        self.validator = StatisticalTestValidator()
    
    async def __aenter__(self):
        """Async context manager entry with pre-flight checks."""
        # Verify deterministic port allocation
        assert DeterministicPortManager.verify_port_determinism(self.test_id, self.port)
        
        # Verify port is free
        assert not await self.resource_tracker._is_port_in_use(self.port)
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Guaranteed cleanup with verification."""
        # Stop all processes
        for pid, info in self.resource_tracker.processes.items():
            await self.process_manager.stop_process(info)
        
        # Verify cleanup completed
        cleanup_success = await self.resource_tracker.verify_cleanup()
        if not cleanup_success:
            logger.error(f"Cleanup verification failed for {self.test_id}")
            
        return False  # Don't suppress exceptions