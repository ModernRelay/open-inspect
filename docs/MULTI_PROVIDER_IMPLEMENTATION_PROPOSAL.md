# Multi-Provider Implementation Proposal

This document proposes concrete code changes to support Bitbucket and GitLab alongside GitHub.

## Overview

The implementation adds multi-provider support through:
1. A `SourceControlProviderType` discriminator across all layers
2. Provider factory pattern in the control plane
3. Provider-aware git operations in Modal infrastructure
4. Multi-provider OAuth in the web app

## 1. Shared Types (`packages/shared`)

### 1.1 New Provider Type Definition

**File: `packages/shared/src/source-control.ts`** (NEW)

```typescript
/**
 * Source control provider types and utilities.
 */

/**
 * Supported source control providers.
 */
export type SourceControlProviderType = "github" | "gitlab" | "bitbucket";

/**
 * Provider-specific configuration for git clone operations.
 */
export interface GitCloneConfig {
  provider: SourceControlProviderType;
  owner: string;
  name: string;
  /** Base URL for self-hosted instances (e.g., "https://gitlab.mycompany.com") */
  baseUrl?: string;
}

/**
 * Get the git host for a provider.
 */
export function getProviderHost(provider: SourceControlProviderType, baseUrl?: string): string {
  if (baseUrl) {
    return new URL(baseUrl).host;
  }
  switch (provider) {
    case "github":
      return "github.com";
    case "gitlab":
      return "gitlab.com";
    case "bitbucket":
      return "bitbucket.org";
  }
}

/**
 * Get the clone URL for a repository.
 *
 * @param config - Clone configuration
 * @param token - Optional authentication token
 * @returns Git clone URL
 */
export function getCloneUrl(config: GitCloneConfig, token?: string): string {
  const host = getProviderHost(config.provider, config.baseUrl);

  if (!token) {
    return `https://${host}/${config.owner}/${config.name}.git`;
  }

  // Each provider uses different token authentication formats
  switch (config.provider) {
    case "github":
      // GitHub uses x-access-token for App tokens and OAuth tokens
      return `https://x-access-token:${token}@${host}/${config.owner}/${config.name}.git`;
    case "gitlab":
      // GitLab uses oauth2 for OAuth tokens, or gitlab-ci-token for CI
      return `https://oauth2:${token}@${host}/${config.owner}/${config.name}.git`;
    case "bitbucket":
      // Bitbucket uses x-token-auth for app passwords/tokens
      return `https://x-token-auth:${token}@${host}/${config.owner}/${config.name}.git`;
  }
}

/**
 * Get the web URL for a repository.
 */
export function getRepoWebUrl(config: GitCloneConfig): string {
  const host = getProviderHost(config.provider, config.baseUrl);
  return `https://${host}/${config.owner}/${config.name}`;
}

/**
 * Get the web URL for a pull/merge request.
 */
export function getPullRequestWebUrl(
  config: GitCloneConfig,
  prNumber: number
): string {
  const host = getProviderHost(config.provider, config.baseUrl);

  switch (config.provider) {
    case "github":
      return `https://${host}/${config.owner}/${config.name}/pull/${prNumber}`;
    case "gitlab":
      return `https://${host}/${config.owner}/${config.name}/-/merge_requests/${prNumber}`;
    case "bitbucket":
      return `https://${host}/${config.owner}/${config.name}/pull-requests/${prNumber}`;
  }
}

/**
 * Provider display names for UI.
 */
export const PROVIDER_DISPLAY_NAMES: Record<SourceControlProviderType, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  bitbucket: "Bitbucket",
};

/**
 * Default provider if none specified.
 */
export const DEFAULT_PROVIDER: SourceControlProviderType = "github";
```

### 1.2 Update Shared Types

**File: `packages/shared/src/types.ts`** (UPDATE)

```typescript
// Add import at top
import type { SourceControlProviderType } from "./source-control";

// Update Session interface
export interface Session {
  id: string;
  title: string | null;
  repoOwner: string;
  repoName: string;
  repoDefaultBranch: string;
  branchName: string | null;
  baseSha: string | null;
  currentSha: string | null;
  opencodeSessionId: string | null;
  status: SessionStatus;
  createdAt: number;
  updatedAt: number;
  /** Source control provider (defaults to "github" for backwards compatibility) */
  provider?: SourceControlProviderType;
}

