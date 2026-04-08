import { SELECTION_UI_CSS, SELECTION_UI_JS } from "../generated/selection_ui_bundle.js";

const DEFAULT_NOTION_VERSION = "2026-03-11";
const DEFAULT_OAUTH_BASE_URL = "https://superplanner.ai/notion/oauth";
const BROKER_API_VERSION = 1;
const SUPPORTED_BROKER_API_VERSIONS = Object.freeze([BROKER_API_VERSION]);
const DEFAULT_PAGE_LIMIT = 200;
const MIN_PAGE_LIMIT = 25;
const MAX_PAGE_LIMIT = 1000;
const DEFAULT_SEARCH_LIMIT = 50;
const MAX_SEARCH_LIMIT = 100;
const DEFAULT_DISCOVERY_NODE_LIMIT = 2000;
const MAX_DISCOVERY_NODE_LIMIT = 5000;
const MAX_BLOCK_SCAN_LIMIT = 20000;
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

function clampInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? fallback), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(parsed, minimum), maximum);
}

function getBaseUrl(request, env) {
  const configured = String(env.PUBLIC_BASE_URL || "").trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  return new URL(request.url).origin;
}

function normalizeBasePath(pathname) {
  let normalized = String(pathname || "").trim();
  if (!normalized || normalized === "/") {
    return "";
  }
  if (!normalized.startsWith("/")) {
    normalized = `/${normalized}`;
  }
  normalized = normalized.replace(/\/+$/, "");
  return normalized === "/" ? "" : normalized;
}

function getBasePath(request, env) {
  const configured = String(env.PUBLIC_BASE_URL || "").trim();
  if (!configured) {
    return "";
  }
  try {
    return normalizeBasePath(new URL(configured).pathname);
  } catch {
    return "";
  }
}

function getWorkerPath(request, env) {
  const pathname = new URL(request.url).pathname || "/";
  const basePath = getBasePath(request, env);
  if (!basePath) {
    return pathname;
  }
  if (pathname === basePath || pathname === `${basePath}/`) {
    return "/";
  }
  if (pathname.startsWith(`${basePath}/`)) {
    return pathname.slice(basePath.length) || "/";
  }
  return null;
}

function getNotionVersion(env) {
  return String(env.NOTION_VERSION || DEFAULT_NOTION_VERSION).trim() || DEFAULT_NOTION_VERSION;
}

function getOauthBaseUrl(env) {
  const configured = String(env.NOTION_OAUTH_BASE_URL || DEFAULT_OAUTH_BASE_URL).trim();
  return configured.replace(/\/+$/, "") || DEFAULT_OAUTH_BASE_URL;
}

function oauthInternalUrl(env, path) {
  const pathname = normalizeBasePath(new URL(getOauthBaseUrl(env)).pathname);
  return new URL(`${pathname}${path}`, "https://notion-access-broker").toString();
}

async function postOauthJson(env, path, payload) {
  function validateOauthPayload(decoded) {
    if (!(decoded && typeof decoded === "object" && !Array.isArray(decoded))) {
      throw new Error("OAuth backend returned an unexpected payload.");
    }
    if (!Array.isArray(decoded.supported_api_versions) || decoded.supported_api_versions.length === 0) {
      throw new Error("OAuth backend did not declare supported_api_versions.");
    }
    if (!decoded.supported_api_versions.includes(decoded.api_version)) {
      throw new Error(
        `OAuth backend returned a malformed compatibility envelope. api_version=${JSON.stringify(decoded.api_version)}, supported_api_versions=${JSON.stringify(decoded.supported_api_versions)}.`,
      );
    }
    if (!SUPPORTED_BROKER_API_VERSIONS.includes(decoded.api_version)) {
      throw new Error(
        `OAuth backend API version mismatch. Client supports ${JSON.stringify(SUPPORTED_BROKER_API_VERSIONS)}, broker reported api_version=${JSON.stringify(decoded.api_version)} and supported_api_versions=${JSON.stringify(decoded.supported_api_versions)}.`,
      );
    }
    return decoded;
  }

  const requestInit = {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "x-notion-access-broker-accept-api-versions": SUPPORTED_BROKER_API_VERSIONS.join(","),
    },
    body: JSON.stringify(payload),
  };
  const response =
    env.NOTION_OAUTH_SERVICE && typeof env.NOTION_OAUTH_SERVICE.fetch === "function"
      ? await env.NOTION_OAUTH_SERVICE.fetch(oauthInternalUrl(env, path), requestInit)
      : await fetch(`${getOauthBaseUrl(env)}${path}`, requestInit);
  const raw = await response.text();
  let decoded = {};
  if (raw.trim()) {
    try {
      decoded = JSON.parse(raw);
    } catch {
      decoded = {
        error: raw.trim(),
      };
    }
  }
  decoded = validateOauthPayload(decoded);
  if (!response.ok) {
    throw new Error(String(decoded.error || `OAuth backend returned HTTP ${response.status}`));
  }
  return decoded;
}

async function resolveSharedOauthSession(env, oauthSession) {
  const payload = await postOauthJson(env, "/api/resolve-session", {
    integration: "agent-labbook",
    oauth_session: oauthSession,
  });
  if (!payload.ok || !(payload.session && typeof payload.session === "object")) {
    throw new Error(String(payload.error || "OAuth backend could not resolve the OAuth session."));
  }
  return payload.session;
}

