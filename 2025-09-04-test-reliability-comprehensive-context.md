# HeadlessPM Test Reliability Issue - Complete Context

**Date**: 2025-09-04  
**Repository**: https://github.com/madviking/headless-pm.git  
**Branch**: `uv-integration-setup`  
**Problem**: Tests pass inconsistently, preventing reliable 140 passed, 0 failed, 0 skipped target

## Problem Statement (Concrete)

**Target**: 140 tests passed, 0 failed, 0 skipped  
**Current Reality**: Results vary between runs on identical code  
**Evidence from concurrent test runs at 2025-09-04T05:05:03**: 
- **Run fd4758**: `137 passed, 3 skipped, 225 warnings in 41.88s`
- **Run dbef6f**: `2 failed, 138 passed, 199 warnings in 91.92s`  
- **Pattern**: `s..s...s...` (skip, pass, pass, skip, pass, pass, pass, skip...)
- **Failing tests**: `test_backend_dev_scenarios`, `test_multiple_mcp_clients_scenario`

## Repository Structure and Key Files

### **Core Application Files**
```
headless-pm/
├── src/
│   ├── mcp/
│   │   ├── server.py              # MCP server with multi-client coordination
│   │   └── token_tracker.py       # Usage tracking (has deprecation warnings)
│   ├── utils/
│   │   ├── __init__.py
│   │   └── atomic_file_ops.py     # NEW: Atomic file operations utility
│   ├── main.py                    # FastAPI app entry point
│   ├── models/database.py         # Database configuration  
│   └── cli/main.py                # CLI commands
├── tests/
│   ├── test_mcp_autodiscovery.py  # Integration tests (main problem area)
│   ├── test_helpers.py            # ServerManager class for test coordination
│   ├── test_race_condition_detector.py  # NEW: Race condition diagnostic tool
│   └── unit/test_*.py             # Unit tests (generally stable)
└── notes/
    ├── 2025-09-04-mcp-coordination-race-condition-analysis.md
    ├── 2025-09-04-atomic-file-ops-exit-lock-design.md  
    └── 2025-09-04-headless-pm-upstream-pr-workflow.md
```

### **Environment and Setup**
- **Platform**: Darwin 23.6.0 (macOS)  
- **Python**: 3.13.7  
- **Virtual Environment**: `venv/` (must be activated: `source venv/bin/activate`)
- **Working Directory**: `/Users/athundt/source/agentic/headless-pm`

### **Test Execution Commands (Concrete)**
```bash
# Full test suite - expect inconsistent results
source venv/bin/activate && python -m pytest tests/ --tb=no
# Expected variations: 137 passed/3 skipped OR 2 failed/138 passed

# Demonstrate race condition - run 3 times 
for i in {1..3}; do
  echo "=== Test Run $i ===" 
  source venv/bin/activate && python -m pytest tests/ --tb=no | tail -1
done
# Will show different results each time

# Specific problem test (sometimes passes, sometimes fails)
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_multiple_mcp_clients_scenario -xvs

# Race condition detector (always passes, reports diagnostics)
source venv/bin/activate && python -m pytest tests/test_race_condition_detector.py::TestRaceConditionDetector::test_reliable_race_condition_detection -v -s

# Manual coordination test (outside pytest)
source venv/bin/activate && env SERVICE_PORT=8888 python -c "
import subprocess, time, httpx, asyncio, os
proc1 = subprocess.Popen(['python', '-m', 'src.mcp'], env={'SERVICE_PORT': '8888', **os.environ})
# Test coordination manually here
"
```

## Technical Root Cause Analysis

### **Primary Issue: Multi-Client Process Coordination**
**File**: `src/mcp/server.py`  
**Problem**: Multiple MCP clients try to coordinate shared API server process  
**Coordination File**: `/tmp/headless_pm_mcp_clients_{port}.json`

**Current Coordination Logic**:
1. `_register_mcp_client()` - Add client to coordination file, return True if should start API
2. `_unregister_mcp_client()` - Remove client, return True if should cleanup API  
3. Signal handlers attempt graceful cleanup but often fail in subprocess environment