// Update InstallationRepository interface
export interface InstallationRepository {
  id: number;
  owner: string;
  name: string;
  fullName: string;
  description: string | null;
  private: boolean;
  defaultBranch: string;
  /** Source control provider */
  provider?: SourceControlProviderType;
}

// Update CreateSessionRequest
export interface CreateSessionRequest {
  repoOwner: string;
  repoName: string;
  title?: string;
  /** Source control provider (defaults to "github") */
  provider?: SourceControlProviderType;
}

// Update SessionState
export interface SessionState {
  id: string;
  title: string | null;
  repoOwner: string;
  repoName: string;
  branchName: string | null;
  status: SessionStatus;
  sandboxStatus: SandboxStatus;
  messageCount: number;
  createdAt: number;
  /** Source control provider */
  provider?: SourceControlProviderType;
}
```

### 1.3 Update Git Utilities

**File: `packages/shared/src/git.ts`** (UPDATE)

```typescript
import type { GitUser } from "./types";
import type { SourceControlProviderType } from "./source-control";

// ... existing code ...

/**
 * Generate a noreply email for users who hide their email.
 *
 * Note: GitLab and Bitbucket don't have noreply email services.
 * For those providers, this returns null and callers should use
 * the user's actual email or skip co-author attribution.
 *
 * @param provider - Source control provider
 * @param userId - Provider user ID
 * @param login - Provider username
 * @returns Noreply email or null if provider doesn't support it
 */
export function generateNoreplyEmail(
  provider: SourceControlProviderType,
  userId: number | string,
  login: string
): string | null {
  switch (provider) {
    case "github":
      return `${userId}+${login}@users.noreply.github.com`;
    case "gitlab":
      // GitLab doesn't have noreply emails
      return null;
    case "bitbucket":
      // Bitbucket doesn't have noreply emails
      return null;
  }
}

/**
 * Get the best email for git commit attribution.
 *
 * @param provider - Source control provider
 * @param publicEmail - User's public email (may be null)
 * @param userId - Provider user ID
 * @param login - Provider username
 * @returns Email to use for commits, or null if unavailable
 */
export function getCommitEmail(
  provider: SourceControlProviderType,
  publicEmail: string | null,
  userId: number | string,
  login: string
): string | null {
  if (publicEmail) {
    return publicEmail;
  }
  return generateNoreplyEmail(provider, userId, login);
}

/**
 * Create GitUser from provider profile data.
 */
export function createGitUser(
  provider: SourceControlProviderType,
  login: string,
  name: string | null,
  publicEmail: string | null,
  userId: number | string
): GitUser | null {
  const email = getCommitEmail(provider, publicEmail, userId, login);
  if (!email) {
    // Cannot create git user without email
    return null;
  }
  return {
    name: name || login,
    email,
  };
}

/**
 * Generate a commit message for automated commits.
 */
export function generateCommitMessage(
  action: string,
  description: string,
  sessionId: string,
  provider: SourceControlProviderType = "github"
): string {
  // Only add co-author for GitHub (which has noreply emails)
  const coAuthor = provider === "github"
    ? `\n\nCo-authored-by: Open-Inspect <open-inspect@noreply.github.com>`
    : "";

  return `${action}: ${description}${coAuthor}\nSession-ID: ${sessionId}`;
}
```

## 2. Control Plane (`packages/control-plane`)

### 2.1 Provider Types

**File: `packages/control-plane/src/source-control/providers/types.ts`** (UPDATE)

```typescript
/**
 * Provider-specific types.
 */

import type { GitHubAppConfig } from "../../auth/github-app";
import type { SourceControlProviderType } from "@open-inspect/shared";

/**
 * Configuration for GitHubSourceControlProvider.
 */
export interface GitHubProviderConfig {
  /** GitHub App configuration (required for push auth) */
  appConfig?: GitHubAppConfig;
}

/**
 * Configuration for GitLabSourceControlProvider.
 */
export interface GitLabProviderConfig {
  /** GitLab instance URL (defaults to https://gitlab.com) */
  baseUrl?: string;
  /** Personal Access Token or Deploy Token for push auth */
  accessToken?: string;
}

/**
 * Configuration for BitbucketSourceControlProvider.
 */
