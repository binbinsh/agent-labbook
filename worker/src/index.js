import { SELECTION_UI_CSS, SELECTION_UI_JS } from "../generated/selection_ui_bundle.js";

const DEFAULT_NOTION_VERSION = "2026-03-11";
const DEFAULT_PAGE_LIMIT = 5000;
const MIN_PAGE_LIMIT = 500;
const MAX_PAGE_LIMIT = 5000;
const STATE_TTL_SECONDS = 3600;
const SELECTION_TOKEN_TTL_SECONDS = 3600;
const HANDOFF_BUNDLE_TTL_SECONDS = 3600;
const PRIVATE_PAYLOAD_VERSION = "v1";
const DEFAULT_DISCOVERY_NODE_LIMIT = 2000;
const MAX_DISCOVERY_NODE_LIMIT = 5000;
const MAX_BLOCK_SCAN_LIMIT = 20000;
const MAX_DISCOVERY_DEPTH_LIMIT = 128;
const NOTION_OAUTH_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize";
const NOTION_API_BASE = "https://api.notion.com/v1";

function jsonResponse(payload, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(payload, null, 2), {
    ...init,
    headers,
  });
}

function htmlResponse(html, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("content-type", "text/html; charset=utf-8");
  headers.set(
    "content-security-policy",
    [
      "default-src 'none'",
      "style-src 'unsafe-inline'",
      "script-src 'unsafe-inline'",
      "img-src data: https:",
      "connect-src 'self'",
      "base-uri 'none'",
      "frame-ancestors 'none'",
      "form-action 'self' http://127.0.0.1:* http://localhost:*",
    ].join("; "),
  );
  return new Response(html, {
    ...init,
    headers,
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inlineJson(value) {
  return JSON.stringify(value)
    .replaceAll("&", "\\u0026")
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
}

function inlineScriptText(value) {
  return String(value).replaceAll("</script>", "<\\/script>");
}

function encodeBase64Url(value) {
  const bytes = value instanceof Uint8Array ? value : new TextEncoder().encode(String(value));
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function decodeBase64Url(value) {
  const bytes = decodeBase64UrlBytes(value);
  return new TextDecoder().decode(bytes);
}

function decodeBase64UrlBytes(value) {
  const padded = `${value}${"=".repeat((4 - (value.length % 4)) % 4)}`
    .replaceAll("-", "+")
    .replaceAll("_", "/");
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function clampInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? fallback), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(parsed, minimum), maximum);
}

async function hmacSha256(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return new Uint8Array(signature);
}

async function signSignedPayload(env, payload) {
  const body = encodeBase64Url(JSON.stringify(payload));
  const signature = encodeBase64Url(await hmacSha256(env.NOTION_CLIENT_SECRET, body));
  return `${body}.${signature}`;
}

async function verifySignedPayload(env, signedValue) {
  const [body, signature] = String(signedValue || "").split(".");
  if (!body || !signature) {
    throw new Error("Missing or malformed signed payload.");
  }
  const expectedSignature = encodeBase64Url(await hmacSha256(env.NOTION_CLIENT_SECRET, body));
  if (expectedSignature !== signature) {
    throw new Error("Signed payload signature mismatch.");
  }
  return JSON.parse(decodeBase64Url(body));
}

async function privatePayloadKey(env) {
  const secret = new TextEncoder().encode(`${env.NOTION_CLIENT_SECRET}:agent-labbook-private-payload:${PRIVATE_PAYLOAD_VERSION}`);
  const digest = await crypto.subtle.digest("SHA-256", secret);
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, ["encrypt", "decrypt"]);
}

async function encryptPrivatePayload(env, payload) {
  const key = await privatePayloadKey(env);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(payload));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext));
  return [PRIVATE_PAYLOAD_VERSION, encodeBase64Url(iv), encodeBase64Url(ciphertext)].join(".");
}

async function decryptPrivatePayload(env, encryptedValue) {
  const [version, ivPart, ciphertextPart] = String(encryptedValue || "").split(".");
  if (version !== PRIVATE_PAYLOAD_VERSION || !ivPart || !ciphertextPart) {
    throw new Error("Missing or malformed encrypted payload.");
  }

  const key = await privatePayloadKey(env);
  const iv = decodeBase64UrlBytes(ivPart);
  const ciphertext = decodeBase64UrlBytes(ciphertextPart);
  const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ciphertext);
  const decoded = JSON.parse(new TextDecoder().decode(plaintext));
  if (!(decoded && typeof decoded === "object")) {
    throw new Error("Encrypted payload did not decode to an object.");
  }
  return decoded;
}

function ensureRecentPayload(payload, ttlSeconds, errorMessage) {
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload?.issued_at !== "number" || now - payload.issued_at > ttlSeconds) {
    throw new Error(errorMessage);
  }
}

async function signState(env, payload) {
  return signSignedPayload(env, payload);
}

async function verifyState(env, signedState) {
  const payload = await verifySignedPayload(env, signedState);
  ensureRecentPayload(payload, STATE_TTL_SECONDS, "Authorization state expired.");
  return payload;
}

async function resolveSelectionSession(env, selectionToken) {
  const payload = await decryptPrivatePayload(env, selectionToken);
  ensureRecentPayload(payload, SELECTION_TOKEN_TTL_SECONDS, "Selection session expired.");
  if (payload?.purpose !== "selection_session") {
    throw new Error("Invalid selection token.");
  }
  if (!(payload.token && typeof payload.token === "object")) {
    throw new Error("Selection token did not contain a token payload.");
  }
  return payload;
}

function getBaseUrl(request, env) {
  const configured = String(env.PUBLIC_BASE_URL || "").trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  return new URL(request.url).origin;
}

function getNotionVersion(env) {
  return String(env.NOTION_VERSION || DEFAULT_NOTION_VERSION).trim() || DEFAULT_NOTION_VERSION;
}

function validateLocalReturnUrl(value) {
  const parsed = new URL(value);
  const hostname = parsed.hostname.toLowerCase();
  const allowedHosts = new Set(["127.0.0.1", "localhost"]);
  if (parsed.protocol !== "http:" || !allowedHosts.has(hostname)) {
    throw new Error("return_to must point at a localhost HTTP address.");
  }
  return parsed.toString();
}