### **Secondary Issue: Test State Interference** 
**File**: `tests/test_mcp_autodiscovery.py`  
**Problem**: Tests share port 6969, causing state contamination between tests
**Evidence**: Individual tests pass, full suite shows skips and failures

**Test Pattern Analysis**:
```python
# TestMCPAutoDiscovery methods all use same port
def setup_method(self, method):
    self.server_manager = ServerManager(port=6969)  # <- Shared state
```

### **Tertiary Issue: Database State Pollution**
**File**: Various test files  
**Problem**: Tests share SQLite database files between runs
**Evidence**: `test_backend_dev_scenarios` passes individually, fails in full suite

## Specific Failing Tests and Error Messages

### **Intermittent Failures**
1. **`test_multiple_mcp_clients_scenario`**
   - **Error**: `AssertionError: API should remain running with second client active`
   - **Location**: `tests/test_mcp_autodiscovery.py:464`
   - **Cause**: First client exits before second client fully registers

2. **`test_backend_dev_scenarios`** 
   - **Error**: `requests.exceptions.ConnectionError` or similar
   - **Location**: `tests/test_backend_dev_assignment.py`
   - **Cause**: Database state pollution from previous tests

3. **Various MCP tests**
   - **Error**: `AssertionError: API should have started`
   - **Cause**: Port conflicts and process lifecycle timing issues

## Solutions Implemented (Commits 9ae96b3, 966c03d)

### **✅ Atomic File Operations** (`src/utils/atomic_file_ops.py`)
**Purpose**: Eliminate file corruption during concurrent coordination operations  
**Implementation**: `tempfile.NamedTemporaryFile()` + `os.rename()` for atomic updates  
**Classes**: `AtomicFileOperations`, `ProcessCoordinationLock`, `with_coordination_lock()`

**Key Function**:
```python
@staticmethod
def atomic_json_update(file_path: Path, update_func: Callable[[Dict], Dict], 
                      default_data: Optional[Dict] = None) -> Dict:
    """Atomically update JSON file using tempfile + rename pattern."""
```

### **✅ MCP Server Coordination Fixes** (`src/mcp/server.py`)
**Changes**:
- Replaced file locking with atomic operations in `_register_mcp_client()`
- Added coordination locks to `_unregister_mcp_client()` using exit lock pattern
- Fixed import compatibility for both relative and absolute imports

**Example**:
```python
def _unregister_mcp_client(self) -> bool:
    # Use coordination lock for atomic unregister + cleanup decision
    result = with_coordination_lock(
        f"api_exit_{port}", 
        coordinated_unregister,
        timeout=10,
        client_id=self._client_id
    )
```

### **✅ Test Isolation** (`tests/test_mcp_autodiscovery.py`)
**Change**: Each test method gets unique port (9000-9999 range)  
**Implementation**:
```python
def setup_method(self, method):
    method_hash = abs(hash(f"{self.__class__.__name__}::{method.__name__}")) % 1000
    unique_port = 9000 + method_hash
    self.server_manager = ServerManager(port=unique_port)
```

### **✅ Race Condition Detection** (`tests/test_race_condition_detector.py`)
**Purpose**: Reliable reproduction and diagnosis of coordination issues  
**Features**: 4-phase testing, detailed JSON results, specific technical fixes

## Current Status and Remaining Work

### **Progress Made**
- ✅ **Eliminated test skips** through port isolation
- ✅ **Fixed main race condition** in `test_multiple_mcp_clients_scenario`  
- ✅ **Atomic file operations** prevent coordination file corruption
- ✅ **Comprehensive diagnostic tools** for future debugging

### **Outstanding Issues**
- ❌ **API startup failures** on non-standard ports in isolated tests
- ❌ **Inconsistent test results** - still not achieving 140 passed, 0 failed, 0 skipped
- ❌ **Some process lifecycle timing** issues remain

**Specific Error Pattern**:
```
FAILED tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_connect_to_existing_api - AssertionError: Manual API should have started
FAILED tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_process_cleanup_on_shutdown - AssertionError: API should have started on port 7879
```

## Next Steps for Reliable 140/140 Tests

