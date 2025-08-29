"""
Headless PM MCP Server - Model Context Protocol server for Headless PM integration
Provides standardized interface for Claude Code and other MCP clients.

Environment Variables:
    HEADLESS_PM_COMMAND: Full command to start HeadlessPM (overrides all discovery)
    HEADLESS_PM_DIR: Project/working directory for HeadlessPM
    HEADLESS_PM_NO_AUTOSTART: Skip auto-start, connection-only mode (any non-empty value)
    HEADLESS_PM_URL: API base URL (overrides default, e.g., http://localhost:6969)
    SERVICE_PORT: HeadlessPM API port (default: 6969)

Design Principles:
    1. Connection-first: Try existing API before starting new process
    2. Smart discovery: Prioritize same Python interpreter for consistency
    3. Simple configuration: Minimal, non-redundant environment variables
    4. Context preservation: Respect user's working directory when possible

Example Usage:
    # Connection-only mode
    HEADLESS_PM_NO_AUTOSTART=1 headless-pm-mcp

    # Override discovery with specific command
    HEADLESS_PM_COMMAND="uv run headless-pm" headless-pm-mcp

    # Run in specific directory
    HEADLESS_PM_DIR=/path/to/project headless-pm-mcp
"""

import asyncio
import json
import logging
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime
from pathlib import Path
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListResourcesRequest,
    ListResourcesResult,
    ListToolsRequest,
    ListToolsResult,
    ReadResourceRequest,
    ReadResourceResult,
    Resource,
    TextContent,
    Tool,
    EmbeddedResource,
)
try:
    from .token_tracker import TokenTracker
except ImportError:
    # Handle case where this is run as a standalone script
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from token_tracker import TokenTracker

# Configure logging
logging.basicConfig(level=logging.INFO, format='[MCP] %(message)s')
logger = logging.getLogger("headless-pm-mcp")


