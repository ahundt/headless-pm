# GitHub Fork and Branch Setup Plan

**Date**: 2025-09-04  
**Purpose**: Create fork and feature branch for submitting UV integration and multi-client coordination PR to upstream repository

## Repository Context

- **Upstream repository**: `https://github.com/madviking/headless-pm.git` 
- **Current working directory**: `/Users/athundt/source/agentic/headless-pm`
- **Current branch**: `main`
- **Modified files**: 11 files changed (see git status)
- **PR content**: Comprehensive UV integration with multi-client MCP coordination

## Fork and Branch Strategy

### Phase 1: Setup Fork
```bash
# 1. Fork repository to personal GitHub account
gh repo fork madviking/headless-pm --clone=false

# 2. Add fork as new remote
gh repo view --json url | jq -r '.url'  # Get your fork URL
git remote add fork <your_fork_url>     # Will be provided by gh command

# 3. Verify remotes are configured
git remote -v
# Should show:
# origin  https://github.com/madviking/headless-pm.git
# fork    https://github.com/YOUR_USERNAME/headless-pm.git
```

### Phase 2: Create Feature Branch
```bash
# 1. Create descriptive feature branch
git checkout -b feature/uv-integration-multi-client-coordination

# 2. Verify current changes are ready
git status
git diff --staged  # Should show all intended changes

# 3. Commit current changes with proper message
git add .
git commit -m "$(cat PR_DESCRIPTION.md | head -1 | sed 's/# //')" -m "$(cat PR_DESCRIPTION.md)"

# 4. Push feature branch to fork
git push -u fork feature/uv-integration-multi-client-coordination
```

### Phase 3: Create Pull Request
```bash
# 1. Create PR from feature branch to upstream main
gh pr create \
  --repo madviking/headless-pm \
  --base main \
  --head YOUR_USERNAME:feature/uv-integration-multi-client-coordination \
  --title "UV Integration with Enhanced MCP Auto-Discovery and Multi-Client Coordination" \
  --body-file PR_DESCRIPTION.md

# 2. Verify PR was created
gh pr view --repo madviking/headless-pm
```

## Taskshow Integration Updates

### Update .mcp.json for Fork Workflow
```bash
# Update taskshow configuration to point to forked repository
cd /Users/athundt/source/agentic/taskshow

# Edit .mcp.json to reference the fork for development
# Update HEADLESS_PM_PATH to point to fork working directory
# This ensures taskshow continues working with our fork
```

### Environment Variables Setup
```bash
# Add to taskshow .env or environment
export HEADLESS_PM_PATH="/Users/athundt/source/agentic/headless-pm"
export HEADLESS_PM_REPO="https://github.com/YOUR_USERNAME/headless-pm.git"
export HEADLESS_PM_UPSTREAM="https://github.com/madviking/headless-pm.git"
```

## Quality Assurance Checklist

### Pre-Submission Verification
- [ ] All tests pass: `source venv/bin/activate && python -m pytest tests/`
- [ ] Test count verification: Confirm 140 tests passed, 0 failed, 0 skipped
- [ ] Multi-client coordination works: Verify with manual test script
- [ ] UV installation works: `uv pip install git+<fork_url>`
- [ ] Documentation is accurate: Review README.md changes
- [ ] No secrets in commit: Check for API keys, passwords, tokens

### Post-Fork Verification
- [ ] Fork relationship correct: `gh repo view --json parent`
- [ ] Feature branch pushed: `gh pr list --repo madviking/headless-pm`
- [ ] Taskshow still functional: `taskshow start` works with fork
- [ ] MCP integration preserved: `taskshow mcp serve` still works

## Risk Assessment and Mitigation

### Potential Issues
1. **Fork sync divergence**: Fork may fall behind upstream
   - **Mitigation**: Regular sync with `gh repo sync YOUR_USERNAME/headless-pm`

2. **Taskshow path dependencies**: Taskshow .mcp.json hardcoded paths
   - **Mitigation**: Update HEADLESS_PM_PATH environment variables

3. **PR review feedback**: Upstream may request changes
   - **Mitigation**: Keep feature branch active for iterative updates

4. **Merge conflicts**: Upstream changes during review
   - **Mitigation**: Rebase feature branch before final merge

## Success Criteria

- [ ] Fork created and accessible via GitHub
- [ ] Feature branch contains all changes from current working directory
- [ ] PR submitted to upstream repository with complete description
- [ ] Taskshow continues working with fork configuration
- [ ] All tests pass in forked environment
- [ ] No disruption to current development workflow

## Rollback Plan

If issues arise:
```bash
# Restore to original state
git remote remove fork
git checkout main
git branch -D feature/uv-integration-multi-client-coordination

# Restore taskshow configuration
# Reset .mcp.json to original upstream references
```

## Next Steps After Approval

1. Execute Phase 1: Fork setup
2. Execute Phase 2: Feature branch and commit
3. Execute Phase 3: Pull request creation
4. Update taskshow configuration for fork workflow
5. Monitor PR for review feedback and respond promptly

**Note**: This plan maintains current development workflow while enabling contribution to upstream repository.