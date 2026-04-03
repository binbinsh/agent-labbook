import http from "node:http";

import worker from "../../../worker/src/index.js";

export const TEST_IDS = {
  PROJECT_HUB_ID: "11111111-1111-1111-1111-111111111111",
  TEAM_SPACE_ID: "22222222-2222-2222-2222-222222222222",
  ENGINEERING_HANDBOOK_ID: "33333333-3333-3333-3333-333333333333",
  ROADMAP_DOC_ID: "44444444-4444-4444-4444-444444444444",
  CHILD_DATABASE_ID: "55555555-5555-5555-5555-555555555555",
  SPECS_DATA_SOURCE_ID: "66666666-6666-6666-6666-666666666666",
  PYNINI_PAGE_ID: "77777777-7777-7777-7777-777777777777",
  HIDDEN_PARENT_ID: "88888888-8888-8888-8888-888888888888",
};

function jsonResponse(payload, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("content-type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(payload), {
    ...init,
    headers,
  });
}

function makePage({ id, title, parent = { type: "workspace" }, url }) {
  return {
    object: "page",
    id,
    url: url || `https://www.notion.so/${id.replaceAll("-", "")}`,
    parent,
    last_edited_time: "2026-04-03T12:00:00.000Z",
    properties: {
      title: {
        id: "title",
        type: "title",
        title: [{ plain_text: title }],
      },
    },
  };
}

function makeDataSource({ id, title, parent = { type: "workspace" }, url }) {
  return {
    object: "data_source",
    id,
    url: url || `https://www.notion.so/${id.replaceAll("-", "")}`,
    parent,
    last_edited_time: "2026-04-03T12:00:00.000Z",
    title: [{ plain_text: title }],
  };
}

async function readRequestJson(request) {
  const raw = await request.text();
  return raw ? JSON.parse(raw) : {};
}

async function handleNotionRequest(request, mockState) {
  const url = new URL(request.url);
  const pathname = url.pathname;

  if (pathname === "/v1/oauth/token" && request.method === "POST") {
    mockState.tokenExchangeCount += 1;
    return jsonResponse({
      access_token: "test-access-token",
      refresh_token: "test-refresh-token",
      token_type: "bearer",
      bot_id: "test-bot-id",
      workspace_id: "test-workspace-id",
      workspace_name: "Test Workspace",
      workspace_icon: null,
      duplicated_template_id: null,
      owner: {
        type: "user",
        user: {
          id: "test-user-id",
        },
      },
    });
  }

  if (pathname === "/v1/search" && request.method === "POST") {
    const body = await readRequestJson(request);
    mockState.searchBodies.push(body);
    const query = String(body.query || "").trim().toLowerCase();
    if (query === "engineering") {
      return jsonResponse({
        object: "list",
        results: [
          makePage({
            id: TEST_IDS.ENGINEERING_HANDBOOK_ID,
            title: "Engineering Handbook",
          }),
        ],
        has_more: false,
        next_cursor: null,
      });
    }
    if (query === "pynini") {
      return jsonResponse({
        object: "list",
        results: [
          makePage({
            id: TEST_IDS.PYNINI_PAGE_ID,
            title: "Pynini Notes",
            parent: {
              type: "page_id",
              page_id: TEST_IDS.HIDDEN_PARENT_ID,
            },
          }),
        ],
        has_more: false,
        next_cursor: null,
      });
    }
    return jsonResponse({
      object: "list",
      results: [
        makePage({
          id: TEST_IDS.PROJECT_HUB_ID,
          title: "Project Hub",
        }),
        makePage({
          id: TEST_IDS.TEAM_SPACE_ID,
          title: "Team Space",
        }),
      ],
      has_more: false,
      next_cursor: null,
    });
  }

  if (pathname === `/v1/blocks/${TEST_IDS.PROJECT_HUB_ID}/children` && request.method === "GET") {
    mockState.blockChildrenRequests.push(TEST_IDS.PROJECT_HUB_ID);
    return jsonResponse({
      object: "list",
      results: [
        {
          object: "block",
          id: TEST_IDS.ROADMAP_DOC_ID,
          type: "child_page",
          has_children: false,
          child_page: {
            title: "Roadmap Doc",
          },
        },
        {
          object: "block",
          id: TEST_IDS.CHILD_DATABASE_ID,
          type: "child_database",
          has_children: false,
          child_database: {
            title: "Specs Database",
          },
        },
      ],
      has_more: false,
      next_cursor: null,
    });
  }

  if (pathname === `/v1/pages/${TEST_IDS.ROADMAP_DOC_ID}` && request.method === "GET") {
    return jsonResponse(
      makePage({
        id: TEST_IDS.ROADMAP_DOC_ID,
        title: "Roadmap Doc",
        parent: {
          type: "page_id",
          page_id: TEST_IDS.PROJECT_HUB_ID,
        },
      }),
    );
  }

  if (pathname === `/v1/databases/${TEST_IDS.CHILD_DATABASE_ID}` && request.method === "GET") {
    return jsonResponse({
      object: "database",
      id: TEST_IDS.CHILD_DATABASE_ID,
      data_sources: [
        {
          id: TEST_IDS.SPECS_DATA_SOURCE_ID,
          name: "Specs DB",
        },
      ],
    });
  }

  if (pathname === `/v1/data_sources/${TEST_IDS.SPECS_DATA_SOURCE_ID}` && request.method === "GET") {
    return jsonResponse(
      makeDataSource({
        id: TEST_IDS.SPECS_DATA_SOURCE_ID,
        title: "Specs DB",
        parent: {
          type: "page_id",
          page_id: TEST_IDS.PROJECT_HUB_ID,
        },
      }),
    );
  }

  return jsonResponse(
    {
      code: "object_not_found",
      message: `No mock for ${request.method} ${pathname}`,
    },
    { status: 404 },
  );
}

