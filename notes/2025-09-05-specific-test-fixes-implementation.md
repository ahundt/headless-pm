# Specific Test Fixes Implementation Plan

**Date**: 2025-09-05  
**Context**: Post-rollback targeted fixes for remaining failing tests

## Test Execution Order Analysis

Based on pytest collection and "F..." pattern:

1. **test_auto_start_when_no_api_running** - FAILING (F)
2. **test_command_discovery** - PASSING (.)  
3. **test_connect_to_existing_api** - PASSING (.)
4. **test_process_cleanup_on_shutdown** - PASSING or FAILING (.)
5. **test_recovery_after_api_crash** - Status pending
6. **test_unit_auto_discovery_logic** - Status pending
7. **test_api_functionality_with_http_client** - PASSING (verified individually)
8. **test_multiple_mcp_clients_scenario** - Status pending
9. **test_api_endpoint_comprehensive_functionality** - PASSING (verified individually)

## Specific Fix Plans

### Fix 1: test_auto_start_when_no_api_running 
**File**: `tests/test_mcp_autodiscovery.py:113-190`  
**Issue**: First test in suite fails, likely due to stdin/process handling

**Current Code Analysis**:
```python
# Lines 118-126: MCP process startup
mcp_process = subprocess.Popen([
    sys.executable, "-m", "src.mcp.server"  # Module execution
], 
stdin=subprocess.PIPE,  # Has stdin
stdout=subprocess.PIPE, 
stderr=subprocess.PIPE,
text=True,
cwd=Path(__file__).parent.parent,
env={**os.environ, "SERVICE_PORT": "6969"}
)
```

**Original Working Code (77899ec)**:
```python
mcp_process = subprocess.Popen([
    "python", str(mcp_server_path)  # File execution  
], 
stdin=subprocess.PIPE,
stdout=subprocess.PIPE, 
stderr=subprocess.PIPE,
text=True,
cwd=mcp_server_path.parent.parent.parent,
env={**os.environ, "SERVICE_PORT": "6969"}
)
```

**Problem**: Module execution + stdin causes different behavior than file execution + stdin

**Solution Options**:
A. Remove stdin from this test (like other passing tests)  
B. Add proper JSON-RPC initialization for module execution
C. Revert to file execution for this specific test

**Recommended**: Option A - Remove stdin, simplest fix matching other working tests

### Fix 2: test_process_cleanup_on_shutdown
**File**: `tests/test_mcp_autodiscovery.py:269-377`  
**Issue**: Process cleanup timing issues

**Implementation**:
1. Check if test uses port 7879 correctly
2. Verify API startup wait logic  
3. Ensure proper cleanup validation timing
4. Add specific process termination checks

### Fix 3: test_recovery_after_api_crash  
**File**: `tests/test_mcp_autodiscovery.py:376-451`
**Issue**: API crash simulation and recovery

**Implementation**:
1. Verify pkill command works with port 6969: `pkill -f "uvicorn.*6969"`
2. Check API down validation timing
3. Ensure recovery MCP server can start new API
4. Add proper process lifecycle validation

## Implementation Commands

### Test Individual Fixes
```bash
# Test specific fix
source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_auto_start_when_no_api_running -xvs

# Expected outcome: PASSED (target: <20 seconds)
```

### Consistency Validation  
```bash
# Run MCP suite 3 times
for i in {1..3}; do
  echo "=== Run $i ==="
  source venv/bin/activate && python -m pytest tests/test_mcp_autodiscovery.py --tb=no -q | tail -1
done

# Target: <2 test variance between runs
# Example acceptable: 
# Run 1: 9 passed, 2 failed
# Run 2: 9 passed, 2 failed  
# Run 3: 8 passed, 3 failed (variance = 1)
```

### Full Suite Baseline
```bash
source venv/bin/activate && python -m pytest tests/ --tb=no -q | tail -1

# Record exact metrics: X passed, Y failed, Z skipped
```

## Success Criteria

### Individual Test Success
- **Acceptance**: `pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::<test_name> --tb=no -q` shows "1 passed"
- **Timing**: Test completes in <20 seconds  
- **Reliability**: Passes 3 times consecutively

### Suite Consistency Success  
- **Variance**: <2 test difference between consecutive runs
- **Pattern**: Consistent pass/fail pattern (not random)
- **Timing**: Suite completes in <120 seconds

### Full Integration Success (Ultimate Goal)
- **Target**: 140 passed, 0 failed, 0 skipped
- **Validation**: 5 consecutive identical runs
- **Command**: `for i in {1..5}; do python -m pytest tests/ --tb=no | tail -1; done`

## Current Implementation Phase

**Status**: Working on foundation layer - individual test reliability  
**Next**: Address remaining specific failures with targeted fixes  
**Goal**: Establish consistent baseline before targeting 140/140 ultimate success