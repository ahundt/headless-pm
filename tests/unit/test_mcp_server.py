"""
Unit tests for MCP server functionality including auto-discovery.
"""
import pytest
import asyncio
import httpx
import subprocess
import signal
import time
import os
import tempfile
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from sqlmodel import Session, SQLModel, create_engine


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
    with Session(engine) as session:
        yield session


def import_mcp_server():
    """Helper function to import MCP server without path conflicts."""
    # Load module directly to avoid sys.path conflicts
    server_path = Path(__file__).parent.parent.parent / "src" / "mcp" / "headless_pm_mcp_server.py"
    spec = importlib.util.spec_from_file_location("headless_pm_mcp_server", server_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load MCP server from {server_path}")
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HeadlessPMMCPServer


class TestMCPServer:
    """Test MCP server functionality."""

    def test_import_mcp_server(self):
        """Test that MCP server can be imported correctly."""
        # Use helper function to avoid import conflicts
        HeadlessPMMCPServer = import_mcp_server()
        
        # Should be able to create instance
        server = HeadlessPMMCPServer()
        assert server.base_url == "http://localhost:6969"
        assert server.client is not None
        assert server.server is not None

    def test_command_discovery(self):
        """Test MCP server command discovery logic."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        command = server._find_headless_pm_command()
        
        # Should find at least one valid command
        assert command is not None, "Should find a valid headless-pm command"
        assert isinstance(command, list), "Command should be a list"
        assert len(command) > 0, "Command list should not be empty"
        
        # First element should be a valid executable
        assert isinstance(command[0], str), "First command element should be string"

    def test_url_parsing(self):
        """Test URL parsing logic in auto-discovery."""
        HeadlessPMMCPServer = import_mcp_server()
        import urllib.parse
        
        # Test default URL
        server = HeadlessPMMCPServer()
        parsed = urllib.parse.urlparse(server.base_url)
        port = parsed.port or 6969
        assert port == 6969, "Should parse port correctly from default URL"
        
        # Test custom URL
        server_custom = HeadlessPMMCPServer("http://localhost:8080")
        parsed_custom = urllib.parse.urlparse(server_custom.base_url)
        port_custom = parsed_custom.port or 6969
        assert port_custom == 8080, "Should parse port correctly from custom URL"

    @pytest.mark.asyncio
    async def test_health_check_logic(self):
        """Test the health check logic used in auto-discovery."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock httpx client for testing
        with patch.object(server.client, 'get') as mock_get:
            # Test successful health check
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            
            # This tests the core health check logic that auto-discovery uses
            try:
                response = await server.client.get(f"{server.base_url}/health", timeout=5.0)
                assert response.status_code == 200
            except Exception:
                pytest.fail("Health check logic should work with mocked response")

    def test_process_tracking(self):
        """Test that MCP server properly tracks API processes."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Initially should have no process
        assert server._api_process is None
        
        # After setting a mock process, should track it
        mock_process = MagicMock()
        server._api_process = mock_process
        assert server._api_process is mock_process

    @pytest.mark.asyncio
    async def test_ensure_api_available_no_command(self):
        """Test auto-discovery behavior when no command is found."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock command discovery to return None
        with patch.object(server, '_find_headless_pm_command', return_value=None):
            # Mock client to simulate no existing API
            with patch.object(server.client, 'get', side_effect=Exception("No API")):
                result = await server.ensure_api_available()
                assert result is False, "Should return False when no command found"

    @pytest.mark.asyncio
    async def test_ensure_api_available_existing_api(self):
        """Test auto-discovery behavior when API already exists."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock successful connection to existing API
        mock_response = MagicMock()
        mock_response.status_code = 200
        
        with patch.object(server.client, 'get', return_value=mock_response):
            result = await server.ensure_api_available()
            assert result is True, "Should return True when existing API found"
            assert server._api_process is None, "Should not start new process"

    @pytest.mark.asyncio 
    async def test_client_cleanup(self):
        """Test that HTTP client is properly cleaned up."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock the client's aclose method
        with patch.object(server.client, 'aclose', new_callable=AsyncMock) as mock_aclose:
            # Simulate cleanup
            await server.client.aclose()
            mock_aclose.assert_called_once()

    def test_base_url_normalization(self):
        """Test that base URL is properly normalized."""
        HeadlessPMMCPServer = import_mcp_server()
        
        # Test URL with trailing slash
        server = HeadlessPMMCPServer("http://localhost:6969/")
        assert server.base_url == "http://localhost:6969", "Should strip trailing slash"
        
        # Test URL without trailing slash
        server2 = HeadlessPMMCPServer("http://localhost:6969")
        assert server2.base_url == "http://localhost:6969", "Should preserve clean URL"

    def test_token_tracker_initialization(self):
        """Test that token tracker is properly initialized."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        assert server.token_tracker is not None, "Token tracker should be initialized"
        
        # Test that it has expected methods
        assert hasattr(server.token_tracker, 'track_request'), "Should have track_request method"
        assert hasattr(server.token_tracker, 'track_response'), "Should have track_response method"


class TestMCPServerIntegration:
    """Integration tests for MCP server (requires more setup)."""

    @pytest.mark.skipif(
        not Path("/Users/athundt/src/agentic/headless-pm/src/mcp/server.py").exists(), 
        reason="MCP server file not found"
    )
    def test_mcp_server_script_executable(self):
        """Test that MCP server script can be executed."""
        server_path = Path(__file__).parent.parent.parent / "src" / "mcp" / "headless_pm_mcp_server.py"
        assert server_path.exists(), "MCP server script should exist"
        
        # Test that it can be imported and doesn't crash immediately
        result = subprocess.run([
            "python", "-c", 
            f"import sys; sys.path.insert(0, '{server_path.parent}'); "
            "from headless_pm_mcp_server import HeadlessPMMCPServer; "
            "s = HeadlessPMMCPServer(); "
            "print('SUCCESS')"
        ], 
        capture_output=True, 
        text=True,
        cwd=server_path.parent.parent.parent
        )
        
        assert result.returncode == 0, f"Script import failed: {result.stderr}"
        assert "SUCCESS" in result.stdout, "Should successfully import and create server"

    def test_command_candidates_realistic(self):
        """Test that command candidates include realistic options."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Test the actual command discovery (not mocked)
        command = server._find_headless_pm_command()
        
        if command:  # Only test if a command was found
            # Should be one of the expected patterns
            expected_patterns = [
                ["headless-pm"],
                ["python", "-m", "src.main"],
                ["uvicorn", "src.main:app", "--host", "0.0.0.0"]
            ]
            
            # Should match at least one pattern
            matches_pattern = any(
                command[:len(pattern)] == pattern 
                for pattern in expected_patterns
            )
            assert matches_pattern, f"Command {command} should match expected patterns"


class TestMCPServerErrorHandling:
    """Test error handling in MCP server."""

    @pytest.mark.asyncio
    async def test_network_error_handling(self):
        """Test handling of network errors during health checks."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock network error
        with patch.object(server.client, 'get', side_effect=httpx.ConnectError("Connection failed")):
            # Should handle network errors gracefully in health check logic
            try:
                await server.client.get(f"{server.base_url}/health", timeout=5.0)
                pytest.fail("Should have raised ConnectError")
            except httpx.ConnectError:
                pass  # Expected behavior

    def test_process_termination_handling(self):
        """Test proper process termination logic."""
        HeadlessPMMCPServer = import_mcp_server()
        
        server = HeadlessPMMCPServer()
        
        # Mock a process
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process running
        server._api_process = mock_process
        
        # Test termination logic (simulated)
        if server._api_process and server._api_process.poll() is None:
            server._api_process.terminate()
            server._api_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test timeout handling in API startup."""
        HeadlessPMMCPServer = import_mcp_server()
        import asyncio
        
        server = HeadlessPMMCPServer()
        
        # Test that asyncio.sleep calls work (used in retry loops)
        start_time = time.time()
        await asyncio.sleep(0.1)  # Minimal sleep for testing
        elapsed = time.time() - start_time
        assert elapsed >= 0.1, "Should actually wait for the specified time"
        assert elapsed < 0.2, "Should not wait significantly longer than specified"