async function toNodeResponse(response, res) {
  res.writeHead(response.status, Object.fromEntries(response.headers.entries()));
  const body = Buffer.from(await response.arrayBuffer());
  res.end(body);
}

export async function startWorkerTestServer() {
  const originalFetch = globalThis.fetch;
  const mockState = {
    tokenExchangeCount: 0,
    searchBodies: [],
    blockChildrenRequests: [],
  };
  const env = {
    NOTION_CLIENT_ID: "test-client-id",
    NOTION_CLIENT_SECRET: "test-client-secret",
    NOTION_VERSION: "2026-03-11",
    PUBLIC_BASE_URL: "",
  };

  globalThis.fetch = async (input, init) => {
    const request = input instanceof Request && init === undefined ? input : new Request(input, init);
    if (request.url.startsWith("https://api.notion.com/")) {
      return handleNotionRequest(request, mockState);
    }
    return originalFetch(input, init);
  };

  const server = http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const chunk of req) {
        chunks.push(chunk);
      }
      const bodyBuffer = chunks.length ? Buffer.concat(chunks) : null;
      const request = new Request(`${env.PUBLIC_BASE_URL}${req.url}`, {
        method: req.method,
        headers: req.headers,
        body: bodyBuffer && !["GET", "HEAD"].includes(String(req.method || "").toUpperCase()) ? bodyBuffer : undefined,
        duplex: "half",
      });
      const response = await worker.fetch(request, env);
      await toNodeResponse(response, res);
    } catch (error) {
      res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
      res.end(error instanceof Error ? error.stack || error.message : String(error));
    }
  });

  await new Promise((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Could not determine test server address.");
  }

  env.PUBLIC_BASE_URL = `http://${address.address}:${address.port}`;

  return {
    baseUrl: env.PUBLIC_BASE_URL,
    mockState,
    async stop() {
      globalThis.fetch = originalFetch;
      await new Promise((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      });
    },
  };
}