### **Option 1: Fix API Startup on Non-Standard Ports**
**Investigation needed**: Why does API fail to start on ports other than 6969?  
**Files to check**: `src/main.py` port configuration, uvicorn startup logic  
**Test**: `env SERVICE_PORT=9999 python -m src.main` should work

### **Option 2: Database Isolation**
**Problem**: Tests share database state  
**Solution**: Unique database per test using tempfile  
**Implementation**: Modify test fixtures to use isolated databases

### **Option 3: Process Lifecycle Debugging**
**Problem**: MCP server process management has timing issues  
**Tool**: Use `tests/test_race_condition_detector.py` to identify remaining problems  
**Focus**: Signal handler reliability, subprocess cleanup timing

## Debugging Commands for New Developer

### **Reproduce Race Conditions**
```bash
# Run race condition detector
source venv/bin/activate && python -m pytest tests/test_race_condition_detector.py -v -s

# Test multi-client coordination manually
source venv/bin/activate && env SERVICE_PORT=8888 python -c "
import subprocess, asyncio, httpx, time, json, os
# Start two MCP clients and verify coordination
"

# Run problematic test in isolation  
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_multiple_mcp_clients_scenario -xvs
```

### **Check Coordination State**
```bash
# View coordination files
ls -la /tmp/headless_pm_*

# Check processes
ps aux | grep -E "uvicorn|src.main|mcp"

# Test API on different ports
source venv/bin/activate && env SERVICE_PORT=9999 python -m src.main &
curl http://localhost:9999/health
```

### **Analyze Test Timing**
```bash
# Sequential execution (eliminates parallelism races)
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py -v -x

# With detailed output
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py -v -s --tb=short
```

## Git History Context

**Recent Commits**:
- `966c03d` - Renamed notes files with specific names
- `9ae96b3` - Implemented atomic file operations and test isolation  
- `77899ec` - **FALSE POSITIVE** - Claims "achieve 100% test pass rate" but tests fail intermittently
- `2d5ad7d` - Made MCP tests robust to existing servers

**Key Insight**: Commit `77899ec` was made during a lucky passing run, but the underlying race conditions weren't fixed.

## Environment Variables and Configuration

### **Critical Environment Variables**
- `SERVICE_PORT` - API server port (default: 6969, tests use 9000-9999)  
- `DATABASE_URL` - SQLite database path (default: `sqlite:///headless-pm.db`)
- `HEADLESS_PM_URL` - Override API base URL
- `HEADLESS_PM_NO_AUTOSTART` - Skip auto-start, connection-only mode

### **Test Environment**
- Virtual environment must be activated before all commands
- Tests require actual subprocess spawning (`python -m src.mcp`)  
- Uses real HTTP connections and process management
- No mocking - tests real coordination behavior

## Key Technical Concepts

### **MCP (Model Context Protocol) Server**
- Stdio-based server using `mcp.server.stdio.stdio_server()`
- Requires `stdin=subprocess.PIPE` to prevent immediate exit
- Provides natural language interface for Claude Code integration

### **Multi-Client Coordination Pattern**
- Multiple Claude Code instances can connect to same API
- First client starts API, subsequent clients connect to existing
- Reference counting in coordination file manages API lifecycle  
- API shuts down when last client disconnects

### **Process Lifecycle Management**
- `subprocess.Popen()` for MCP client processes
- Signal handlers (SIGTERM, SIGINT) for graceful shutdown
- Process discovery using `psutil.process_iter()` for PID validation
- Cross-platform file locking (`fcntl.flock()` Unix, `msvcrt.locking()` Windows)

## Concrete Reproduction Steps

1. **Start with clean environment**:
   ```bash
   cd /Users/athundt/source/agentic/headless-pm
   source venv/bin/activate
   ```

2. **Reproduce race condition**:
   ```bash
   # Run tests multiple times - results will vary
   for i in {1..3}; do
     echo "=== Run $i ==="
     python -m pytest tests/ --tb=no | tail -1
   done
   ```

3. **Investigate specific failures**:
   ```bash
   # Use race condition detector
   python -m pytest tests/test_race_condition_detector.py -v -s
   
   # Check coordination files during test
   python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_multiple_mcp_clients_scenario -v -s
   ```

## Success Criteria