export interface BitbucketProviderConfig {
  /** Bitbucket instance URL (defaults to https://bitbucket.org) */
  baseUrl?: string;
  /** App Password or Access Token for push auth */
  accessToken?: string;
  /** Workspace ID (required for Bitbucket Cloud) */
  workspaceId?: string;
}

/**
 * Combined provider configuration.
 */
export interface ProviderConfigs {
  github?: GitHubProviderConfig;
  gitlab?: GitLabProviderConfig;
  bitbucket?: BitbucketProviderConfig;
}
```

### 2.2 Provider Factory

**File: `packages/control-plane/src/source-control/providers/index.ts`** (UPDATE)

```typescript
/**
 * Source control provider factory and exports.
 */

import type { SourceControlProvider } from "../types";
import type { SourceControlProviderType } from "@open-inspect/shared";

// Types
export type {
  GitHubProviderConfig,
  GitLabProviderConfig,
  BitbucketProviderConfig,
  ProviderConfigs,
} from "./types";

// Constants
export { USER_AGENT, GITHUB_API_BASE } from "./constants";

// Providers
export { GitHubSourceControlProvider, createGitHubProvider } from "./github-provider";
export { GitLabSourceControlProvider, createGitLabProvider } from "./gitlab-provider";
export { BitbucketSourceControlProvider, createBitbucketProvider } from "./bitbucket-provider";

/**
 * Create a source control provider based on type.
 *
 * @param providerType - The type of provider to create
 * @param configs - Provider-specific configurations
 * @returns A configured source control provider
 */
export function createSourceControlProvider(
  providerType: SourceControlProviderType,
  configs: ProviderConfigs
): SourceControlProvider {
  switch (providerType) {
    case "github":
      return createGitHubProvider(configs.github || {});
    case "gitlab":
      return createGitLabProvider(configs.gitlab || {});
    case "bitbucket":
      return createBitbucketProvider(configs.bitbucket || {});
    default:
      // TypeScript exhaustiveness check
      const _exhaustive: never = providerType;
      throw new Error(`Unknown provider type: ${providerType}`);
  }
}
```

### 2.3 GitLab Provider (Stub)

**File: `packages/control-plane/src/source-control/providers/gitlab-provider.ts`** (NEW)

```typescript
/**
 * GitLab source control provider implementation.
 */

import type {
  SourceControlProvider,
  SourceControlAuthContext,
  GetRepositoryConfig,
  RepositoryInfo,
  CreatePullRequestConfig,
  CreatePullRequestResult,
  GitPushAuthContext,
} from "../types";
import { SourceControlProviderError } from "../errors";
import type { GitLabProviderConfig } from "./types";
import { USER_AGENT } from "./constants";

const GITLAB_API_BASE = "https://gitlab.com/api/v4";

/**
 * GitLab implementation of SourceControlProvider.
 */
export class GitLabSourceControlProvider implements SourceControlProvider {
  readonly name = "gitlab";

  private readonly baseUrl: string;
  private readonly accessToken?: string;

  constructor(config: GitLabProviderConfig = {}) {
    this.baseUrl = config.baseUrl
      ? `${config.baseUrl}/api/v4`
      : GITLAB_API_BASE;
    this.accessToken = config.accessToken;
  }

