/**
 * UiPath client — auth + a thin REST layer for the platform capabilities we consume.
 *
 * Auth model: the backend is registered as a UiPath **External Application** using the
 * OAuth 2.0 **client-credentials** flow. Access tokens live ~1 hour and there is NO
 * refresh token, so we cache the token and re-fetch on (near-)expiry.
 *
 * We implement the token + HTTP layer ourselves (global fetch) so it is dependency-light
 * and unit-testable. The official `@uipath/uipath-typescript` SDK is the intended
 * higher-level layer for Orchestrator / Action Center / Data Service / Maestro / Agents —
 * wrap it here as those surfaces stabilize (the v1.x SDK is young; pin versions).
 * Document Understanding is NOT in the SDK — it has its own REST API (see `du.ts`).
 */

export interface UiPathConfig {
  baseUrl: string; // e.g. https://cloud.uipath.com
  org: string;
  tenant: string;
  clientId: string;
  clientSecret: string;
  scopes: string[];
}

export interface UiPathConfigInput extends Partial<UiPathConfig> {}

/** Build config from environment variables (see .env.example). */
export function uiPathConfigFromEnv(
  env: Record<string, string | undefined> = process.env,
): UiPathConfig {
  const required = (k: string): string => {
    const v = env[k];
    if (!v) throw new Error(`missing required env var ${k}`);
    return v;
  };
  return {
    baseUrl: env.UIPATH_BASE_URL ?? "https://cloud.uipath.com",
    org: required("UIPATH_ORG"),
    tenant: required("UIPATH_TENANT"),
    clientId: required("UIPATH_CLIENT_ID"),
    clientSecret: required("UIPATH_CLIENT_SECRET"),
    scopes: (env.UIPATH_SCOPES ?? "").split(/\s+/).filter(Boolean),
  };
}

interface CachedToken {
  accessToken: string;
  /** epoch ms at which we should refresh (with safety margin). */
  refreshAt: number;
}

type FetchFn = typeof fetch;

/** Refresh this many ms before the real expiry to avoid mid-flight 401s. */
const EXPIRY_SAFETY_MARGIN_MS = 60_000;

export class UiPathClient {
  private token: CachedToken | null = null;
  private inFlight: Promise<string> | null = null;

  constructor(
    private readonly cfg: UiPathConfig,
    private readonly deps: { fetch?: FetchFn; now?: () => number } = {},
  ) {}

  private get fetchFn(): FetchFn {
    return this.deps.fetch ?? fetch;
  }

  private now(): number {
    return this.deps.now ? this.deps.now() : Date.now();
  }

  /** Identity token endpoint for the org. */
  private tokenUrl(): string {
    return `${this.cfg.baseUrl}/identity_/connect/token`;
  }

  /** Base URL for Orchestrator/platform APIs scoped to org + tenant. */
  orgTenantUrl(): string {
    return `${this.cfg.baseUrl}/${this.cfg.org}/${this.cfg.tenant}`;
  }

  /** Return a valid access token, fetching/refreshing as needed (single-flight). */
  async getAccessToken(): Promise<string> {
    if (this.token && this.now() < this.token.refreshAt) {
      return this.token.accessToken;
    }
    if (this.inFlight) return this.inFlight;
    this.inFlight = this.fetchToken().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  private async fetchToken(): Promise<string> {
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.cfg.clientId,
      client_secret: this.cfg.clientSecret,
    });
    if (this.cfg.scopes.length) body.set("scope", this.cfg.scopes.join(" "));

    const res = await this.fetchFn(this.tokenUrl(), {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!res.ok) {
      throw new Error(`UiPath token request failed: ${res.status} ${await safeText(res)}`);
    }
    const json = (await res.json()) as { access_token: string; expires_in: number };
    const ttlMs = (json.expires_in ?? 3600) * 1000;
    this.token = {
      accessToken: json.access_token,
      refreshAt: this.now() + Math.max(0, ttlMs - EXPIRY_SAFETY_MARGIN_MS),
    };
    return json.access_token;
  }

  /** Authenticated JSON request against an org/tenant-scoped path. */
  async request<T>(
    method: string,
    path: string,
    opts: { body?: unknown; folderId?: number | string; headers?: Record<string, string> } = {},
  ): Promise<T> {
    const token = await this.getAccessToken();
    const headers: Record<string, string> = {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      ...opts.headers,
    };
    // Orchestrator scopes most resources to a folder (Organization Unit).
    if (opts.folderId !== undefined) headers["X-UIPATH-OrganizationUnitId"] = String(opts.folderId);

    const url = path.startsWith("http") ? path : `${this.orgTenantUrl()}${path}`;
    const res = await this.fetchFn(url, {
      method,
      headers,
      ...(opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}),
    });
    if (!res.ok) {
      throw new Error(`UiPath ${method} ${path} failed: ${res.status} ${await safeText(res)}`);
    }
    return (await res.json()) as T;
  }

  // --- Capability helpers (documented REST endpoints; verify shapes against your tenant) ---

  /** Enqueue a work item onto an Orchestrator queue (e.g. a JE-posting for a robot). */
  async addQueueItem(
    folderId: number | string,
    queueName: string,
    specificContent: Record<string, unknown>,
    reference?: string,
  ): Promise<unknown> {
    return this.request("POST", "/orchestrator_/odata/Queues/UiPathODataSvc.AddQueueItem", {
      folderId,
      body: {
        itemData: {
          Name: queueName,
          Priority: "Normal",
          SpecificContent: specificContent,
          ...(reference ? { Reference: reference } : {}),
        },
      },
    });
  }

  /**
   * Create an Action Center task for a human (maker-checker / exception review).
   * The exception data itself lives in our Postgres; the task carries a pointer + payload.
   */
  async createAppTask(
    folderId: number | string,
    task: { title: string; data: Record<string, unknown>; appId?: string; priority?: string },
  ): Promise<unknown> {
    return this.request("POST", "/orchestrator_/tasks/AppTasks/CreateAppTask", {
      folderId,
      body: {
        title: task.title,
        data: task.data,
        priority: task.priority ?? "Medium",
        ...(task.appId ? { appId: task.appId } : {}),
      },
    });
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return (await res.text()).slice(0, 500);
  } catch {
    return "<no body>";
  }
}
