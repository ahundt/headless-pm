from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import asyncio
import subprocess
import signal
import atexit
import socket
from pathlib import Path
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

from src.models.database import create_db_and_tables
from src.api.routes import router
from src.api.document_routes import router as document_router
from src.api.service_routes import router as service_router
from src.api.mention_routes import router as mention_router
from src.api.changes_routes import router as changes_router
from src.services.health_checker import health_checker

# Global dashboard process for cleanup
dashboard_process = None

def find_available_port(start_port, max_attempts=50):
    """Find next available port starting from start_port (KISS approach)"""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    # Fallback to original port if all attempts fail
    return start_port

def get_port(env_var=None, default_port=None, auto_discover=True, 
             deterministic_id=None, service_name=None, quiet=False):
    """Universal port allocation with configurable behavior and deterministic mode.
    
    Args:
        env_var: Environment variable to check (e.g., 'SERVICE_PORT') 
        default_port: Default port to try (required if env_var not set)
        auto_discover: If True, find alternative port when default/env port occupied
        deterministic_id: If provided, use deterministic allocation based on this ID
        service_name: Service name for user messages (auto-derived if not provided)
        quiet: If True, suppress user feedback messages
        
    Returns:
        int: Available port number
        
    Usage patterns:
        # Production use (existing patterns preserved)
        port = get_port("SERVICE_PORT", 6969)
        port = get_port("SERVICE_PORT", 6969, auto_discover=False)  # Strict mode
        port = get_port(default_port=8000)
        port = get_port("MCP_PORT", 6968, quiet=True)
        
        # Deterministic allocation (for tests, automation, reproducible deployments)
        port = get_port(default_port=9000, deterministic_id="test-instance-1")
        port = get_port("SERVICE_PORT", 6969, deterministic_id="repo-branch-abc")
    """
    if not env_var and default_port is None:
        raise ValueError("Must provide either env_var or default_port")
    
    # Deterministic allocation mode
    if deterministic_id:
        base_port = default_port or 9000
        # Generate deterministic offset using hash
        hash_offset = abs(hash(deterministic_id)) % 1000
        deterministic_port = base_port + hash_offset
        
        # Check environment variable first (higher priority)
        if env_var and env_var in os.environ:
            requested_port = int(os.environ[env_var])
            if is_port_available(requested_port):
                return requested_port
            elif auto_discover:
                return find_available_port(requested_port)
            else:
                return requested_port
        
        # Use deterministic port with auto-discovery if needed
        if is_port_available(deterministic_port):
            return deterministic_port
        elif auto_discover:
            return find_available_port(deterministic_port)
        else:
            return deterministic_port
    
    service_display = service_name or (env_var.replace('_PORT', '').lower() if env_var else f"port-{default_port}")
    
    # 1. Environment variable takes highest priority
    if env_var and env_var in os.environ:
        requested_port = int(os.environ[env_var])
        if is_port_available(requested_port):
            return requested_port
        elif auto_discover:
            if not quiet:
                print(f"⚠️  {service_display} port {requested_port} from {env_var} is occupied")
            discovered_port = find_available_port(requested_port)
            if not quiet:
                print(f"✅ Using {service_display} port {discovered_port}")
            return discovered_port
        else:
            # Strict mode - return requested port even if occupied (let caller handle)
            return requested_port
    
    # 2. Try default port if provided
    if default_port is not None:
        if is_port_available(default_port):
            return default_port
        elif auto_discover:
            if not quiet:
                print(f"ℹ️  Default {service_display} port {default_port} occupied, discovering alternative...")
            discovered_port = find_available_port(default_port)
            if not quiet:
                print(f"✅ Using {service_display} port {discovered_port}")
            return discovered_port
        else:
            # Strict mode - return default even if occupied
            return default_port
    
    # 3. Should not reach here given input validation
    raise ValueError("No port determination method available")

