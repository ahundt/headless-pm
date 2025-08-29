from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import asyncio
import subprocess
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

def start_dashboard_if_available():
    """Simple dashboard startup - no complex orchestration"""
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "3001"))
    auto_start = os.getenv("HEADLESS_PM_AUTO_DASHBOARD", "true").lower() == "true"
    
    if not auto_start or not os.getenv("DASHBOARD_PORT"):
        return None
        
    # Check for dashboard directory
    dashboard_dir = Path("dashboard")
    if not dashboard_dir.exists() or not (dashboard_dir / "package.json").exists():
        return None
    
    try:
        # Start dashboard as simple background process
        return subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(dashboard_port)],
            cwd=dashboard_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    create_db_and_tables()
    await health_checker.start()
    
    yield
    
    # Shutdown
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
    """Enhanced health check endpoint with database status"""
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
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "headless-pm-api",
        "version": "1.0.0",
        "database": db_status,
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
    
    # Start server
    port = int(os.getenv("SERVICE_PORT", "6969"))
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "3001"))
    
    print(f"🚀 HeadlessPM starting...")
    print(f"   API: http://localhost:{port}")
    print(f"   Docs: http://localhost:{port}/api/v1/docs")
    
    # Show dashboard info if started
    if dashboard_process and Path("dashboard").exists():
        print(f"   Dashboard: http://localhost:{dashboard_port}")
    
    print(f"   Stop with Ctrl+C")
    print()
    
    # Start the server
    reload_mode = os.getenv("HEADLESS_PM_RELOAD", "false").lower() == "true"
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=reload_mode)

if __name__ == "__main__":
    main()