async function resolveAuthContext(env, payload) {
  const oauthSession = String(payload?.oauth_session || "").trim();
  if (oauthSession) {
    const sharedSession = await resolveSharedOauthSession(env, oauthSession);
    return {
      kind: "shared",
      accessToken: String(sharedSession.access_token || "").trim(),
      mode: sharedSession.mode || null,
      sharedSession,
      oauthSession,
    };
  }

  throw new Error("oauth_session is required.");
}

function normalizePageLimit(value) {
  return clampInteger(value, DEFAULT_PAGE_LIMIT, MIN_PAGE_LIMIT, MAX_PAGE_LIMIT);
}

function normalizeSearchLimit(value) {
  return clampInteger(value, DEFAULT_SEARCH_LIMIT, 1, MAX_SEARCH_LIMIT);
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

function notionBearerHeaders(env, accessToken) {
  return {
    authorization: `Bearer ${accessToken}`,
    accept: "application/json",
    "content-type": "application/json",
    "notion-version": getNotionVersion(env),
  };
}

async function fetchSelectableResources(env, accessToken, pageLimit, options = {}) {
  const resources = [];
  const seenIds = new Set();
  let nextCursor = null;
  const query = String(options.query || "").trim();

  while (resources.length < pageLimit) {
    const body = {
      page_size: Math.min(100, pageLimit - resources.length),
      sort: {
        direction: "descending",
        timestamp: "last_edited_time",
      },
    };
    if (query) {
      body.query = query;
    }
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
      break;
    }
    nextCursor = payload.next_cursor;
  }

  return resources;
}

async function queryDataSourceEntries(env, accessToken, dataSourceId, remainingLimit, discoveryMeta = {}) {
  const resources = [];
  const seenIds = new Set();
  let nextCursor = null;

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
      break;
    }
    nextCursor = payload.next_cursor;
  }

  return resources;
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

  return resources;
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

async function buildSelectionCatalog(env, accessToken, pageLimit) {
  return fetchSelectableResources(env, accessToken, pageLimit);
}

async function searchSelectableResources(env, accessToken, query, limit) {
  return fetchSelectableResources(env, accessToken, limit, {
    query,
  });
}

async function discoverImmediateChildren(env, accessToken, pageIds, dataSourceIds, options = {}) {
  const nodeLimit = clampInteger(options.node_limit, DEFAULT_DISCOVERY_NODE_LIMIT, 1, MAX_DISCOVERY_NODE_LIMIT);
  const resources = [];
  const scanState = {
    scannedBlockCount: 0,
    truncated: false,
  };
  const remainingCapacity = () => Math.max(1, nodeLimit - resources.length);

  for (const pageId of pageIds) {
    if (resources.length >= nodeLimit) {
      break;
    }
    const payload = await discoverPageImmediateChildren(env, accessToken, pageId, {
      root_id: pageId,
      depth: 0,
      remaining_limit: remainingCapacity(),
      scan_state: scanState,
    });
    resources.push(...payload);
    if (scanState.truncated) {
      break;
    }
  }

  for (const dataSourceId of dataSourceIds) {
    if (resources.length >= nodeLimit || scanState.truncated) {
      break;
    }
    const payload = await discoverDataSourceImmediateChildren(env, accessToken, dataSourceId, {
      root_id: dataSourceId,
      depth: 0,
      remaining_limit: remainingCapacity(),
    });
    resources.push(...payload);
  }

  return mergeResources(resources);
}