function normalizePageLimit(value) {
  return clampInteger(value, DEFAULT_PAGE_LIMIT, MIN_PAGE_LIMIT, MAX_PAGE_LIMIT);
}

function richTextToPlainText(items) {
  if (!Array.isArray(items)) {
    return "";
  }
  return items
    .map((item) => (item && typeof item === "object" ? String(item.plain_text || "") : ""))
    .join("")
    .trim();
}

function normalizeNotionIdLike(value) {
  const raw = String(value || "").trim();
  let candidateSource = raw;
  try {
    const parsedUrl = new URL(raw);
    if (parsedUrl.pathname) {
      candidateSource = parsedUrl.pathname;
    }
  } catch {}
  const matches = candidateSource.match(
    /[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g,
  );
  const candidate = matches?.length ? matches[matches.length - 1] : raw;
  const collapsed = String(candidate || "").replaceAll("-", "").toLowerCase();
  if (/^[0-9a-f]{32}$/.test(collapsed)) {
    return [
      collapsed.slice(0, 8),
      collapsed.slice(8, 12),
      collapsed.slice(12, 16),
      collapsed.slice(16, 20),
      collapsed.slice(20),
    ].join("-");
  }
  return raw;
}

function normalizeResourceType(value) {
  const resourceType = String(value || "").trim().toLowerCase();
  if (resourceType === "database") {
    return "data_source";
  }
  return resourceType || "unknown";
}

function resourceTitle(resource) {
  if (!resource || typeof resource !== "object") {
    return "";
  }
  if (Array.isArray(resource.title)) {
    const fromRootTitle = richTextToPlainText(resource.title);
    if (fromRootTitle) {
      return fromRootTitle;
    }
  }
  const properties = resource.properties;
  if (properties && typeof properties === "object") {
    for (const prop of Object.values(properties)) {
      if (!prop || typeof prop !== "object") {
        continue;
      }
      if (Array.isArray(prop.title)) {
        const plain = richTextToPlainText(prop.title);
        if (plain) {
          return plain;
        }
      }
      if (prop.type === "title" && Array.isArray(prop.title)) {
        const plain = richTextToPlainText(prop.title);
        if (plain) {
          return plain;
        }
      }
    }
  }
  return String(resource.url || resource.id || "").trim();
}

function iconEmoji(icon) {
  if (!icon || typeof icon !== "object") {
    return null;
  }
  if (icon.type === "emoji") {
    return String(icon.emoji || "").trim() || null;
  }
  return null;
}

function normalizeParent(parent) {
  if (!parent || typeof parent !== "object") {
    return {
      parent_type: null,
      parent_id: null,
      parent_database_id: null,
    };
  }

  const parentType = String(parent.type || "").trim() || null;
  if (!parentType || parentType === "workspace") {
    return {
      parent_type: parentType,
      parent_id: null,
      parent_database_id: null,
    };
  }

  if (parentType === "page_id") {
    return {
      parent_type: parentType,
      parent_id: normalizeNotionIdLike(parent.page_id) || null,
      parent_database_id: null,
    };
  }

  if (parentType === "data_source_id") {
    return {
      parent_type: parentType,
      parent_id: normalizeNotionIdLike(parent.data_source_id) || null,
      parent_database_id: normalizeNotionIdLike(parent.database_id) || null,
    };
  }

  if (parentType === "database_id") {
    return {
      parent_type: parentType,
      parent_id: normalizeNotionIdLike(parent.database_id) || null,
      parent_database_id: normalizeNotionIdLike(parent.database_id) || null,
    };
  }

  if (parentType === "block_id") {
    return {
      parent_type: parentType,
      parent_id: normalizeNotionIdLike(parent.block_id) || null,
      parent_database_id: null,
    };
  }

  return {
    parent_type: parentType,
    parent_id: null,
    parent_database_id: null,
  };
}

function normalizeResource(resource, overrides = {}) {
  const parent = normalizeParent(resource.parent);
  const title = String(overrides.title ?? resourceTitle(resource) ?? "").trim();

  return {
    resource_id: normalizeNotionIdLike(overrides.resource_id ?? resource.id ?? ""),
    resource_type: normalizeResourceType(overrides.resource_type ?? resource.object ?? "unknown"),
    resource_url: String(overrides.resource_url ?? resource.url ?? "").trim() || null,
    title: title || "Untitled Notion resource",
    parent_type: overrides.parent_type ?? parent.parent_type,
    parent_id: normalizeNotionIdLike(overrides.parent_id ?? parent.parent_id),
    parent_database_id: normalizeNotionIdLike(overrides.parent_database_id ?? parent.parent_database_id),
    icon_emoji: overrides.icon_emoji ?? iconEmoji(resource.icon),
    last_edited_time: String(overrides.last_edited_time ?? resource.last_edited_time ?? "").trim() || null,
    discovered_parent_id: normalizeNotionIdLike(overrides.discovered_parent_id ?? null),
    discovered_root_id: normalizeNotionIdLike(overrides.discovered_root_id ?? null),
    discovered_depth:
      typeof overrides.discovered_depth === "number" && Number.isFinite(overrides.discovered_depth)
        ? overrides.discovered_depth
        : null,
  };
}

async function notionJson(env, path, init = {}) {
  const response = await fetch(`${NOTION_API_BASE}${path}`, init);
  const raw = await response.text();
  let payload = {};
  if (raw.trim()) {
    try {
      payload = JSON.parse(raw);
    } catch (error) {
      payload = {
        message: raw.trim(),
      };
    }
  }
  if (!response.ok) {
    const code = String(payload.code || "").trim();
    const message = String(payload.message || response.statusText || "Unknown Notion API error").trim();
    throw new Error(`Notion API ${response.status}${code ? ` (${code})` : ""}: ${message}`);
  }
  return payload;
}

function basicAuthHeader(env) {
  const encoded = btoa(`${env.NOTION_CLIENT_ID}:${env.NOTION_CLIENT_SECRET}`);
  return `Basic ${encoded}`;
}

function notionBearerHeaders(env, accessToken) {
  return {
    authorization: `Bearer ${accessToken}`,
    accept: "application/json",
    "content-type": "application/json",
    "notion-version": getNotionVersion(env),
  };
}

function normalizeDepthLimit(value) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return null;
  }
  return Math.min(parsed, MAX_DISCOVERY_DEPTH_LIMIT);
}