  /**
   * Get repository information from GitLab API.
   */
  async getRepository(
    auth: SourceControlAuthContext,
    config: GetRepositoryConfig
  ): Promise<RepositoryInfo> {
    // GitLab uses URL-encoded project path
    const projectPath = encodeURIComponent(`${config.owner}/${config.name}`);

    const response = await fetch(`${this.baseUrl}/projects/${projectPath}`, {
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth.token}`,
        "User-Agent": USER_AGENT,
      },
    });

    if (!response.ok) {
      const error = await response.text();
      throw SourceControlProviderError.fromFetchError(
        `Failed to get repository: ${response.status} ${error}`,
        new Error(error),
        response.status
      );
    }

    const data = (await response.json()) as {
      id: number;
      path: string;
      path_with_namespace: string;
      default_branch: string;
      visibility: string;
      namespace: { path: string };
    };

    return {
      owner: data.namespace.path,
      name: data.path,
      fullName: data.path_with_namespace,
      defaultBranch: data.default_branch,
      isPrivate: data.visibility !== "public",
      providerRepoId: data.id,
    };
  }

  /**
   * Create a merge request on GitLab.
   */
  async createPullRequest(
    auth: SourceControlAuthContext,
    config: CreatePullRequestConfig
  ): Promise<CreatePullRequestResult> {
    const projectPath = encodeURIComponent(config.repository.fullName);

    const requestBody: Record<string, unknown> = {
      title: config.title,
      description: config.body,
      source_branch: config.sourceBranch,
      target_branch: config.targetBranch,
    };

    // GitLab 15.0+ supports draft MRs via title prefix or API flag
    if (config.draft) {
      requestBody.draft = true;
    }

    // GitLab supports labels directly in MR creation
    if (config.labels && config.labels.length > 0) {
      requestBody.labels = config.labels.join(",");
    }

    const response = await fetch(
      `${this.baseUrl}/projects/${projectPath}/merge_requests`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${auth.token}`,
          "User-Agent": USER_AGENT,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      }
    );

    if (!response.ok) {
      const error = await response.text();
      throw SourceControlProviderError.fromFetchError(
        `Failed to create MR: ${response.status} ${error}`,
        new Error(error),
        response.status
      );
    }

    const data = (await response.json()) as {
      iid: number;
      web_url: string;
      state: string;
      draft: boolean;
      source_branch: string;
      target_branch: string;
    };

    // Map GitLab state to our state type
    let state: CreatePullRequestResult["state"];
    if (data.draft) {
      state = "draft";
    } else if (data.state === "merged") {
      state = "merged";
    } else if (data.state === "opened") {
      state = "open";
    } else if (data.state === "closed") {
      state = "closed";
    } else {
      state = "open";
    }

    return {
      id: data.iid,
      webUrl: data.web_url,
      apiUrl: `${this.baseUrl}/projects/${projectPath}/merge_requests/${data.iid}`,
      state,
      sourceBranch: data.source_branch,
      targetBranch: data.target_branch,
    };
  }

  /**
   * Generate authentication for git push operations.
   * Uses the configured access token (PAT or Deploy Token).
   */
  async generatePushAuth(): Promise<GitPushAuthContext> {
    if (!this.accessToken) {
      throw new SourceControlProviderError(
        "GitLab access token not configured - cannot generate push auth",
        "permanent"
      );
    }

    return {
      authType: "pat",
      token: this.accessToken,
    };
  }
}

/**
 * Create a GitLab source control provider.
 */
export function createGitLabProvider(
  config: GitLabProviderConfig = {}
): SourceControlProvider {
  return new GitLabSourceControlProvider(config);
}
```

### 2.4 Bitbucket Provider (Stub)

**File: `packages/control-plane/src/source-control/providers/bitbucket-provider.ts`** (NEW)

```typescript
/**
 * Bitbucket source control provider implementation.
 */

import type {
  SourceControlProvider,
  SourceControlAuthContext,
  GetRepositoryConfig,
  RepositoryInfo,
  CreatePullRequestConfig,
  CreatePullRequestResult,
  GitPushAuthContext,
} from "../types";
import { SourceControlProviderError } from "../errors";
import type { BitbucketProviderConfig } from "./types";
import { USER_AGENT } from "./constants";

const BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0";

/**
 * Bitbucket implementation of SourceControlProvider.
 */
export class BitbucketSourceControlProvider implements SourceControlProvider {
  readonly name = "bitbucket";

  private readonly baseUrl: string;
  private readonly accessToken?: string;
  private readonly workspaceId?: string;

  constructor(config: BitbucketProviderConfig = {}) {
    this.baseUrl = config.baseUrl || BITBUCKET_API_BASE;
    this.accessToken = config.accessToken;
    this.workspaceId = config.workspaceId;
  }

