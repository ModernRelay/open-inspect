# Multi-Provider Support Analysis

This document analyzes the changes required to support Bitbucket and GitLab alongside GitHub in Open-Inspect.

## Executive Summary

The recent refactoring (PR #68) introduced a `SourceControlProvider` interface that provides a solid abstraction layer for source control operations. However, **~95% of the implementation is still GitHub-specific**, spread across multiple system layers:

| Layer | GitHub-Specific Files | Key Changes Needed |
|-------|----------------------|-------------------|
| Control Plane | 7+ files | OAuth, App auth, provider factory |
| Modal Infrastructure | 5+ files | Git clone URLs, token generation |
| Web Application | 2 files | NextAuth provider config |
| Shared Utilities | 2 files | Email formats, git user creation |

## Current Architecture

### SourceControlProvider Interface (Already Abstracted)

Location: `packages/control-plane/src/source-control/types.ts`

```typescript
interface SourceControlProvider {
  readonly name: string;

  // User-authenticated operations
  getRepository(auth: SourceControlAuthContext, config: GetRepositoryConfig): Promise<RepositoryInfo>;
  createPullRequest(auth: SourceControlAuthContext, config: CreatePullRequestConfig): Promise<CreatePullRequestResult>;

  // App-authenticated operations
  generatePushAuth(): Promise<GitPushAuthContext>;
}
```

This interface is provider-agnostic and supports:
- Repository information retrieval
- Pull/Merge request creation with draft, labels, and reviewers
- App-level authentication for git push operations
- Error classification for retry logic

### Current GitHub Implementation

Location: `packages/control-plane/src/source-control/providers/github-provider.ts`

The `GitHubSourceControlProvider` class implements the interface using:
- GitHub REST API v3 (`api.github.com`)
- GitHub App installation tokens for push auth
- Bearer token authentication

## Changes Required by Layer

### 1. Control Plane (Cloudflare Workers)

#### A. New Provider Implementations

Create new provider classes:

```
packages/control-plane/src/source-control/providers/
├── github-provider.ts     # Existing
├── gitlab-provider.ts     # NEW
├── bitbucket-provider.ts  # NEW
└── index.ts               # Update factory
```

**GitLab Provider** (`gitlab-provider.ts`):
- API Base: `https://gitlab.com/api/v4` (or self-hosted)
- PR endpoint: `POST /projects/:id/merge_requests`
- State mapping: `opened` → `open`, `merged` → `merged`, `closed` → `closed`
- Uses Project ID (numeric) instead of owner/repo path
- Supports Personal Access Tokens or OAuth

**Bitbucket Provider** (`bitbucket-provider.ts`):
- API Base: `https://api.bitbucket.org/2.0`
- PR endpoint: `POST /repositories/{workspace}/{repo}/pullrequests`
- State mapping: `OPEN` → `open`, `MERGED` → `merged`, `DECLINED` → `closed`
- Uses workspace/repo-slug identifiers
- Supports App Passwords or OAuth

#### B. Authentication Updates

**Current GitHub OAuth** (`packages/control-plane/src/auth/github.ts`):
- Token endpoint: `https://github.com/login/oauth/access_token`
- Scopes: `read:user user:email repo`
- Refresh token support

**Required for GitLab**:
- Token endpoint: `https://gitlab.com/oauth/token`
- Scopes: `read_user api`
- Different refresh token flow (PKCE support)

**Required for Bitbucket**:
- Token endpoint: `https://bitbucket.org/site/oauth2/access_token`
- Scopes: `repository:write pullrequest:write account`
- Client credentials grant for app auth

#### C. App Authentication

**Current GitHub App** (`packages/control-plane/src/auth/github-app.ts`):
- JWT signed with private key
- Exchange JWT for installation token
- Single installation ID (single-tenant)

**GitLab Alternative**:
- Deploy keys (read-only) or Project Access Tokens
- Group-level access tokens for multi-repo
- No direct equivalent to GitHub Apps

**Bitbucket Alternative**:
- App Passwords (user-scoped)
- Repository Access Keys (read-only)
- Workspace access tokens (Bitbucket Cloud)

#### D. Provider Factory & Selection

Update `packages/control-plane/src/source-control/providers/index.ts`:

```typescript
export function createSourceControlProvider(
  providerType: "github" | "gitlab" | "bitbucket",
  config: ProviderConfig
): SourceControlProvider {
  switch (providerType) {
    case "github":
      return new GitHubSourceControlProvider(config.github);
    case "gitlab":
      return new GitLabSourceControlProvider(config.gitlab);
    case "bitbucket":
      return new BitbucketSourceControlProvider(config.bitbucket);
  }
}
```

#### E. Session & Router Updates

Files to update:
- `packages/control-plane/src/router.ts` - Add provider parameter to session creation
- `packages/control-plane/src/session/durable-object.ts` - Store provider type, use correct provider
- `packages/control-plane/src/types.ts` - Add `provider` field to session types

### 2. Modal Infrastructure (Python)

#### A. Git Clone URLs

**Current** (`packages/modal-infra/src/sandbox/entrypoint.py:112-115`):
```python
if self.github_app_token:
    clone_url = f"https://x-access-token:{self.github_app_token}@github.com/{self.repo_owner}/{self.repo_name}.git"
else:
    clone_url = f"https://github.com/{self.repo_owner}/{self.repo_name}.git"
```

**Required Changes**:
```python
def get_clone_url(provider: str, owner: str, name: str, token: str | None) -> str:
    if provider == "github":
        host = "github.com"
        auth_format = f"x-access-token:{token}@" if token else ""
    elif provider == "gitlab":
        host = "gitlab.com"  # or self-hosted URL
        auth_format = f"oauth2:{token}@" if token else ""
    elif provider == "bitbucket":
        host = "bitbucket.org"
        auth_format = f"x-token-auth:{token}@" if token else ""

    return f"https://{auth_format}{host}/{owner}/{name}.git"
```

Files to update:
- `packages/modal-infra/src/sandbox/entrypoint.py` - Clone URL generation
- `packages/modal-infra/src/sandbox/bridge.py` - Token handling
- `packages/modal-infra/src/auth/github_app.py` - Rename or create provider-specific modules

#### B. Token Generation

Create provider-agnostic token generation:

```
packages/modal-infra/src/auth/
├── github_app.py      # Existing
├── gitlab_token.py    # NEW
├── bitbucket_token.py # NEW
└── __init__.py        # Factory function
```

#### C. Environment Variables

Current (GitHub-only):
```
GITHUB_APP_TOKEN
GITHUB_APP_ID
GITHUB_APP_PRIVATE_KEY
GITHUB_APP_INSTALLATION_ID
```

Proposed (provider-agnostic):
```
SOURCE_CONTROL_PROVIDER=github|gitlab|bitbucket
SOURCE_CONTROL_TOKEN         # Push auth token
SOURCE_CONTROL_HOST          # For self-hosted instances

# Provider-specific (still needed for token generation)
GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, GITHUB_APP_INSTALLATION_ID
GITLAB_PAT or GITLAB_DEPLOY_TOKEN
BITBUCKET_APP_PASSWORD or BITBUCKET_ACCESS_TOKEN
```

### 3. Web Application (Next.js)

#### A. NextAuth Configuration

**Current** (`packages/web/src/lib/auth.ts`):
```typescript
import GitHubProvider from "next-auth/providers/github";

providers: [
  GitHubProvider({
    clientId: process.env.GITHUB_CLIENT_ID!,
    clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    authorization: { params: { scope: "read:user user:email repo" } },
  }),
]
```

**Required Changes**:
```typescript
import GitHubProvider from "next-auth/providers/github";
import GitLabProvider from "next-auth/providers/gitlab";
import AtlassianProvider from "next-auth/providers/atlassian"; // For Bitbucket

const providers = [];

if (process.env.GITHUB_CLIENT_ID) {
  providers.push(GitHubProvider({...}));
}
if (process.env.GITLAB_CLIENT_ID) {
  providers.push(GitLabProvider({
    clientId: process.env.GITLAB_CLIENT_ID,
    clientSecret: process.env.GITLAB_CLIENT_SECRET,
    authorization: { params: { scope: "read_user api" } },
  }));
}
if (process.env.BITBUCKET_CLIENT_ID) {
  providers.push(AtlassianProvider({...})); // or custom Bitbucket provider
}
```

#### B. User Profile Normalization

Different providers return different user profile structures:

| Field | GitHub | GitLab | Bitbucket |
|-------|--------|--------|-----------|
| ID | `id` (number) | `id` (number) | `account_id` (string) |
| Username | `login` | `username` | `username` |
| Name | `name` | `name` | `display_name` |
| Email | Separate API call | `email` | `email` |
| Avatar | `avatar_url` | `avatar_url` | `links.avatar.href` |

#### C. Web Links

**Current** (`packages/web/src/components/sidebar/metadata-section.tsx:124`):
```typescript
href={`https://github.com/${repoOwner}/${repoName}`}
```

**Required**:
```typescript
function getRepoUrl(provider: string, owner: string, name: string): string {
  switch (provider) {
    case "github": return `https://github.com/${owner}/${name}`;
    case "gitlab": return `https://gitlab.com/${owner}/${name}`;
    case "bitbucket": return `https://bitbucket.org/${owner}/${name}`;
  }
}
```

### 4. Shared Utilities

#### A. Email Generation

**Current** (`packages/shared/src/git.ts`):
```typescript
export function generateNoreplyEmail(userId: number, login: string): string {
  return `${userId}+${login}@users.noreply.github.com`;
}
```

**Required Changes**:
```typescript
export function generateNoreplyEmail(
  provider: string,
  userId: number | string,
  login: string
): string {
  switch (provider) {
    case "github":
      return `${userId}+${login}@users.noreply.github.com`;
    case "gitlab":
      // GitLab doesn't have noreply emails - use primary email
      return null; // Or fetch from API
    case "bitbucket":
      // Bitbucket doesn't have noreply emails - use primary email
      return null;
  }
}
```

#### B. Git User Creation

Update `createGitUser()` to accept provider context and handle provider-specific email formats.

### 5. Infrastructure & Configuration

#### A. Terraform Updates

`terraform/environments/production/main.tf`:

```hcl
# Add provider selection
variable "source_control_provider" {
  description = "Source control provider: github, gitlab, or bitbucket"
  default     = "github"
}

# Provider-specific secrets
variable "gitlab_pat" {
  description = "GitLab Personal Access Token"
  default     = ""
  sensitive   = true
}

variable "bitbucket_app_password" {
  description = "Bitbucket App Password"
  default     = ""
  sensitive   = true
}

# Update Modal secrets
module "modal_app" {
  secrets = {
    "source-control" = {
      PROVIDER = var.source_control_provider
      # Include provider-specific tokens based on selection
    }
  }
}
```

#### B. Environment Variables

`.env.example` updates:
```bash
# Source Control Provider (github, gitlab, bitbucket)
SOURCE_CONTROL_PROVIDER=github

# GitHub (if using GitHub)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_APP_INSTALLATION_ID=

# GitLab (if using GitLab)
GITLAB_CLIENT_ID=
GITLAB_CLIENT_SECRET=
GITLAB_PAT=
GITLAB_INSTANCE_URL=https://gitlab.com

# Bitbucket (if using Bitbucket)
BITBUCKET_CLIENT_ID=
BITBUCKET_CLIENT_SECRET=
BITBUCKET_APP_PASSWORD=
BITBUCKET_WORKSPACE=
```

## API Comparison

### Repository Info

| Operation | GitHub | GitLab | Bitbucket |
|-----------|--------|--------|-----------|
| Get repo | `GET /repos/{owner}/{repo}` | `GET /projects/{id}` | `GET /repositories/{workspace}/{slug}` |
| Default branch | `default_branch` | `default_branch` | `mainbranch.name` |
| Private flag | `private` | `visibility !== "public"` | `is_private` |

### Pull/Merge Request Creation

| Field | GitHub | GitLab | Bitbucket |
|-------|--------|--------|-----------|
| Endpoint | `POST /repos/.../pulls` | `POST /projects/.../merge_requests` | `POST /repositories/.../pullrequests` |
| Title | `title` | `title` | `title` |
| Description | `body` | `description` | `description` |
| Source branch | `head` | `source_branch` | `source.branch.name` |
| Target branch | `base` | `target_branch` | `destination.branch.name` |
| Draft | `draft: true` | `draft: true` (GitLab 15.0+) | Not supported |
| Labels | Separate API call | `labels` parameter | Not natively supported |
| Reviewers | Separate API call | `reviewer_ids` parameter | `reviewers` array |

## Migration Strategy

### Phase 1: Interface Refinement
1. Review and finalize `SourceControlProvider` interface
2. Add provider type to session and repository models
3. Create provider factory with configuration

### Phase 2: GitLab Implementation
1. Implement `GitLabSourceControlProvider`
2. Add GitLab OAuth to web app
3. Update Modal for GitLab clone URLs
4. Test end-to-end GitLab flow

### Phase 3: Bitbucket Implementation
1. Implement `BitbucketSourceControlProvider`
2. Add Bitbucket OAuth to web app
3. Update Modal for Bitbucket clone URLs
4. Test end-to-end Bitbucket flow

### Phase 4: Multi-Provider Support
1. Allow multiple providers in single deployment
2. Per-session provider selection
3. UI updates for provider selection

## Challenges & Considerations

### 1. App Authentication Differences

GitHub Apps provide a robust mechanism for repository access without user tokens. GitLab and Bitbucket don't have direct equivalents:

- **GitLab**: Use Project/Group Access Tokens or Deploy Tokens
- **Bitbucket**: Use App Passwords or Repository Access Keys

This may require storing additional credentials per-repository or workspace.

### 2. Self-Hosted Instances

GitLab and Bitbucket both support self-hosted deployments:
- Need configurable API base URLs
- SSL certificate handling
- Network access from Modal sandboxes

### 3. Rate Limiting

Each provider has different rate limits:
- GitHub: 5,000 requests/hour (authenticated)
- GitLab: 2,000 requests/minute (authenticated)
- Bitbucket: 1,000 requests/hour

May need provider-specific throttling.

### 4. Webhook Integration (Future)

If adding webhook support for PR events:
- GitHub: Well-documented webhooks
- GitLab: System hooks or project webhooks
- Bitbucket: Webhooks with different payload formats

## Files to Modify Summary

### Control Plane
- `src/source-control/providers/gitlab-provider.ts` (NEW)
- `src/source-control/providers/bitbucket-provider.ts` (NEW)
- `src/source-control/providers/index.ts` (UPDATE)
- `src/source-control/providers/types.ts` (UPDATE)
- `src/auth/gitlab.ts` (NEW)
- `src/auth/bitbucket.ts` (NEW)
- `src/router.ts` (UPDATE)
- `src/session/durable-object.ts` (UPDATE)
- `src/types.ts` (UPDATE)

### Modal Infrastructure
- `src/sandbox/entrypoint.py` (UPDATE)
- `src/sandbox/bridge.py` (UPDATE)
- `src/auth/gitlab_token.py` (NEW)
- `src/auth/bitbucket_token.py` (NEW)
- `src/web_api.py` (UPDATE)

### Web Application
- `src/lib/auth.ts` (UPDATE)
- `src/components/sidebar/metadata-section.tsx` (UPDATE)

### Shared
- `src/git.ts` (UPDATE)
- `src/types.ts` (UPDATE)

### Infrastructure
- `terraform/environments/production/main.tf` (UPDATE)
- `terraform/environments/production/variables.tf` (UPDATE)
