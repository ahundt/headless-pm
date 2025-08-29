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

def get_safe_port(env_var, default_port):
    """Get port with environment override or safe auto-discovery"""
    if env_var in os.environ:
        # Respect explicit environment setting
        return int(os.environ[env_var])
    else:
        # Use auto-discovery for multi-project safety
        return find_available_port(default_port)

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
    
    # Use safe port discovery if DASHBOARD_PORT not explicitly set
    dashboard_port = get_safe_port("DASHBOARD_PORT", 3001)
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
    
    # Get safe ports with auto-discovery for multi-project support
    port = get_safe_port("SERVICE_PORT", 6969)
    dashboard_port = get_safe_port("DASHBOARD_PORT", 3001)
    
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

