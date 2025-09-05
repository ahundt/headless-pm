# Port 6969 vs Unique Ports Investigation

**Date**: 2025-09-05  
**Context**: Debugging HeadlessPM test reliability issues

## Key Finding

**Port 6969 works reliably for individual tests, unique ports (9000-9999) cause failures.**

## Evidence

### Working State (Commit 77899ec)
- **Setup**: `self.server_manager = ServerManager(port=6969)`
- **Environment**: `env={**os.environ, "SERVICE_PORT": "6969"}`
- **Result**: Individual tests pass consistently
- **Test Command**: `python -m pytest tests/test_mcp_autodiscovery.py::TestMCPAutoDiscovery::test_api_functionality_with_http_client`
- **Outcome**: ✅ PASSED in 6.82s

### Broken State (Unique Ports)
- **Setup**: `unique_port = 9000 + method_hash; ServerManager(port=unique_port)`
- **Environment**: `env={**os.environ, "SERVICE_PORT": str(self.server_manager.port)}`
- **Result**: Individual tests fail with "API should have started on port XXXX"
- **Example Failure**: Port 9072, 9250, 9920 all fail
- **Outcome**: ❌ FAILED - AssertionError: API should have started

## Empirical Test Results

### MCP Server Startup Test
```bash
# Port 6969: ✅ Works
SERVICE_PORT=6969 python3 -m src.mcp.server
# Result: API starts successfully, responds to health checks

# Port 9999: ✅ Works (when run standalone) 
SERVICE_PORT=9999 python3 -m src.mcp.server
# Result: API starts successfully, responds to health checks

# In test context: ❌ Port 9999 fails
# Result: MCP process dies before API becomes available
```

## Potential Explanations

### Theory 1: Default Port Configuration
- MCP server or coordination logic may have hardcoded assumptions about port 6969
- File coordination paths may be port-specific: `/tmp/headless_pm_mcp_clients_{port}.json`
- Database URL or other config may not adapt properly to different ports

### Theory 2: Process Coordination Race Condition  
- Port 6969 may have different timing characteristics
- Coordination file logic might work differently on default vs custom ports
- Signal handling or cleanup logic could be port-dependent

### Theory 3: Environment Variable Precedence
- MCP server might prioritize different port discovery methods
- get_port() function in src/main.py might behave differently for default vs custom
- Environment variable inheritance could differ between test contexts

## Investigation Plan

### Immediate Actions (Completed)
- ✅ **Rollback to port 6969**: Restore working individual test behavior
- ✅ **Keep functional improvements**: logger fix, sys.executable, module execution
- ✅ **Verify individual tests pass**: Confirm rollback restores functionality

### Future Investigation (When Tests Stabilized)
1. **Compare coordination file behavior**: 
   - Check `/tmp/headless_pm_mcp_clients_6969.json` vs `/tmp/headless_pm_mcp_clients_9999.json`
   - Monitor file creation, updates, and cleanup timing
   
2. **Analyze port discovery logic**:
   - Review `get_port()` function in src/main.py for special 6969 handling
   - Check environment variable precedence in MCP server startup
   
3. **Test coordination timing**:
   - Measure API startup time on port 6969 vs others
   - Check if process coordination has timing dependencies on default port

## Code References

### Port Discovery Logic
- **File**: `src/main.py:40-103` - `get_port()` function with auto-discovery
- **Default**: `port = get_port("SERVICE_PORT", 6969)` at src/main.py:378

### Coordination File Logic  
- **File**: `src/mcp/server.py:559-560` - `f"headless_pm_mcp_clients_{port}.json"`
- **Registration**: `src/mcp/server.py:562-604` - `_register_mcp_client()`
- **Cleanup**: `src/mcp/server.py:606-663` - `_unregister_mcp_client()`

### Test Setup
- **Working**: `ServerManager(port=6969)` in tests/test_mcp_autodiscovery.py:86
- **Broken**: `ServerManager(port=9000+hash)` - unique port allocation

## Current Status

**Solution**: Use port 6969 for test reliability, investigate port behavior separately.  
**Result**: Individual tests now pass consistently with surgical rollback approach.  
**Next Steps**: Validate full test suite consistency, then investigate port behavior in isolated environment.