  /**
   * Get repository information from Bitbucket API.
   */
  async getRepository(
    auth: SourceControlAuthContext,
    config: GetRepositoryConfig
  ): Promise<RepositoryInfo> {
    const response = await fetch(
      `${this.baseUrl}/repositories/${config.owner}/${config.name}`,
      {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${auth.token}`,
          "User-Agent": USER_AGENT,
        },
      }
    );

    if (!response.ok) {
      const error = await response.text();
      throw SourceControlProviderError.fromFetchError(
        `Failed to get repository: ${response.status} ${error}`,
        new Error(error),
        response.status
      );
    }

    const data = (await response.json()) as {
      uuid: string;
      slug: string;
      full_name: string;
      mainbranch: { name: string } | null;
      is_private: boolean;
      owner: { username: string };
    };

    return {
      owner: data.owner.username,
      name: data.slug,
      fullName: data.full_name,
      defaultBranch: data.mainbranch?.name || "main",
      isPrivate: data.is_private,
      providerRepoId: data.uuid,
    };
  }

  /**
   * Create a pull request on Bitbucket.
   */
  async createPullRequest(
    auth: SourceControlAuthContext,
    config: CreatePullRequestConfig
  ): Promise<CreatePullRequestResult> {
    const requestBody = {
      title: config.title,
      description: config.body,
      source: {
        branch: {
          name: config.sourceBranch,
        },
      },
      destination: {
        branch: {
          name: config.targetBranch,
        },
      },
      // Note: Bitbucket doesn't natively support draft PRs
      // Reviewers can be added directly
      reviewers: config.reviewers?.map((username) => ({ username })) || [],
    };

    const response = await fetch(
      `${this.baseUrl}/repositories/${config.repository.owner}/${config.repository.name}/pullrequests`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${auth.token}`,
          "User-Agent": USER_AGENT,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      }
    );

    if (!response.ok) {
      const error = await response.text();
      throw SourceControlProviderError.fromFetchError(
        `Failed to create PR: ${response.status} ${error}`,
        new Error(error),
        response.status
      );
    }

    const data = (await response.json()) as {
      id: number;
      links: { html: { href: string }; self: { href: string } };
      state: string;
      source: { branch: { name: string } };
      destination: { branch: { name: string } };
    };

    // Map Bitbucket state to our state type
    // Bitbucket uses: OPEN, MERGED, DECLINED, SUPERSEDED
    let state: CreatePullRequestResult["state"];
    switch (data.state) {
      case "OPEN":
        state = "open";
        break;
      case "MERGED":
        state = "merged";
        break;
      case "DECLINED":
      case "SUPERSEDED":
        state = "closed";
        break;
      default:
        state = "open";
    }

    return {
      id: data.id,
      webUrl: data.links.html.href,
      apiUrl: data.links.self.href,
      state,
      sourceBranch: data.source.branch.name,
      targetBranch: data.destination.branch.name,
    };
  }

  /**
   * Generate authentication for git push operations.
   * Uses the configured access token (App Password).
   */
  async generatePushAuth(): Promise<GitPushAuthContext> {
    if (!this.accessToken) {
      throw new SourceControlProviderError(
        "Bitbucket access token not configured - cannot generate push auth",
        "permanent"
      );
    }

    return {
      authType: "token",
      token: this.accessToken,
    };
  }
}

/**
 * Create a Bitbucket source control provider.
 */
export function createBitbucketProvider(
  config: BitbucketProviderConfig = {}
): SourceControlProvider {
  return new BitbucketSourceControlProvider(config);
}
```

### 2.5 Update Session Durable Object

**Key changes to `packages/control-plane/src/session/durable-object.ts`:**

```typescript
// Add import
import {
  createSourceControlProvider,
  type ProviderConfigs
} from "../source-control";
import type { SourceControlProviderType } from "@open-inspect/shared";

// Update session initialization to accept provider
interface InitParams {
  sessionName: string;
  repoOwner: string;
  repoName: string;
  repoId: number;
  title?: string;
  model?: string;
  provider?: SourceControlProviderType;  // NEW
  // ... other fields
}

// Store provider in session row
interface SessionRow {
  // ... existing fields
  provider: SourceControlProviderType;
}

// Update getSourceControlProvider method
private getSourceControlProvider(): SourceControlProvider {
  const session = this.repo.getSession();
  const provider = (session?.provider as SourceControlProviderType) || "github";

  const configs: ProviderConfigs = {
    github: {
      appConfig: getGitHubAppConfig(this.env),
    },
    gitlab: {
      baseUrl: this.env.GITLAB_BASE_URL,
      accessToken: this.env.GITLAB_ACCESS_TOKEN,
    },
    bitbucket: {
      baseUrl: this.env.BITBUCKET_BASE_URL,
      accessToken: this.env.BITBUCKET_ACCESS_TOKEN,
      workspaceId: this.env.BITBUCKET_WORKSPACE_ID,
    },
  };

  return createSourceControlProvider(provider, configs);
}
```

### 2.6 Update Router

**Key changes to `packages/control-plane/src/router.ts`:**

```typescript
import type { SourceControlProviderType } from "@open-inspect/shared";