async function exchangeCode(env, redirectUri, code) {
  const body = JSON.stringify({
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
  });
  return notionJson(env, "/oauth/token", {
    method: "POST",
    headers: {
      authorization: basicAuthHeader(env),
      "content-type": "application/json",
      accept: "application/json",
      "notion-version": getNotionVersion(env),
    },
    body,
  });
}

async function refreshAccessToken(env, refreshToken) {
  const body = JSON.stringify({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
  });
  return notionJson(env, "/oauth/token", {
    method: "POST",
    headers: {
      authorization: basicAuthHeader(env),
      "content-type": "application/json",
      accept: "application/json",
      "notion-version": getNotionVersion(env),
    },
    body,
  });
}

async function fetchSelectableResources(env, accessToken, pageLimit) {
  const resources = [];
  const seenIds = new Set();
  let nextCursor = null;
  let truncated = false;

  while (resources.length < pageLimit) {
    const body = {
      page_size: Math.min(100, pageLimit - resources.length),
      sort: {
        direction: "descending",
        timestamp: "last_edited_time",
      },
    };
    if (nextCursor) {
      body.start_cursor = nextCursor;
    }

    const payload = await notionJson(env, "/search", {
      method: "POST",
      headers: notionBearerHeaders(env, accessToken),
      body: JSON.stringify(body),
    });

    const results = Array.isArray(payload.results) ? payload.results : [];
    for (const item of results) {
      if (!(item && typeof item === "object" && item.id)) {
        continue;
      }
      const normalized = normalizeResource(item);
      if (!normalized.resource_id || seenIds.has(normalized.resource_id)) {
        continue;
      }
      seenIds.add(normalized.resource_id);
      resources.push(normalized);
      if (resources.length >= pageLimit) {
        break;
      }
    }

    if (!payload.has_more || !payload.next_cursor) {
      break;
    }
    if (resources.length >= pageLimit) {
      truncated = true;
      break;
    }
    nextCursor = payload.next_cursor;
  }

  return {
    truncated,
    resources,
  };
}

async function queryDataSourceEntries(env, accessToken, dataSourceId, remainingLimit, discoveryMeta = {}) {
  const resources = [];
  const seenIds = new Set();
  let nextCursor = null;
  let truncated = false;

  while (resources.length < remainingLimit) {
    const body = {
      page_size: Math.min(100, Math.max(1, remainingLimit - resources.length)),
    };
    if (nextCursor) {
      body.start_cursor = nextCursor;
    }

    const payload = await notionJson(env, `/data_sources/${dataSourceId}/query`, {
      method: "POST",
      headers: notionBearerHeaders(env, accessToken),
      body: JSON.stringify(body),
    });

    const results = Array.isArray(payload.results) ? payload.results : [];
    for (const item of results) {
      if (!(item && typeof item === "object" && item.id)) {
        continue;
      }
      const normalized = normalizeResource(item, {
        discovered_parent_id: discoveryMeta.discovered_parent_id ?? dataSourceId,
        discovered_root_id: discoveryMeta.discovered_root_id ?? dataSourceId,
        discovered_depth:
          typeof discoveryMeta.discovered_depth === "number" ? discoveryMeta.discovered_depth : 1,
        parent_type: discoveryMeta.parent_type,
        parent_id: discoveryMeta.parent_id,
        parent_database_id: discoveryMeta.parent_database_id,
      });
      if (!normalized.resource_id || seenIds.has(normalized.resource_id)) {
        continue;
      }
      seenIds.add(normalized.resource_id);
      resources.push(normalized);
      if (resources.length >= remainingLimit) {
        break;
      }
    }

    if (!payload.has_more || !payload.next_cursor) {
      break;
    }
    if (resources.length >= remainingLimit) {
      truncated = true;
      break;
    }
    nextCursor = payload.next_cursor;
  }

  return {
    truncated,
    resources,
  };
}

function mergeResources(...resourceLists) {
  const byId = new Map();
  for (const list of resourceLists) {
    for (const item of Array.isArray(list) ? list : []) {
      if (!(item && typeof item === "object")) {
        continue;
      }
      const normalized = normalizeResource(item, item);
      if (!normalized.resource_id) {
        continue;
      }
      byId.set(normalized.resource_id, {
        ...(byId.get(normalized.resource_id) || normalized),
        ...normalized,
      });
    }
  }
  return Array.from(byId.values()).sort((left, right) => {
    const typeOrder = { page: 0, data_source: 1 };
    const leftRank = typeOrder[left.resource_type] ?? 2;
    const rightRank = typeOrder[right.resource_type] ?? 2;
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    const titleCompare = String(left.title || "").localeCompare(String(right.title || ""), undefined, {
      sensitivity: "base",
    });
    if (titleCompare !== 0) {
      return titleCompare;
    }
    return String(left.resource_id || "").localeCompare(String(right.resource_id || ""));
  });
}

async function listAllBlockChildren(env, accessToken, blockId) {
  const results = [];
  let nextCursor = null;

  while (true) {
    const params = new URLSearchParams({ page_size: "100" });
    if (nextCursor) {
      params.set("start_cursor", nextCursor);
    }
    const payload = await notionJson(env, `/blocks/${blockId}/children?${params.toString()}`, {
      method: "GET",
      headers: notionBearerHeaders(env, accessToken),
    });

    const children = Array.isArray(payload.results) ? payload.results : [];
    results.push(...children);

    if (!payload.has_more || !payload.next_cursor) {
      break;
    }
    nextCursor = payload.next_cursor;
  }

  return results;
}

async function retrievePageResource(env, accessToken, pageId, fallbackTitle, discoveryMeta = {}) {
  try {
    const payload = await notionJson(env, `/pages/${pageId}`, {
      method: "GET",
      headers: notionBearerHeaders(env, accessToken),
    });
    return normalizeResource(payload, {
      resource_type: "page",
      title: resourceTitle(payload) || fallbackTitle || undefined,
      ...discoveryMeta,
    });
  } catch (error) {
    return normalizeResource(
      {
        id: pageId,
        object: "page",
        url: null,
        parent: {
          type: "page_id",
          page_id: discoveryMeta.discovered_parent_id || null,
        },
      },
      {
        resource_type: "page",
        title: fallbackTitle || `Child page ${pageId.slice(0, 8)}`,
        ...discoveryMeta,
      },
    );
  }
}

