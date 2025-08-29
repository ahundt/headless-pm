from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import asyncio
import subprocess
import signal
import threading
import time
from pathlib import Path
from typing import Optional
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

class DashboardOrchestrator:
    """Clean dashboard orchestration with graceful lifecycle management"""
    
    def __init__(self):
        self.dashboard_process = None
        self.dashboard_enabled = os.getenv("DASHBOARD_PORT") is not None
        self.dashboard_port = int(os.getenv("DASHBOARD_PORT", "3001"))
        self.shutdown_event = threading.Event()
        # Detect if running via start.sh (has multiple services) vs UV install (single service)
        self.integrated_mode = os.getenv("HEADLESS_PM_INTEGRATED_MODE", "true").lower() == "true"
        
    def discover_dashboard(self) -> Optional[Path]:
        """Discover dashboard directory in development or UV installation"""
        # Check development layout first
        dashboard_dev = Path("dashboard")
        if dashboard_dev.exists() and (dashboard_dev / "package.json").exists():
            return dashboard_dev
            
        # Check UV installation layout (site-packages/headless_pm/dashboard/)
        import sys
        for site_path in sys.path:
            if "site-packages" in site_path:
                dashboard_uv = Path(site_path) / "headless_pm" / "dashboard"
                if dashboard_uv.exists() and (dashboard_uv / "package.json").exists():
                    return dashboard_uv
                    
        return None
        
    async def start(self):
        """Start dashboard if enabled and available"""
        if not self.dashboard_enabled or not self.integrated_mode:
            return
            
        dashboard_dir = self.discover_dashboard()
        if not dashboard_dir:
            print("ℹ️  Dashboard: Not found - API running standalone")
            return
            
        try:
            # Install Node.js dependencies if needed
            if not (dashboard_dir / "node_modules").exists():
                print("📦 Installing dashboard dependencies...")
                install_result = subprocess.run(
                    ["npm", "install"], 
                    cwd=dashboard_dir, 
                    capture_output=True, 
                    text=True,
                    timeout=120  # 2 minute timeout
                )
                if install_result.returncode != 0:
                    print(f"⚠️  Dashboard dependency installation failed: {install_result.stderr}")
                    return
                    
            # Start dashboard in development mode (integrated with API process)
            print(f"🖥️  Starting integrated dashboard on http://localhost:{self.dashboard_port}")
            env = os.environ.copy()
            env["PORT"] = str(self.dashboard_port)
            
            # Use Next.js turbo mode for faster startup
            self.dashboard_process = subprocess.Popen(
                ["npx", "next", "dev", "--port", str(self.dashboard_port), "--turbopack"],
                cwd=dashboard_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Start monitoring thread
            monitor_thread = threading.Thread(target=self._monitor_dashboard, daemon=True)
            monitor_thread.start()
            
            print(f"✅ Integrated dashboard started on port {self.dashboard_port}")
            
        except Exception as e:
            print(f"⚠️  Dashboard startup failed: {e}")
            
    def _monitor_dashboard(self):
        """Monitor dashboard process and handle failures gracefully"""
        if not self.dashboard_process:
            return
            
        try:
            # Wait for process to complete or shutdown signal
            while not self.shutdown_event.is_set():
                if self.dashboard_process.poll() is not None:
                    # Process ended unexpectedly
                    stdout, stderr = self.dashboard_process.communicate()
                    if stderr:
                        print(f"⚠️  Dashboard process ended: {stderr.strip()}")
                    break
                time.sleep(1)
        except Exception as e:
            print(f"⚠️  Dashboard monitoring error: {e}")
            
    async def stop(self):
        """Gracefully stop dashboard process"""
        if not self.dashboard_process:
            return
            
        print("🛑 Stopping dashboard...")
        self.shutdown_event.set()
        
        try:
            # Send SIGTERM for graceful shutdown
            self.dashboard_process.terminate()
            
            # Wait up to 10 seconds for graceful shutdown
            try:
                self.dashboard_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force kill if not responding
                print("⚠️  Dashboard not responding, forcing shutdown")
                self.dashboard_process.kill()
                self.dashboard_process.wait()
                
            print("✅ Dashboard stopped")
        except Exception as e:
            print(f"⚠️  Dashboard shutdown error: {e}")

# Global dashboard orchestrator instance
dashboard_orchestrator = DashboardOrchestrator()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    create_db_and_tables()
    await health_checker.start()
    await dashboard_orchestrator.start()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        print(f"\n📡 Received signal {signum}, initiating graceful shutdown...")
        asyncio.create_task(dashboard_orchestrator.stop())
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    yield
    
    # Shutdown
    await dashboard_orchestrator.stop()
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
        print("🚀 HeadlessPM: First run detected - auto-configuring...")
        
        # Try to find env-example (local development or installed package)
        env_example = None
        for candidate in [Path("env-example"), Path("../env-example")]:
            if candidate.exists():
                env_example = candidate
                break
        
        if env_example:
            # Create .env from template
            shutil.copy2(env_example, env_file)
            print("✅ Created .env configuration file")
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
            print("✅ Created .env configuration file")
        
        # Initialize database
        try:
            create_db_and_tables()
            print("✅ Database initialized")
        except Exception as e:
            print(f"⚠️  Database initialization: {e}")
        
        # Provide guidance about dashboard
        orchestrator = DashboardOrchestrator()
        dashboard_dir = orchestrator.discover_dashboard()
        if dashboard_dir:
            dashboard_port = orchestrator.dashboard_port
            print(f"🖥️  Dashboard available at http://localhost:{dashboard_port}")
        else:
            print("ℹ️  For complete system with dashboard: git clone repo and use './start.sh'")
        
        print("✅ HeadlessPM API ready! Edit .env file if needed.")
        print("")

def main():
    """Main entry point for headless-pm command"""
    # Enable integrated mode for UV installations
    os.environ["HEADLESS_PM_INTEGRATED_MODE"] = "true"
    
    # Auto-setup on first run
    auto_setup_on_first_run()
    
    # Start server with integrated dashboard
    port = int(os.getenv("SERVICE_PORT", "6969"))
    print(f"🚀 Starting HeadlessPM with integrated dashboard")
    print(f"🌐 API server: http://localhost:{port}")
    print(f"📚 API documentation: http://localhost:{port}/api/v1/docs")
    
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "3001"))
    orchestrator = DashboardOrchestrator()
    if orchestrator.discover_dashboard():
        print(f"🖥️  Web dashboard: http://localhost:{dashboard_port}")
    
    print("\n🛡️  Stop with Ctrl+C")
    print("="*50)
    
    # Configurable reload for development
    reload_mode = os.getenv("HEADLESS_PM_RELOAD", "false").lower() == "true"
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, reload=reload_mode)

if __name__ == "__main__":
    main()

