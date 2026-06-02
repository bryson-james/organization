# gdrive MCP server

A small Model Context Protocol server that lets Paperclip's Claude Code agents
read, list, upload, and share files in Google Drive — including Shared Drives.

It runs as a **stdio subprocess** of `claude` (no separate service, no SSE,
no public URL).

## Setup (production / Railway)

1. Create a Google Cloud service account, enable the **Google Drive API**, and
   download its JSON key.
2. In your target Shared Drive: **Manage members → add the service account
   email → Content manager** (or higher).
3. In Railway, set the following environment variable on the Paperclip service:

   ```
   GOOGLE_SERVICE_ACCOUNT_JSON=<paste the entire JSON contents>
   ```

   (Base64-encoded is also accepted.)

4. On container start, `scripts/docker-entrypoint.sh` will register this MCP
   with Claude Code at user scope. Registration persists across deploys
   because `/paperclip/.claude` is on the Railway volume.

## Tools

| Tool | Purpose |
|---|---|
| `list_files` | List files (supports `drive_id` for a single Shared Drive) |
| `get_file_info` | Metadata for one file |
| `create_folder` | Create a folder (in My Drive or a Shared Drive) |
| `upload_file` | Upload a base64-encoded file (works in Shared Drives) |
| `download_file` | Download a file (<=10MB) as base64 |
| `share_file` | Grant a user reader/writer/commenter/owner |
| `debug_api_connection` | Sanity check the service account |

## Local sanity check (inside the container)

```
GOOGLE_SERVICE_ACCOUNT_JSON='{...}' \
  python /app/mcp-gdrive/main.py --transport stdio
```

(You won't see output — stdio MCP servers speak JSON-RPC over stdio.)
The proper smoke test is from `claude`:

```
/mcp
# then ask: "run the gdrive debug_api_connection tool"
```

## Credits

Built on [asadudin/mcp-server-gdrive](https://github.com/asadudin/mcp-server-gdrive)
with patches for Shared Drive support and env-var credential loading.
