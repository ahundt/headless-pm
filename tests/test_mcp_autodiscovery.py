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
    return Path(__file__).parent.parent / "src" / "mcp" / "headless_pm_mcp_server.py"


@pytest.mark.integration
class TestMCPAutoDiscovery:
    """Integration tests for MCP server auto-discovery functionality."""

    async def is_api_running(self, base_url: str = "http://localhost:6969") -> bool:
        """Check if API is responding."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{base_url}/health")
                return response.status_code == 200
        except Exception:
            return False

    def ensure_no_api_running(self):
        """Ensure no API processes are running on test port."""
        # Kill any existing processes on port 6969
        try:
            subprocess.run(["pkill", "-f", "headless-pm"], check=False, capture_output=True)
            subprocess.run(["pkill", "-f", "6969"], check=False, capture_output=True)
            time.sleep(1)  # Give processes time to die
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_auto_start_when_no_api_running(self, mcp_server_path):
        """Test that MCP server starts API when none is running."""
        self.ensure_no_api_running()
        
        # Verify no API is running
        assert not await self.is_api_running(), "API should not be running initially"
        
        # Start MCP server process
        mcp_process = subprocess.Popen([
            "python", str(mcp_server_path)
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=mcp_server_path.parent.parent.parent,  # headless-pm root directory
        env={**os.environ, "SERVICE_PORT": "6969"}
        )
        
        try:
            # Wait for API to start (with timeout)
            api_started = False
            for attempt in range(30):  # 15 seconds max
                await asyncio.sleep(0.5)
                if await self.is_api_running():
                    api_started = True
                    break
            
            assert api_started, "API should have been started by MCP server"
            
            # Test API functionality
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("http://localhost:6969/health")
                assert response.status_code == 200
                
                response = await client.get("http://localhost:6969/api/v1/context")
                assert response.status_code == 200
                
        finally:
            # Cleanup
            if mcp_process.poll() is None:
                mcp_process.terminate()
                try:
                    mcp_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    mcp_process.kill()
                    mcp_process.wait()

    @pytest.mark.asyncio
    async def test_connect_to_existing_api(self, mcp_server_path):
        """Test that MCP server connects to existing API without starting new one."""
        self.ensure_no_api_running()
        
        # Start API manually first
        api_process = subprocess.Popen([
            "python", "-m", "src.main"
        ], 
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=mcp_server_path.parent.parent.parent,  # headless-pm root directory
        env={**os.environ, "SERVICE_PORT": "6969"}
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
                "python", str(mcp_server_path)
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=mcp_server_path.parent.parent.parent,
            env={**os.environ, "SERVICE_PORT": "6969"}
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
        sys.path.insert(0, str(mcp_server_path.parent))
        
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
        
        # Start MCP server
        mcp_process = subprocess.Popen([
            "python", str(mcp_server_path)
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=mcp_server_path.parent.parent.parent,
        env={**os.environ, "SERVICE_PORT": "6969"}
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
            
            # Terminate MCP server gracefully
            mcp_process.terminate()
            mcp_process.wait(timeout=10)
            
            # Give cleanup time to work
            await asyncio.sleep(2)
            
            # API should be shut down
            api_running = await self.is_api_running()
            assert not api_running, "API should be shut down after MCP server cleanup"
            
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
        
        # Start MCP server with auto-start
        mcp_process = subprocess.Popen([
            "python", str(mcp_server_path)
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        text=True,
        cwd=mcp_server_path.parent.parent.parent,
        env={**os.environ, "SERVICE_PORT": "6969"}
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
            
            # Kill the API process to simulate crash
            subprocess.run(["pkill", "-f", "uvicorn.*6969"], check=False)
            await asyncio.sleep(1)
            
            # Verify API is down
            assert not await self.is_api_running(), "API should be down after simulated crash"
            
            # Start a new MCP server - should be able to start new API
            new_mcp_process = subprocess.Popen([
                "python", str(mcp_server_path)
            ], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            cwd=mcp_server_path.parent.parent.parent,
            env={**os.environ, "SERVICE_PORT": "6969"}
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
        
        from headless_pm_mcp_server import HeadlessPMMCPServer
        
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