**Definition of Success**: 
- Run `python -m pytest tests/ --tb=no` 5 times consecutively  
- All 5 runs must show: `140 passed, 0 failed, 0 skipped`
- No intermittent results, no timing-dependent failures

**Validation Process**:
1. Fix remaining issues
2. Run test suite 5 times: `for i in {1..5}; do python -m pytest tests/ --tb=no | tail -1; done`  
3. Verify all runs show identical results
4. Commit with accurate description of actual test status

**Critical Requirement**: Never commit claiming test success unless verified with multiple consecutive runs.

## Files Requiring Investigation

### **High Priority**
1. **`src/main.py`** - API startup logic, port binding issues
2. **`tests/test_mcp_autodiscovery.py`** - Main failing tests, coordination logic
3. **`src/mcp/server.py`** - Process coordination, signal handling

### **Medium Priority** 
4. **`tests/test_helpers.py`** - ServerManager coordination utilities
5. **Database fixtures** - Shared state causing test pollution
6. **`src/models/database.py`** - Database URL configuration

### **Diagnostic Tools**
- **`tests/test_race_condition_detector.py`** - Comprehensive race condition analysis
- **`2025-09-04-mcp-coordination-race-condition-analysis.md`** - Detailed problem analysis
- **`2025-09-04-atomic-file-ops-exit-lock-design.md`** - Solution design patterns

## Technical Context for New Developer

### **Python Environment Setup**
```bash
# Required setup (one-time)
cd /Users/athundt/source/agentic/headless-pm  
python -m venv venv
source venv/bin/activate
pip install -r setup/requirements.txt

# Every session
source venv/bin/activate
```

### **API Server Startup**
```bash
# Manual API start (for testing)
source venv/bin/activate
python -m src.main  # Starts on port 6969

# Custom port
env SERVICE_PORT=9999 python -m src.main
```

### **MCP Server Testing**  
```bash
# Manual MCP client start
source venv/bin/activate  
env SERVICE_PORT=6969 python -m src.mcp  # Connects to API on 6969

# Custom port for isolation
env SERVICE_PORT=9999 python -m src.mcp
```

### **Process Coordination Files**
- **Location**: `/tmp/headless_pm_mcp_clients_{port}.json`
- **Content**: `{"clients": {"client_id": {"pid": 12345, "timestamp": 1234567890}}}`  
- **Lifecycle**: Created on first client, updated on each client, deleted when empty

### **Signal Handling Context**
- **Problem**: Signal handlers often don't execute in subprocess environment
- **Evidence**: Logs show `"Signal handler didn't run, performing backup cleanup..."`
- **Impact**: Backup cleanup assumes last client, terminates API prematurely

## Current State After Fixes

### **Implemented Solutions**
- ✅ **Atomic file operations** - Prevents coordination file corruption
- ✅ **Process coordination locks** - Serializes exit decisions
- ✅ **Test port isolation** - Each test gets unique port (9000-9999)
- ✅ **Import compatibility** - Works in multiple execution contexts
- ✅ **Comprehensive diagnostics** - Race condition detector tool

### **Remaining Issues**  
- ❌ **API startup failures** on non-standard ports
- ❌ **Process lifecycle timing** issues  
- ❌ **Database state sharing** between tests

### **Test Results Progress**
- **Before fixes**: 100% API startup failure on isolated ports
- **After atomic operations**: API starts successfully, coordination improved  
- **After port isolation**: No more skips, but some startup failures remain
- **Current status**: Closer to target but not yet reliable 140/140

## Development Environment Notes

### **IDE/Editor Setup**
- Code located in `/Users/athundt/source/agentic/headless-pm`
- Virtual environment at `venv/` relative to project root
- Python path includes `src/` directory for imports

### **Dependencies**
- **FastAPI** - REST API framework  
- **SQLModel** - Database ORM
- **httpx** - HTTP client for API calls
- **psutil** - Process management (optional, graceful fallback)
- **pytest** - Testing framework with asyncio support

### **No External Dependencies Added**
- Atomic operations use only standard library (`tempfile`, `json`, `os`)
- Cross-platform compatibility maintained
- No new pip install requirements

