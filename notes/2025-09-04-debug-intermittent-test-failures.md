# Intermittent Test Failures - Race Condition Analysis

**Date**: 2025-09-04  
**Issue**: Tests pass intermittently, claiming 100% success but actually failing on subsequent runs  
**Status**: CRITICAL - False positive commit made claiming "achieve 100% test pass rate"

## Problem Statement

Tests are exhibiting race conditions causing inconsistent results:
- **Target**: 140 tests passed, 0 failed, 0 skipped  
- **Current Reality**: Sometimes 137 passed/3 skipped, sometimes 138 passed/2 failed
- **False Commit**: `77899ec` claimed success during a lucky passing run

## Evidence of Race Condition

### Parallel Test Run Results (Same Codebase, Same Time)

**Run 1** (quiet mode, bash fd4758):
```
================ 137 passed, 3 skipped, 225 warnings in 41.88s =================
```

**Run 2** (verbose mode, bash dbef6f):  
```
============ 2 failed, 138 passed, 199 warnings in 91.92s (0:01:31) ============
```

**Key Observation**: Same code, different results = race condition

## Failing Tests

### Primary Failure: `test_multiple_mcp_clients_scenario`
**Location**: `tests/test_mcp_autodiscovery.py:464`  
**Error**: `AssertionError: API should remain running with second client active`  
**Root Cause**: Multi-client coordination logic has timing issues

### Secondary Failure: `test_backend_dev_scenarios`  
**Location**: `tests/test_backend_dev_assignment.py`
**Behavior**: Passes when run individually, fails in full suite
**Root Cause**: Likely resource contention or database state pollution

## Technical Analysis

### Multi-Client Coordination Race Condition

The coordination logic in `src/mcp/server.py` uses file-based reference counting:
```python
# /tmp/headless_pm_mcp_clients_{port}.json tracks active clients
# Race condition: File read/write operations not properly atomic
```

**Timing Issue Points**:
1. **Client registration**: Two clients registering simultaneously
2. **Process termination**: First client exit triggers API shutdown before second client updates file
3. **File locking**: Cross-platform file locking may have timing gaps
4. **Process discovery**: PID validation and process creation time checks

### Database State Pollution

`test_backend_dev_scenarios` fails in full suite but passes individually:
- **Cause**: Database state not properly isolated between tests
- **Evidence**: Tests use same database file without proper cleanup
- **Impact**: Later tests see artifacts from earlier tests

## Reproduction Commands

### Reliable Reproduction Method
```bash
# Run multiple times to see inconsistency
for i in {1..5}; do
  echo "=== Test Run $i ==="
  source venv/bin/activate && python -m pytest tests/ --tb=no | tail -1
  echo "---"
done
```

### Specific Failing Test Isolation
```bash
# Test the coordination logic specifically
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_multiple_mcp_clients_scenario -xvs

# Check for race condition patterns
source venv/bin/activate && python -m pytest tests/test_backend_dev_assignment.py::test_backend_dev_scenarios -v
```

### Manual Coordination Test Results

**Port 9999 Test** (clean environment):
```
✅ 1. First client started API: 200
✅ 2. Two clients running: 200  
❌ API died when first client exited: All connection attempts failed
```

**Debug Logs from Port 9999 Test**:
```
[MCP] Registered MCP client mcp_4755_1756938465 (1 total clients)
[MCP] ✅ Successfully started HeadlessPM API
[MCP] Found API server process: PID=4760
[MCP] Signal handler didn't run, performing backup cleanup...
[MCP] Unregistered MCP client mcp_4755_1756938465 (0 remaining clients)
[MCP] Last MCP client - cleaning up API process (PID: 4760)...
[MCP] ✅ HeadlessPM API process terminated gracefully
```

**Issue Identified**: Signal handler not running, backup cleanup immediately terminates API when first client exits, regardless of other clients.

## Root Cause Analysis

### Signal Handler Failure (Primary Issue)
**Location**: `src/mcp/server.py` signal handler registration  
**Problem**: Signal handler not executing, backup cleanup runs instead  
**Evidence**: Log shows `"Signal handler didn't run, performing backup cleanup..."`

**Critical Flow Failure**:
1. First client terminates (SIGTERM/SIGINT)  
2. Signal handler **should** unregister client and check remaining clients
3. Instead: signal handler fails to run
4. Backup cleanup executes: assumes last client, terminates API
5. Second client left running but API dead = test failure