function pageShell({ title, body }) {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(title)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Inter:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet" />
    <style>
      :root {
        color-scheme: light;
        --bg: #faf7f2;
        --canvas: rgba(255, 253, 250, 0.82);
        --panel: #ffffff;
        --panel-strong: #ffffff;
        --ink: #1a1410;
        --muted: #6b5e52;
        --muted-strong: #5a4e43;
        --subtle: #9a8d80;
        --line: rgba(28, 20, 15, 0.08);
        --line-strong: rgba(28, 20, 15, 0.16);
        --accent: #c4532d;
        --accent-strong: #a8432a;
        --accent-soft: rgba(196, 83, 45, 0.10);
        --accent-faint: rgba(196, 83, 45, 0.06);
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
        font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        -webkit-font-smoothing: antialiased;
      }
      .sp-nav {
        position: sticky;
        top: 0;
        z-index: 100;
        backdrop-filter: blur(20px) saturate(1.4);
        -webkit-backdrop-filter: blur(20px) saturate(1.4);
        background: rgba(250, 247, 242, 0.85);
        border-bottom: 1px solid var(--line);
      }
      .sp-nav-inner {
        max-width: 1000px;
        margin: 0 auto;
        padding: 0 24px;
        height: 56px;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .sp-nav-brand {
        font-family: 'DM Serif Display', serif;
        font-size: 20px;
        color: var(--ink);
        text-decoration: none;
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
        box-shadow: 0 1px 2px rgba(40, 24, 10, 0.03), 0 8px 24px rgba(40, 24, 10, 0.05);
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
        font-family: 'DM Serif Display', serif;
        font-size: clamp(1.7rem, 4vw, 2.25rem);
        line-height: 1.15;
        letter-spacing: -0.02em;
        font-weight: 400;
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
        color: var(--accent);
        background: var(--accent-soft);
        border: 1px solid rgba(196, 83, 45, 0.18);
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
        box-shadow: 0 1px 2px rgba(40, 24, 10, 0.03), 0 6px 18px rgba(40, 24, 10, 0.05);
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
        outline: 2px solid rgba(196, 83, 45, 0.16);
        outline-offset: 2px;
        border-color: rgba(196, 83, 45, 0.32);
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
        border-color: rgba(196, 83, 45, 0.34);
        background: #fef8f5;
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
        outline: 2px solid rgba(196, 83, 45, 0.18);
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
        background: #faf7f2;
        color: var(--muted-strong);
      }
      .tree__icon--root {
        border-color: rgba(196, 83, 45, 0.16);
        background: rgba(196, 83, 45, 0.08);
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
        border-color: rgba(196, 83, 45, 0.18);
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
        background: rgba(255, 253, 250, 0.65);
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
        box-shadow: 0 1px 2px rgba(40, 24, 10, 0.03), 0 6px 18px rgba(40, 24, 10, 0.05);
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
        background: var(--accent);
        cursor: pointer;
        transition: background .2s, transform .15s;
      }
      .action-button:hover:enabled {
        background: var(--accent-strong);
        transform: translateY(-1px);
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
        background: #faf7f2;
      }
      .headless-output {
        margin-top: 18px;
        padding: 20px;
        border-radius: 16px;
        border: 1px solid var(--line);
        background: #ffffff;
        box-shadow: 0 1px 2px rgba(40, 24, 10, 0.03), 0 6px 18px rgba(40, 24, 10, 0.05);
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
      .sp-footer {
        border-top: 1px solid var(--line);
        padding: 24px;
        text-align: center;
        font-size: 13px;
        color: var(--subtle);
      }
      .sp-footer a {
        color: var(--muted);
        text-decoration: none;
      }
      .sp-footer a:hover {
        text-decoration: underline;
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
    <nav class="sp-nav">
      <div class="sp-nav-inner">
        <a href="https://superplanner.ai/notion/agent-labbook/" class="sp-nav-brand">Agent Labbook</a>
      </div>
    </nav>
    <main>
      <section class="frame">
        ${body}
      </section>
    </main>
    <footer class="sp-footer">
      &copy; ${new Date().getFullYear()} <a href="https://gridheap.com/">Grid Heap</a>. All rights reserved.
    </footer>
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
        <div class="notice">You can restart the authorization flow from the integration when you are ready.</div>
      </div>
    `,
  });
}

function selectionPage({ baseUrl, state, oauthSession, workspaceName, resources, catalogLoaded }) {
  const bootstrap = {
    baseUrl,
    state,
    oauthSession,
    workspaceName,
    resources,
    catalogLoaded: Boolean(catalogLoaded),
  };

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light" />
    <title>Choose Notion Resources</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Inter:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet" />
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

function landingPage() {
  const year = new Date().getFullYear();
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Agent Labbook — Connect Your AI Agent to Notion</title>
    <meta name="description" content="Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. One command to connect, per-project scoping, secure credential reuse." />
    <meta property="og:title" content="Agent Labbook — Connect Your AI Agent to Notion" />
    <meta property="og:description" content="The Notion connection layer for AI coding agents. One command to connect, per-project scoping, secure credential storage." />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://superplanner.ai/notion/agent-labbook/" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Inter:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700&family=Noto+Serif+TC:wght@600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
    <style>
      :root {
        --bg: #faf7f2;
        --ink: #1a1410;
        --muted: #6b5e52;
        --subtle: #9a8d80;
        --accent: #c4532d;
        --accent-hover: #a8432a;
        --accent-soft: rgba(196,83,45,0.10);
        --surface: rgba(255,253,250,0.82);
        --line: rgba(28,20,15,0.08);
        --radius: 20px;
        --radius-lg: 28px;
        --shadow-sm: 0 1px 3px rgba(40,24,10,0.06);
        --shadow-md: 0 8px 32px rgba(40,24,10,0.08);
      }
      *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
      html{scroll-behavior:smooth}
      body{font-family:'Inter',system-ui,sans-serif;color:var(--ink);background:var(--bg);min-height:100vh;-webkit-font-smoothing:antialiased}
      body.zh-hans h1,body.zh-hans .section-title{font-family:'Noto Serif SC','DM Serif Display',serif}
      body.zh-hant h1,body.zh-hant .section-title{font-family:'Noto Serif TC','DM Serif Display',serif}

      /* NAV */
      nav{position:sticky;top:0;z-index:100;backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);background:rgba(250,247,242,0.85);border-bottom:1px solid var(--line)}
      .nav-inner{max-width:1000px;margin:0 auto;padding:0 24px;height:56px;display:flex;align-items:center;justify-content:space-between}
      .nav-brand{font-family:'DM Serif Display',serif;font-size:20px;color:var(--ink);text-decoration:none}
      .nav-links{display:flex;align-items:center;gap:6px}
      .nav-links a,.lang-btn{font-size:14px;font-weight:500;color:var(--muted);text-decoration:none;padding:6px 12px;border-radius:10px;border:none;background:none;cursor:pointer;transition:all .2s}
      .nav-links a:hover,.lang-btn:hover{background:var(--accent-soft);color:var(--accent)}
      .lang-btn.active{background:var(--accent-soft);color:var(--accent);font-weight:600}
      .lang-sep{color:var(--line);font-size:14px;user-select:none}

      /* HERO */
      .hero{max-width:1000px;margin:0 auto;padding:80px 24px 64px;text-align:center}
      .hero-badge{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:13px;font-weight:600;letter-spacing:.04em}
      .hero-badge svg{width:16px;height:16px}
      h1{font-family:'DM Serif Display',serif;font-size:clamp(40px,7vw,72px);line-height:1.05;margin:20px 0 0;letter-spacing:-.02em}
      .hero-sub{margin:20px auto 0;max-width:600px;font-size:clamp(16px,2vw,19px);line-height:1.65;color:var(--muted)}
      .hero-pills{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:28px}
      .hero-pills span{padding:8px 14px;border:1px solid var(--line);border-radius:999px;font-size:13px;font-weight:500;color:var(--muted);background:var(--surface)}

      /* STEPS */
      .steps{max-width:1000px;margin:0 auto;padding:0 24px 80px}
      .section-label{font-size:13px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
      .section-title{font-family:'DM Serif Display',serif;font-size:clamp(28px,5vw,44px);line-height:1.1;margin-bottom:48px}
      .steps-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
      .step-card{padding:28px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);position:relative}
      .step-num{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:12px;background:var(--accent);color:#fff;font-family:'DM Serif Display',serif;font-size:18px;margin-bottom:16px}
      .step-card h3{font-size:18px;font-weight:700;margin-bottom:8px}
      .step-card p{font-size:15px;line-height:1.6;color:var(--muted)}

      /* FEATURES */
      .features{max-width:1000px;margin:0 auto;padding:0 24px 80px}
      .feature-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
      .feature-card{padding:28px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);transition:box-shadow .25s,transform .25s}
      .feature-card:hover{box-shadow:var(--shadow-md);transform:translateY(-2px)}
      .feature-icon{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);display:flex;align-items:center;justify-content:center;margin-bottom:18px}
      .feature-icon svg{width:24px;height:24px;color:var(--accent)}
      .feature-card h3{font-size:18px;font-weight:700;margin-bottom:8px}
      .feature-card p{font-size:15px;line-height:1.6;color:var(--muted)}

      /* QUICKSTART */
      .quickstart{max-width:1000px;margin:0 auto;padding:0 24px 80px}
      .qs-card{padding:32px 36px;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);box-shadow:var(--shadow-sm)}
      .qs-card h3{font-size:20px;font-weight:700;margin-bottom:6px}
      .qs-card>p{font-size:15px;line-height:1.6;color:var(--muted);margin-bottom:24px}
      .code-block{position:relative;background:#1a1410;color:#e8e0d6;border-radius:14px;padding:18px 20px;font-family:'JetBrains Mono','SFMono-Regular',Consolas,monospace;font-size:13px;line-height:1.7;overflow-x:auto;margin-bottom:12px}
      .code-block:last-child{margin-bottom:0}
      .code-comment{color:#7a6e62}
      .code-cmd{color:#e8734e}
      .qs-or{display:block;text-align:center;color:var(--subtle);font-size:13px;font-weight:500;margin:12px 0}

      /* GITHUB CTA */
      .gh-cta{max-width:1000px;margin:0 auto;padding:0 24px 80px;text-align:center}
      .gh-link{display:inline-flex;align-items:center;gap:8px;padding:14px 28px;border-radius:14px;background:var(--ink);color:#fff;font-size:16px;font-weight:600;text-decoration:none;transition:background .2s,transform .15s}
      .gh-link:hover{background:#2d2520;transform:translateY(-1px)}
      .gh-link svg{width:20px;height:20px}

      /* FOOTER */
      footer{border-top:1px solid var(--line);padding:32px 24px;text-align:center}
      footer p{font-size:13px;color:var(--subtle)}
      footer a{color:var(--muted);text-decoration:none}
      footer a:hover{text-decoration:underline}

      /* LANG */
      [data-lang="zh-hans"],[data-lang="zh-hant"]{display:none}
      body.zh-hans [data-lang="en"],body.zh-hans [data-lang="zh-hant"]{display:none}
      body.zh-hans [data-lang="zh-hans"]{display:revert}
      body.zh-hant [data-lang="en"],body.zh-hant [data-lang="zh-hans"]{display:none}
      body.zh-hant [data-lang="zh-hant"]{display:revert}

      @media(max-width:900px){
        .steps-grid,.feature-grid{grid-template-columns:1fr}
      }
      @media(max-width:600px){
        .hero{padding:48px 20px 40px}
        .steps,.features,.quickstart,.gh-cta{padding-left:20px;padding-right:20px}
      }
    </style>
  </head>
  <body>
    <nav>
      <div class="nav-inner">
        <a href="https://superplanner.ai/" class="nav-brand">SuperPlanner</a>
        <div class="nav-links">
          <a href="https://superplanner.ai/mise/" data-lang="en">Chef de Mise</a>
          <a href="https://superplanner.ai/mise/" data-lang="zh-hans">Chef de Mise</a>
          <a href="https://superplanner.ai/mise/" data-lang="zh-hant">Chef de Mise</a>
          <a href="https://superplanner.ai/notion/agent-labbook/" data-lang="en">Agent Labbook</a>
          <a href="https://superplanner.ai/notion/agent-labbook/" data-lang="zh-hans">Agent Labbook</a>
          <a href="https://superplanner.ai/notion/agent-labbook/" data-lang="zh-hant">Agent Labbook</a>
          <span class="lang-sep">|</span>
          <button class="lang-btn active" id="btn-en" onclick="setLang('en')">EN</button>
          <button class="lang-btn" id="btn-zh-hans" onclick="setLang('zh-hans')">简体</button>
          <button class="lang-btn" id="btn-zh-hant" onclick="setLang('zh-hant')">繁體</button>
        </div>
      </div>
    </nav>

    <!-- ====== HERO — EN ====== -->
    <section class="hero" data-lang="en">
      <span class="hero-badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
        Notion Public Integration
      </span>
      <h1>Connect Your AI Agent<br/>to Notion</h1>
      <p class="hero-sub">Agent Labbook is the Notion connection layer for Codex, Claude Code, and other MCP clients. One command to authenticate, per-project scoping for the pages and databases your agent needs, and secure credential reuse across every project.</p>
      <div class="hero-pills">
        <span>OpenAI Codex</span>
        <span>Claude Code</span>
        <span>Any MCP Client</span>
        <span>Per-Project Scoping</span>
        <span>Open Source</span>
      </div>
    </section>

    <!-- ====== HERO — 简中 ====== -->
    <section class="hero" data-lang="zh-hans">
      <span class="hero-badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
        Notion 公共集成
      </span>
      <h1>把 Notion 页面<br/>接入你的 AI 助手</h1>
      <p class="hero-sub">Agent Labbook 是面向 Codex、Claude Code 及所有 MCP 客户端的 Notion 连接层。一条命令完成授权，按项目绑定所需的页面和数据库，凭证安全复用，项目之间互不干扰。</p>
      <div class="hero-pills">
        <span>OpenAI Codex</span>
        <span>Claude Code</span>
        <span>所有 MCP 客户端</span>
        <span>按项目隔离</span>
        <span>开源</span>
      </div>
    </section>

    <!-- ====== HERO — 繁中 ====== -->
    <section class="hero" data-lang="zh-hant">
      <span class="hero-badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>
        Notion 公開整合
      </span>
      <h1>把 Notion 頁面<br/>接入你的 AI 助手</h1>
      <p class="hero-sub">Agent Labbook 是為 Codex、Claude Code 及所有 MCP 用戶端打造的 Notion 連線層。一行指令完成授權，依專案綁定所需的頁面與資料庫，憑證安全重複使用，專案之間互不干擾。</p>
      <div class="hero-pills">
        <span>OpenAI Codex</span>
        <span>Claude Code</span>
        <span>所有 MCP 用戶端</span>
        <span>依專案隔離</span>
        <span>開源</span>
      </div>
    </section>

    <!-- ====== HOW IT WORKS — EN ====== -->
    <section class="steps" data-lang="en">
      <p class="section-label">How It Works</p>
      <h2 class="section-title">Three steps to get your Notion pages into your agent</h2>
      <div class="steps-grid">
        <article class="step-card">
          <div class="step-num">1</div>
          <h3>Install</h3>
          <p>Add Agent Labbook to your MCP client with a single command. Works with Codex, Claude Code, or any MCP-capable tool.</p>
        </article>
        <article class="step-card">
          <div class="step-num">2</div>
          <h3>Authorize</h3>
          <p>The agent opens a Notion OAuth consent page. Sign in, choose which pages and databases to share, and approve. Credentials are stored securely in 1Password or your system keyring.</p>
        </article>
        <article class="step-card">
          <div class="step-num">3</div>
          <h3>Build</h3>
          <p>Your agent gets direct access to the official Notion API with proper tokens and bound resource IDs. Read docs, write pages, query databases \u2014 whatever the project needs.</p>
        </article>
      </div>
    </section>

    <!-- ====== HOW IT WORKS — 简中 ====== -->
    <section class="steps" data-lang="zh-hans">
      <p class="section-label">工作流程</p>
      <h2 class="section-title">三步搞定，Notion 页面直达你的 AI 助手</h2>
      <div class="steps-grid">
        <article class="step-card">
          <div class="step-num">1</div>
          <h3>安装</h3>
          <p>一条命令把 Agent Labbook 添加到 MCP 客户端。支持 Codex、Claude Code 及任何兼容 MCP 的工具。</p>
        </article>
        <article class="step-card">
          <div class="step-num">2</div>
          <h3>授权</h3>
          <p>AI 助手会打开 Notion OAuth 授权页面。登录后勾选要共享的页面和数据库，点击同意即可。凭证安全存储在 1Password 或系统钥匙串中。</p>
        </article>
        <article class="step-card">
          <div class="step-num">3</div>
          <h3>开发</h3>
          <p>AI 助手获得 Notion 官方 API 的直接访问权限，持有合法 Token 和已绑定的资源 ID。读文档、写页面、查数据库——项目需要什么就做什么。</p>
        </article>
      </div>
    </section>

    <!-- ====== HOW IT WORKS — 繁中 ====== -->
    <section class="steps" data-lang="zh-hant">
      <p class="section-label">運作方式</p>
      <h2 class="section-title">三個步驟，把 Notion 頁面送進你的 AI 助手</h2>
      <div class="steps-grid">
        <article class="step-card">
          <div class="step-num">1</div>
          <h3>安裝</h3>
          <p>一行指令將 Agent Labbook 加入 MCP 用戶端。支援 Codex、Claude Code 及任何相容 MCP 的工具。</p>
        </article>
        <article class="step-card">
          <div class="step-num">2</div>
          <h3>授權</h3>
          <p>AI 助手會開啟 Notion OAuth 授權頁面。登入後勾選要分享的頁面與資料庫，按下同意即完成。憑證安全儲存於 1Password 或系統鑰匙圈中。</p>
        </article>
        <article class="step-card">
          <div class="step-num">3</div>
          <h3>開發</h3>
          <p>AI 助手取得 Notion 官方 API 的直接存取權限，持有合法 Token 與已綁定的資源 ID。讀文件、寫頁面、查資料庫——專案需要什麼就做什麼。</p>
        </article>
      </div>
    </section>

    <!-- ====== FEATURES — EN ====== -->
    <section class="features" id="features" data-lang="en">
      <p class="section-label">Features</p>
      <h2 class="section-title">Auth infrastructure, not an API bottleneck</h2>
      <div class="feature-grid">
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>
          </div>
          <h3>Per-Project Scoping</h3>
          <p>Each project binds only the Notion pages and databases it needs. Subtree scope covers an entire page tree with one selection. No accidental cross-project data access.</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14L21 3"/><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/></svg>
          </div>
          <h3>Credential Reuse</h3>
          <p>Authorize Notion once on a machine, then attach that credential to any new project without re-running OAuth. Tokens live in 1Password or your system keyring, never in project files.</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>
          </div>
          <h3>Works Everywhere</h3>
          <p>Local machines with a browser, SSH sessions, headless CI \u2014 the system detects the environment and switches between browser-based and headless auth automatically.</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </div>
          <h3>Secure by Design</h3>
          <p>Tokens never touch project files or git. Handoff bundles are cryptographically signed. The hosted backend processes tokens in memory only and never persists them.</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m-7-7h6m6 0h6"/><path d="M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24m0-15.56l-4.24 4.24m-7.08 7.08l-4.24 4.24"/></svg>
          </div>
          <h3>MCP-Native</h3>
          <p>14 tools, 3 resources, 2 prompts \u2014 all with structured output schemas. Works out of the box with any MCP-capable client. Your agent drives the entire flow programmatically.</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>
          </div>
          <h3>Self-Hostable</h3>
          <p>Everything can run on your own Cloudflare account with custom domain routes. Full control over the backend, zero dependency on third-party infrastructure.</p>
        </article>
      </div>
    </section>

    <!-- ====== FEATURES — 简中 ====== -->
    <section class="features" data-lang="zh-hans">
      <p class="section-label">功能特性</p>
      <h2 class="section-title">认证基础设施，而非 API 瓶颈</h2>
      <div class="feature-grid">
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>
          </div>
          <h3>按项目隔离</h3>
          <p>每个项目只绑定它需要的 Notion 页面和数据库。子树范围一次选中即可覆盖整棵页面树，杜绝跨项目的意外数据访问。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14L21 3"/><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/></svg>
          </div>
          <h3>凭证复用</h3>
          <p>同一台机器只需授权一次 Notion，新项目直接复用已有凭证，无需重走 OAuth。Token 存储在 1Password 或系统钥匙串中，绝不出现在项目文件里。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>
          </div>
          <h3>随处可用</h3>
          <p>本地有浏览器的机器、SSH 远程会话、无头 CI——系统自动检测运行环境，在浏览器授权和无头授权之间智能切换。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </div>
          <h3>安全为先</h3>
          <p>Token 绝不触碰项目文件或 Git。交接包经过加密签名。托管后端仅在内存中处理 Token，从不落盘。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m-7-7h6m6 0h6"/><path d="M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24m0-15.56l-4.24 4.24m-7.08 7.08l-4.24 4.24"/></svg>
          </div>
          <h3>MCP 原生</h3>
          <p>提供 14 个工具、3 个资源、2 个提示词，全部带结构化输出 Schema。开箱即用，任何 MCP 客户端均可接入。AI 助手以编程方式驱动全部流程。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>
          </div>
          <h3>可自部署</h3>
          <p>所有组件都可部署在你自己的 Cloudflare 账号上，支持自定义域名路由。完全掌控后端，零依赖第三方基础设施。</p>
        </article>
      </div>
    </section>

    <!-- ====== FEATURES — 繁中 ====== -->
    <section class="features" data-lang="zh-hant">
      <p class="section-label">功能特色</p>
      <h2 class="section-title">認證基礎建設，而非 API 瓶頸</h2>
      <div class="feature-grid">
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>
          </div>
          <h3>依專案隔離</h3>
          <p>每個專案只綁定所需的 Notion 頁面與資料庫。子樹範圍一次勾選即可涵蓋整棵頁面樹，杜絕跨專案的意外資料存取。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14L21 3"/><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/></svg>
          </div>
          <h3>憑證重複使用</h3>
          <p>同一台機器只需授權一次 Notion，新專案直接沿用既有憑證，無需重跑 OAuth。Token 儲存在 1Password 或系統鑰匙圈中，絕不會出現在專案檔案裡。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>
          </div>
          <h3>隨處可用</h3>
          <p>本機有瀏覽器、SSH 遠端連線、無頭 CI——系統自動偵測執行環境，在瀏覽器授權與無頭授權之間智慧切換。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </div>
          <h3>安全至上</h3>
          <p>Token 絕不碰觸專案檔案或 Git。交接包經過加密簽章。託管後端僅在記憶體中處理 Token，從不寫入磁碟。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6m-7-7h6m6 0h6"/><path d="M4.22 4.22l4.24 4.24m7.08 7.08l4.24 4.24m0-15.56l-4.24 4.24m-7.08 7.08l-4.24 4.24"/></svg>
          </div>
          <h3>MCP 原生</h3>
          <p>提供 14 個工具、3 個資源、2 個提示詞，全部附帶結構化輸出 Schema。開箱即用，任何 MCP 用戶端皆可接入。AI 助手以程式驅動完整流程。</p>
        </article>
        <article class="feature-card">
          <div class="feature-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>
          </div>
          <h3>可自行部署</h3>
          <p>所有元件皆可部署至你自己的 Cloudflare 帳號，支援自訂網域路由。完全掌控後端，零依賴第三方基礎建設。</p>
        </article>
      </div>
    </section>

    <!-- ====== QUICKSTART — EN ====== -->
    <section class="quickstart" data-lang="en">
      <div class="qs-card">
        <h3>Quick Start</h3>
        <p>Add Agent Labbook to your MCP client, then let the agent handle the rest.</p>
        <div class="code-block"><span class="code-comment"># OpenAI Codex</span><br/><span class="code-cmd">codex</span> mcp add labbook -- uvx agent-labbook mcp</div>
        <span class="qs-or">or</span>
        <div class="code-block"><span class="code-comment"># Anthropic Claude Code</span><br/><span class="code-cmd">claude</span> mcp add --scope project labbook -- uvx agent-labbook mcp</div>
      </div>
    </section>

    <!-- ====== QUICKSTART — 简中 ====== -->
    <section class="quickstart" data-lang="zh-hans">
      <div class="qs-card">
        <h3>\u5feb\u901f\u5f00\u59cb</h3>
        <p>把 Agent Labbook 添加到 MCP 客户端，剩下的交给 AI 助手。</p>
        <div class="code-block"><span class="code-comment"># OpenAI Codex</span><br/><span class="code-cmd">codex</span> mcp add labbook -- uvx agent-labbook mcp</div>
        <span class="qs-or">\u6216\u8005</span>
        <div class="code-block"><span class="code-comment"># Anthropic Claude Code</span><br/><span class="code-cmd">claude</span> mcp add --scope project labbook -- uvx agent-labbook mcp</div>
      </div>
    </section>

    <!-- ====== QUICKSTART — 繁中 ====== -->
    <section class="quickstart" data-lang="zh-hant">
      <div class="qs-card">
        <h3>\u5feb\u901f\u958b\u59cb</h3>
        <p>將 Agent Labbook 加入 MCP 用戶端，其餘交給 AI 助手搞定。</p>
        <div class="code-block"><span class="code-comment"># OpenAI Codex</span><br/><span class="code-cmd">codex</span> mcp add labbook -- uvx agent-labbook mcp</div>
        <span class="qs-or">\u6216\u8005</span>
        <div class="code-block"><span class="code-comment"># Anthropic Claude Code</span><br/><span class="code-cmd">claude</span> mcp add --scope project labbook -- uvx agent-labbook mcp</div>
      </div>
    </section>

    <!-- ====== GITHUB CTA ====== -->
    <div class="gh-cta" data-lang="en">
      <a href="https://github.com/binbinsh/agent-labbook" class="gh-link" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
        View on GitHub &rarr;
      </a>
    </div>
    <div class="gh-cta" data-lang="zh-hans">
      <a href="https://github.com/binbinsh/agent-labbook" class="gh-link" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
        \u5728 GitHub \u4e0a\u67e5\u770b &rarr;
      </a>
    </div>
    <div class="gh-cta" data-lang="zh-hant">
      <a href="https://github.com/binbinsh/agent-labbook" class="gh-link" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>
        \u5728 GitHub \u4e0a\u67e5\u770b &rarr;
      </a>
    </div>

    <footer>
      <p data-lang="en">&copy; ${year} <a href="https://gridheap.com/">Grid Heap</a>. All rights reserved. &nbsp;|&nbsp; <a href="https://superplanner.ai/privacy-policy/">Privacy Policy</a> &nbsp;|&nbsp; <a href="https://superplanner.ai/terms-of-use/">Terms of Use</a></p>
      <p data-lang="zh-hans">&copy; ${year} <a href="https://gridheap.com/">Grid Heap</a> \u7248\u6743\u6240\u6709 &nbsp;|&nbsp; <a href="https://superplanner.ai/privacy-policy/">\u9690\u79c1\u653f\u7b56</a> &nbsp;|&nbsp; <a href="https://superplanner.ai/terms-of-use/">\u4f7f\u7528\u6761\u6b3e</a></p>
      <p data-lang="zh-hant">&copy; ${year} <a href="https://gridheap.com/">Grid Heap</a> \u7248\u6b0a\u6240\u6709 &nbsp;|&nbsp; <a href="https://superplanner.ai/privacy-policy/">\u96b1\u79c1\u6b0a\u653f\u7b56</a> &nbsp;|&nbsp; <a href="https://superplanner.ai/terms-of-use/">\u4f7f\u7528\u689d\u6b3e</a></p>
    </footer>

    <script>
      function setLang(lang) {
        document.body.className = lang === 'en' ? '' : lang;
        document.querySelectorAll('.lang-btn').forEach(function(b) { b.classList.remove('active'); });
        var btn = document.getElementById('btn-' + lang);
        if (btn) btn.classList.add('active');
        try { localStorage.setItem('sp-lang', lang); } catch(e) {}
      }
      (function() {
        var saved = null;
        try { saved = localStorage.getItem('sp-lang'); } catch(e) {}
        if (saved && saved !== 'en') { setLang(saved); return; }
        if (saved) return;
        var nav = navigator.language || '';
        if (/^zh[\\-_](tw|hk|mo|hant)/i.test(nav) || nav === 'zh-Hant') { setLang('zh-hant'); }
        else if (/^zh/i.test(nav)) { setLang('zh-hans'); }
      })();
    </script>
  </body>
</html>`;
}

function handleLandingPage() {
  const headers = new Headers();
  headers.set("content-type", "text/html; charset=utf-8");
  headers.set("cache-control", "public, max-age=300, s-maxage=600");
  return new Response(landingPage(), { headers });
}

async function handleOauthContinue(request, env) {
  const url = new URL(request.url);
  const oauthSession = String(url.searchParams.get("oauth_session") || "").trim();
  if (!oauthSession) {
    return htmlResponse(errorPage("Missing Parameters", "oauth_session is required."), { status: 400 });
  }

  try {
    const sharedSession = await resolveSharedOauthSession(env, oauthSession);
    const accessToken = String(sharedSession.access_token || "").trim();
    const pageLimit = normalizePageLimit(sharedSession.page_limit);

    let initialCatalog = {
      resources: [],
      catalogLoaded: false,
      catalogError: null,
    };
    try {
      if (accessToken) {
        const preloadedCatalog = await buildSelectionCatalog(env, accessToken, pageLimit);
        initialCatalog = {
          resources: preloadedCatalog,
          catalogLoaded: true,
          catalogError: null,
        };
      }
    } catch (preloadError) {
      const message = String(preloadError?.message || preloadError || "Could not load the initial Notion catalog.");
      console.warn("Selection catalog preload failed:", message);
      initialCatalog = {
        resources: [],
        catalogLoaded: false,
        catalogError: message,
      };
    }

    return htmlResponse(
      selectionPage({
        baseUrl: getBaseUrl(request, env),
        state: {
          mode: sharedSession.mode || "headless",
          session_id: sharedSession.session_id || "",
          return_to: sharedSession.return_to || null,
          project_name: sharedSession.project_name || null,
          page_limit: pageLimit,
        },
        oauthSession,
        workspaceName: sharedSession.workspace_name || null,
        resources: initialCatalog.resources,
        catalogLoaded: initialCatalog.catalogLoaded,
        catalogError: initialCatalog.catalogError,
      }),
    );
  } catch (error) {
    return htmlResponse(errorPage("OAuth Session Failed", String(error.message || error)), { status: 500 });
  }
}

async function handleDiscoverChildren(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const authContext = await resolveAuthContext(env, payload);
    const accessToken = authContext.accessToken;

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
      return jsonResponse({ ok: true, resources: [] });
    }

    const nodeLimit = clampInteger(
      payload?.node_limit,
      DEFAULT_DISCOVERY_NODE_LIMIT,
      1,
      MAX_DISCOVERY_NODE_LIMIT,
    );
    const children = await discoverImmediateChildren(
      env,
      accessToken,
      pageIds,
      dataSourceIds,
      {
        node_limit: nodeLimit,
      },
    );

    return jsonResponse({
      ok: true,
      resources: children,
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
    const authContext = await resolveAuthContext(env, payload);
    const accessToken = authContext.accessToken;

    const pageLimit = normalizePageLimit(payload?.page_limit);
    const searchPayload = await buildSelectionCatalog(env, accessToken, pageLimit);
    return jsonResponse({
      ok: true,
      resources: searchPayload,
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleSearch(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed." }, { status: 405 });
  }

  try {
    const payload = await request.json();
    const query = String(payload?.query || "").trim();
    if (!query) {
      return jsonResponse({ ok: true, resources: [] });
    }

    const authContext = await resolveAuthContext(env, payload);
    const accessToken = authContext.accessToken;
    const limit = normalizeSearchLimit(payload?.limit);
    const searchPayload = await searchSelectableResources(env, accessToken, query, limit);
    return jsonResponse({
      ok: true,
      resources: searchPayload,
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
    const selectedResources = Array.isArray(payload?.selected_resources)
      ? payload.selected_resources.filter((item) => item && typeof item === "object")
      : [];

    const oauthSession = String(payload?.oauth_session || "").trim();
    if (!oauthSession) {
      return jsonResponse({ ok: false, error: "oauth_session is required." }, { status: 400 });
    }
    const finalizePayload = await postOauthJson(env, "/api/finalize-handoff", {
      oauth_session: oauthSession,
      selected_resources: selectedResources,
    });
    if (!finalizePayload.ok) {
      return jsonResponse(
        { ok: false, error: String(finalizePayload.error || "OAuth backend could not finalize the handoff.") },
        { status: 500 },
      );
    }

    return jsonResponse({
      ok: true,
      handoff_bundle: String(finalizePayload.handoff_bundle || "").trim(),
    });
  } catch (exc) {
    return jsonResponse({ ok: false, error: String(exc.message || exc) }, { status: 500 });
  }
}

async function handleHealth(request, env) {
  const baseUrl = getBaseUrl(request, env);
  return jsonResponse({
    ok: true,
    configured: Boolean(getOauthBaseUrl(env)),
    base_url: baseUrl,
    oauth_base_url: getOauthBaseUrl(env),
    continue_url: `${baseUrl}/oauth/continue`,
    notion_version: getNotionVersion(env),
  });
}

export default {
  async fetch(request, env) {
    const workerPath = getWorkerPath(request, env);
    if (workerPath === "/") {
      return handleLandingPage();
    }
    if (workerPath === "/health") {
      return handleHealth(request, env);
    }
    if (workerPath === "/oauth/continue") {
      return handleOauthContinue(request, env);
    }
    if (workerPath === "/api/discover-children") {
      return handleDiscoverChildren(request, env);
    }
    if (workerPath === "/api/catalog") {
      return handleCatalog(request, env);
    }
    if (workerPath === "/api/search") {
      return handleSearch(request, env);
    }
    if (workerPath === "/api/finalize-selection") {
      return handleFinalizeSelection(request, env);
    }
    return jsonResponse({ ok: false, error: "Not found." }, { status: 404 });
  },
};