async function retrieveDataSourceResourcesForDatabase(
  env,
  accessToken,
  databaseId,
  discoveryMeta = {},
) {
  try {
    const databasePayload = await notionJson(env, `/databases/${databaseId}`, {
      method: "GET",
      headers: notionBearerHeaders(env, accessToken),
    });
    const dataSources = Array.isArray(databasePayload?.data_sources) ? databasePayload.data_sources : [];
    const resources = [];

    for (const item of dataSources) {
      const dataSourceId = normalizeNotionIdLike(item?.id);
      if (!dataSourceId) {
        continue;
      }
      try {
        const payload = await notionJson(env, `/data_sources/${dataSourceId}`, {
          method: "GET",
          headers: notionBearerHeaders(env, accessToken),
        });
        resources.push(
          normalizeResource(payload, {
            resource_type: "data_source",
            title: resourceTitle(payload) || String(item?.name || "").trim() || undefined,
            ...discoveryMeta,
          }),
        );
      } catch {
        resources.push(
          normalizeResource(
            {
              id: dataSourceId,
              object: "data_source",
              parent: {
                type: "page_id",
                page_id: discoveryMeta.discovered_parent_id || discoveryMeta.parent_id || null,
              },
            },
            {
              resource_type: "data_source",
              title: String(item?.name || "").trim() || `Data source ${dataSourceId.slice(0, 8)}`,
              ...discoveryMeta,
            },
          ),
        );
      }
    }

    return resources;
  } catch {
    return [];
  }
}

async function discoverPageImmediateChildren(env, accessToken, pageId, options = {}) {
  const scanState = options.scan_state || { scannedBlockCount: 0, truncated: false };
  const rootId = normalizeNotionIdLike(options.root_id || pageId) || pageId;
  const depth = Number.isFinite(options.depth) ? options.depth : 0;
  const remainingLimit = Math.max(1, Number.parseInt(String(options.remaining_limit || 1), 10) || 1);
  const resources = [];
  const containerQueue = [pageId];
  const scannedContainers = new Set();

  while (containerQueue.length && !scanState.truncated) {
    const containerId = String(containerQueue.shift() || "").trim();
    if (!containerId || scannedContainers.has(containerId)) {
      continue;
    }
    scannedContainers.add(containerId);

    const blocks = await listAllBlockChildren(env, accessToken, containerId);
    scanState.scannedBlockCount += blocks.length;
    if (scanState.scannedBlockCount > MAX_BLOCK_SCAN_LIMIT) {
      scanState.truncated = true;
      break;
    }

    for (const block of blocks) {
      const blockId = normalizeNotionIdLike(block?.id);
      const blockType = String(block?.type || "").trim();

      if (blockType === "child_page" && blockId) {
        resources.push(
          await retrievePageResource(
            env,
            accessToken,
            blockId,
            String(block?.child_page?.title || "").trim() || null,
            {
              discovered_parent_id: pageId,
              discovered_root_id: rootId,
              discovered_depth: depth + 1,
              parent_type: "page_id",
              parent_id: pageId,
            },
          ),
        );
      } else if (blockType === "child_database" && blockId) {
        resources.push(
          ...(
            await retrieveDataSourceResourcesForDatabase(
              env,
              accessToken,
              blockId,
              {
                discovered_parent_id: pageId,
                discovered_root_id: rootId,
                discovered_depth: depth + 1,
                parent_type: "page_id",
                parent_id: pageId,
              },
            )
          ),
        );
      } else if (block?.has_children && blockId) {
        containerQueue.push(blockId);
      }

      if (resources.length >= remainingLimit) {
        scanState.truncated = true;
        break;
      }
    }
  }

  return {
    truncated: scanState.truncated,
    resources,
  };
}

async function discoverDataSourceImmediateChildren(env, accessToken, dataSourceId, options = {}) {
  const rootId = normalizeNotionIdLike(options.root_id || dataSourceId) || dataSourceId;
  const depth = Number.isFinite(options.depth) ? options.depth : 0;
  const remainingLimit = Math.max(1, Number.parseInt(String(options.remaining_limit || 1), 10) || 1);
  return queryDataSourceEntries(env, accessToken, dataSourceId, remainingLimit, {
    discovered_parent_id: dataSourceId,
    discovered_root_id: rootId,
    discovered_depth: depth + 1,
    parent_type: "data_source_id",
    parent_id: dataSourceId,
  });
}