// Update handleCreateSession
async function handleCreateSession(
  request: Request,
  env: Env,
  _match: RegExpMatchArray,
  ctx: RequestContext
): Promise<Response> {
  const body = (await request.json()) as CreateSessionRequest & {
    // ... existing fields
    provider?: SourceControlProviderType;  // NEW
  };

  // Default to github for backwards compatibility
  const provider: SourceControlProviderType = body.provider || "github";

  // Validate provider
  if (!["github", "gitlab", "bitbucket"].includes(provider)) {
    return error(`Invalid provider: ${provider}`);
  }

  // For now, only GitHub is fully supported
  // Other providers can be enabled as implementations are completed
  if (provider !== "github") {
    return error(`Provider '${provider}' is not yet supported`, 501);
  }

  // ... rest of implementation passes provider to DO init
}
```

## 3. Modal Infrastructure (`packages/modal-infra`)

### 3.1 Git Utilities Module

**File: `packages/modal-infra/src/utils/git.py`** (NEW)

```python
"""
Git utilities for multi-provider support.
"""

from typing import Literal

SourceControlProvider = Literal["github", "gitlab", "bitbucket"]


def get_provider_host(provider: SourceControlProvider, base_url: str | None = None) -> str:
    """Get the git host for a provider."""
    if base_url:
        from urllib.parse import urlparse
        return urlparse(base_url).netloc

    hosts = {
        "github": "github.com",
        "gitlab": "gitlab.com",
        "bitbucket": "bitbucket.org",
    }
    return hosts[provider]


def get_clone_url(
    provider: SourceControlProvider,
    owner: str,
    name: str,
    token: str | None = None,
    base_url: str | None = None,
) -> str:
    """
    Get the clone URL for a repository.

    Args:
        provider: Source control provider
        owner: Repository owner/namespace
        name: Repository name
        token: Optional authentication token
        base_url: Optional base URL for self-hosted instances

    Returns:
        Git clone URL with optional authentication
    """
    host = get_provider_host(provider, base_url)

    if not token:
        return f"https://{host}/{owner}/{name}.git"

    # Each provider uses different token authentication formats
    auth_formats = {
        "github": f"x-access-token:{token}",
        "gitlab": f"oauth2:{token}",
        "bitbucket": f"x-token-auth:{token}",
    }
    auth = auth_formats[provider]

    return f"https://{auth}@{host}/{owner}/{name}.git"


def get_remote_url_with_auth(
    provider: SourceControlProvider,
    owner: str,
    name: str,
    token: str,
    base_url: str | None = None,
) -> str:
    """Get remote URL with authentication for push operations."""
    return get_clone_url(provider, owner, name, token, base_url)
```

### 3.2 Update Sandbox Entrypoint

**Key changes to `packages/modal-infra/src/sandbox/entrypoint.py`:**

```python
from ..utils.git import get_clone_url, get_remote_url_with_auth, SourceControlProvider