## CRITICAL CONCRETE EVIDENCE (Latest Test Runs)

### **Exact Test Results from 2025-09-04T05:05:03 (Same Code, Same Time)**

**Run fd4758** (Bash ID for reference):
```
================ 137 passed, 3 skipped, 225 warnings in 41.88s =================
Pattern: tests/test_mcp_autodiscovery.py s..s...s...
```

**Run dbef6f** (Bash ID for reference):  
```
============ 2 failed, 138 passed, 199 warnings in 91.92s (0:01:31) ============
FAILED tests/test_backend_dev_assignment.py::test_backend_dev_scenarios - req...
FAILED tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_multiple_mcp_clients_scenario
```

### **Exact Commands to Reproduce Inconsistency**
```bash
cd /Users/athundt/source/agentic/headless-pm
source venv/bin/activate

# Run test suite 3 times - results will vary
python -m pytest tests/ --tb=no | tail -1  # Run 1
python -m pytest tests/ --tb=no | tail -1  # Run 2  
python -m pytest tests/ --tb=no | tail -1  # Run 3
# Expect different pass/fail/skip counts each time
```

### **Exact Test Count Analysis**
```
Total test files collected: 140 items
├── tests/test_backend_dev_assignment.py: 1 test
├── tests/test_fork_bomb_prevention.py: 20 tests  
├── tests/test_headless_pm_client.py: 8 tests
├── tests/test_mcp_autodiscovery.py: 11 tests ← MAIN PROBLEM AREA
├── tests/test_race_condition_detector.py: 3 tests ← NEW DIAGNOSTIC TOOL
├── tests/test_task_assignment_fix.py: 2 tests
├── tests/unit/test_api_routes.py: 21 tests
├── tests/unit/test_database.py: 5 tests
├── tests/unit/test_mcp_server.py: 25 tests
├── tests/unit/test_models.py: 16 tests
├── tests/unit/test_schemas.py: 17 tests
└── tests/unit/test_services.py: 14 tests

Target: All 140 must pass in single run, 5 consecutive times
Current: 137-138 pass, 0-2 fail, 0-3 skip (inconsistent)
```

### **Exact Success Validation Process**
```bash
# Must pass 5 times consecutively before claiming success
cd /Users/athundt/source/agentic/headless-pm
source venv/bin/activate

for i in {1..5}; do
  echo "=== Validation Run $i ==="
  result=$(python -m pytest tests/ --tb=no 2>/dev/null | tail -1)
  echo "$result"
  if [[ "$result" != *"140 passed"* ]] || [[ "$result" == *"failed"* ]] || [[ "$result" == *"skipped"* ]]; then
    echo "❌ FAILED: Inconsistent results detected"
    exit 1
  fi
done
echo "✅ SUCCESS: All 5 runs passed with 140 passed, 0 failed, 0 skipped"
```

**CRITICAL**: Never commit claiming test success without this validation process.

This document provides everything needed for a new developer to understand the race condition issues and continue working toward the 140 passed, 0 failed, 0 skipped goal.

## Update 2025-09-05: Debugging Progress

Applied systematic debugging using multithreading, multiprocess, and lock contention expertise.

### Issues Identified and Fixed:
1. **Logger initialization order** (`src/mcp/server.py:45-47`): Logger used before definition caused import errors
2. **MCP command discovery** (`src/mcp/server.py:484-514`): Global commands ignored SERVICE_PORT environment 
3. **Port isolation** (`tests/test_mcp_autodiscovery.py:84-90`): Tests needed unique ports with working command discovery
4. **Test cleanup design** (`tests/test_headless_pm_client.py:129-164`): PM agents need separate admin for proper cleanup

### Current Results:
- **Full suite**: 141 passed, 2 failed from 143 tests
- **MCP autodiscovery**: 10 passed, 1 failed (improved from ~5/11)  
- **Individual tests**: Pass reliably with proper port isolation
- **Consistency**: Same results across multiple runs (not intermittent)

### Remaining Work:
- Fix `test_race_condition_detector.py::test_coordination_file_atomicity`
- Investigate intermittent `test_api_functionality_with_http_client` in suite context
- Validate 5 consecutive identical runs for full reliability