### File-Based Coordination Issues (Secondary)

1. **Signal Handler Race**: Signal handling vs backup cleanup race condition
   ```python
   # Expected: Signal handler unregisters client, checks count
   # Actual: Backup cleanup assumes it's the last client
   ```

2. **Process Discovery Timing**: 
   ```python
   # Process may be discovered correctly but cleanup logic is wrong
   # Shows PID=4760 found but still terminates when 2nd client exists
   ```

3. **Client Count Management**:
   ```python
   # Shows "1 total clients" but should be 2 clients when both running
   # Coordination file not properly tracking concurrent clients
   ```

### Database State Issues

1. **Shared Database**: Tests use same SQLite file
2. **Incomplete Cleanup**: Database state persists between tests  
3. **Timing Sensitivity**: Fast tests may see state from slow tests

## Historical Context

### Previous Working State
- **Commit History**: Need to check when tests were reliably passing
- **Changes Made**: Recent coordination features introduced race conditions
- **Test Environment**: May have worked in different environment conditions

### Git Analysis Needed
```bash
# Find last definitely working commit
git log --oneline | head -10

# Check what changed in recent commits  
git show 77899ec  # The false positive commit
git show 2d5ad7d  # Previous commit
```

## Debugging Approach

### 1. Eliminate Database State Issues
```bash
# Use unique database per test
export DATABASE_URL="sqlite:///test-$(date +%s).db"

# Or use in-memory databases  
export DATABASE_URL="sqlite:///:memory:"
```

### 2. Add Coordination Debugging
```python
# Add logging to coordination functions
import logging
logging.basicConfig(level=logging.DEBUG)

# Log every file operation with timestamp
# Log every client registration/deregistration
# Log every process discovery attempt
```

### 3. Sequential Test Execution
```bash
# Force sequential execution to eliminate timing races
python -m pytest tests/ -x --maxfail=1 -v

# Run with longer delays
SLEEP_BETWEEN_OPERATIONS=1 python -m pytest tests/test_mcp_autodiscovery.py -v
```

## Specific Technical Issues Identified

### Signal Handler Registration Problem
**File**: `src/mcp/server.py`  
**Issue**: Signal handlers (SIGTERM, SIGINT, SIGHUP) not executing  
**Evidence**: Debug log shows `"Signal handler didn't run, performing backup cleanup..."`

**Expected Flow**:
```python
# First client exits → signal handler → unregister client → check remaining count
# If count > 0: keep API running  
# If count = 0: terminate API
```

**Actual Flow**:
```python  
# First client exits → signal handler fails → backup cleanup
# Backup cleanup assumes last client → always terminates API
```

### Client Registration Synchronization  
**Problem**: Two clients may not both register before first exits  
**Evidence**: Logs show "1 total clients" when 2 should be running  
**Race Window**: Client 2 starts after Client 1 already begins exit sequence

### Database State Pollution
**File**: `tests/test_backend_dev_assignment.py`  
**Problem**: Shared SQLite database causes test interdependence  
**Evidence**: Passes alone, fails in full suite

## Immediate Action Items

1. **Stop claiming success**: The current commit `77899ec` is misleading
2. **Fix signal handler**: Make signal handling reliable in subprocess environment  
3. **Fix client registration**: Ensure all clients register before any can exit
4. **Isolate database state**: Use unique database per test to prevent pollution
5. **Commit only when consistently passing**: Run tests 5+ times before claiming success

## Test Environment Context

**Platform**: Darwin 23.6.0 (macOS)  
**Python**: 3.13.7  
**Pytest**: 8.4.1  
**Virtual Environment**: `venv/` (activated before all tests)  
**Working Directory**: `/Users/athundt/source/agentic/headless-pm`  
**Port Usage**: Tests use ports 6969 (main), 9999 (isolation), and others

## Warning Signs of Race Conditions

1. **Different results on identical code**
2. **Tests pass individually but fail in suite** 
3. **Timing-dependent assertions** ("API should remain running")
4. **File-based coordination** with concurrent access
5. **Process lifecycle management** across multiple subprocesses

## Next Steps

1. **Fix the race condition** in multi-client coordination
2. **Isolate test database state** to prevent pollution
3. **Add proper synchronization** to file operations
4. **Verify consistent results** before any claims of success
5. **Update false positive commit** with accurate information

**CRITICAL**: Do not submit PR until tests consistently pass 140/140 with 0 failures, 0 skips across multiple runs.