class SandboxSupervisor:
    def __init__(self):
        # ... existing code ...

        # Source control provider (defaults to github for backwards compatibility)
        self.provider: SourceControlProvider = os.environ.get("SOURCE_CONTROL_PROVIDER", "github")

        # Rename for clarity (was github_app_token)
        self.source_control_token = os.environ.get("SOURCE_CONTROL_TOKEN") or os.environ.get("GITHUB_APP_TOKEN", "")

        # Optional base URL for self-hosted instances
        self.source_control_base_url = os.environ.get("SOURCE_CONTROL_BASE_URL")

    async def perform_git_sync(self) -> bool:
        """Clone repository if needed, then synchronize with latest changes."""
        # ... existing logging ...

        if not self.repo_path.exists():
            if not self.repo_owner or not self.repo_name:
                self.log.info("git.skip_clone", reason="no_repo_configured")
                self.git_sync_complete.set()
                return True

            self.log.info(
                "git.clone_start",
                repo_owner=self.repo_owner,
                repo_name=self.repo_name,
                provider=self.provider,
                authenticated=bool(self.source_control_token),
            )

            # Use provider-aware clone URL
            clone_url = get_clone_url(
                provider=self.provider,
                owner=self.repo_owner,
                name=self.repo_name,
                token=self.source_control_token if self.source_control_token else None,
                base_url=self.source_control_base_url,
            )

            result = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", clone_url, str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # ... rest of clone logic ...

        try:
            # Configure remote URL with auth token if available
            if self.source_control_token:
                auth_url = get_remote_url_with_auth(
                    provider=self.provider,
                    owner=self.repo_owner,
                    name=self.repo_name,
                    token=self.source_control_token,
                    base_url=self.source_control_base_url,
                )
                await asyncio.create_subprocess_exec(
                    "git", "remote", "set-url", "origin", auth_url,
                    cwd=self.repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            # ... rest of sync logic ...
```

### 3.3 Update Web API

**Key changes to `packages/modal-infra/src/web_api.py`:**

```python
from .utils.git import SourceControlProvider

@app.function(...)
@fastapi_endpoint(method="POST")
async def api_create_sandbox(request: SandboxRequest) -> SandboxResponse:
    """Create a new sandbox for a session."""

    # Get provider from request (default to github)
    provider: SourceControlProvider = getattr(request, 'provider', 'github')

    # Generate token based on provider
    if provider == "github":
        token = generate_github_token(...)
    elif provider == "gitlab":
        token = os.environ.get("GITLAB_ACCESS_TOKEN")
    elif provider == "bitbucket":
        token = os.environ.get("BITBUCKET_ACCESS_TOKEN")
    else:
        token = None

    # Pass provider info to sandbox
    env_vars = {
        "SOURCE_CONTROL_PROVIDER": provider,
        "SOURCE_CONTROL_TOKEN": token or "",
        "SOURCE_CONTROL_BASE_URL": os.environ.get(f"{provider.upper()}_BASE_URL", ""),
        # ... other env vars
    }
```

## 4. Web Application (`packages/web`)

### 4.1 Multi-Provider Auth

**File: `packages/web/src/lib/auth.ts`** (UPDATE)

```typescript
import type { NextAuthOptions } from "next-auth";
import GitHubProvider from "next-auth/providers/github";
import GitLabProvider from "next-auth/providers/gitlab";
import type { SourceControlProviderType } from "@open-inspect/shared";

// Extend NextAuth types for multi-provider support
declare module "next-auth" {
  interface Session {
    user: {
      id?: string;
      login?: string;
      name?: string | null;
      email?: string | null;
      image?: string | null;
      provider?: SourceControlProviderType;  // NEW
    };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    accessTokenExpiresAt?: number;
    userId?: string;
    login?: string;
    provider?: SourceControlProviderType;  // NEW
  }
}

// Build providers list dynamically
const providers = [];

if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
  providers.push(
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID,
      clientSecret: process.env.GITHUB_CLIENT_SECRET,
      authorization: {
        params: { scope: "read:user user:email repo" },
      },
    })
  );
}

if (process.env.GITLAB_CLIENT_ID && process.env.GITLAB_CLIENT_SECRET) {
  providers.push(
    GitLabProvider({
      clientId: process.env.GITLAB_CLIENT_ID,
      clientSecret: process.env.GITLAB_CLIENT_SECRET,
      authorization: {
        params: { scope: "read_user api" },
      },
    })
  );
}

// Note: Bitbucket requires a custom provider or the Atlassian provider
// which can be added when Bitbucket support is fully implemented

export const authOptions: NextAuthOptions = {
  providers,
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token as string | undefined;
        token.accessTokenExpiresAt = account.expires_at
          ? account.expires_at * 1000
          : undefined;

        // Track which provider was used
        token.provider = account.provider as SourceControlProviderType;
      }

      if (profile) {
        // Handle different profile shapes
        if (token.provider === "github") {
          const githubProfile = profile as { id?: number; login?: string };
          token.userId = githubProfile.id?.toString();
          token.login = githubProfile.login;
        } else if (token.provider === "gitlab") {
          const gitlabProfile = profile as { id?: number; username?: string };
          token.userId = gitlabProfile.id?.toString();
          token.login = gitlabProfile.username;
        }
      }

      return token;
    },

    async session({ session, token }) {
      if (session.user) {
        session.user.id = token.userId;
        session.user.login = token.login;
        session.user.provider = token.provider;
      }
      return session;
    },
  },
  // ... rest of config
};
```

### 4.2 Update UI Components

**File: `packages/web/src/components/sidebar/metadata-section.tsx`** (UPDATE)

```typescript
import { getRepoWebUrl, type GitCloneConfig } from "@open-inspect/shared";

