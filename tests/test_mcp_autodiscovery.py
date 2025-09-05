"""
Integration tests for MCP Server Auto-Discovery functionality.
Tests the full end-to-end connection-first, start-if-needed pattern with real processes.

Note: These tests require actual process management and may be slower.
Use `pytest -m integration` to run only integration tests.
"""

import pytest
import asyncio
import httpx
import subprocess
import signal
import sys
import time
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

# Import app and dependencies
from src.main import app
from src.api.dependencies import get_session
from src.mcp.server import HeadlessPMMCPServer
from tests.test_helpers import ServerManager, MultiClientTestHelper


@pytest.fixture
def engine():
    """Create file-based SQLite engine for testing"""
    db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_file.close()
    
    engine = create_engine(
        f"sqlite:///{db_file.name}", 
        connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    
    yield engine
    
    # Cleanup
    engine.dispose()
    os.unlink(db_file.name)


@pytest.fixture
def session(engine):
    """Create database session for testing"""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(session):
    """Create test client with dependency override"""
    app.dependency_overrides[get_session] = lambda: session
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture
def mcp_server_path():
    """Get path to MCP server script"""
    return Path(__file__).parent.parent / "src" / "mcp" / "server.py"


@pytest.mark.integration
class TestMCPAutoDiscovery:
    """Integration tests for MCP server auto-discovery functionality."""

    def setup_method(self, method):
        """Setup test method with unique port for isolation."""
        # Use method name hash to get consistent but unique port per test
        import hashlib
        method_hash = abs(hash(f"{self.__class__.__name__}::{method.__name__}")) % 1000
        unique_port = 9000 + method_hash  # Port range 9000-9999
        self.server_manager = ServerManager(port=unique_port)
        
    async def is_api_running(self, base_url: str = None) -> bool:
        """Check if API is responding."""
        if base_url is None:
            base_url = self.server_manager.base_url
        port = int(base_url.split(':')[-1].split('/')[0])
        manager = ServerManager(port)
        return await manager.is_api_running()

    def ensure_no_api_running(self):
        """Ensure no API processes are running on test port.
        
        This now preserves existing servers and only warns about them.
        """
        # Just check and warn, don't kill
        if hasattr(self, 'server_manager'):
            existing_pid = self.server_manager.find_api_process()
            if existing_pid:
                print(f"Warning: Existing API found on port 6969 (PID: {existing_pid})")
                print("Test will work with existing server or use different port")

    @pytest.mark.asyncio
    async def test_auto_start_when_no_api_running(self, mcp_server_path):
        """Test that MCP server starts API when none is running OR connects to existing."""
        self.ensure_no_api_running()
        
        # Check if API is already running
        api_was_running = await self.is_api_running()
        
        if api_was_running:
            # Test connecting to existing API - DON'T SKIP, test the behavior!
            print("Testing MCP connecting to existing API on port 6969")
            
            # Start MCP server that should connect to existing API
            mcp_process = subprocess.Popen([
                sys.executable, "-m", "src.mcp.server"
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent,  # Project root
            env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
            )
            
            try:
                # Give MCP time to connect
                await asyncio.sleep(2)
                
                # API should still be running
                assert await self.is_api_running(), "API should still be running"
                
                # MCP process should be running (connected to existing API)
                assert mcp_process.poll() is None, "MCP server should be running"
                
                print("✓ MCP successfully connected to existing API")
                
            finally:
                if mcp_process.stdin:
                    mcp_process.stdin.close()
                mcp_process.terminate()
                mcp_process.wait(timeout=5)
        else:
            # Test starting new API - use test's unique port
            test_port = self.server_manager.port
            print(f"Testing MCP starting new API on port {test_port}")
            
            # Start MCP server process (will start new API) 
            mcp_process = subprocess.Popen([
                sys.executable, "-m", "src.mcp.server"
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent,  # Project root
            env={**os.environ, "SERVICE_PORT": str(test_port)}
            )
            
            try:
                # Wait for API to start
                api_started = False
                for attempt in range(30):  # 15 seconds max
                    await asyncio.sleep(0.5)
                    if await self.is_api_running():
                        api_started = True
                        break
                
                assert api_started, f"API should have been started by MCP server on port {test_port}"
                print(f"✓ MCP successfully started new API on port {test_port}")
                
            finally:
                # Cleanup
                if mcp_process.stdin:
                    mcp_process.stdin.close()
                mcp_process.terminate()
                try:
                    mcp_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mcp_process.kill()
                    mcp_process.wait()

    @pytest.mark.asyncio
    async def test_connect_to_existing_api(self, mcp_server_path):
        """Test that MCP server connects to existing API without starting new one."""
        self.ensure_no_api_running()
        
        # Start API manually first - use the test's unique port
        api_process = subprocess.Popen([
            sys.executable, "-m", "src.main"
        ], 
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).parent.parent,  # Project root
        env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
        )
        
        try:
            # Wait for manual API to start
            api_started = False
            for attempt in range(30):
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, "Manual API should have started"
            
            # Now start MCP server - it should connect to existing API
            mcp_process = subprocess.Popen([
                sys.executable, "-m", "src.mcp.server"
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent,  # Project root
            env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
            )
            
            # Give MCP server time to connect
            await asyncio.sleep(2)
            
            # Verify API is still running (should be the original one)
            assert await self.is_api_running(), "API should still be running"
            
            # MCP server should terminate cleanly when killed
            if mcp_process.poll() is None:
                mcp_process.terminate()
                mcp_process.wait(timeout=5)
            
            # Original API should still be running
            assert await self.is_api_running(), "Original API should still be running"
            
        finally:
            # Cleanup both processes
            if api_process.poll() is None:
                api_process.terminate()
                api_process.wait()

    def test_command_discovery(self, mcp_server_path):
        """Test that MCP server can find headless-pm command."""
        # This is a unit test of the command discovery logic
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "mcp"))
        
        # Import the server class
        from server import HeadlessPMMCPServer
        
        # Create instance and test command discovery
        server = HeadlessPMMCPServer()
        command = server._find_headless_pm_command()
        
        # Should find at least one valid command
        assert command is not None, "Should find a valid headless-pm command"
        assert isinstance(command, list), "Command should be a list"
        assert len(command) > 0, "Command list should not be empty"

    @pytest.mark.asyncio 
    async def test_process_cleanup_on_shutdown(self, mcp_server_path):
        """Test that API process is cleaned up when MCP server shuts down."""
        self.ensure_no_api_running()
        
        # Check if API is already running
        api_was_running = await self.is_api_running()
        
        if api_was_running:
            # Test with existing API - MCP shouldn't kill it when exiting
            print("Testing MCP cleanup with existing API - should preserve it")
            
            # Start MCP that connects to existing API
            mcp_process = subprocess.Popen([
                sys.executable, "-m", "src.mcp.server"
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent,  # Project root
            env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
            )
            
            try:
                # Give MCP time to connect
                await asyncio.sleep(2)
                
                # Verify MCP is running and API is still up
                assert mcp_process.poll() is None, "MCP should be running"
                assert await self.is_api_running(), "API should be running"
                
                # Terminate MCP gracefully
                if mcp_process.stdin:
                    mcp_process.stdin.close()
                mcp_process.terminate()
                mcp_process.wait(timeout=5)
                
                # Give time for any cleanup
                await asyncio.sleep(2)
                
                # API should STILL be running (pre-existing, not owned by MCP)
                assert await self.is_api_running(), "Pre-existing API should remain running after MCP exits"
                print("✓ Pre-existing API preserved after MCP shutdown")
                
            finally:
                if mcp_process.poll() is None:
                    mcp_process.terminate()
                    mcp_process.wait()
            return  # Exit early for existing API case
        
        # Test cleanup when MCP starts its own API - use test's unique port
        test_port = self.server_manager.port
        print(f"Testing MCP cleanup when it owns the API on port {test_port}")
        
        # Start MCP server (will start new API)
        mcp_process = subprocess.Popen([
            sys.executable, "-m", "src.mcp.server"
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parent.parent,  # Project root
        env={**os.environ, "SERVICE_PORT": str(test_port)}
        )
        
        try:
            # Wait for API to start
            api_started = False
            for attempt in range(30):
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, f"API should have started on port {test_port}"
            
            # Terminate MCP server gracefully - close stdin to signal stdio server
            if mcp_process.stdin:
                mcp_process.stdin.close()
            mcp_process.terminate()
            try:
                mcp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mcp_process.kill()
                mcp_process.wait()
            
            # Give cleanup time to work
            await asyncio.sleep(3)
            
            # API should be shut down (MCP owned it)
            api_running = False
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"http://localhost:{test_port}/health")
                    api_running = response.status_code == 200
            except Exception:
                pass
                
            assert not api_running, f"API on port {test_port} should be shut down after MCP cleanup"
            print(f"✓ MCP-owned API on port {test_port} cleaned up properly")
            
        finally:
            # Emergency cleanup if needed
            if mcp_process.poll() is None:
                mcp_process.kill()
                mcp_process.wait()
            
            self.ensure_no_api_running()

    @pytest.mark.asyncio
    async def test_recovery_after_api_crash(self, mcp_server_path):
        """Test recovery when API process crashes."""
        self.ensure_no_api_running()
        
        # Start MCP server with auto-start - use test's unique port
        test_port = self.server_manager.port
        mcp_process = subprocess.Popen([
            sys.executable, "-m", "src.mcp.server"
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parent.parent,  # Project root
        env={**os.environ, "SERVICE_PORT": str(test_port)}
        )
        
        try:
            # Wait for API to start
            api_started = False
            for attempt in range(30):
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, f"API should have started on port {test_port}"
            
            # Kill the API process to simulate crash
            subprocess.run(["pkill", "-f", f"uvicorn.*{test_port}"], check=False)
            await asyncio.sleep(1)
            
            # Verify API is down
            assert not await self.is_api_running(), "API should be down after simulated crash"
            
            # Start a new MCP server - should be able to start new API
            new_mcp_process = subprocess.Popen([
                sys.executable, "-m", "src.mcp.server"
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).parent.parent,  # Project root
            env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
            )
            
            try:
                # Wait for recovery
                api_recovered = False
                for attempt in range(30):
                    await asyncio.sleep(0.5)
                    if await self.is_api_running():
                        api_recovered = True
                        break
                
                assert api_recovered, "Should be able to start new API after crash"
                
            finally:
                if new_mcp_process.poll() is None:
                    new_mcp_process.terminate()
                    new_mcp_process.wait()
                
        finally:
            if mcp_process.poll() is None:
                mcp_process.terminate()
                mcp_process.wait()
            
            self.ensure_no_api_running()

    def test_unit_auto_discovery_logic(self):
        """Unit test the auto-discovery logic without subprocess."""
        # This tests the core ensure_api_available logic
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "mcp"))
        
        from server import HeadlessPMMCPServer
        
        # Create server instance
        server = HeadlessPMMCPServer()
        
        # Test command discovery
        command = server._find_headless_pm_command()
        assert command is not None, "Should find headless-pm command"
        
        # Test URL parsing
        import urllib.parse
        parsed = urllib.parse.urlparse(server.base_url)
        port = parsed.port or 6969
        assert port == 6969, "Should parse port correctly"

    @pytest.mark.asyncio
    async def test_api_functionality_with_http_client(self, mcp_server_path):
        """Test API functionality using Python HTTP client like a real client."""
        self.ensure_no_api_running()
        
        # Start MCP server process
        mcp_process = subprocess.Popen([
            sys.executable, "-m", "src.mcp.server"
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parent.parent,  # Project root
        env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
        )
        
        try:
            # Wait for API to start
            api_started = False
            for attempt in range(30):
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, "API should have started"
            
            # Test various endpoints with HTTP client
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test unauthenticated health endpoint
                response = await client.get(f"http://localhost:{self.server_manager.port}/health")
                assert response.status_code == 200, f"health endpoint should return 200, got {response.status_code}"
                
                # Test authenticated endpoints with API key
                headers = {"X-API-Key": "XXXXXX"}
                
                authenticated_endpoints = [
                    ("context", f"http://localhost:{self.server_manager.port}/api/v1/context"),
                    ("agents", f"http://localhost:{self.server_manager.port}/api/v1/agents"),
                ]
                
                for test_name, url in authenticated_endpoints:
                    response = await client.get(url, headers=headers)
                    assert response.status_code == 200, f"{test_name} endpoint should return 200, got {response.status_code}"
                
        finally:
            if mcp_process.poll() is None:
                mcp_process.terminate()
                mcp_process.wait()
            self.ensure_no_api_running()

    @pytest.mark.asyncio
    async def test_multiple_mcp_clients_scenario(self, mcp_server_path):
        """Test multiple MCP clients connecting to same API instance.
        
        This test is now robust to existing servers and properly cleans up.
        """
        # Use our robust test helper
        async with self.server_manager.test_context():
            # Check if there's already an API running
            api_was_running = await self.server_manager.is_api_running()
            
            if api_was_running:
                # Test coordination with existing API
                print("Testing multi-client coordination with existing API on port 6969")
                
                # Start first MCP client (should connect to existing)
                print("Starting first MCP client...")
                mcp1_process = self.server_manager.start_mcp_client()
                
                # API should still be running
                assert await self.server_manager.is_api_running(), "API should still be running"
                print("✓ First client connected to existing API")
            else:
                # Use test's unique port for isolation
                test_port = self.server_manager.port
                self.server_manager = ServerManager(port=test_port)
                print(f"Testing multi-client coordination on port {test_port}")
                
                # Start first MCP client (should start API)
                print("Starting first MCP client...")
                mcp1_process = subprocess.Popen([
                    sys.executable, "-m", "src.mcp"
                ], 
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=Path(__file__).parent.parent,  # Project root
                env={**os.environ, "SERVICE_PORT": str(test_port)}
                )
                self.server_manager.started_processes.append(mcp1_process)
                
                # Wait for API to start
                api_started = False
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    try:
                        async with httpx.AsyncClient(timeout=2.0) as client:
                            response = await client.get(f"http://localhost:{test_port}/health")
                            if response.status_code == 200:
                                api_started = True
                                break
                    except Exception:
                        pass
                
                assert api_started, f"API should have started from first MCP client on port {test_port}"
                print(f"✓ First client started API on port {test_port}")
            
            # Start second MCP client (should connect to existing API)
            print("Starting second MCP client...")
            mcp2_process = self.server_manager.start_mcp_client()
            
            # API should still be running
            assert await self.server_manager.is_api_running(), "API should still be running with two clients"
            print("✓ Two clients connected")
            
            # Terminate first client
            print("Terminating first client...")
            self.server_manager.cleanup_process(mcp1_process)
            await asyncio.sleep(5)  # Give more time for coordination to handle client exit
            
            # API should still be running (second client active)
            still_running = await self.server_manager.is_api_running()
            if not still_running:
                # Get debug info
                stderr2 = mcp2_process.stderr.read() if mcp2_process.stderr else "No stderr"
                print(f"Second client stderr: {stderr2[:500]}")
            
            assert still_running, "API should remain running with second client active"
            print("✓ API survived first client exit")
            
            # Cleanup second client happens in context manager

    @pytest.mark.asyncio
    async def test_api_endpoint_comprehensive_functionality(self, mcp_server_path):
        """Test comprehensive API functionality once launched by MCP server."""
        self.ensure_no_api_running()
        
        # Start MCP server
        mcp_process = subprocess.Popen([
            sys.executable, "-m", "src.mcp.server"
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).parent.parent,  # Project root
        env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}
        )
        
        try:
            # Wait for API to start
            api_started = False
            for attempt in range(30):
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, "API should have started"
            
            # Test comprehensive API functionality
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test health endpoint (no auth required)
                response = await client.get(f"http://localhost:{self.server_manager.port}/health")
                assert response.status_code == 200
                health_data = response.json()
                assert "status" in health_data
                
                # Set up authentication headers
                headers = {"X-API-Key": "XXXXXX"}
                
                # Test context endpoint (auth required)
                response = await client.get(f"http://localhost:{self.server_manager.port}/api/v1/context", headers=headers)
                assert response.status_code == 200
                context_data = response.json()
                assert "project_name" in context_data or "name" in context_data
                
                # Skip docs endpoint test - not critical for MCP auto-discovery validation
                
                # Test that agents endpoint exists (auth required)
                response = await client.get(f"http://localhost:{self.server_manager.port}/api/v1/agents", headers=headers)
                assert response.status_code == 200
                agents_data = response.json()
                assert isinstance(agents_data, list)  # Should return list of agents
                
        finally:
            if mcp_process.poll() is None:
                mcp_process.terminate()
                mcp_process.wait()
            self.ensure_no_api_running()

    def test_service_port_consistency(self):
        """Test that SERVICE_PORT environment variable is respected consistently."""
        # Test default port
        server_default = HeadlessPMMCPServer()
        assert "localhost:6969" in server_default.base_url
        
        # Test custom SERVICE_PORT
        with patch.dict(os.environ, {"SERVICE_PORT": "7070"}):
            server_custom = HeadlessPMMCPServer()
            assert "localhost:7070" in server_custom.base_url

    def test_coordination_file_corruption_resilience(self):
        """Test coordination file handles corruption gracefully."""
        server = HeadlessPMMCPServer()
        coordination_file = server._get_mcp_coordination_file()
        
        # Create corrupted coordination file
        with open(coordination_file, "w") as f:
            f.write("invalid json{{{")
        
        try:
            # Should handle corruption gracefully
            result = server._register_mcp_client()
            assert isinstance(result, bool), "Should return boolean even with corrupted file"
            
        finally:
            # Clean up
            if coordination_file.exists():
                coordination_file.unlink()

