import { SELECTION_UI_CSS, SELECTION_UI_JS } from "../generated/selection_ui_bundle.js";

const DEFAULT_NOTION_VERSION = "2026-03-11";
const DEFAULT_PAGE_LIMIT = 500;
const MAX_PAGE_LIMIT = 500;
const STATE_TTL_SECONDS = 900;
const SELECTION_TOKEN_TTL_SECONDS = 3600;
const HANDOFF_BUNDLE_TTL_SECONDS = 3600;
const DEFAULT_DISCOVERY_DEPTH = 4;
const MAX_DISCOVERY_DEPTH = 6;
const DEFAULT_DISCOVERY_NODE_LIMIT = 200;
const MAX_DISCOVERY_NODE_LIMIT = 400;
const MAX_BLOCK_SCAN_LIMIT = 2000;
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
  const padded = `${value}${"=".repeat((4 - (value.length % 4)) % 4)}`
    .replaceAll("-", "+")
    .replaceAll("_", "/");
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
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
  return clampInteger(value, DEFAULT_PAGE_LIMIT, 1, MAX_PAGE_LIMIT);
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
  const collapsed = raw.replaceAll("-", "").toLowerCase();
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

async function discoverChildPages(env, accessToken, pageIds, options = {}) {
  const depthLimit = clampInteger(options.depth_limit, DEFAULT_DISCOVERY_DEPTH, 1, MAX_DISCOVERY_DEPTH);
  const nodeLimit = clampInteger(options.node_limit, DEFAULT_DISCOVERY_NODE_LIMIT, 1, MAX_DISCOVERY_NODE_LIMIT);

  const discovered = new Map();
  const queue = [];
  const visitedPages = new Set();
  let scannedBlockCount = 0;
  let truncated = false;

  for (const pageId of pageIds) {
    const cleanId = normalizeNotionIdLike(pageId);
    if (!cleanId || visitedPages.has(cleanId)) {
      continue;
    }
    visitedPages.add(cleanId);
    queue.push({
      page_id: cleanId,
      depth: 0,
      root_id: cleanId,
    });
  }

  while (queue.length && !truncated) {
    const current = queue.shift();
    const containerQueue = [current.page_id];
    const scannedContainers = new Set();

    while (containerQueue.length && !truncated) {
      const containerId = String(containerQueue.shift() || "").trim();
      if (!containerId || scannedContainers.has(containerId)) {
        continue;
      }
      scannedContainers.add(containerId);

      const blocks = await listAllBlockChildren(env, accessToken, containerId);
      scannedBlockCount += blocks.length;
      if (scannedBlockCount > MAX_BLOCK_SCAN_LIMIT) {
        truncated = true;
        break;
      }

      for (const block of blocks) {
        const blockId = normalizeNotionIdLike(block?.id);
        const blockType = String(block?.type || "").trim();

        if (blockType === "child_page" && blockId && !visitedPages.has(blockId)) {
          visitedPages.add(blockId);
          const childResource = await retrievePageResource(
            env,
            accessToken,
            blockId,
            String(block?.child_page?.title || "").trim() || null,
            {
              discovered_parent_id: current.page_id,
              discovered_root_id: current.root_id,
              discovered_depth: current.depth + 1,
              parent_type: "page_id",
              parent_id: current.page_id,
            },
          );
          discovered.set(blockId, childResource);

          if (discovered.size >= nodeLimit) {
            truncated = true;
            break;
          }
          if (current.depth + 1 < depthLimit) {
            queue.push({
              page_id: blockId,
              depth: current.depth + 1,
              root_id: current.root_id,
            });
          }
          continue;
        }

        if (block?.has_children && blockType !== "child_page" && blockId) {
          containerQueue.push(blockId);
        }
      }
    }
  }

  return {
    truncated,
    resources: Array.from(discovered.values()),
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

function selectionPage({ baseUrl, state, selectionToken, tokenPayload, resources, truncated }) {
  const bootstrap = {
    baseUrl,
    state,
    selectionToken,
    tokenPayload,
    resources,
    truncated: Boolean(truncated),
  };

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="color-scheme" content="light" />
    <title>Agent Labbook Authorization</title>
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
    const selectionToken = await signSignedPayload(env, {
      version: 1,
      purpose: "selection_session",
      issued_at: Math.floor(Date.now() / 1000),
      session_id: state.session_id,
      backend_url: baseUrl,
      token: sanitizedTokenPayload,
    });
    const searchPayload = await fetchSelectableResources(env, tokenPayload.access_token, state.page_limit || DEFAULT_PAGE_LIMIT);
    return htmlResponse(
      selectionPage({
        baseUrl,
        state,
        selectionToken,
        tokenPayload: sanitizedTokenPayload,
        resources: searchPayload.resources,
        truncated: searchPayload.truncated,
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
    const accessToken = String(payload?.access_token || "").trim();
    if (!accessToken) {
      return jsonResponse({ ok: false, error: "access_token is required." }, { status: 400 });
    }

    const pageIds = Array.from(
      new Set(
        (Array.isArray(payload?.page_ids) ? payload.page_ids : [])
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    );

    if (!pageIds.length) {
      return jsonResponse({ ok: true, truncated: false, resources: [] });
    }

    const discovery = await discoverChildPages(env, accessToken, pageIds, {
      depth_limit: payload?.depth_limit,
      node_limit: payload?.node_limit,
    });

    return jsonResponse({
      ok: true,
      truncated: discovery.truncated,
      resources: discovery.resources,
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
    const accessToken = String(payload?.access_token || "").trim();
    if (!accessToken) {
      return jsonResponse({ ok: false, error: "access_token is required." }, { status: 400 });
    }

    const pageLimit = normalizePageLimit(payload?.page_limit);
    const searchPayload = await fetchSelectableResources(env, accessToken, pageLimit);
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

    const signedSelection = await verifySignedPayload(env, selectionToken);
    ensureRecentPayload(signedSelection, SELECTION_TOKEN_TTL_SECONDS, "Selection session expired.");
    if (signedSelection?.purpose !== "selection_session") {
      return jsonResponse({ ok: false, error: "Invalid selection token." }, { status: 400 });
    }

    const tokenPayload = signedSelection?.token;
    if (!(tokenPayload && typeof tokenPayload === "object")) {
      return jsonResponse({ ok: false, error: "Selection token did not contain a token payload." }, { status: 400 });
    }

    const selectedResources = Array.isArray(payload?.selected_resources)
      ? payload.selected_resources.filter((item) => item && typeof item === "object")
      : [];

    const handoffBundle = await signSignedPayload(env, {
      version: 1,
      purpose: "handoff_bundle",
      issued_at: Math.floor(Date.now() / 1000),
      session_id: String(signedSelection.session_id || "").trim(),
      backend_url: String(signedSelection.backend_url || getBaseUrl(request, env)).trim(),
      token: tokenPayload,
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
    if (!handoffBundle) {
      return jsonResponse({ ok: false, error: "handoff_bundle is required." }, { status: 400 });
    }

    const signedHandoff = await verifySignedPayload(env, handoffBundle);
    ensureRecentPayload(signedHandoff, HANDOFF_BUNDLE_TTL_SECONDS, "Handoff bundle expired.");
    if (signedHandoff?.purpose !== "handoff_bundle") {
      return jsonResponse({ ok: false, error: "Invalid handoff bundle." }, { status: 400 });
    }
    if (sessionId && String(signedHandoff.session_id || "").trim() !== sessionId) {
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
