import http from "node:http";

import { expect, test } from "@playwright/test";

import { TEST_IDS, startWorkerTestServer } from "./support/worker-test-server.mjs";

function startLocalHandoffServer(expectedSessionId) {
  let resolveBody;
  const bodyPromise = new Promise((resolve) => {
    resolveBody = resolve;
  });

  const server = http.createServer((request, response) => {
    if (request.method !== "POST" || request.url !== "/oauth/handoff") {
      response.writeHead(404);
      response.end();
      return;
    }

    let rawBody = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      rawBody += chunk;
    });
    request.on("end", () => {
      const form = new URLSearchParams(rawBody);
      resolveBody({
        sessionId: form.get("session_id"),
        handoffBundle: form.get("handoff_bundle"),
      });
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      response.end(`<!doctype html><html><body><script>
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage(${JSON.stringify(
            {
              type: "agent-labbook-local-handoff-success",
              session_id: expectedSessionId,
            },
          )}, "*");
        }
      </script><h1>Connected</h1></body></html>`);
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        close: () =>
          new Promise((closeResolve, closeReject) => {
            server.close((error) => {
              if (error) {
                closeReject(error);
                return;
              }
              closeResolve();
            });
          }),
        url: `http://127.0.0.1:${address.port}/oauth/handoff`,
        bodyPromise,
      });
    });
  });
}

function startOauthBrokerServer(options = {}) {
  const calls = {
    resolve: [],
    finalize: [],
  };
  const mode = String(options.mode || "headless");
  const returnTo = options.returnTo ?? null;
  const sessionId = String(options.sessionId || "shared-session-1");
  const pageLimit = Number(options.pageLimit || 25);
  const workspaceName = String(options.workspaceName || "Shared OAuth Workspace");
  const workspaceId = String(options.workspaceId || "shared-workspace-id");
  const botId = String(options.botId || "shared-bot-id");
  const handoffBundle = String(options.handoffBundle || "shared-oauth-handoff-bundle");
  const continueTo = String(
    options.continueTo || "https://superplanner.ai/notion/agent-labbook/oauth/continue",
  );

  const server = http.createServer((request, response) => {
    let rawBody = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      rawBody += chunk;
    });
    request.on("end", () => {
      const body = rawBody ? JSON.parse(rawBody) : {};
      const acceptApiVersions = request.headers["x-notion-access-broker-accept-api-versions"] || null;
      if (request.method === "POST" && request.url === "/api/resolve-session") {
        calls.resolve.push({ body, acceptApiVersions });
        response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        response.end(
          JSON.stringify({
            ok: true,
            api_version: 1,
            supported_api_versions: [1],
            session: {
              integration: "agent-labbook",
              mode,
              session_id: sessionId,
              return_to: returnTo,
              continue_to: continueTo,
              project_name: "agent-labbook-e2e",
              page_limit: pageLimit,
              access_token: "shared-access-token",
              token_type: "bearer",
              workspace_name: workspaceName,
              workspace_id: workspaceId,
              bot_id: botId,
            },
          }),
        );
        return;
      }
      if (request.method === "POST" && request.url === "/api/finalize-handoff") {
        calls.finalize.push({ body, acceptApiVersions });
        response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        response.end(
          JSON.stringify({
            ok: true,
            api_version: 1,
            supported_api_versions: [1],
            handoff_bundle: handoffBundle,
          }),
        );
        return;
      }
      response.writeHead(404, { "content-type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ ok: false, api_version: 1, supported_api_versions: [1], error: "Not found" }));
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        baseUrl: `http://127.0.0.1:${address.port}`,
        calls,
        close: () =>
          new Promise((closeResolve, closeReject) => {
            server.close((error) => {
              if (error) {
                closeReject(error);
                return;
              }
              closeResolve();
            });
          }),
      });
    });
  });
}