async function discoverResourceGraph(env, accessToken, seedResources, options = {}) {
  const depthLimit = normalizeDepthLimit(options.depth_limit);
  const nodeLimit = clampInteger(options.node_limit, DEFAULT_DISCOVERY_NODE_LIMIT, 1, MAX_DISCOVERY_NODE_LIMIT);
  const discovered = new Map();
  const queue = [];
  const queuedKeys = new Set();
  const visitedPages = new Set();
  const visitedDataSources = new Set();
  const scanState = {
    scannedBlockCount: 0,
    truncated: false,
  };

  function enqueue(resourceId, resourceType, rootId, depth) {
    const normalizedId = normalizeNotionIdLike(resourceId);
    const normalizedType = normalizeResourceType(resourceType);
    if (!normalizedId || !["page", "data_source"].includes(normalizedType)) {
      return;
    }
    const queueKey = `${normalizedType}:${normalizedId}`;
    if (queuedKeys.has(queueKey)) {
      return;
    }
    queuedKeys.add(queueKey);
    queue.push({
      queue_key: queueKey,
      resource_id: normalizedId,
      resource_type: normalizedType,
      root_id: normalizeNotionIdLike(rootId) || normalizedId,
      depth: Number.isFinite(depth) ? depth : 0,
    });
  }

  for (const seed of Array.isArray(seedResources) ? seedResources : []) {
    enqueue(seed?.resource_id, seed?.resource_type, seed?.root_id || seed?.resource_id, seed?.depth || 0);
  }

  while (queue.length && !scanState.truncated && discovered.size < nodeLimit) {
    const current = queue.shift();
    queuedKeys.delete(current.queue_key);

    if (current.resource_type === "page") {
      if (visitedPages.has(current.resource_id)) {
        continue;
      }
      visitedPages.add(current.resource_id);
    } else if (current.resource_type === "data_source") {
      if (visitedDataSources.has(current.resource_id)) {
        continue;
      }
      visitedDataSources.add(current.resource_id);
    } else {
      continue;
    }

    if (depthLimit !== null && current.depth >= depthLimit) {
      continue;
    }

    const remainingLimit = Math.max(1, nodeLimit - discovered.size);
    const nextDiscovery =
      current.resource_type === "page"
        ? await discoverPageImmediateChildren(env, accessToken, current.resource_id, {
            root_id: current.root_id,
            depth: current.depth,
            remaining_limit: remainingLimit,
            scan_state: scanState,
          })
        : await discoverDataSourceImmediateChildren(env, accessToken, current.resource_id, {
            root_id: current.root_id,
            depth: current.depth,
            remaining_limit: remainingLimit,
          });

    if (nextDiscovery.truncated) {
      scanState.truncated = true;
    }

    for (const resource of nextDiscovery.resources) {
      if (!resource?.resource_id) {
        continue;
      }

      const existing = discovered.get(resource.resource_id);
      discovered.set(resource.resource_id, {
        ...(existing || resource),
        ...resource,
      });

      if (discovered.size >= nodeLimit) {
        scanState.truncated = true;
        break;
      }

      const normalizedType = normalizeResourceType(resource.resource_type);
      if (normalizedType === "page" && !visitedPages.has(resource.resource_id)) {
        enqueue(resource.resource_id, normalizedType, current.root_id, current.depth + 1);
      } else if (normalizedType === "data_source" && !visitedDataSources.has(resource.resource_id)) {
        enqueue(resource.resource_id, normalizedType, current.root_id, current.depth + 1);
      }
    }
  }

  return {
    truncated: scanState.truncated || discovered.size >= nodeLimit,
    resources: Array.from(discovered.values()),
  };
}

async function discoverChildPages(env, accessToken, pageIds, options = {}) {
  return discoverResourceGraph(
    env,
    accessToken,
    pageIds.map((pageId) => ({
      resource_id: pageId,
      resource_type: "page",
    })),
    options,
  );
}

async function discoverDataSourceContents(env, accessToken, dataSourceIds, options = {}) {
  return discoverResourceGraph(
    env,
    accessToken,
    dataSourceIds.map((dataSourceId) => ({
      resource_id: dataSourceId,
      resource_type: "data_source",
    })),
    options,
  );
}

async function buildSelectionCatalog(env, accessToken, pageLimit) {
  const searchPayload = await fetchSelectableResources(env, accessToken, pageLimit);
  const seedResources = searchPayload.resources.filter((resource) => {
    const resourceType = normalizeResourceType(resource.resource_type);
    return resourceType === "page" || resourceType === "data_source";
  });
  const graphDiscovery = seedResources.length
    ? await discoverResourceGraph(env, accessToken, seedResources, {
        node_limit: pageLimit,
      })
    : { truncated: false, resources: [] };

  return {
    truncated: Boolean(searchPayload.truncated || graphDiscovery.truncated),
    resources: mergeResources(searchPayload.resources, graphDiscovery.resources),
  };
}