class HeadlessPMMCPServer:
    """MCP Server for Headless PM integration."""

    def __init__(self, base_url: str = "http://localhost:6969"):
        # Prioritize env var, then constructor arg (maintains backward compatibility)
        self.base_url = (os.getenv('HEADLESS_PM_URL') or base_url).rstrip('/')
        self.server = Server("headless-pm")
        self.client = httpx.AsyncClient(timeout=30.0)
        self.agent_id: Optional[str] = None
        self.agent_role: Optional[str] = None
        self.agent_skill_level: Optional[str] = None
        self.token_tracker = TokenTracker()
        self._api_process: Optional[subprocess.Popen] = None

        # Register handlers
        self._register_handlers()

    async def ensure_api_available(self) -> bool:
        """Ensure HeadlessPM API is available using connection-first pattern."""
        # Extract host and port from base_url
        import urllib.parse
        parsed = urllib.parse.urlparse(self.base_url)
        port = parsed.port or 6969

        # Step 1: Try to connect to existing API
        try:
            logger.info(f"Checking for existing API at {self.base_url}...")
            response = await self.client.get(f"{self.base_url}/health", timeout=5.0)
            if response.status_code == 200:
                logger.info("✅ Connected to existing HeadlessPM API")
                return True
        except Exception:
            logger.info("No existing API found")

        # Check if auto-start is disabled
        if os.environ.get("HEADLESS_PM_NO_AUTOSTART"):
            logger.info("Auto-start disabled via HEADLESS_PM_NO_AUTOSTART")
            return False

        # Step 2: Try to start API process
        logger.info("Attempting to start HeadlessPM API...")
        try:
            # Find headless-pm executable in common locations
            headless_pm_cmd = self._find_headless_pm_command()
            if not headless_pm_cmd:
                logger.error("❌ headless-pm command not found")
                logger.error("   Install: pip install headless-pm")
                logger.error("   Override: HEADLESS_PM_COMMAND='your command'")
                return False

            logger.info(f"Starting HeadlessPM API with: {headless_pm_cmd}")

            # Determine working directory with better user context preservation
            working_dir = self._determine_working_directory(headless_pm_cmd)

            # Start API process
            self._api_process = subprocess.Popen(
                headless_pm_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=working_dir,
                env={**os.environ, "SERVICE_PORT": str(port)}
            )

            # Step 3: Wait for API to become available (with retries)
            for attempt in range(12):  # 12 attempts over 6 seconds
                try:
                    await asyncio.sleep(0.5)
                    response = await self.client.get(f"{self.base_url}/health", timeout=5.0)
                    if response.status_code == 200:
                        logger.info("✅ Successfully started HeadlessPM API")
                        return True
                except Exception:
                    continue

            # If we get here, startup failed
            if self._api_process.poll() is None:
                logger.error(f"❌ API process started but not responding at {self.base_url}")
                self._api_process.terminate()
            else:
                logger.error("❌ API process exited during startup")

            self._api_process = None
            return False

        except Exception as e:
            logger.error(f"❌ Failed to start HeadlessPM API: {e}")
            return False

    def _find_project_directory(self) -> Optional[Path]:
        """Find HeadlessPM project directory by searching common locations."""
        # Check environment variable first (highest priority)
        if "HEADLESS_PM_DIR" in os.environ:
            env_dir = Path(os.environ["HEADLESS_PM_DIR"])
            if env_dir.exists():
                return env_dir

        # Smart discovery: look for pyproject.toml with headless-pm
        candidates = [
            Path.cwd(),  # Current directory
            Path.cwd() / "headless-pm",  # Subdirectory
            Path.home() / "source" / "agentic" / "headless-pm",  # Common dev path
        ]

        for candidate in candidates:
            if (candidate / "pyproject.toml").exists():
                try:
                    content = (candidate / "pyproject.toml").read_text()
                    if 'name = "headless-pm"' in content:
                        return candidate
                except Exception:
                    continue
        return None

    def _get_current_python(self) -> str:
        """Get the current Python interpreter path."""
        return sys.executable

    def _determine_working_directory(self, cmd: List[str]) -> Optional[Path]:
        """Determine working directory for the command."""
        # Use explicit directory if set
        if "HEADLESS_PM_DIR" in os.environ:
            return Path(os.environ["HEADLESS_PM_DIR"])

        # Commands that need project context
        needs_project_context = any([
            "src.main" in str(cmd),
            "uv" in cmd and any(x in cmd for x in ["run", "start", "api-only"])
        ])

        if needs_project_context:
            project_dir = self._find_project_directory()
            if project_dir:
                return project_dir

        # Default: preserve user's working directory
        return None  # None means use current directory

    def _find_headless_pm_command(self) -> Optional[List[str]]:
        """Find headless-pm command using smart discovery."""
        # Environment override - highest priority
        if "HEADLESS_PM_COMMAND" in os.environ:
            return os.environ["HEADLESS_PM_COMMAND"].split()

        current_python = self._get_current_python()

        # Build candidate commands in priority order
        candidates = [
            # 1. Global installations
            ["headless-pm"],
            ["headless-pm-mcp"],

            # 2. Same Python interpreter (consistency)
            [current_python, "-m", "headless_pm"],

            # 3. UV commands
            ["uv", "run", "headless-pm"],
            ["uv", "run", "start"],

            # 4. Virtual environments
            *self._get_venv_commands(),

            # 5. Direct Python execution
            ["python3", "-m", "headless_pm"],
            ["python", "-m", "headless_pm"],
        ]

        # Add project-specific commands if project found
        project_dir = self._find_project_directory()
        if project_dir:
            candidates.extend([
                [current_python, "-m", "src.main"],
                ["python3", "-m", "src.main"],
                ["uv", "run", "--", "python", "-m", "src.main"],
                ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "6969"],
            ])

        # Test candidates
        for cmd in candidates:
            if self._test_command(cmd, self._determine_working_directory(cmd)):
                return cmd

        logger.error("❌ No working HeadlessPM command found")
        logger.error("   Install: pip install headless-pm")
        logger.error("   Override: HEADLESS_PM_COMMAND='your command'")
        return None

    def _get_venv_commands(self) -> List[List[str]]:
        """Get virtual environment commands to try."""
        commands = []
        project_dir = self._find_project_directory()
        # Search in CWD first, then project_dir if it's different
        search_dirs = [Path.cwd()]
        if project_dir and project_dir != Path.cwd():
            search_dirs.append(project_dir)

        for venv_name in [".venv", "venv", "claude_venv"]:
            for base_dir in search_dirs:
                venv_path = base_dir / venv_name / "bin" / "headless-pm"
                if venv_path.exists():
                    commands.append([str(venv_path)])
        return commands

    def _test_command(self, cmd: List[str], working_dir: Optional[Path]) -> bool:
        """Test if a command is executable and working."""
        try:
            # Skip non-existent venv binaries quickly
            if len(cmd) == 1 and "/" in cmd[0] and not Path(cmd[0]).exists():
                return False

            # Use --help for general compatibility
            test_args = cmd + ["--help"]

            result = subprocess.run(
                test_args,
                capture_output=True,
                timeout=3,
                cwd=working_dir or Path.cwd(),
                env=os.environ
            )
            return result.returncode == 0
        except Exception:
            return False

    def _register_handlers(self):
        """Register MCP handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> ListToolsResult:
            """List available tools."""
            return ListToolsResult(
                tools=[
                    Tool(
                        name="register_agent",
                        description="Register agent with Headless PM system",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "agent_id": {
                                    "type": "string",
                                    "description": "Unique identifier for the agent"
                                },
                                "role": {
                                    "type": "string",
                                    "description": "Agent role (frontend_dev, backend_dev, architect, pm, qa)",
                                    "enum": ["frontend_dev", "backend_dev", "architect", "pm", "qa"]
                                },
                                "skill_level": {
                                    "type": "string",
                                    "description": "Agent skill level",
                                    "enum": ["junior", "senior", "principal"],
                                    "default": "senior"
                                }
                            },
                            "required": ["agent_id", "role"]
                        }
                    ),
                    Tool(
                        name="get_project_context",
                        description="Get project configuration and context information",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    ),
                    Tool(
                        name="get_next_task",
                        description="Get next available task for the registered agent",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Override agent role for task search"
                                },
                                "skill_level": {
                                    "type": "string",
                                    "description": "Override skill level for task search"
                                }
                            }
                        }
                    ),
                    Tool(
                        name="create_task",
                        description="Create a new task",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Task title"
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Detailed task description"
                                },
                                "complexity": {
                                    "type": "string",
                                    "description": "Task complexity level",
                                    "enum": ["minor", "major"]
                                },
                                "role": {
                                    "type": "string",
                                    "description": "Required role for the task"
                                },
                                "skill_level": {
                                    "type": "string",
                                    "description": "Required skill level for the task",
                                    "enum": ["junior", "senior", "principal"]
                                }
                            },
                            "required": ["title", "description", "complexity"]
                        }
                    ),
                    Tool(
                        name="lock_task",
                        description="Lock a task to prevent other agents from picking it up",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "task_id": {
                                    "type": "integer",
                                    "description": "ID of the task to lock"
                                }
                            },
                            "required": ["task_id"]
                        }
                    ),
                    Tool(
                        name="update_task_status",
                        description="Update task status and progress",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "task_id": {
                                    "type": "integer",
                                    "description": "ID of the task to update"
                                },
                                "status": {
                                    "type": "string",
                                    "description": "New task status",
                                    "enum": ["created", "assigned", "under_work", "dev_done", "testing", "completed",
                                             "blocked"]
                                },
                                "notes": {
                                    "type": "string",
                                    "description": "Optional notes about the update"
                                }
                            },
                            "required": ["task_id", "status"]
                        }
                    ),
                    Tool(
                        name="create_document",
                        description="Create a document with optional @mentions for team communication",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Document title"
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Document content (supports @mentions)"
                                },
                                "doc_type": {
                                    "type": "string",
                                    "description": "Document type",
                                    "default": "note"
                                },
                                "mentions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of agent IDs to mention"
                                }
                            },
                            "required": ["title", "content"]
                        }
                    ),
                    Tool(
                        name="get_mentions",
                        description="Get notifications and mentions for the registered agent",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    ),
                    Tool(
                        name="register_service",
                        description="Register a microservice with the system",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "service_name": {
                                    "type": "string",
                                    "description": "Name of the service"
                                },
                                "service_url": {
                                    "type": "string",
                                    "description": "Service URL"
                                },
                                "health_check_url": {
                                    "type": "string",
                                    "description": "Health check endpoint URL"
                                }
                            },
                            "required": ["service_name", "service_url"]
                        }
                    ),
                    Tool(
                        name="send_heartbeat",
                        description="Send heartbeat for a registered service",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "service_name": {
                                    "type": "string",
                                    "description": "Name of the service"
                                },
                                "status": {
                                    "type": "string",
                                    "description": "Service status",
                                    "default": "healthy"
                                }
                            },
                            "required": ["service_name"]
                        }
                    ),
                    Tool(
                        name="poll_changes",
                        description="Poll for system changes since a given timestamp",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "since_timestamp": {
                                    "type": "string",
                                    "description": "ISO timestamp to poll changes since"
                                }
                            }
                        }
                    ),
                    Tool(
                        name="get_token_usage",
                        description="Get MCP token usage statistics",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    )
                ]
            )

        @self.server.list_resources()
        async def handle_list_resources() -> ListResourcesResult:
            """List available resources."""
            return ListResourcesResult(
                resources=[
                    Resource(
                        uri="headless-pm://tasks/list",
                        name="Current Tasks",
                        description="List of all current tasks in the system",
                        mimeType="application/json"
                    ),
                    Resource(
                        uri="headless-pm://agents/list",
                        name="Active Agents",
                        description="List of all registered agents",
                        mimeType="application/json"
                    ),
                    Resource(
                        uri="headless-pm://documents/recent",
                        name="Recent Documents",
                        description="Recently created documents and communications",
                        mimeType="application/json"
                    ),
                    Resource(
                        uri="headless-pm://services/status",
                        name="Service Status",
                        description="Status of all registered microservices",
                        mimeType="application/json"
                    ),
                    Resource(
                        uri="headless-pm://changelog/recent",
                        name="Recent Activity",
                        description="Recent system activity and changes",
                        mimeType="application/json"
                    ),
                    Resource(
                        uri="headless-pm://context/project",
                        name="Project Context",
                        description="Current project configuration and context",
                        mimeType="application/json"
                    )
                ]
            )

        @self.server.read_resource()
        async def handle_read_resource(request: ReadResourceRequest) -> ReadResourceResult:
            """Read resource content."""
            uri = request.uri

            try:
                if uri == "headless-pm://tasks/list":
                    response = await self.client.get(f"{self.base_url}/api/v1/tasks")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                elif uri == "headless-pm://agents/list":
                    response = await self.client.get(f"{self.base_url}/api/v1/agents")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                elif uri == "headless-pm://documents/recent":
                    response = await self.client.get(f"{self.base_url}/api/v1/documents?limit=20")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                elif uri == "headless-pm://services/status":
                    response = await self.client.get(f"{self.base_url}/api/v1/services")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                elif uri == "headless-pm://changelog/recent":
                    response = await self.client.get(f"{self.base_url}/api/v1/changelog?limit=50")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                elif uri == "headless-pm://context/project":
                    response = await self.client.get(f"{self.base_url}/api/v1/context")
                    data = response.json()
                    content = json.dumps(data, indent=2)

                else:
                    raise ValueError(f"Unknown resource URI: {uri}")

                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=content
                        )
                    ]
                )

            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}")
                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=f"Error reading resource: {str(e)}"
                        )
                    ]
                )

        @self.server.call_tool()
        async def handle_call_tool(request: CallToolRequest) -> CallToolResult:
            """Handle tool calls."""
            try:
                # Track request tokens
                self.token_tracker.track_request({"tool": request.name, "args": request.arguments})
                if request.name == "register_agent":
                    return await self._register_agent(request.arguments)
                elif request.name == "get_project_context":
                    return await self._get_project_context(request.arguments)
                elif request.name == "get_next_task":
                    return await self._get_next_task(request.arguments)
                elif request.name == "create_task":
                    return await self._create_task(request.arguments)
                elif request.name == "lock_task":
                    return await self._lock_task(request.arguments)
                elif request.name == "update_task_status":
                    return await self._update_task_status(request.arguments)
                elif request.name == "create_document":
                    return await self._create_document(request.arguments)
                elif request.name == "get_mentions":
                    return await self._get_mentions(request.arguments)
                elif request.name == "register_service":
                    return await self._register_service(request.arguments)
                elif request.name == "send_heartbeat":
                    return await self._send_heartbeat(request.arguments)
                elif request.name == "poll_changes":
                    return await self._poll_changes(request.arguments)
                elif request.name == "get_token_usage":
                    return await self._get_token_usage(request.arguments)
                else:
                    raise ValueError(f"Unknown tool: {request.name}")

            except Exception as e:
                logger.error(f"Error calling tool {request.name}: {e}")
                result = CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: {str(e)}"
                        )
                    ]
                )
                # Track response tokens
                self.token_tracker.track_response({"error": str(e)})
                return result

    async def _register_agent(self, args: Dict[str, Any]) -> CallToolResult:
        """Register agent with the system."""
        self.agent_id = args["agent_id"]
        self.agent_role = args["role"]
        self.agent_skill_level = args.get("skill_level", "senior")

        data = {
            "agent_id": self.agent_id,
            "role": self.agent_role,
            "level": self.agent_skill_level,  # Changed from skill_level to level
            "connection_type": "mcp"  # Set connection type to MCP
        }

        response = await self.client.post(f"{self.base_url}/api/v1/register", json=data)
        response.raise_for_status()
        result = response.json()

        call_result = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Agent {self.agent_id} registered as {self.agent_role} ({self.agent_skill_level})"
                )
            ]
        )

        # Track response tokens
        self.token_tracker.track_response(result)
        return call_result

    async def _get_project_context(self, args: Dict[str, Any]) -> CallToolResult:
        """Get project context."""
        response = await self.client.get(f"{self.base_url}/api/v1/context")
        response.raise_for_status()
        result = response.json()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2)
                )
            ]
        )

    async def _get_next_task(self, args: Dict[str, Any]) -> CallToolResult:
        """Get next available task."""
        params = {
            "role": args.get("role", self.agent_role),
            "skill_level": args.get("skill_level", self.agent_skill_level)
        }

        response = await self.client.get(f"{self.base_url}/api/v1/tasks/next", params=params)
        response.raise_for_status()
        result = response.json()

        if not result:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="No tasks available"
                    )
                ]
            )

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Task {result.get('id')}: {result.get('title')}\nComplexity: {result.get('complexity')}\n{result.get('description')}"
                )
            ]
        )

    async def _create_task(self, args: Dict[str, Any]) -> CallToolResult:
        """Create a new task."""
        data = {
            "title": args["title"],
            "description": args["description"],
            "complexity": args["complexity"],
            "role": args.get("role", self.agent_role),
            "skill_level": args.get("skill_level", self.agent_skill_level)
        }

        response = await self.client.post(f"{self.base_url}/api/v1/tasks/create", json=data)
        response.raise_for_status()
        result = response.json()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Task {result.get('id')} created: {result.get('title')}"
                )
            ]
        )

    async def _lock_task(self, args: Dict[str, Any]) -> CallToolResult:
        """Lock a task."""
        task_id = args["task_id"]
        data = {"agent_id": self.agent_id}

        response = await self.client.post(f"{self.base_url}/api/v1/tasks/{task_id}/lock", json=data)
        response.raise_for_status()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Task {task_id} locked"
                )
            ]
        )

    async def _update_task_status(self, args: Dict[str, Any]) -> CallToolResult:
        """Update task status."""
        task_id = args["task_id"]
        data = {
            "status": args["status"],
            "agent_id": self.agent_id
        }

        if "notes" in args:
            data["notes"] = args["notes"]

        response = await self.client.put(f"{self.base_url}/api/v1/tasks/{task_id}/status", json=data)
        response.raise_for_status()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Task {task_id} status: {args['status']}"
                )
            ]
        )

    async def _create_document(self, args: Dict[str, Any]) -> CallToolResult:
        """Create a document."""
        data = {
            "title": args["title"],
            "content": args["content"],
            "type": args.get("doc_type", "note"),
            "author": self.agent_id
        }

        if "mentions" in args:
            data["mentions"] = args["mentions"]

        response = await self.client.post(f"{self.base_url}/api/v1/documents", json=data)
        response.raise_for_status()
        result = response.json()

        mentions_text = ""
        if "mentions" in args and args["mentions"]:
            mentions_text = f"\n**Mentions:** {', '.join(args['mentions'])}"

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Document {result.get('id')} created: {result.get('title')}"
                )
            ]
        )

    async def _get_mentions(self, args: Dict[str, Any]) -> CallToolResult:
        """Get mentions for the agent."""
        params = {"agent_id": self.agent_id}
        response = await self.client.get(f"{self.base_url}/api/v1/mentions", params=params)
        response.raise_for_status()
        result = response.json()

        if not result:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="No mentions"
                    )
                ]
            )

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"{len(result)} mentions: {json.dumps(result, indent=2)}"
                )
            ]
        )

    async def _register_service(self, args: Dict[str, Any]) -> CallToolResult:
        """Register a service."""
        data = {
            "name": args["service_name"],
            "url": args["service_url"],
            "registered_by": self.agent_id
        }

        if "health_check_url" in args:
            data["health_check_url"] = args["health_check_url"]

        response = await self.client.post(f"{self.base_url}/api/v1/services/register", json=data)
        response.raise_for_status()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Service '{args['service_name']}' registered"
                )
            ]
        )

    async def _send_heartbeat(self, args: Dict[str, Any]) -> CallToolResult:
        """Send service heartbeat."""
        service_name = args["service_name"]
        data = {"status": args.get("status", "healthy")}

        response = await self.client.post(f"{self.base_url}/api/v1/services/{service_name}/heartbeat", json=data)
        response.raise_for_status()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"Heartbeat sent: {service_name}"
                )
            ]
        )

    async def _poll_changes(self, args: Dict[str, Any]) -> CallToolResult:
        """Poll for changes."""
        params = {}
        if "since_timestamp" in args:
            params["since"] = args["since_timestamp"]

        response = await self.client.get(f"{self.base_url}/api/v1/changes", params=params)
        response.raise_for_status()
        result = response.json()

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2)
                )
            ]
        )

    async def _get_token_usage(self, args: Dict[str, Any]) -> CallToolResult:
        """Get token usage statistics."""
        usage_summary = self.token_tracker.get_usage_summary()

        result = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(usage_summary, indent=2)
                )
            ]
        )

        # Track response tokens
        self.token_tracker.track_response(usage_summary)
        return result

    async def run(self):
        """Run the MCP server."""
        if not await self.ensure_api_available():
            logger.error("❌ Could not start or connect to HeadlessPM API")
            logger.error("   Please ensure HeadlessPM is installed or start it manually with: headless-pm")
            return

        try:
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="headless-pm",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(
                                prompts_changed=True,
                                resources_changed=True,
                                tools_changed=True
                            ),
                            experimental_capabilities={}
                        )
                    )
                )
        finally:
            # Save token usage on shutdown
            if self.agent_id:
                self.token_tracker.end_session(self.agent_id)
            await self.client.aclose()

            # Clean up subprocess if we started one
            if self._api_process and self._api_process.poll() is None:
                # Only terminate if we can confirm API is idle (no other clients)
                try:
                    # Quick check if API is still healthy and potentially serving other clients
                    response = await self.client.get(f"{self.base_url}/api/v1/agents", timeout=2.0)
                    if response.status_code == 200:
                        agents = response.json()
                        if len(agents) > 1:  # Other agents still active
                            logger.info("Other MCP clients active, leaving API process running")
                            return
                except Exception:
                    pass  # If check fails, proceed with cleanup as failsafe

                logger.info("Cleaning up HeadlessPM API process...")
                self._api_process.terminate()
                try:
                    self._api_process.wait(timeout=5)
                    logger.info("✅ HeadlessPM API process terminated gracefully")
                except subprocess.TimeoutExpired:
                    logger.warning("API process did not stop gracefully, forcing shutdown...")
                    self._api_process.kill()
                    self._api_process.wait()
                    logger.info("✅ HeadlessPM API process force-killed")


async def main():
    """Main entry point."""
    import sys
    import os

    # Get base URL from environment or command line args
    base_url = os.getenv('HEADLESS_PM_URL', 'http://localhost:6969')
    if len(sys.argv) > 1:
        base_url = sys.argv[1]

    logger.info(f"Starting MCP server, connecting to API at {base_url}")
    server = HeadlessPMMCPServer(base_url)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())