// In component:
const repoConfig: GitCloneConfig = {
  provider: session.provider || "github",
  owner: repoOwner,
  name: repoName,
};

// Replace hardcoded GitHub URL:
<a href={getRepoWebUrl(repoConfig)} target="_blank" rel="noopener noreferrer">
  {repoOwner}/{repoName}
</a>
```

## 5. Database Schema Updates

### 5.1 D1 Migration

**File: `terraform/d1/migrations/0005_add_provider_column.sql`** (NEW)

```sql
-- Add provider column to sessions table
ALTER TABLE sessions ADD COLUMN provider TEXT DEFAULT 'github';

-- Create index for filtering by provider
CREATE INDEX idx_sessions_provider ON sessions(provider);
```

## 6. Environment Configuration

### 6.1 Control Plane Environment

```bash
# GitHub (existing)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_APP_INSTALLATION_ID=

# GitLab (new)
GITLAB_CLIENT_ID=
GITLAB_CLIENT_SECRET=
GITLAB_BASE_URL=https://gitlab.com  # or self-hosted URL
GITLAB_ACCESS_TOKEN=                 # PAT or Deploy Token for push

# Bitbucket (new)
BITBUCKET_CLIENT_ID=
BITBUCKET_CLIENT_SECRET=
BITBUCKET_BASE_URL=https://bitbucket.org
BITBUCKET_ACCESS_TOKEN=              # App Password for push
BITBUCKET_WORKSPACE_ID=              # Required for Bitbucket Cloud
```

### 6.2 Modal Secrets

```bash
# Create combined source-control secret
modal secret create source-control \
  GITHUB_APP_ID="..." \
  GITHUB_APP_PRIVATE_KEY="..." \
  GITHUB_APP_INSTALLATION_ID="..." \
  GITLAB_ACCESS_TOKEN="..." \
  GITLAB_BASE_URL="https://gitlab.com" \
  BITBUCKET_ACCESS_TOKEN="..." \
  BITBUCKET_WORKSPACE_ID="..."
```

## 7. Migration Path

### Phase 1: Foundation (This Proposal)
- Add `SourceControlProviderType` across all layers
- Add provider stubs (GitLab, Bitbucket)
- Update git utilities to be provider-aware
- Default to "github" everywhere for backwards compatibility

### Phase 2: GitLab Implementation
- Complete GitLab OAuth flow in web app
- Implement GitLab repository listing
- Test full GitLab flow end-to-end
- Enable GitLab in production

### Phase 3: Bitbucket Implementation
- Add Bitbucket OAuth provider
- Complete Bitbucket API implementation
- Test full Bitbucket flow end-to-end
- Enable Bitbucket in production

### Phase 4: Multi-Provider Sessions
- Allow users to select provider when creating sessions
- Support multiple providers in a single deployment
- Add provider indicators in UI

## 8. Backwards Compatibility

All changes maintain backwards compatibility:

1. **Type defaults**: `provider` defaults to `"github"` when not specified
2. **Environment variables**: `GITHUB_APP_TOKEN` still works (aliased to `SOURCE_CONTROL_TOKEN`)
3. **API compatibility**: Existing API requests without `provider` field work unchanged
4. **Database**: Migration adds column with default value, no data migration needed
5. **Shared utilities**: Functions accept optional `provider` parameter, defaulting to GitHub behavior

## Summary

This proposal provides:

1. **Clean abstractions**: Provider type flows through all layers
2. **Minimal changes**: Most code changes are additive
3. **Type safety**: TypeScript ensures provider handling is exhaustive
4. **Backwards compatible**: Existing GitHub-only deployments continue to work
5. **Extensible**: Adding new providers follows established patterns