function pageShell({ title, body }) {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(title)}</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f7f6f3;
        --canvas: rgba(255, 255, 255, 0.78);
        --panel: #ffffff;
        --panel-strong: #ffffff;
        --ink: #37352f;
        --muted: #787774;
        --muted-strong: #5f5e5b;
        --line: rgba(55, 53, 47, 0.09);
        --line-strong: rgba(55, 53, 47, 0.16);
        --accent: #2383e2;
        --accent-strong: #1a6fbd;
        --accent-soft: rgba(35, 131, 226, 0.1);
        --accent-faint: rgba(35, 131, 226, 0.06);
        --warning: #d9730d;
        --warning-soft: rgba(217, 115, 13, 0.1);
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        min-height: 100vh;
        background: var(--bg);
        color: var(--ink);
        font-family:
          ui-sans-serif,
          -apple-system,
          BlinkMacSystemFont,
          "Segoe UI",
          Helvetica,
          Arial,
          sans-serif;
      }
      main {
        width: min(820px, calc(100% - 24px));
        margin: 20px auto 36px;
      }
      .frame {
        background: var(--canvas);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 18px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), 0 8px 24px rgba(15, 23, 42, 0.04);
      }
      .stack {
        display: grid;
        gap: 14px;
      }
      .meta-row {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
      }
      .title {
        margin: 0;
        font-size: clamp(1.7rem, 4vw, 2.25rem);
        line-height: 1.15;
        letter-spacing: -0.03em;
        font-weight: 700;
      }
      p,
      button,
      input,
      textarea,
      code {
        font-family: inherit;
      }
      .lede {
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
        font-size: 15px;
      }
      .eyebrow,
      .pill,
      .tag {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        width: fit-content;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        line-height: 1.2;
      }
      .eyebrow {
        padding: 6px 10px;
        color: var(--muted-strong);
        background: #ffffff;
        border: 1px solid var(--line);
      }
      .pill {
        padding: 6px 10px;
        color: var(--ink);
        background: #ffffff;
        border: 1px solid var(--line);
      }
      .pill strong {
        font-weight: 600;
      }
      .notice {
        padding: 12px 14px;
        border-radius: 12px;
        border: 1px solid var(--line);
        background: #ffffff;
        color: var(--muted);
        line-height: 1.55;
      }
      .notice--warn {
        border-color: rgba(217, 115, 13, 0.24);
        background: #fff7ed;
        color: var(--warning);
      }
      .panel {
        display: grid;
        gap: 14px;
        padding: 16px;
        border-radius: 14px;
        background: var(--panel);
        border: 1px solid var(--line);
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), 0 6px 18px rgba(15, 23, 42, 0.04);
      }
      .panel__head {
        display: flex;
        gap: 12px;
        justify-content: space-between;
        align-items: start;
        flex-wrap: wrap;
      }
      .panel__head h2 {
        margin: 0 0 6px;
        font-size: 1.1rem;
        letter-spacing: -0.02em;
      }
      .panel__head p {
        margin: 0;
        color: var(--muted);
        line-height: 1.55;
        font-size: 14px;
      }
      .toolbar {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
      }
      input[type="search"],
      textarea {
        width: 100%;
        border: 1px solid var(--line-strong);
        border-radius: 12px;
        padding: 11px 14px;
        font-size: 14px;
        background: var(--panel-strong);
        color: var(--ink);
      }
      input[type="search"]:focus,
      textarea:focus {
        outline: 2px solid rgba(35, 131, 226, 0.16);
        outline-offset: 2px;
        border-color: rgba(35, 131, 226, 0.32);
      }
      .resource-list {
        display: grid;
        gap: 12px;
        align-content: start;
      }
      .resource-card {
        display: grid;
        gap: 12px;
        padding: 12px;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #ffffff;
        transition: border-color 120ms ease, background 120ms ease;
      }
      .resource-card.is-selected {
        border-color: rgba(35, 131, 226, 0.34);
        background: #f8fbff;
      }
      .resource-card__toggle {
        display: grid;
        grid-template-columns: 22px 36px minmax(0, 1fr);
        gap: 14px;
        align-items: start;
        width: 100%;
        padding: 0;
        border: 0;
        background: transparent;
        color: inherit;
        text-align: left;
        appearance: none;
        cursor: pointer;
      }
      .resource-card__toggle:focus-visible {
        outline: 2px solid rgba(35, 131, 226, 0.18);
        outline-offset: 2px;
        border-radius: 10px;
      }
      .resource-card__check {
        margin-top: 2px;
        display: grid;
        place-items: center;
        inline-size: 20px;
        block-size: 20px;
        border-radius: 6px;
        border: 1px solid var(--line-strong);
        background: #ffffff;
        color: transparent;
        font-size: 13px;
        font-weight: 700;
        line-height: 1;
      }
      .resource-card.is-selected .resource-card__check {
        border-color: var(--accent);
        background: var(--accent);
        color: white;
      }
      .resource-card__icon,
      .tree__icon {
        display: grid;
        place-items: center;
        inline-size: 36px;
        block-size: 36px;
        border-radius: 10px;
        border: 1px solid var(--line);
        background: #f7f7f5;
        color: var(--muted-strong);
      }
      .tree__icon--root {
        border-color: rgba(35, 131, 226, 0.16);
        background: rgba(35, 131, 226, 0.08);
        color: var(--accent-strong);
      }
      .resource-card__glyph,
      .tree__glyph {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        line-height: 1;
      }
      .resource-card__glyph svg,
      .tree__glyph svg {
        width: 18px;
        height: 18px;
      }
      .resource-card__body {
        min-width: 0;
      }
      .resource-card__title {
        margin: 0;
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        font-size: 15px;
      }
      .resource-card__title strong {
        min-width: 0;
        overflow-wrap: anywhere;
      }
      .tag {
        padding: 4px 8px;
        border: 1px solid var(--line);
        background: #ffffff;
        color: var(--muted);
      }
      .tag--accent {
        border-color: rgba(35, 131, 226, 0.18);
        background: var(--accent-soft);
        color: var(--accent-strong);
      }
      .tag--warm {
        border-color: rgba(217, 115, 13, 0.2);
        background: var(--warning-soft);
        color: var(--warning);
      }
      .resource-card__meta,
      .resource-card__id {
        margin-top: 6px;
        color: var(--muted);
        line-height: 1.55;
        font-size: 13px;
      }
      .resource-card__id,
      code {
        font-family: "SFMono-Regular", Consolas, Monaco, monospace;
        font-size: 12px;
      }
      .empty-state {
        padding: 26px 18px;
        border: 1px dashed var(--line-strong);
        border-radius: 14px;
        text-align: center;
        color: var(--muted);
        background: rgba(255, 255, 255, 0.65);
        line-height: 1.65;
      }
      .resource-card__details {
        display: grid;
        gap: 10px;
        margin-top: 4px;
        padding-top: 10px;
        border-top: 1px solid var(--line);
      }
      .resource-card__details-head {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
      }
      .resource-card__details-title {
        margin: 0;
        font-size: 13px;
        font-weight: 600;
        color: var(--muted-strong);
      }
      .resource-card__details-note {
        color: var(--muted);
        font-size: 13px;
        line-height: 1.55;
      }
      .tree,
      .tree ul {
        list-style: none;
        margin: 0;
        padding: 0;
      }
      .tree {
        display: grid;
        gap: 8px;
      }
      .tree ul {
        margin-top: 8px;
        margin-left: 18px;
        padding-left: 14px;
        border-left: 1px solid var(--line);
      }
      .tree__item {
        display: grid;
        gap: 8px;
      }
      .tree__row {
        display: grid;
        grid-template-columns: 36px minmax(0, 1fr);
        gap: 12px;
        align-items: start;
      }
      .tree__title {
        margin: 0;
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        font-size: 14px;
      }
      .tree__title strong {
        min-width: 0;
        overflow-wrap: anywhere;
      }
      .tree__meta {
        margin-top: 4px;
        color: var(--muted);
        font-size: 12px;
        line-height: 1.5;
      }
      .action-bar {
        display: grid;
        gap: 16px;
        margin-top: 16px;
        padding: 16px;
        border-radius: 14px;
        border: 1px solid var(--line);
        background: #ffffff;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), 0 6px 18px rgba(15, 23, 42, 0.04);
      }
      .action-bar__copy {
        display: grid;
        gap: 4px;
      }
      .action-bar__copy strong {
        font-size: 1rem;
      }
      .action-bar__copy span {
        color: var(--muted);
        font-size: 14px;
        line-height: 1.5;
      }
      .controls {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }
      button {
        font: inherit;
      }
      .action-button {
        border: 0;
        border-radius: 10px;
        padding: 10px 14px;
        font-size: 14px;
        font-weight: 600;
        color: white;
        background: var(--ink);
        cursor: pointer;
      }
      .action-button:hover:enabled {
        background: #2d2b27;
      }
      .action-button:disabled {
        cursor: wait;
        opacity: 0.65;
      }
      .action-button.secondary {
        background: white;
        color: var(--ink);
        border: 1px solid var(--line);
      }
      .action-button.secondary:hover:enabled {
        background: #f7f7f5;
      }
      .headless-output {
        margin-top: 18px;
        padding: 20px;
        border-radius: 16px;
        border: 1px solid var(--line);
        background: #ffffff;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), 0 6px 18px rgba(15, 23, 42, 0.04);
      }
      .headless-output[hidden] {
        display: none;
      }
      textarea {
        min-height: 220px;
        resize: vertical;
      }
      .subtle {
        color: var(--muted);
      }
      @media (max-width: 720px) {
        main {
          width: min(100% - 16px, 100%);
          margin: 12px auto 24px;
        }
        .frame {
          padding: 14px;
          border-radius: 14px;
        }
        .panel,
        .action-bar {
          padding: 14px;
          border-radius: 12px;
        }
        .title {
          font-size: 1.5rem;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <section class="frame">
        ${body}
      </section>
    </main>
  </body>
</html>`;
}

function errorPage(title, message) {
  return pageShell({
    title,
    body: `
      <div class="stack">
        <div class="meta-row">
          <span class="eyebrow">Agent Labbook</span>
        </div>
        <h1 class="title">${escapeHtml(title)}</h1>
        <p class="lede">${escapeHtml(message)}</p>
        <div class="notice">You can restart the authorization flow from the plugin when you are ready.</div>
      </div>
    `,
  });
}

function selectionPage({ baseUrl, state, selectionToken, workspaceName, resources, truncated }) {
  const bootstrap = {
    baseUrl,
    state,
    selectionToken,
    workspaceName,
    resources,
    truncated: Boolean(truncated),
  };

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light" />
    <title>Choose Notion Resources</title>
    <style>
      html, body, #selection-app-root {
        min-height: 100%;
        margin: 0;
      }
    </style>
    <style>${SELECTION_UI_CSS}</style>
  </head>
  <body>
    <div id="selection-app-root"></div>
    <script>
      window.__AGENT_LABBOOK_SELECTION__ = ${inlineJson(bootstrap)};
    </script>
    <script>${inlineScriptText(SELECTION_UI_JS)}</script>
  </body>
</html>`;
}

async function handleStart(request, env) {
  if (!env.NOTION_CLIENT_ID || !env.NOTION_CLIENT_SECRET) {
    return jsonResponse(
      {
        ok: false,
        error: "Worker is missing one or more required secrets: NOTION_CLIENT_ID, NOTION_CLIENT_SECRET.",
      },
      { status: 500 },
    );
  }

  const url = new URL(request.url);
  const mode = String(url.searchParams.get("mode") || "local_browser").trim();
  const sessionId = String(url.searchParams.get("session_id") || "").trim();
  const projectName = String(url.searchParams.get("project_name") || "").trim() || null;
  const pageLimit = normalizePageLimit(url.searchParams.get("page_limit"));
  if (!sessionId) {
    return jsonResponse({ ok: false, error: "session_id is required." }, { status: 400 });
  }
  if (!["local_browser", "headless"].includes(mode)) {
    return jsonResponse({ ok: false, error: "mode must be local_browser or headless." }, { status: 400 });
  }

  let returnTo = null;
  if (mode === "local_browser") {
    try {
      returnTo = validateLocalReturnUrl(String(url.searchParams.get("return_to") || ""));
    } catch (exc) {
      return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 400 });
    }
  }

  const baseUrl = getBaseUrl(request, env);
  const redirectUri = `${baseUrl}/oauth/callback`;
  const state = await signState(env, {
    version: 1,
    mode,
    session_id: sessionId,
    return_to: returnTo,
    project_name: projectName,
    page_limit: pageLimit,
    issued_at: Math.floor(Date.now() / 1000),
  });

  const notionUrl = new URL(NOTION_OAUTH_AUTHORIZE_URL);
  notionUrl.searchParams.set("client_id", env.NOTION_CLIENT_ID);
  notionUrl.searchParams.set("redirect_uri", redirectUri);
  notionUrl.searchParams.set("response_type", "code");
  notionUrl.searchParams.set("owner", "user");
  notionUrl.searchParams.set("state", state);

  return Response.redirect(notionUrl.toString(), 302);
}