test("shared oauth continue page supports remote search, on-demand expansion, and headless handoff", async ({ page }) => {
  const oauthBroker = await startOauthBrokerServer({
    mode: "headless",
    handoffBundle: "shared-headless-handoff-bundle",
  });
  const appServer = await startWorkerTestServer({
    oauthBaseUrl: oauthBroker.baseUrl,
  });

  try {
    await page.goto(`${appServer.baseUrl}/oauth/continue?oauth_session=test-oauth-session`);

    await expect(page.getByRole("heading", { name: "Choose Notion Content" })).toBeVisible();
    await expect(page.getByText("Shared OAuth Workspace")).toBeVisible();
    await expect(page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`)).toContainText("Project Hub");
    await expect(page.locator(`[data-resource-id="${TEST_IDS.ENGINEERING_HANDBOOK_ID}"]`)).toHaveCount(0);
    await expect(page.locator(`[data-resource-id="${TEST_IDS.PYNINI_PAGE_ID}"]`)).toHaveCount(0);

    const searchInput = page.getByPlaceholder("Search workspace by title, type, or ID");
    await searchInput.fill("Engineering");
    await expect(page.locator(`[data-resource-id="${TEST_IDS.ENGINEERING_HANDBOOK_ID}"]`)).toContainText(
      "Engineering Handbook",
    );
    expect(appServer.mockState.searchBodies.some((body) => body.query === "engineering")).toBeTruthy();

    await searchInput.fill("pynini");
    await expect(page.locator(`[data-resource-id="${TEST_IDS.PYNINI_PAGE_ID}"]`)).toContainText("Pynini Notes");
    expect(appServer.mockState.searchBodies.some((body) => body.query === "pynini")).toBeTruthy();

    await searchInput.fill("");
    const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
    await expect(projectHubRow).toBeVisible();
    await projectHubRow.getByRole("button", { name: "Expand nested items" }).click();
    await expect(page.locator(`[data-resource-id="${TEST_IDS.ROADMAP_DOC_ID}"]`)).toContainText("Roadmap Doc");
    await expect(page.locator(`[data-resource-id="${TEST_IDS.SPECS_DATA_SOURCE_ID}"]`)).toContainText("Specs DB");

    await projectHubRow.getByRole("checkbox").click();
    await expect(projectHubRow).toContainText("Includes subtree");
    await page.getByRole("button", { name: "Connect Selected" }).click();

    await expect(page.locator("textarea")).toHaveValue("shared-headless-handoff-bundle");
    expect(oauthBroker.calls.resolve.length).toBeGreaterThan(0);
    expect(
      oauthBroker.calls.resolve.every(
        (call) =>
          call.acceptApiVersions === "1" &&
          call.body.integration === "agent-labbook" &&
          call.body.oauth_session === "test-oauth-session",
      ),
    ).toBeTruthy();
    expect(oauthBroker.calls.finalize).toHaveLength(1);
    expect(oauthBroker.calls.finalize[0].acceptApiVersions).toBe("1");
    expect(oauthBroker.calls.finalize[0].body.oauth_session).toBe("test-oauth-session");
    expect(oauthBroker.calls.finalize[0].body.selected_resources[0].resource_id).toBe(TEST_IDS.PROJECT_HUB_ID);
    expect(oauthBroker.calls.finalize[0].body.selected_resources[0].selection_scope).toBe("subtree");
  } finally {
    await appServer.stop();
    await oauthBroker.close();
  }
});

test("continue page shows inline Notion load errors instead of blocking alerts", async ({ page }) => {
  const oauthBroker = await startOauthBrokerServer({
    mode: "headless",
    handoffBundle: "inline-error-bundle",
    workspaceName: "Live Test Workspace",
  });
  const appServer = await startWorkerTestServer({
    oauthBaseUrl: oauthBroker.baseUrl,
    forceUnauthorized: true,
  });
  const dialogs = [];
  page.on("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  });

  try {
    await page.goto(`${appServer.baseUrl}/oauth/continue?oauth_session=test-inline-error-session`);

    await expect(page.getByRole("heading", { name: "Choose Notion Content" })).toBeVisible();
    await expect(page.getByText("Live Test Workspace")).toBeVisible();
    await expect(page.getByText("Could not load available resources from Notion.")).toBeVisible();
    await expect(page.getByText("API token is invalid.")).toBeVisible();

    await page.getByPlaceholder("Search workspace by title, type, or ID").fill("engineering");
    await expect(page.getByText("Search could not reach Notion.")).toBeVisible();
    expect(dialogs).toEqual([]);
  } finally {
    await appServer.stop();
    await oauthBroker.close();
  }
});

test("prefixed backend routes work under /notion/agent-labbook", async ({ page }) => {
  const oauthBroker = await startOauthBrokerServer({
    mode: "headless",
    handoffBundle: "prefixed-handoff-bundle",
    continueTo: "https://superplanner.ai/notion/agent-labbook/oauth/continue",
  });

  try {
    const appServer = await startWorkerTestServer({
      basePath: "/notion/agent-labbook",
      oauthBaseUrl: oauthBroker.baseUrl,
    });
    try {
      await page.goto(`${appServer.baseUrl}/oauth/continue?oauth_session=test-prefixed-oauth-session`);

      const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
      await expect(projectHubRow).toBeVisible();
      await projectHubRow.getByRole("checkbox").click();
      await page.getByRole("button", { name: "Connect Selected" }).click();

      await expect(page.locator("textarea")).toHaveValue("prefixed-handoff-bundle");
      expect(oauthBroker.calls.resolve[0].acceptApiVersions).toBe("1");
      expect(oauthBroker.calls.resolve[0].body.oauth_session).toBe("test-prefixed-oauth-session");
      expect(oauthBroker.calls.finalize[0].body.selected_resources[0].resource_id).toBe(TEST_IDS.PROJECT_HUB_ID);
    } finally {
      await appServer.stop();
    }
  } finally {
    await oauthBroker.close();
  }
});

test("local browser flow falls back to showing the handoff bundle when localhost callback is unreachable", async ({
  page,
}) => {
  const oauthBroker = await startOauthBrokerServer({
    mode: "local_browser",
    sessionId: "e2e-session-local-fallback",
    returnTo: "http://127.0.0.1:8765/oauth/handoff",
    handoffBundle: "local-browser-fallback-bundle",
  });
  const appServer = await startWorkerTestServer({
    oauthBaseUrl: oauthBroker.baseUrl,
  });

  try {
    await page.goto(`${appServer.baseUrl}/oauth/continue?oauth_session=test-local-browser-fallback`);
    const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
    await projectHubRow.getByRole("checkbox").click();
    await page.getByRole("button", { name: "Connect Selected" }).click();

    await expect(page.getByText("could not reach the MCP server on 127.0.0.1")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("textarea")).toHaveValue("local-browser-fallback-bundle");
    expect(oauthBroker.calls.resolve[0].acceptApiVersions).toBe("1");
    expect(oauthBroker.calls.resolve[0].body.oauth_session).toBe("test-local-browser-fallback");
  } finally {
    await appServer.stop();
    await oauthBroker.close();
  }
});

test("local browser flow can deliver the handoff through a popup localhost navigation", async ({ page }) => {
  const sessionId = "e2e-session-local-success";
  const localServer = await startLocalHandoffServer(sessionId);
  const oauthBroker = await startOauthBrokerServer({
    mode: "local_browser",
    sessionId,
    returnTo: localServer.url,
    handoffBundle: "local-browser-success-bundle",
  });
  const appServer = await startWorkerTestServer({
    oauthBaseUrl: oauthBroker.baseUrl,
  });

  try {
    await page.goto(`${appServer.baseUrl}/oauth/continue?oauth_session=test-local-browser-success`);
    const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
    await projectHubRow.getByRole("checkbox").click();

    const popupPromise = page.waitForEvent("popup");
    await page.getByRole("button", { name: "Connect Selected" }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState("domcontentloaded");

    await expect(page.getByText("The handoff was sent back to the local MCP server.")).toBeVisible();
    const delivered = await localServer.bodyPromise;
    expect(delivered.sessionId).toBe(sessionId);
    expect(delivered.handoffBundle).toBe("local-browser-success-bundle");
  } finally {
    await appServer.stop();
    await oauthBroker.close();
    await localServer.close();
  }
});