def is_port_available(port):
    """Check if a specific port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('localhost', port))
            return True
    except OSError:
        return False

def cleanup_dashboard():
    """Clean up dashboard process on exit"""
    global dashboard_process
    if dashboard_process and dashboard_process.poll() is None:
        try:
            dashboard_process.terminate()
            dashboard_process.wait(timeout=5)
        except (subprocess.TimeoutExpired, Exception):
            try:
                dashboard_process.kill()
                dashboard_process.wait()
            except Exception:
                pass
        dashboard_process = None

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    cleanup_dashboard()
    # Re-raise the signal to allow normal shutdown
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

def check_dashboard_health():
    """Lightweight dashboard health check - called only when needed"""
    global dashboard_process
    if not dashboard_process:
        return False
        
    # Non-blocking check if process exited
    if dashboard_process.poll() is not None:
        # Process died - capture any final output for debugging
        try:
            stdout, stderr = dashboard_process.communicate(timeout=1)
            if stderr:
                print(f"⚠️  Dashboard ended unexpectedly: {stderr.decode().strip()[:100]}")
        except:
            print("⚠️  Dashboard process ended unexpectedly")
        dashboard_process = None
        return False
    return True

def start_dashboard_if_available():
    """Start dashboard with lightweight monitoring"""
    global dashboard_process
    
    # Use auto-discovery for dashboard port  
    dashboard_port = get_port("DASHBOARD_PORT", 3001)
    auto_start = os.getenv("HEADLESS_PM_AUTO_DASHBOARD", "true").lower() == "true"
    
    if not auto_start:
        return None
        
    # Check for dashboard directory
    dashboard_dir = Path("dashboard")
    if not dashboard_dir.exists() or not (dashboard_dir / "package.json").exists():
        return None
    
    try:
        # Start dashboard process with error capture
        dashboard_process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(dashboard_port)],
            cwd=dashboard_dir,
            stdout=subprocess.PIPE,  # Capture for error reporting
            stderr=subprocess.PIPE,  # Capture for error reporting
            text=True
        )
        
        # Register cleanup handlers
        atexit.register(cleanup_dashboard)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        return dashboard_process
        
    except Exception as e:
        print(f"⚠️  Dashboard startup failed: {e}")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    create_db_and_tables()
    await health_checker.start()
    
    yield
    
    # Shutdown
    cleanup_dashboard()
    await health_checker.stop()

app = FastAPI(
    title="Headless PM API",
    description="A lightweight project management API for LLM agent coordination",
    version="1.0.0",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)
app.include_router(document_router)
app.include_router(service_router)
app.include_router(mention_router)
app.include_router(changes_router)


@app.get("/", tags=["Root"])
def read_root():
    return {
        "message": "Headless PM API",
        "docs": "/api/v1/docs",
        "health": "ok"
    }

@app.get("/health", tags=["Health"])
def health_check():
    """Enhanced health check endpoint with database and dashboard status"""
    from src.models.database import get_session
    from sqlmodel import select
    from src.models.models import Agent
    from datetime import datetime
    
    try:
        # Test database connection
        db = next(get_session())
        db.exec(select(Agent).limit(1))
        db.close()
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    # Check dashboard health (lightweight - no thread needed)
    dashboard_healthy = check_dashboard_health()
    dashboard_status = "running" if dashboard_healthy else "stopped"
    
    overall_status = "healthy"
    if db_status != "healthy":
        overall_status = "degraded"
    elif not dashboard_healthy and os.getenv("DASHBOARD_PORT"):
        overall_status = "degraded"
    
    return {
        "status": overall_status,
        "service": "headless-pm-api",
        "version": "1.0.0",
        "database": db_status,
        "dashboard": dashboard_status,
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/status", tags=["Health"])
def status_check():
    """Detailed status endpoint with system metrics"""
    from src.models.database import get_session
    from sqlmodel import select, func
    from src.models.models import Agent, Task, Document, Service
    from datetime import datetime, timedelta
    
    try:
        db = next(get_session())
        
        # Get counts
        agent_count = db.exec(select(func.count(Agent.id))).first()
        task_count = db.exec(select(func.count(Task.id))).first()
        document_count = db.exec(select(func.count(Document.id))).first()
        service_count = db.exec(select(func.count(Service.id))).first()
        
        # Get active agents (seen in last 5 minutes)
        five_minutes_ago = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=5)
        active_agents = db.exec(
            select(func.count(Agent.id)).where(Agent.last_seen > five_minutes_ago)
        ).first()
        
        db.close()
        
        return {
            "service": "headless-pm-api",
            "version": "1.0.0",
            "metrics": {
                "total_agents": agent_count,
                "active_agents": active_agents,
                "total_tasks": task_count,
                "total_documents": document_count,
                "total_services": service_count
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "service": "headless-pm-api",
            "version": "1.0.0",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

def auto_setup_on_first_run():
    """Automatically set up HeadlessPM on first run - seamless like numpy"""
    from pathlib import Path
    import shutil
    
    # Check if this is first run (no .env file exists)
    env_file = Path(".env")
    
    if not env_file.exists():
        print("🔧 First run detected - setting up...")
        
        # Try to find env-example (local development or installed package)
        env_example = None
        for candidate in [Path("env-example"), Path("../env-example")]:
            if candidate.exists():
                env_example = candidate
                break
        
        if env_example:
            # Create .env from template
            shutil.copy2(env_example, env_file)
        else:
            # Create minimal .env for clean installation
            env_content = """# HeadlessPM Configuration (auto-generated)
SERVICE_PORT=6969
DASHBOARD_PORT=3001
MCP_PORT=6968
DB_CONNECTION=sqlite
DATABASE_URL=sqlite:///headless-pm.db
API_KEY=your-api-key-here
ENVIRONMENT=development
"""
            env_file.write_text(env_content)
        
        # Initialize database
        try:
            create_db_and_tables()
        except Exception as e:
            print(f"⚠️  Database setup failed: {e}")
            return
        
        print("✅ Setup complete!")
        print()

def main():
    """Main entry point for headless-pm command"""
    # Enable auto-dashboard for UV usage
    os.environ["HEADLESS_PM_AUTO_DASHBOARD"] = "true"
    
    # Auto-setup on first run
    auto_setup_on_first_run()
    
    # Start dashboard if available
    dashboard_process = start_dashboard_if_available()
    
    # Get ports with auto-discovery
    port = get_port("SERVICE_PORT", 6969)
    dashboard_port = get_port("DASHBOARD_PORT", 3001)
    
    # Update environment for dashboard process
    os.environ["DASHBOARD_PORT"] = str(dashboard_port)
    
    print(f"🚀 HeadlessPM starting...")
    print(f"   API: http://localhost:{port}")
    print(f"   Docs: http://localhost:{port}/api/v1/docs")
    
    # Show dashboard info if started
    if dashboard_process and Path("dashboard").exists():
        print(f"   Dashboard: http://localhost:{dashboard_port}")
    
    # Show MCP connection info for easy setup
    print(f"   MCP Server: headless-pm-mcp (connects to http://localhost:{port})")
    print(f"   Stop with Ctrl+C")
    print()
    
    # Start the server
    reload_mode = os.getenv("HEADLESS_PM_RELOAD", "false").lower() == "true"
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=reload_mode)

if __name__ == "__main__":
    main()