async function handleCallback(request, env) {
  const url = new URL(request.url);
  const baseUrl = getBaseUrl(request, env);
  const redirectUri = `${baseUrl}/oauth/callback`;

  if (url.searchParams.get("error")) {
    return htmlResponse(
      errorPage(
        "Authorization Cancelled",
        String(url.searchParams.get("error_description") || url.searchParams.get("error") || "Notion did not complete the authorization."),
      ),
      { status: 400 },
    );
  }

  const code = String(url.searchParams.get("code") || "").trim();
  const rawState = String(url.searchParams.get("state") || "").trim();
  if (!code || !rawState) {
    return htmlResponse(errorPage("Missing Parameters", "Both code and state are required."), { status: 400 });
  }

  try {
    const state = await verifyState(env, rawState);
    const tokenPayload = await exchangeCode(env, redirectUri, code);
    const sanitizedTokenPayload = {
      access_token: tokenPayload.access_token,
      refresh_token: tokenPayload.refresh_token,
      token_type: tokenPayload.token_type,
      bot_id: tokenPayload.bot_id,
      workspace_id: tokenPayload.workspace_id,
      workspace_name: tokenPayload.workspace_name,
      workspace_icon: tokenPayload.workspace_icon,
      duplicated_template_id: tokenPayload.duplicated_template_id,
      owner: tokenPayload.owner || null,
    };
    const selectionToken = await encryptPrivatePayload(env, {
      version: 1,
      purpose: "selection_session",
      issued_at: Math.floor(Date.now() / 1000),
      session_id: state.session_id,
      backend_url: baseUrl,
      token: sanitizedTokenPayload,
    });
    return htmlResponse(
      selectionPage({
        baseUrl,
        state,
        selectionToken,
        workspaceName: sanitizedTokenPayload.workspace_name || null,
        resources: [],
        truncated: false,
      }),
    );
  } catch (exc) {
    console.error("OAuth callback failed", exc);
    return htmlResponse(errorPage("Authorization Failed", String(exc.message || exc)), { status: 500 });
  }
}

