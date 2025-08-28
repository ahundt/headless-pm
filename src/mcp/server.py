"""
Headless PM MCP Server - Model Context Protocol server for Headless PM integration
Provides standardized interface for Claude Code and other MCP clients.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime

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
from .token_tracker import TokenTracker

# Configure logging
logging.basicConfig(level=logging.INFO, format='[MCP] %(message)s')
logger = logging.getLogger("headless-pm-mcp")


class HeadlessPMMCPServer:
    """MCP Server for Headless PM integration."""

    def __init__(self, base_url: str = "http://localhost:6969"):
        self.base_url = base_url.rstrip('/')
        self.server = Server("headless-pm")
        self.client = httpx.AsyncClient(timeout=30.0)
        self.agent_id: Optional[str] = None
        self.agent_role: Optional[str] = None
        self.agent_skill_level: Optional[str] = None
        self.token_tracker = TokenTracker()

        # Register handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register MCP handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> ListToolsResult:
            """List available tools."""
            logger.info("Starting handle_list_tools() - preparing to create 12 Tool objects")
            
            try:
                # Create first tool with debug logging
                logger.info("Creating Tool 1: register_agent")
                tool1 = Tool(
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
                )
                logger.info(f"Successfully created tool1: {tool1}")
                
                # Create the tools list
                tools_list = [tool1]
                logger.info(f"Created tools_list: {tools_list}")
                logger.info(f"tools_list type: {type(tools_list)}")
                logger.info(f"tools_list[0] type: {type(tools_list[0])}")
                
                # Create ListToolsResult
                logger.info("Creating ListToolsResult...")
                result = ListToolsResult(tools=tools_list)
                logger.info(f"Successfully created ListToolsResult: {result}")
                logger.info(f"ListToolsResult.tools type: {type(result.tools)}")
                logger.info(f"ListToolsResult.tools[0] type: {type(result.tools[0])}")
                
                # Test JSON serialization to see where error occurs
                try:
                    import json
                    logger.info("Testing Tool JSON serialization...")
                    tool_dict = tool1.model_dump()
                    logger.info(f"Tool.model_dump() succeeded: {tool_dict}")
                    
                    logger.info("Testing ListToolsResult JSON serialization...")
                    result_dict = result.model_dump()
                    logger.info(f"ListToolsResult.model_dump() succeeded: {len(str(result_dict))} chars")
                    
                except Exception as serialize_error:
                    logger.error(f"JSON serialization error: {serialize_error}")
                    logger.error(f"Serialization error type: {type(serialize_error)}")
                    import traceback
                    logger.error(f"Serialization traceback: {traceback.format_exc()}")
                
                return result
            
            except Exception as e:
                logger.error(f"Error creating Tool objects in handle_list_tools(): {e}")
                logger.error(f"Exception type: {type(e)}")
                logger.error(f"Exception args: {e.args}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise

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


async def async_main():
    """Async main entry point."""
    import sys
    import os

    # Get base URL from environment or command line args
    base_url = os.getenv('HEADLESS_PM_URL', 'http://localhost:6969')
    if len(sys.argv) > 1:
        base_url = sys.argv[1]

    logger.info(f"Starting MCP server, connecting to API at {base_url}")
    server = HeadlessPMMCPServer(base_url)
    await server.run()


def main():
    """Synchronous main entry point for CLI."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