async function handleDiscoverChildren(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const selectionToken = String(payload?.selection_token || "").trim();
    if (!selectionToken) {
      return jsonResponse({ ok: false, error: "selection_token is required." }, { status: 400 });
    }
    const selectionSession = await resolveSelectionSession(env, selectionToken);
    const accessToken = String(selectionSession.token.access_token || "").trim();

    const pageIds = Array.from(
      new Set(
        (Array.isArray(payload?.page_ids) ? payload.page_ids : [])
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    );
    const dataSourceIds = Array.from(
      new Set(
        (Array.isArray(payload?.data_source_ids) ? payload.data_source_ids : [])
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    );

    if (!pageIds.length && !dataSourceIds.length) {
      return jsonResponse({ ok: true, truncated: false, resources: [] });
    }

    const graphDiscovery = await discoverResourceGraph(
      env,
      accessToken,
      [
        ...pageIds.map((pageId) => ({ resource_id: pageId, resource_type: "page" })),
        ...dataSourceIds.map((dataSourceId) => ({ resource_id: dataSourceId, resource_type: "data_source" })),
      ],
      {
        depth_limit: payload?.depth_limit,
        node_limit: payload?.node_limit,
      },
    );

    return jsonResponse({
      ok: true,
      truncated: graphDiscovery.truncated,
      resources: graphDiscovery.resources,
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleCatalog(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const selectionToken = String(payload?.selection_token || "").trim();
    if (!selectionToken) {
      return jsonResponse({ ok: false, error: "selection_token is required." }, { status: 400 });
    }
    const selectionSession = await resolveSelectionSession(env, selectionToken);
    const accessToken = String(selectionSession.token.access_token || "").trim();

    const pageLimit = normalizePageLimit(payload?.page_limit);
    const searchPayload = await buildSelectionCatalog(env, accessToken, pageLimit);
    return jsonResponse({
      ok: true,
      truncated: searchPayload.truncated,
      resources: searchPayload.resources,
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleFinalizeSelection(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const selectionToken = String(payload?.selection_token || "").trim();
    if (!selectionToken) {
      return jsonResponse({ ok: false, error: "selection_token is required." }, { status: 400 });
    }

    const selectionSession = await resolveSelectionSession(env, selectionToken);

    const selectedResources = Array.isArray(payload?.selected_resources)
      ? payload.selected_resources.filter((item) => item && typeof item === "object")
      : [];

    const handoffBundle = await encryptPrivatePayload(env, {
      version: 1,
      purpose: "handoff_bundle",
      issued_at: Math.floor(Date.now() / 1000),
      session_id: String(selectionSession.session_id || "").trim(),
      backend_url: String(selectionSession.backend_url || getBaseUrl(request, env)).trim(),
      token: selectionSession.token,
      selected_resources: selectedResources,
    });

    return jsonResponse({
      ok: true,
      handoff_bundle: handoffBundle,
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleConsumeHandoff(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const handoffBundle = String(payload?.handoff_bundle || "").trim();
    const sessionId = String(payload?.session_id || "").trim();
    if (!sessionId) {
      return jsonResponse({ ok: false, error: "session_id is required." }, { status: 400 });
    }
    if (!handoffBundle) {
      return jsonResponse({ ok: false, error: "handoff_bundle is required." }, { status: 400 });
    }

    const signedHandoff = await decryptPrivatePayload(env, handoffBundle);
    ensureRecentPayload(signedHandoff, HANDOFF_BUNDLE_TTL_SECONDS, "Handoff bundle expired.");
    if (signedHandoff?.purpose !== "handoff_bundle") {
      return jsonResponse({ ok: false, error: "Invalid handoff bundle." }, { status: 400 });
    }
    if (String(signedHandoff.session_id || "").trim() !== sessionId) {
      return jsonResponse({ ok: false, error: "Handoff bundle session mismatch." }, { status: 400 });
    }

    return jsonResponse({
      ok: true,
      payload: {
        session_id: String(signedHandoff.session_id || "").trim(),
        backend_url: String(signedHandoff.backend_url || getBaseUrl(request, env)).trim(),
        token: signedHandoff.token && typeof signedHandoff.token === "object" ? signedHandoff.token : {},
        selected_resources: Array.isArray(signedHandoff.selected_resources)
          ? signedHandoff.selected_resources.filter((item) => item && typeof item === "object")
          : [],
      },
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleRefresh(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }
  try {
    const payload = await request.json();
    const refreshToken = String(payload.refresh_token || "").trim();
    if (!refreshToken) {
      return jsonResponse({ ok: false, error: "refresh_token is required." }, { status: 400 });
    }
    const tokenPayload = await refreshAccessToken(env, refreshToken);
    return jsonResponse({
      ok: true,
      token: {
        access_token: tokenPayload.access_token,
        refresh_token: tokenPayload.refresh_token,
        token_type: tokenPayload.token_type,
        bot_id: tokenPayload.bot_id,
        workspace_id: tokenPayload.workspace_id,
        workspace_name: tokenPayload.workspace_name,
        workspace_icon: tokenPayload.workspace_icon,
        duplicated_template_id: tokenPayload.duplicated_template_id,
        owner: tokenPayload.owner || null,
      },
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleHealth(request, env) {
  const baseUrl = getBaseUrl(request, env);
  return jsonResponse({
    ok: true,
    configured: Boolean(env.NOTION_CLIENT_ID && env.NOTION_CLIENT_SECRET),
    base_url: baseUrl,
    redirect_uri: `${baseUrl}/oauth/callback`,
    notion_version: getNotionVersion(env),
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/health") {
      return handleHealth(request, env);
    }
    if (url.pathname === "/oauth/start") {
      return handleStart(request, env);
    }
    if (url.pathname === "/oauth/callback") {
      return handleCallback(request, env);
    }
    if (url.pathname === "/api/discover-children") {
      return handleDiscoverChildren(request, env);
    }
    if (url.pathname === "/api/catalog") {
      return handleCatalog(request, env);
    }
    if (url.pathname === "/api/finalize-selection") {
      return handleFinalizeSelection(request, env);
    }
    if (url.pathname === "/api/consume-handoff") {
      return handleConsumeHandoff(request, env);
    }
    if (url.pathname === "/api/refresh") {
      return handleRefresh(request, env);
    }
    return jsonResponse({ ok: false, error: "Not found." }, { status: 404 });
  },
};
